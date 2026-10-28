"""Estimate the unknown DC (brightness/colour) offset of a JPEG scan decoded mid-stream.

Decoding from the middle of a scan gives every block's DC value minus one unknown
constant per component. We return the correction to add, per component.
"""
import numpy as np


def estimate_v1(donor, dcs, flats):
    """Original heuristic: luma median -> 115, chroma medians -> neutral."""
    out = []
    for ci in range(len(donor.scan)):
        q = donor.dc_quant(ci); want = (115 - 128) * 8 / q if ci == 0 else 0.0
        out.append(int(round(want - float(np.median(dcs[ci])))))
    return out


def estimate_v2(donor, dcs, flats):
    """Constraint + clipping + bright-neutral estimator.

    1. Block means must stay inside 0..255, which bounds each offset.
    2. Blown highlights / crushed shadows show up as many perfectly flat luma blocks
       sitting exactly at the extreme DC value; they pin the luma offset exactly.
    3. Chroma: clipped-white blocks are neutral; otherwise bright blocks are mostly
       near-neutral (white walls, sky, clothing), blended with the global median.
    """
    ny = donor.comps[donor.scan[0][0]]["H"] * donor.comps[donor.scan[0][0]]["V"]
    y = np.asarray(dcs[0], np.int64); yf = np.asarray(flats[0], bool)
    nmcu = len(y) // ny
    if nmcu < 20:
        return estimate_v1(donor, dcs, flats)
    q0 = donor.dc_quant(0)
    lo0, hi0 = int(np.ceil(-1024 / q0)), int(np.floor(1016 / q0))
    # robust feasible range (ignore a few garbage blocks right after resync)
    ylo, yhi = np.percentile(y, 0.05), np.percentile(y, 99.95)
    c_lo, c_hi = lo0 - ylo, hi0 - yhi
    c_y = None
    # clipping detection
    top, bot = y.max(), y.min()
    n_top = int(np.sum((y >= top - 0) & yf)); n_bot = int(np.sum((y <= bot) & yf))
    need = max(12, int(0.0015 * len(y)))
    sat_mask = None
    if n_top >= need:
        c_y = hi0 - top; sat_mask = (y >= top) & yf
    elif n_bot >= need:
        c_y = lo0 - bot
    if c_y is None:
        c_y = (115 - 128) * 8 / q0 - float(np.median(y))
        if c_lo <= c_hi:
            c_y = min(max(c_y, c_lo), c_hi)
    out = [int(round(c_y))]
    # per-MCU luma (mean of the MCU's Y blocks), corrected
    ym = y[:nmcu * ny].reshape(nmcu, ny).mean(1) + c_y
    sat_mcu = sat_mask[:nmcu * ny].reshape(nmcu, ny).all(1) if sat_mask is not None else None
    for ci in range(1, len(donor.scan)):
        c = np.asarray(dcs[ci], np.int64)
        per = len(c) // nmcu if nmcu else 1
        cm = c[:nmcu * per].reshape(nmcu, per).mean(1) if per > 1 else c[:nmcu]
        q = donor.dc_quant(ci)
        lo, hi = int(np.ceil(-1024 / q)), int(np.floor(1016 / q))
        if sat_mcu is not None and sat_mcu.sum() >= 8:
            est = -float(np.median(cm[sat_mcu]))
        else:
            bright = cm[ym >= np.percentile(ym, 85)]
            est = -(0.5 * float(np.median(bright)) + 0.5 * float(np.median(cm)))
        flo, fhi = lo - np.percentile(c, 0.05), hi - np.percentile(c, 99.95)
        if flo <= fhi:
            est = min(max(est, flo), fhi)
        out.append(int(round(est)))
    return out
