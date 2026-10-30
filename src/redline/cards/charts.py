"""Inline SVG plots for the Redline cards.

Only the MARKS live here (bars, gridlines); everything made of words is
HTML, laid out by the stylesheet, except the figures that sit ON a mark. Drawn in
the card's own pixel grid (no viewBox scaling), so a stroke of 1 is a hairline.

Colours come from theme.css through the `plot__*` classes -- never hardcode a hex
here, or the palette stops being a single source of truth.

One vocabulary for every plot: a solid bar is what was done, a pale
bar behind or between it is its frame (the plan, the recoveries), and there is no
line but the gridlines. No reference lines, no dashes, no ticks: a threshold is
said by the colour of the bar that crossed it, anything else in words.
"""

from redline.coach.fr import cards as text

from .format import fmt_num, fmt_pace, fmt_rest, nice_ticks
from .html import esc

CONTENT_W = 952       # 1080 card - 2 * 64 padding
GUTTER = 46           # left strip for the y-axis labels
TOP_BAND = 26         # above the plot: the unit caption, inside the SVG box
AXIS_BAND = 40        # under the plot: category labels
SUB_BAND = 24         # under those: the second line (a word, a heart rate)
BAR_W = 24            # what was done
PLAN_W = 46           # the plan, drawn behind it: wider, so its top stays
                      # visible on both sides even when the bar overshoots it


def _text(x: float, y: float, content: str, cls: str, anchor: str = "middle") -> str:
    return (f'<text x="{x:.1f}" y="{y:.1f}" class="{cls}" text-anchor="{anchor}" '
            f'dominant-baseline="middle">{esc(content)}</text>')


def bars(items: list, *, height: int = 300, width: int = CONTENT_W,
         unit: str = "") -> str:
    """A column chart with a zero baseline.

    items: {label, value, planned?, note?}. `planned` draws the plan as a pale,
    wider column BEHIND the bar: under-done leaves pale room above the bar,
    over-done shows the bar standing out of it. `note` is a word under the label
    ("qualité"), said instead of drawn.

    The 1.22 headroom above the tallest column is what keeps the value labels
    inside the SVG box -- the render guard fails the card if anything escapes it.
    """
    if not items:
        return ""
    values = [float(item.get("value") or 0) for item in items]
    planned = [float(item.get("planned") or 0) for item in items]
    y_max = max(max(values + planned) * 1.22, 5.0)

    has_note = any(item.get("note") for item in items)
    total_h = TOP_BAND + height + AXIS_BAND + (SUB_BAND if has_note else 0)
    x0, x1 = GUTTER, width
    slot = (x1 - x0) / len(items)

    def y_of(value: float) -> float:
        """Data value -> y, with the plot pushed down by the unit caption band."""
        return TOP_BAND + height - (value / y_max) * height

    parts = [f'<svg class="plot" width="{width}" height="{total_h}" '
             f'viewBox="0 0 {width} {total_h}" xmlns="http://www.w3.org/2000/svg">']

    for tick in nice_ticks(0, y_max, steps=(2, 5, 10, 20, 25), max_ticks=6):
        y = y_of(tick)
        parts.append(f'<line x1="{x0}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}" class="plot__grid"/>')
        parts.append(_text(x0 - 12, y, f"{tick:g}", "plot__tick", anchor="end"))
    if unit:
        parts.append(_text(0, TOP_BAND / 2, unit, "plot__note", anchor="start"))

    base = y_of(0)
    for i, item in enumerate(items):
        cx = x0 + slot * (i + 0.5)
        value, plan = values[i], planned[i]
        if plan > 0:
            y = y_of(plan)
            parts.append(f'<rect x="{cx - PLAN_W / 2:.1f}" y="{y:.1f}" width="{PLAN_W}" '
                         f'height="{base - y:.1f}" rx="3" class="plot__ghost"/>')
        if value > 0:
            y = y_of(value)
            parts.append(f'<rect x="{cx - BAR_W / 2:.1f}" y="{y:.1f}" width="{BAR_W}" '
                         f'height="{base - y:.1f}" rx="3" class="plot__bar"/>')
            # Above whichever column is taller, so it never sits inside the plan.
            parts.append(_text(cx, y_of(max(value, plan)) - 15, fmt_num(value), "plot__value"))

        parts.append(_text(cx, base + AXIS_BAND / 2, str(item.get("label", "")), "plot__cat"))
        if item.get("note"):
            parts.append(_text(cx, base + AXIS_BAND + SUB_BAND / 2 - 4,
                               str(item["note"]), "plot__sub"))

    parts.append("</svg>")
    return "".join(parts)


MIN_REP_W = 64        # a rep must hold its gap to R1 ("−18 s") inside it
MIN_REC_W = 8         # a recovery may shrink to a sliver, never vanish
SEGMENT_GAP = 6
REST_LABEL_W = 40     # narrower than this, a recovery keeps its duration to itself
WIDE_REP_W = 92       # narrower than this, the pace on top drops to the compact size
RIBBON_BASE = 64      # height of a rep run at R1's pace
RIBBON_RISE = 150     # extra height of a rep RISE_SCALE s/km faster than R1, or more
RISE_SCALE = 15       # floor of the scale: a 1 s gap must not rise like an 18 s one
REST_H = 8
HR_GAP = 120          # from the floor to the top of the heart-rate band
HR_H = 90


def _widths(times: list, minimums: list, room: float) -> list:
    """Widths proportional to time, each held at or above its minimum.

    A segment pinned at its minimum leaves the others to share what is left, so
    a run of short reps between long recoveries still gets room for its labels.
    """
    pinned = [False] * len(times)
    while True:
        free = room - sum(m for m, p in zip(minimums, pinned, strict=True) if p)
        loose = sum(t for t, p in zip(times, pinned, strict=True) if not p)
        scale = free / loose if loose else 0
        widths = [m if p else t * scale
                  for t, m, p in zip(times, minimums, pinned, strict=True)]
        newly = [i for i, w in enumerate(widths) if not pinned[i] and w < minimums[i]]
        if not newly:
            return widths
        for i in newly:
            pinned[i] = True


def gap_text(gap: int | None) -> str:
    """A rep's pace against R1's, in s/km, signed like a split: −3 s is faster."""
    if gap is None:
        return text.REFERENCE
    if gap == 0:
        return "0 s"
    return f"−{-gap} s" if gap < 0 else f"+{gap} s"


def ribbon(segments: list, *, width: int = CONTENT_W) -> str:
    """A quality session as a ribbon in relief, with its heart rate underneath.

    segments, in the order run: {kind: "rep", time_s, pace (s/km), gap (s/km to
    R1, None for R1 itself), label, hr?} or {kind: "rec", time_s}.

    WIDTH is duration: reps and recoveries keep their real proportions.
    HEIGHT is how much faster than R1 a rep went -- a rep at R1's pace (or
    slower) stands at the base height, the session's fastest rises highest, on a
    scale never shorter than RISE_SCALE s/km so that noise stays flat -- so the
    shape of the session (flat, progressive, a final break) reads before a figure.
    Every rep is logo red: a faster rep is not a fault in itself (the guardrail judges
    the reserve, not the gap to R1). The pace sits on top, the
    gap to R1 inside, the label below; a recovery is a pale sliver on the floor
    with its duration. Under the ribbon, when the reps carry one, the heart rate
    as a line across the rep centres: what each acceleration cost.
    """
    if not segments:
        return ""
    reps = [seg for seg in segments if seg["kind"] == "rep"]
    fastest = max([-(seg.get("gap") or 0) for seg in reps] + [0])
    room = width - SEGMENT_GAP * (len(segments) - 1)
    # Many short reps cannot all get the comfortable minimum: they share what the
    # recoveries leave, and the compact type below takes over.
    rep_min = min(MIN_REP_W, (room - (len(segments) - len(reps)) * MIN_REC_W) / len(reps))
    widths = _widths([float(seg["time_s"]) for seg in segments],
                     [rep_min if seg["kind"] == "rep" else MIN_REC_W for seg in segments],
                     room)
    compact = min(w for seg, w in zip(segments, widths, strict=True)
                  if seg["kind"] == "rep") < WIDE_REP_W

    pace_band = 48 if compact else 60
    # Only the rise the session actually reached takes room: a steady session
    # does not float under a band of empty paper.
    floor = pace_band + RIBBON_BASE + fastest / max(fastest, RISE_SCALE) * RIBBON_RISE
    hrs = [float(seg["hr"]) for seg in reps if seg.get("hr")]
    has_hr = len(hrs) == len(reps)
    total_h = floor + AXIS_BAND + (HR_GAP + HR_H - AXIS_BAND + 16 if has_hr else 0)

    parts = [f'<svg class="plot" width="{width}" height="{total_h}" '
             f'viewBox="0 0 {width} {total_h}" xmlns="http://www.w3.org/2000/svg">']
    pace_cls = "ribbon__pace ribbon__pace--compact" if compact else "ribbon__pace"
    gap_cls = "ribbon__gap ribbon__gap--compact" if compact else "ribbon__gap"
    centers = []
    x = 0.0
    for seg, w in zip(segments, widths, strict=True):
        cx = x + w / 2
        if seg["kind"] == "rep":
            faster = max(-(seg.get("gap") or 0), 0)
            h = RIBBON_BASE + faster / max(fastest, RISE_SCALE) * RIBBON_RISE
            y = floor - h
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
                         f'rx="5" class="plot__bar plot__bar--effort"/>')
            parts.append(_text(cx, y - pace_band / 2, fmt_pace(seg["pace"]), pace_cls))
            parts.append(_text(cx, floor - RIBBON_BASE / 2, gap_text(seg.get("gap")), gap_cls))
            parts.append(_text(cx, floor + AXIS_BAND / 2 + 4, str(seg.get("label", "")),
                               "plot__cat"))
            centers.append(cx)
        else:
            parts.append(f'<rect x="{x:.1f}" y="{floor - REST_H}" width="{w:.1f}" '
                         f'height="{REST_H}" rx="{REST_H / 2}" class="plot__ghost"/>')
            if w >= REST_LABEL_W:
                parts.append(_text(cx, floor - REST_H - 18, fmt_rest(float(seg["time_s"])),
                                   "ribbon__rest"))
        x += w + SEGMENT_GAP

    if has_hr:
        top = floor + HR_GAP
        lo, hi = min(hrs) - 4, max(hrs) + 2
        def hy(v: float) -> float:
            return top + HR_H - (v - lo) / (hi - lo) * HR_H
        points = [(cx, hy(v), v) for cx, v in zip(centers, hrs, strict=True)]
        parts.append(_text(0, top - 46, text.HEART_RATE_AXIS, "plot__note", anchor="start"))
        parts.append('<polyline points="' + " ".join(f"{px:.1f},{py:.1f}" for px, py, _ in points)
                     + '" class="ribbon__hr"/>')
        for px, py, v in points:
            parts.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="5" class="ribbon__hr-point"/>')
            parts.append(_text(px, py - 20, f"{v:g}", "plot__value"))

    parts.append("</svg>")
    return "".join(parts)
