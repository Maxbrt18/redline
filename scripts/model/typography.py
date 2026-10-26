"""Les signes qu'un clavier de téléphone ne porte pas, et qui ne doivent donc entrer
nulle part dans ce qui finit devant le coach ou devant Sam.

⭐ LA RÈGLE A DEUX CANAUX, ET UN SEUL ÉTAIT GARDÉ (2026-08-19). Le 17/08, la consigne
typographique a reçu son porteur : `BANNED_GLYPHS` plus un test qui scanne les textes
injectés. Ce test ne voit que les chaînes ÉCRITES DANS LE CODE. Or le plus gros bloc
injecté à chaque tour prescriptif est le programme lui-même, rendu par
`weekly_plan.as_json()` -- des textes que le coach a écrits la veille et que le service
lui remet sous les yeux le lendemain.

Au 19/08, le programme W34 en portait quatre : un tiret cadratin dans la `question` du
contrôle du 23/08, que `prompts.gate_note()` interpole VERBATIM dans l'instruction
invisible; un signe moins U+2212 dans la `note` du 22/08, qui part sur le téléphone de
Sam et sur la carte; deux autres dans `constraints`. La consigne se faisait donc
démentir par son propre contexte, exactement comme avant le 17/08, mais par le canal
des données au lieu du canal de la prose. Et la boucle se referme sur elle-même : le
coach écrit la révision suivante après avoir lu la précédente.

Pourquoi un REFUS et pas une réécriture : `redline/textutils.py` pose que `strip_links`
est la seule réécriture mécanique autorisée sur la sortie du coach, et que le reste se
corrige dans le prompt. Une normalisation silencieuse ici serait la régression que cette
règle interdit -- elle nettoierait la trace sans jamais apprendre la règle.

Pourquoi à l'ÉCRITURE et pas à la lecture : cette règle porte sur le clavier du
producteur, pas sur la structure persistée. Seul le texte qui entre est jugé ; le
schéma du plan, lui, reste unique et ne garde aucun numéro de version historique.
"""

from __future__ import annotations

#: Ce qu'un clavier de téléphone ne porte pas. `server/redline/prompts.py` réexporte
#: cette liste : le prompt qui l'interdit et le contrôle qui la fait respecter ne
#: peuvent pas diverger s'ils lisent la même constante.
BANNED_GLYPHS = "—–·…«»“”’→•−≥≤⚠⭐"

#: Ce qu'on tape à la place, nommé pour que le message d'erreur ouvre le chemin correct
#: au lieu de se contenter de fermer le mauvais (⭐ règle du 2026-08-06 : un garde-fou
#: qui n'ouvre pas de chemin correct fabrique son contournement).
REPLACEMENTS = {
    "—": "une virgule, un point, ou deux phrases",
    "–": "un trait d'union simple (-)",
    "·": "une virgule ou un point",
    "…": "trois points (...)",
    "«": 'un guillemet droit (")',
    "»": 'un guillemet droit (")',
    "“": 'un guillemet droit (")',
    "”": 'un guillemet droit (")',
    "’": "l'apostrophe droite (')",
    "→": "« donne », « devient », ou deux phrases",
    "•": "un tiret simple (-)",
    "−": "le signe moins du clavier (-)",
    "≥": "« au moins »",
    "≤": "« au plus »",
    "⚠": "rien, ou le mot qui porte l'alerte",
    "⭐": "rien",
}


def banned_in(text: str) -> list[str]:
    """Les signes interdits présents, dans l'ordre où ils apparaissent."""
    seen: list[str] = []
    for char in text or "":
        if char in BANNED_GLYPHS and char not in seen:
            seen.append(char)
    return seen


def errors(text: str, where: str, label: str) -> list[str]:
    """Le refus, avec le signe fautif, son contexte et ce qui se tape à la place."""
    problems = []
    for glyph in banned_in(text):
        index = text.find(glyph)
        around = " ".join(text[max(0, index - 40):index + 40].split())
        problems.append(
            f"{where} : {label} contient « {glyph} », qu'un clavier de téléphone ne "
            f"porte pas (autour de « {around} »). Ce texte est réinjecté au coach à "
            f"chaque tour, et title/note partent sur le téléphone de Sam. "
            f"À la place : {REPLACEMENTS.get(glyph, 'un signe du clavier')}.")
    return problems
