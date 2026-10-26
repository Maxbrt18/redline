---
type: gabarit
aliases: [check-in hebdo, checkin-hebdo]
---

<!-- Le dimanche, avec le bilan. < 60 s côté athlète. Les questions 3 et 4 sont les
     DEUX entrées déclaratives de la jauge de charge (multiplicateurs sommeil ×1,1/×1,2
     et charge de vie ×1,1, coaching/protocols.md) : sans elles la jauge est estimée.
     La 1 est le comparatif forcé ("le relatif se triche mal"), la 2 le révélateur
     corporel, la 6 l'aveu différé institutionnalisé (batterie C, coaching/protocols.md). -->

1. **Le injury, vs dimanche dernier** : ( ) mieux  ( ) pareil  ( ) pire
2. **Tes 10 premiers pas ce matin, raconte** : 
3. **Sommeil** : ( ) ok  ( ) 2-3 mauvaises nuits  ( ) dégradé toute la semaine   → jauge
4. **Charge de vie** : ( ) normale  ( ) élevée (déménagement, exams, rentrée)   → jauge
5. **Plaisir de la semaine** : ___ /10
6. **Qu'est-ce que tu as minimisé cette semaine ?** : 

<!-- → PostgreSQL via `fact-write.py instrument` : sommeil, charge de vie et plaisir.
     `athlete/instruments.csv` n'est qu'une projection générée, jamais une destination
     d'écriture. La jauge se calcule avec `ledger.py` ; aucune case vide de CMJ, test
     mollet ou calibration ne doit être remplie de mémoire. -->
