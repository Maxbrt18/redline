"""The strength-session capability, sidecar and report."""

import pytest

from redline.model.dose import Dose
from redline.service import strength


def dose(**data) -> dict:
    """A dose exactly as chart-strength.py hands it to the page."""
    return Dose.read(data).payload()


# The real shape, built by the real algebra: this fixture used to carry the
# pre-2026-07-25 keys (`mode`, `sets`, a French `dose` string), so every assertion
# below was made against a session the page could no longer receive.
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
        {"slug": "calf-raise-bent", "name": "Mollet unipodal, genou FLÉCHI ~30°",
         "target": "soléaire", "dose": dose(measure="amrap", sets=2, side="each")},
        {"slug": "plyometrics", "name": "Pliométrie",
         "dose": dose(measure="contacts", contacts=40, block=10)},
    ],
}


ZONES = {"injury": "Injury"}


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
            "pains": {"injury": {"after": 99}}, "minutes": -5, "note": "ok",
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
        "pains": {"injury": {"after": 1}}, "minutes": 32,
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
    # Les deux jambes se lisent séparément ou pas du tout : la page valide
    # droite puis gauche, donc 14 et 11 sont la droite, 12 et 10 la gauche. Les
    # additionner rendrait invisible l'écart G/D, qui est un drapeau.
    assert "droite 14, 11 · gauche 12, 10" in message
    assert "- Pliométrie : pas fait" in message
    assert "Injury : 1/10 après la séance (rien avant)." in message
    assert "« Le soléaire a lâché avant le gastroc. »" in message


def test_format_message_without_any_report():
    message = strength.format_message(SESSION, parsed({}))
    assert message.startswith("Séance de renfo terminée")
    assert "Injury" not in message


# ------------------------------------------------- le journal typé des séances

def test_a_blank_set_does_not_shift_the_other_leg():
    """LE bug que l'attribution positionnelle existe pour empêcher.

    La page valide droite puis gauche ; elle poste ses comptes AVEC les blancs.
    Filtrer les blancs avant l'envoi (ce qu'elle faisait) décalait tout d'un cran
    dès qu'une série était laissée vide, et rangeait les comptes suivants sous la
    mauvaise jambe — silencieusement, dans le seul chiffre qui pilote la
    progression.
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
        {"date": "2026-08-01", "sid": "2026-07-25-A", "seance": "a",
         "slug": "calf-raise-bent", "droite": "14|11", "gauche": "12|10", "score": 6,
         "status": "completed", "completed_slots": 4, "note": ""},
        {"date": "2026-08-01", "sid": "2026-07-25-A", "seance": "a",
         "slug": "plyometrics", "passe": "oui", "status": "skipped", "completed_slots": 0,
         "note": ""},
    ]


def test_partial_work_keeps_counts_and_unmeasured_validations():
    clean = parsed({"exercises": [{"i": 1, "status": "partial", "reps": [17, None],
                                    "note": "Arrêt, équilibre difficile à gauche."}]})
    row = strength.log_entries(SESSION, clean, "2026-10-02")[0]
    assert row["droite"] == "17" and "passe" not in row
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
    assert rows[0]["valeurs"] == "10|10|8|10"
    assert "droite" not in rows[0]


def test_an_exercise_without_a_slug_is_not_logged():
    """Une dérogation (`add` dans la sélection du matin) n'est pas un exercice du
    programme : elle n'a pas d'identité, donc rien à quoi la comparer la semaine
    suivante. Le nom est de la prose et bouge ; ce n'est pas une clé."""
    session = dict(SESSION, exercises=[
        {"name": "Un truc improvisé", "dose": dose(measure="reps", sets=2, reps=8)}])
    assert strength.log_entries(
        session, parsed({"exercises": [{"i": 0, "score": 4}]}, session),
        day="2026-08-01") == []


def test_an_untouched_exercise_leaves_no_row():
    """Une absence d'observation n'est pas un « pas fait » : même règle qu'au
    registre, où une journée sans séance n'appelle pas de ligne."""
    rows = strength.log_entries(
        SESSION, parsed({"exercises": [{"i": 0}]}), day="2026-08-01")
    assert [row["slug"] for row in rows] == ["warmup-a"]


# ---------------------------------------------- les deux lectures de chaque zone

@pytest.mark.parametrize("report,said", [
    ({"before": 1, "after": 3}, "Injury : 1/10 avant, 3/10 après (monté)."),
    ({"before": 3, "after": 1}, "Injury : 3/10 avant, 1/10 après (descendu)."),
    ({"before": 2, "after": 2}, "Injury : 2/10 avant, 2/10 après (stable)."),
    ({"before": 2}, "Injury : 2/10 avant de commencer."),
    ({"after": 2}, "Injury : 2/10 après la séance (rien avant)."),
])
def test_the_two_readings_never_get_merged(report, said):
    """Le registre en fait deux séries qui ne se mélangent pas (2026-07-29) : la
    lecture au repos et le pic à l'effort. Un seul chiffre ne répondait ni à l'une
    ni à l'autre — « 2/10 après » n'a pas de sens sans ce qu'il valait avant."""
    assert said in strength.format_message(SESSION, parsed({"pains": {"injury": report}}))


def test_each_zone_is_reported_under_its_own_name():
    zones = {"genou": "Genou", "cheville": "Cheville"}
    clean = strength.parse_report({"pains": {"genou": {"before": 1, "after": 3},
                                             "cheville": {"after": 0}}},
                                  SESSION["exercises"], zones)
    message = strength.format_message(SESSION, clean)
    assert "Genou : 1/10 avant, 3/10 après (monté)." in message
    assert "Cheville : 0/10 après la séance (rien avant)." in message


def test_a_zone_the_instance_does_not_watch_is_refused():
    with pytest.raises(strength.ReportError, match="non surveillée"):
        strength.parse_report({"pains": {"hanche": {"after": 1}}}, SESSION["exercises"], ZONES)


def test_a_gated_exercise_is_not_an_exercise_that_was_skipped():
    """L'un est une décision du coach que la page applique, l'autre un choix de
    Sam. Les confondre ferait lire une inobservance là où la règle a joué."""
    message = strength.format_message(SESSION, parsed({"exercises": [
        {"i": 1, "skipped": True, "gated": True},
        {"i": 2, "skipped": True},
    ]}))
    assert "retiré par le garde-fou de douleur" in message
    assert "- Pliométrie : pas fait" in message
