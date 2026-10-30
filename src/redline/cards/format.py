"""Formatting of the numbers and paces shown on the Redline cards."""


def fmt_num(value: float) -> str:
    """8.4 -> "8,4" (French decimal comma), 16 -> "16"."""
    return f"{value:g}".replace(".", ",")


def fmt_pace(sec_per_km: float) -> str:
    """272.4 s/km -> "4:32"."""
    total = round(sec_per_km)
    return f"{total // 60}:{total % 60:02d}"


def fmt_rest(seconds: float) -> str:
    """90 -> "1'30", 45 -> "45 s": how a runner writes a recovery."""
    total = round(seconds)
    if total < 60:
        return f"{total} s"
    return f"{total // 60}'{total % 60:02d}"


def parse_pace(value) -> float:
    """"3:45" or 225 -> seconds/km (float)."""
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if ":" in text:
        minutes, _, seconds = text.partition(":")
        return int(minutes) * 60 + float(seconds or 0)
    return float(text)


def nice_ticks(lo: float, hi: float, steps=(1, 2, 2.5, 5, 10, 15, 20, 25, 30, 50),
               max_ticks: int = 7) -> list:
    """Clean tick values covering [lo, hi] with at most max_ticks marks."""
    span = max(hi - lo, 1e-9)
    for step in steps:
        if span / step <= max_ticks - 1:
            break
    first = int(lo // step) * step
    if first < lo - 1e-9:
        first += step
    ticks = []
    value = first
    while value <= hi + 1e-9:
        ticks.append(round(value, 6))
        value += step
    return ticks
