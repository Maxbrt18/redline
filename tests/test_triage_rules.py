import pytest

from redline.service.triage_rules import deterministic_triage


@pytest.mark.parametrize("text", ["salut", "Merci beaucoup !", "coucou", "Bonne nuit"])
def test_closed_social_messages_are_n0_without_a_model(text):
    assert deterministic_triage(text).depth == "N0"


@pytest.mark.parametrize("text", [
    "Finalement peut-être mercredi pour courir, je te confirme.",
    "Je ne peux pas te dire pour jeudi, je te tiens au courant.",
    "Je fais quoi pour récupérer mon fichier ?",
    "Je doute de ton calcul, tu peux vérifier ?",
    "Je décale ma séance à jeudi",
    "J'ai mal au genou, je fais quoi aujourd'hui ?",
    "Je suis découragé, je décale ma séance à jeudi",
    "Merci, mais tu as inversé les jours",
    "Quelle est la prochaine séance ?",
    "Si je remplace ma séance demain, ça donnerait quoi ?",
    "Tu en penses quoi ?", "oui", "ok", "ça marche", "yes",
])
def test_open_messages_are_interpreted_by_the_classifier(text):
    assert deterministic_triage(text) is None


def test_bot_does_not_call_the_classifier_for_a_closed_case(make_bot):
    class AgentWithoutTriage:
        provider = "claude"

        def classify(self, message):
            raise AssertionError("the classifier should not have been woken")

        def run(self, psid, message, **kwargs):
            from redline.service.agent import RunResult
            return RunResult(full_text="Salut !")

        def close(self):
            pass

    bot = make_bot(agent=AgentWithoutTriage())

    bot._respond("4242", "salut")
