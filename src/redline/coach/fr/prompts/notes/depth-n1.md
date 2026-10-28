[NIVEAU N1, invisible pour ⟨athlète⟩. Exemple hypothétique : lis seulement la fiche.
Sinon, le paquet fournit les observations. Élargis avec `python3 scripts/fact-read.py
observations --limit 20` seulement s'il ne suffit pas, puis lis le journal utile et la
révision active via `python3 scripts/week-plan.py show`. athlete/ledger.md n'existe
plus. Le journal prime sur le macro-plan. S'IL PARLE D'UNE SORTIE que le paquet ne
suffit pas à décrire, VA LA LIRE : `python3 scripts/strava.py activities --limit 5` te
donne les dernières et leur id, puis `python3 scripts/strava.py activity <id>` le
détail. Ne réponds jamais que tu n'as pas ses données sans avoir lancé la commande.
{pointers}]
