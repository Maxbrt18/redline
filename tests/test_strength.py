"""The strength-session capability, sidecar and report."""

import pytest

from redline.model.dose import Dose
from redline.service import strength


def dose(**data) -> dict:
    """A dose exactly as chart-strength hands it to the page."""
    return Dose.read(data).payload()


# The real shape, built by the real algebra: the fixture must carry the keys the page
# actually receives, otherwise every assertion below is made against a session the page
# could no longer receive.
SESSION = {
    "sid": "2026-07-25-A",
    "session": "a",
    "title": "Renfo A, os & tendons",
    "date": "samedi 25 juillet",
    "rule": "Jusqu'à l'échec technique.",
    "note": "Demi-dose, sans pliométrie.",
    "exercises": [
        {"slug": "warmup-a", "name": "Échauffement",
         "dose": dose(measure="time", seconds=180)},
        {"slug": "calf-raise-bent", "name": "Mollet unipodal, talon FLÉCHI ~30°",
         "target": "soléaire", "dose": dose(measure="amrap", sets=2, side="each")},
        {"slug": "plyometrics", "name": "Pliométrie",
         "dose": dose(measure="contacts", contacts=40, block=10)},
    ],
}


ZONES = {"genou": "Genou"}


def parsed(report: dict, session: dict = SESSION) -> dict:
    return strength.parse_report(report, session["exercises"], ZONES)


# ----------------------------------------------------------------------- URL

def test_validate_session_refuses_missing_or_expired_expiry():
    with pytest.raises(strength.ReportError):
        strength.validate_session(SESSION)
    with pytest.raises(strength.ExpiredLink):
        strength.validate_session({**SESSION, "expires_at": 99}, now=100)


def test_sidecar_is_off_without_a_public_base_url(monkeypatch):
    monkeypatch.delenv("COACH_BASE_URL", raising=False)
    assert not strength.sidecar_enabled()


def test_sidecar_is_enabled_with_only_a_public_base_url(monkeypatch):
    monkeypatch.setenv("COACH_BASE_URL", "https://coach.test")
    assert strength.sidecar_enabled()


def test_opaque_token_is_fixed_length_url_safe_and_never_embeds_the_session():
    token = strength.new_token()
    assert len(token) == 32
    assert strength.TOKEN_RE.fullmatch(token)
    assert strength.token_digest(token) == __import__("hashlib").sha256(
        token.encode("ascii")).hexdigest()
    url = strength.token_url("https://coach.test/", token)
    assert url == f"https://coach.test/s#t={token}"
    assert SESSION["sid"] not in url


def test_malformed_tokens_have_no_lookup_key():
    assert strength.token_digest("") == ""
    assert strength.token_digest("short") == ""
    with pytest.raises(ValueError):
        strength.token_url("https://coach.test", "short")


def test_expiring_snapshot_is_a_deep_immutable_copy():
    snapshot = strength.expiring_snapshot(SESSION, now=1_000)
    assert snapshot["expires_at"] == 1_000 + strength.LINK_TTL_SECONDS
    snapshot["exercises"][0]["name"] = "modifié"
    assert SESSION["exercises"][0]["name"] == "Échauffement"


# ------------------------------------------------------------------- sidecar

def test_sidecar_roundtrip(tmp_path):
    image = tmp_path / "strength-2026-07-25.png"
    image.write_bytes(b"png")
    strength.write_sidecar(image, "Renfo A · samedi", SESSION)
    assert strength.sidecar_for(image).name == "strength-2026-07-25.png.link"
    assert strength.read_sidecar(image) == ("Renfo A · samedi", SESSION)


def test_sidecar_absent_or_not_a_session(tmp_path):
    image = tmp_path / "card.png"
    image.write_bytes(b"png")
    assert strength.read_sidecar(image) is None
    strength.sidecar_for(image).write_text('{"text":"Renfo","session":"non"}')
    assert strength.read_sidecar(image) is None


# -------------------------------------------------------------------- report

def test_parser_refuses_out_of_range_numbers_instead_of_forging_bounds():
    with pytest.raises(strength.ReportError):
        strength.parse_report({
            "pains": {"genou": {"after": 99}}, "minutes": -5, "note": "ok",
            "exercises": [{"i": 1, "score": 42, "reps": [12, -3, "x", 9999]}],
        }, SESSION["exercises"], ZONES)


def test_parser_refuses_indexes_outside_the_session():
    with pytest.raises(strength.ReportError):
        strength.parse_report({"exercises": [{"i": 99}]}, SESSION["exercises"])


def test_parser_caps_and_flattens_the_note():
    clean = parsed({"note": "a\n\rb\x00c" + "x" * 500})
    assert "\n" not in clean["note"] and "\x00" not in clean["note"]
    assert len(clean["note"]) <= strength.NOTE_MAX


def test_parser_refuses_junk():
    with pytest.raises(strength.ReportError):
        strength.parse_report("pas un dict", SESSION["exercises"])
    with pytest.raises(strength.ReportError):
        strength.parse_report({"exercises": ["nope", None]}, SESSION["exercises"])


def test_format_message_reads_like_the_athlete_wrote_it():
    message = strength.format_message(SESSION, parsed({
        "pains": {"genou": {"after": 1}}, "minutes": 32,
        "note": "Le soléaire a lâché avant le gastroc.",
        "exercises": [
            {"i": 0, "score": 3},
            {"i": 1, "score": 7, "reps": [14, 12, 11, 10]},
            {"i": 2, "skipped": True},
        ],
    }))
    assert "Renfo A, os & tendons" in message and "32 min" in message
    # The number must never travel without saying what it measures.
    assert "Difficulté ressentie" in message
    assert "- Échauffement : 3/10" in message
    # The two legs are read separately or not at all: the page validates right then
    # left, so 14 and 11 are the right leg, 12 and 10 the left. Summing them would hide
    # the left/right gap, which is a flag.
    assert "droite 14, 11 · gauche 12, 10" in message
    assert "- Pliométrie : pas fait" in message
    assert "Genou : 1/10 après la séance (rien avant)." in message
    assert "« Le soléaire a lâché avant le gastroc. »" in message


def test_format_message_without_any_report():
    message = strength.format_message(SESSION, parsed({}))
    assert message.startswith("Séance de renfo terminée")
    assert "Genou" not in message


# ------------------------------------------------- the typed session log

def test_a_blank_set_does_not_shift_the_other_leg():
    """THE bug that positional attribution exists to prevent.

    The page validates right then left; it posts its counts WITH the blanks.
    Filtering the blanks before sending (as it used to) shifted everything by one
    as soon as a set was left empty, and filed the following counts under the
    wrong leg -- silently, in the one number that drives progression.
    """
    clean = strength.parse_report(
        {"exercises": [{"i": 1, "reps": [14, None, 11, 10]}]}, SESSION["exercises"])
    assert clean["items"][1]["sides"] == {"": [], "d": [14, 11], "g": [10]}


def test_the_log_rows_are_the_message_seen_from_the_other_side():
    report = parsed({
        "exercises": [
            {"i": 1, "score": 6, "reps": [14, 12, 11, 10]},
            {"i": 2, "skipped": True},
        ],
    })
    rows = strength.log_entries(SESSION, report, day="2026-08-01")
    assert rows == [
        {"date": "2026-08-01", "sid": "2026-07-25-A", "workout": "a",
         "slug": "calf-raise-bent", "right": "14|11", "left": "12|10", "score": 6,
         "status": "completed", "completed_slots": 4, "note": ""},
        {"date": "2026-08-01", "sid": "2026-07-25-A", "workout": "a",
         "slug": "plyometrics", "skipped": "yes", "status": "skipped", "completed_slots": 0,
         "note": ""},
    ]


def test_partial_work_keeps_counts_and_unmeasured_validations():
    clean = parsed({"exercises": [{"i": 1, "status": "partial", "reps": [17, None],
                                    "note": "Arrêt, équilibre difficile à gauche."}]})
    row = strength.log_entries(SESSION, clean, "2026-10-02")[0]
    assert row["right"] == "17" and "skipped" not in row
    assert row["completed_slots"] == 2 and row["status"] == "partial"
    assert row["note"] in strength.format_message(SESSION, clean)


@pytest.mark.parametrize("status,reps", [("gated", [1]), ("skipped", [None]),
                                       ("partial", []), ("completed", [17])])
def test_explicit_status_cannot_invent_or_hide_work(status, reps):
    with pytest.raises(strength.ReportError):
        parsed({"exercises": [{"i": 1, "status": status, "reps": reps}]})


def test_the_blocks_of_a_contact_exercise_are_logged_as_one_series():
    session = dict(SESSION, exercises=[
        {"slug": "plyometrics", "name": "Pliométrie",
         "dose": dose(measure="contacts", contacts=40, block=10)}])
    report = parsed({"exercises": [{"i": 0, "reps": [10, 10, 8, 10]}]}, session)
    rows = strength.log_entries(session, report, day="2026-08-01")
    assert rows[0]["both"] == "10|10|8|10"
    assert "right" not in rows[0]


def test_an_exercise_without_a_slug_is_not_logged():
    """A deviation (`add` in the morning selection) is not a program exercise: it has
    no identity, so nothing to compare it with the following week. The name is prose
    and moves; it is not a key."""
    session = dict(SESSION, exercises=[
        {"name": "Un truc improvisé", "dose": dose(measure="reps", sets=2, reps=8)}])
    assert strength.log_entries(
        session, parsed({"exercises": [{"i": 0, "score": 4}]}, session),
        day="2026-08-01") == []


def test_an_untouched_exercise_leaves_no_row():
    """No observation is not a "not done": same rule as the ledger, where a day
    without a session calls for no row."""
    rows = strength.log_entries(
        SESSION, parsed({"exercises": [{"i": 0}]}), day="2026-08-01")
    assert [row["slug"] for row in rows] == ["warmup-a"]


# ---------------------------------------------- the two readings of each zone

@pytest.mark.parametrize("report,said", [
    ({"before": 1, "after": 3}, "Genou : 1/10 avant, 3/10 après (monté)."),
    ({"before": 3, "after": 1}, "Genou : 3/10 avant, 1/10 après (descendu)."),
    ({"before": 2, "after": 2}, "Genou : 2/10 avant, 2/10 après (stable)."),
    ({"before": 2}, "Genou : 2/10 avant de commencer."),
    ({"after": 2}, "Genou : 2/10 après la séance (rien avant)."),
])
def test_the_two_readings_never_get_merged(report, said):
    """The ledger keeps two series that never mix: the rest reading and the effort peak.
    A single figure answered neither -- "2/10 after" means nothing without what it was
    before."""
    assert said in strength.format_message(SESSION, parsed({"pains": {"genou": report}}))


def test_each_zone_is_reported_under_its_own_name():
    zones = {"talon": "Talon", "cheville": "Cheville"}
    clean = strength.parse_report({"pains": {"talon": {"before": 1, "after": 3},
                                             "cheville": {"after": 0}}},
                                  SESSION["exercises"], zones)
    message = strength.format_message(SESSION, clean)
    assert "Talon : 1/10 avant, 3/10 après (monté)." in message
    assert "Cheville : 0/10 après la séance (rien avant)." in message


def test_a_zone_the_instance_does_not_watch_is_refused():
    with pytest.raises(strength.ReportError, match="non surveillée"):
        strength.parse_report({"pains": {"hanche": {"after": 1}}}, SESSION["exercises"], ZONES)


def test_a_gated_exercise_is_not_an_exercise_that_was_skipped():
    """One is a coach decision that the page applies, the other a choice of the
    athlete. Confusing them would read a non-compliance where the rule applied."""
    message = strength.format_message(SESSION, parsed({"exercises": [
        {"i": 1, "skipped": True, "gated": True},
        {"i": 2, "skipped": True},
    ]}))
    assert "retiré par le garde-fou de douleur" in message
    assert "- Pliométrie : pas fait" in message
