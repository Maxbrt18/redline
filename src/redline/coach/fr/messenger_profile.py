"""The Messenger persistent menu and ice-breakers, as the athlete sees them."""

from __future__ import annotations

MENU = [
    {"type": "postback", "title": "Le point sur ma semaine",
     "payload": "Fais-moi le point sur ma semaine en cours"},
    {"type": "postback", "title": "Le programme du jour",
     "payload": "C'est quoi le programme du jour ?"},
    {"type": "postback", "title": "Mon carnet Redline",
     "payload": "/carnet"},
]

ICE_BREAKERS = [
    {"question": "C'est quoi ma séance aujourd'hui ?",
     "payload": "C'est quoi ma séance aujourd'hui ?"},
    {"question": "Fais le point sur ma semaine",
     "payload": "Fais-moi le point sur ma semaine en cours"},
    {"question": "Où en est ma préparation ?",
     "payload": "Où en est ma préparation ?"},
    {"question": "Comment va ma blessure ?",
     "payload": "Fais le point sur ma blessure"},
]
