"""The state of the day, and what it changes in the gauge.

Sleep and life load are two gauge multipliers (x1.1 to x1.2) and two documented
co-factors of both injuries -- and they only ever existed as ONE line written on
Sunday, from memory, about seven nights. Guard n°4 ("2-3 bad nights = assimilation
week") could therefore only fire up to six days late.
"""



from redline.model import daily

# -------------------------------------------------------------- aggregation

def _days(**counts):
    out = []
    for index, value in enumerate(["mauvaise"] * counts.get("bad", 0)
                                  + ["bonne"] * counts.get("good", 0)):
        out.append({"date": f"2026-08-{index + 3:02d}", "sommeil": value})
    return out


def test_two_bad_nights_raise_the_alert(_=None):
    """Guard n°4, word for word. The gauge vocabulary already had the two steps; all
    that was missing was something to feed them before Sunday."""
    assert daily.sleep_of(_days(bad=2, good=3)) == "2_3_mauvaises_nuits"


def test_one_bad_night_does_not():
    assert daily.sleep_of(_days(bad=1, good=4)) == "ok"


def test_four_bad_nights_downgrade_the_whole_week():
    assert daily.sleep_of(_days(bad=4)) == "degrade_semaine"


def test_an_undeclared_week_says_nothing_rather_than_ok():
    """None, never "ok": a week nobody declared is not a good week, and saying "ok"
    would silence a multiplier by default."""
    assert daily.sleep_of([]) is None
    assert daily.life_load_of([{"date": "2026-08-03"}]) is None


def test_one_heavy_day_is_not_a_heavy_week():
    """The x1.1 aims at the "stress bucket" of a PERIOD (a move, exam season), not at
    one rough Tuesday."""
    days = [{"date": "2026-08-03", "charge_vie": "elevee"},
            {"date": "2026-08-04", "charge_vie": "normale"}]
    assert daily.life_load_of(days) == "normale"


def test_three_heavy_days_are():
    days = [{"date": f"2026-08-{d:02d}", "charge_vie": "elevee"} for d in (3, 4, 5)]
    assert daily.life_load_of(days) == "elevee"
