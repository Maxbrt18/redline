"""Text splitting: _split (Messenger limit) and _burst_parts (--- protocol)."""

from types import SimpleNamespace

from redline.service.messenger import DeliveredMessage, DeliveryResult, MessengerClient
from redline.service.textutils import BURST_MAX


def test_split_short_text_intact():
    assert MessengerClient._split("Salut Sam !") == ["Salut Sam !"]


def test_split_cuts_at_sentence_end():
    # The sentence boundary sits past half the limit: it wins.
    text = "Une première phrase de bonne taille. Une deuxième phrase pour la suite."
    parts = MessengerClient._split(text, limit=40)
    assert all(len(p) <= 40 for p in parts)
    assert parts[0] == "Une première phrase de bonne taille."
    assert "".join(parts).replace(" ", "") == text.replace(" ", "")  # nothing lost


def test_split_never_inside_a_word():
    text = "mot " * 30  # no sentence punctuation
    parts = MessengerClient._split(text.strip(), limit=25)
    for part in parts:
        assert not part.startswith("ot") and "mo t" not in part
        assert all(chunk == "mot" for chunk in part.split())


def test_burst_parts_splits_on_dashes():
    assert MessengerClient._burst_parts("un\n---\ndeux") == ["un", "deux"]


def test_burst_parts_without_separator():
    assert MessengerClient._burst_parts("un seul texto") == ["un seul texto"]


def test_burst_parts_ignores_empty_blocks():
    assert MessengerClient._burst_parts("un\n---\n   \n---\ndeux") == ["un", "deux"]


def test_burst_parts_caps_and_merges():
    text = "\n---\n".join(f"t{i}" for i in range(1, 9))  # 8 blocks
    parts = MessengerClient._burst_parts(text)
    assert len(parts) == BURST_MAX
    assert parts[-1] == "t5\n\nt6\n\nt7\n\nt8"


# ---- the session button (interactive strength session)

def test_button_payload_is_a_one_button_template(make_config):
    client = MessengerClient(make_config())
    sent = []
    client._post = lambda payload: sent.append(payload)

    client.send_button("4242", "Renfo A · samedi 25 juillet",
                       "https://coach.test/s#abc.def", "Ouvrir la séance")

    message = sent[0]["message"]["attachment"]["payload"]
    assert message["template_type"] == "button"
    assert message["text"] == "Renfo A · samedi 25 juillet"
    assert message["buttons"] == [{"type": "web_url",
                                   "url": "https://coach.test/s#abc.def",
                                   "title": "Ouvrir la séance"}]
    assert sent[0]["recipient"] == {"id": "4242"}
    assert "tag" not in sent[0]


def test_button_truncates_to_what_meta_accepts(make_config):
    client = MessengerClient(make_config())
    sent = []
    client._post = lambda payload: sent.append(payload)

    client.send_button("4242", "t" * 900, "https://coach.test/s#a.b", "l" * 40,
                       messaging_type="UPDATE", tag="ACCOUNT_UPDATE")

    message = sent[0]["message"]["attachment"]["payload"]
    assert len(message["text"]) == MessengerClient.BUTTON_TEXT_MAX
    assert len(message["buttons"][0]["title"]) == MessengerClient.BUTTON_TITLE_MAX
    assert sent[0]["tag"] == "ACCOUNT_UPDATE"
    assert sent[0]["messaging_type"] == "UPDATE"


def test_a_4xx_is_an_explicit_delivery_failure(make_config, monkeypatch):
    client = MessengerClient(make_config())
    monkeypatch.setattr("redline.service.messenger.requests.post", lambda *a, **k:
                        SimpleNamespace(status_code=400, text="bad recipient"))
    result = client.send_text("4242", "salut")
    assert result.ok is False
    assert result.delivered == 0
    assert "400" in result.detail


def test_a_429_is_retried_before_giving_up(make_config, monkeypatch):
    client = MessengerClient(make_config())
    responses = iter([
        SimpleNamespace(status_code=429, text="slow down", headers={}),
        SimpleNamespace(status_code=200, text="{}", headers={}, json=dict),
    ])
    monkeypatch.setattr("redline.service.messenger.requests.post", lambda *a, **k: next(responses))
    monkeypatch.setattr("redline.service.messenger.time.sleep", lambda _seconds: None)

    assert client.send_text("4242", "salut").ok


def test_a_sent_text_keeps_the_meta_id_and_exact_chunk(make_config, monkeypatch):
    client = MessengerClient(make_config())
    response = SimpleNamespace(
        status_code=200,
        text='{"recipient_id":"4242","message_id":"mid.coach.123"}',
        json=lambda: {"recipient_id": "4242", "message_id": "mid.coach.123"},
    )
    monkeypatch.setattr("redline.service.messenger.requests.post", lambda *a, **k: response)

    result = client.send_text("4242", "Le morceau exact envoyé.")

    assert result.messages == (
        DeliveredMessage("mid.coach.123", "Le morceau exact envoyé."),)


def test_a_burst_stops_after_the_first_failed_part(make_config, monkeypatch):
    client = MessengerClient(make_config())
    results = iter([DeliveryResult.success(), DeliveryResult.failure("non livré")])
    client.send_text = lambda *a, **k: next(results)
    # Le rythme entre deux textos passe par une vraie action Meta : un test unitaire
    # ne l'envoie pas sur le réseau (il y tentait trois connexions, sept secondes).
    client.action = lambda *a, **k: DeliveryResult.success()
    monkeypatch.setattr("redline.service.messenger.time.sleep", lambda _seconds: None)
    result = client.send_burst("4242", "un\n---\ndeux\n---\ntrois")
    assert result.ok is False
    assert result.delivered == 1
