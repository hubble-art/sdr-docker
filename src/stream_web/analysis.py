"""Offline diagnostics for a recorded IQ segment (the /api/record_analyze endpoint).

Decodes the packets in the recording via hubble_satnet_decoder, runs the decoder's per-packet
signal analysis on each, and renders a plain-text diagnostic report.

Note: currently only packets that successfully decode are analyzed.
"""
import logging

import numpy as np
from hubble_satnet_decoder import analyze_packet, decode_signal, packet_symbol_grid

from . import config
from .timing import correct_symbol_edges

log = logging.getLogger(__name__)

# 1. Packet detection

def _window_offsets(n: int, win: int) -> list[int]:
    """Decoder-window start offsets covering the recording, overlapping by more than the
    longest packet (~0.53 s) so every packet lands fully inside at least one window."""
    if n <= win:
        return [0]
    step = max(1, win - int(config.DECODE_INTERVAL_S * config.SAMPLE_RATE))
    offsets = list(range(0, n - win + 1, step))
    if offsets[-1] != n - win:
        offsets.append(n - win)
    return offsets


def _is_duplicate(pkt: dict, seen: dict[tuple, list[float]]) -> bool:
    """True if *pkt* is the same packet as one already accepted -- same sequence number,
    auth tag, and payload, within config.DEDUP_START_TOL_S. This collapses a packet caught in two
    overlapping windows while keeping genuinely distinct packets (which differ in payload/
    auth or are further apart in time, even if a short seq_num happens to repeat).

    *seen* buckets accepted packets' abs_time_s by (seq_num, auth_tag, payload_val), so only
    packets that could possibly match are compared -- O(1) on average instead of scanning every
    packet accepted so far. If *pkt* is new, its time is recorded in the bucket."""
    key = (pkt.get("seq_num"), pkt.get("auth_tag"), pkt.get("payload_val"))
    times = seen.setdefault(key, [])
    if any(abs(pkt["abs_time_s"] - t) < config.DEDUP_START_TOL_S for t in times):
        return True
    times.append(pkt["abs_time_s"])
    return False


def decode_packets(iq: np.ndarray) -> list[dict]:
    """Run the decoder over the recording and return each decoded packet once, in time order.

    The decoder works on a fixed-size window, so we slide overlapping windows across the
    recording and keep the fully-decoded packets, deduped by _is_duplicate (a packet caught
    in two overlapping windows is reported once). The decoded *attempt* supplies start_sample
    (the preamble location); the *packet* supplies the decode-only fields (freq offset, PDU
    RS corrections). One failed window is logged and skipped -- it never aborts the capture.
    """
    sr, win = config.SAMPLE_RATE, config.DECODE_SAMPLES
    accepted: list[dict] = []
    seen: dict[tuple, list[float]] = {}
    for offset in _window_offsets(len(iq), win):
        try:
            packets, _detections, attempts = decode_signal(iq[offset:offset + win])
        except Exception:
            log.warning("decode_signal failed on window at offset %d; skipping", offset,
                        exc_info=True)
            continue
        # The 'start sample' we need lives only on the decoded attempts.
        starts = {(a.get("ntw_id"), a.get("seq_num")): a.get("start_sample")
            for a in attempts
            if a.get("decoded") and a.get("start_sample") is not None}

        for p in packets:
            # Searching through attempted decodes with successful decodes to find
            # the start sample for this packet (by ntw_id + seq_num)
            start = starts.get((p.get("ntw_id"), p.get("seq_num")))
            if start is None:
                continue
            d = dict(p)
            d["abs_start"] = offset + start
            d["abs_time_s"] = d["abs_start"] / sr
            if _is_duplicate(d, seen): # If duplicate, don't save
                continue
            n_sym, slot = packet_symbol_grid(d)

            # Find nominal start and end of packet, then correct the symbol edges
            lo = max(0, d["abs_start"] - slot)
            hi = min(len(iq), d["abs_start"] + n_sym * slot + slot)
            local = correct_symbol_edges(iq[lo:hi], d["abs_start"] - lo, 0, n_sym, 0, slot,
                                         config.samples_per_symbol)
            d["edges"] = [(s + lo, e + lo) for s, e in local]   # back to absolute indices
            accepted.append(d)
    accepted.sort(key=lambda d: d["abs_start"])
    return accepted


# 2. Capture-level summary + orchestration

def _rs_corrections(packets: list[dict]) -> dict:
    """Return RS corrections, which are data members of the packet dict."""
    def col(key):
        return [float(p[key]) for p in packets
                if isinstance(p.get(key), (int, float)) and not isinstance(p.get(key), bool)]
    hdr, pdu = col("header_n_corr"), col("pdu_n_corr")
    return {"header_mean": round(float(np.mean(hdr)), 2) if hdr else None,
            "pdu_mean": round(float(np.mean(pdu)), 2) if pdu else None,
            "pdu_max": int(max(pdu)) if pdu else None}


def _packet_info(pkt: dict) -> dict:
    return {"abs_time_s": round(float(pkt["abs_time_s"]), 4),
            "channel_num": pkt.get("channel_num"), "chipset": pkt.get("chipset")}


def _packet_line(pkt: dict, timing: dict, amplitude: dict) -> dict:
    """Report packet info in one line."""
    return {**_packet_info(pkt),
            "sym_mean_ms": timing["sym_mean_ms"], "gap_mean_ms": timing["gap_mean_ms"],
            "total_drift_us": timing["total_drift_us"],
            "amp_mean_dbfs": amplitude["mean_dbfs"], "snr_db": amplitude["snr_db"]}


def analyze_recording(iq: np.ndarray) -> dict:
    """Analyze a recorded IQ segment: decode the packets, run the decoder's per-packet analysis
    on each, pick the healthiest as representative, and summarise. Returns the report dict
    consumed by build_report (the endpoint adds the 'capture' block)."""

    # Decode packets from IQ samples
    packets = decode_packets(iq)
    if not packets:
        return {"summary": {"packets": 0}, "representative": None, "packets": []}

    # Invoke hubble-satnet-decoder's analysis
    analyses = [analyze_packet(iq, p, p["edges"]) for p in packets]

    # Define the best packet based on snr_db
    def _quality(i):
        snr = analyses[i]["amplitude"]["snr_db"]
        return (snr if snr is not None else float("-inf"), packets[i].get("num_pdu_symbols") or 0)

    rep_i = max(range(len(packets)), key=_quality)
    rep, rep_an = packets[rep_i], analyses[rep_i]

    return {
        "summary": {"packets": len(packets), "rs_corrections": _rs_corrections(packets),
                    "chipset": rep_an["chipset"],
                    "freq_delta_hz": round(float(rep["freq_delta_hz"]), 1)
                    if rep.get("freq_delta_hz") is not None else None},
        "representative": {"info": _packet_info(rep), "timing": rep_an["timing"],
                           "amplitude": rep_an["amplitude"], "channels": rep_an["channels"],
                           "hops": rep_an["hops"]},
        "packets": [_packet_line(packets[i], analyses[i]["timing"], analyses[i]["amplitude"])
                    for i in range(len(packets))],
    }


# 3. Report formatting

def build_report(report: dict) -> str:
    """Render the analysis dict into the plain-text diagnostic file the customer returns."""
    s = report["summary"]
    lines = ["=== Hubble sat-record diagnostic ==="]
    # Show capture statistics
    cap = report.get("capture")
    if cap:
        lines.append(f"capture: {cap['seconds']} s @ {cap['sample_rate_hz']} Hz, "
                     f"center {cap['center_freq_hz'] / 1e6:.4f} MHz, {cap['n_samples']} samples")
    lines.append(f"decoded packets: {s['packets']}")

    # Show representative packet
    rep = report.get("representative")
    if not rep:
        lines.append("No packets decoded -- check the device is transmitting on the "
                     "expected channel.")
        return "\n".join(lines) + "\n"

    # Show information about the representative packet
    info = rep["info"]
    lines += ["", "=== representative packet ===",
              f"  t={info['abs_time_s']}s  channel {info['channel_num']}  "
              f"chipset {info['chipset']}"]
    _timing_section(lines, rep["timing"])
    _channel_section(lines, rep["channels"], rep["hops"])
    _amplitude_section(lines, rep["amplitude"])
    _frequency_section(lines, s)

    # Show RS corrections and per-packet table
    rs = s["rs_corrections"]
    lines += ["", f"RS corrections: header_mean={rs['header_mean']} "
              f"pdu_mean={rs['pdu_mean']} pdu_max={rs['pdu_max']}"]
    _packet_table(lines, report["packets"])
    return "\n".join(lines) + "\n"

# Write up timing section, channel section, amplitude section, frequency section for each symbol
def _timing_section(lines: list, t: dict) -> None:
    lines += ["", "PER-SYMBOL TIMING (duration, gap; drift = slot midpoint vs. ideal grid)"]
    if not t["per_symbol"]:
        lines.append("  n/a (not enough symbols)")
        return
    for r in t["per_symbol"]:
        gap = f", gap={r['gap_us']:.2f} us" if r["gap_us"] is not None else ""
        if r["drift_us"] is not None:
            drift = f", drift={r['drift_us']:+.2f} us, rate={r['rate_us_per_sym']:+.2f} us/sym"
        else:
            drift = ", drift=n/a"
        lines.append(f"  Symbol {r['idx']:>2}: duration={r['duration_us']:.2f} us{gap}{drift}")
    lines.append(f"  Overall drift: {t['total_drift_us']:+.2f} us "
                 f"({t['drift_us_per_sym']:+.2f} us/symbol over {len(t['per_symbol'])} symbols)")


def _channel_section(lines: list, ch: dict | None, hops: dict | None) -> None:
    lines += ["", "PER-SYMBOL FREQUENCY / CHANNEL HOPPING"]
    if hops:
        lines.append(f"  hop sequence idx {hops['hop_seq_idx']}, start channel "
                     f"{hops['start_channel']}, expected channels {hops['expected_channels']}")
    if not ch:
        lines.append("  n/a (channel/hop info unavailable for this packet)")
        return
    c = ch["calibration"]
    lines += [f"  calibration: F0={c['f0_hz']} Hz, FSK step={c['step_hz']} Hz, "
              f"channel width={c['channel_width_hz']} Hz",
              f"  channel spacing: {c['spacing_hz']} Hz ({c['spacing_source']})"]
    for r in ch["per_symbol"]:
        mark = "ok" if r["in_window"] else f"OUT by {r['off_by_hz']} Hz"
        lines.append(f"  Symbol {r['idx']:>2}: ch {r['channel']:>2}, "
                     f"freq={r['freq_hz']:>10.1f} Hz  [{mark}]")
    lines.append(f"  channel validation: {'PASS' if ch['all_valid'] else 'FAIL'} "
                 f"({ch['n_in_window']}/{len(ch['per_symbol'])} symbols in window)")


def _amplitude_section(lines: list, a: dict) -> None:
    lines += ["", "PER-SYMBOL AMPLITUDE (RMS dBFS; SNR above the inter-symbol noise floor)"]
    if not a["per_symbol"]:
        lines.append("  n/a (not enough symbols)")
        return
    for r in a["per_symbol"]:
        snr = f", snr={r['snr_db']:.2f} dB" if r["snr_db"] is not None else ""
        lines.append(f"  Symbol {r['idx']:>2}: amp={r['amp_dbfs']:.2f} dBFS{snr}")
    lines.append(f"  summary: mean {a['mean_dbfs']} dBFS, dropoff (p-p) {a['dropoff_db']} dB, "
                 f"noise floor {a['noise_floor_dbfs']} dBFS, SNR {a['snr_db']} dB")


def _frequency_section(lines: list, s: dict) -> None:
    # Prints actual vs expected synth resolution and chipset info
    ch = s["chipset"]
    lines += ["", "FREQUENCY / CHIPSET",
              f"  offset from center: {s['freq_delta_hz']} Hz",
              f"  chipset (decoder): {ch['chipset']}",
              f"  synth-res: measured {ch['measured_synth_res']} Hz "
              f"(chipset nominal {ch['nominal_synth_res']} Hz)"]


def _packet_table(lines: list, packets: list[dict]) -> None:
    # Prints general packet info for that recording
    lines += ["", "PER-PACKET (all packets)"]
    for p in packets:
        lines.append(f"  t={p['abs_time_s']}s ch{p['channel_num']} {p['chipset']}: "
                     f"sym={p['sym_mean_ms']}ms gap={p['gap_mean_ms']}ms "
                     f"drift={p['total_drift_us']}us amp={p['amp_mean_dbfs']}dBFS "
                     f"snr={p['snr_db']}dB")
