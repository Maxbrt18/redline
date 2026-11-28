Une revue du plan est due pour l'objectif {detail} : soit une préparation peut commencer
la semaine prochaine, soit une hypothèse de la préparation en cours est tombée (les
hypothèses tombées ci-dessus) et le reste de sa trame n'est plus atteignable. Les candidats
du moteur sont ci-dessus (`redline cycle candidates`), générés depuis l'état réel, avec leurs
risques, la calibration et ce que l'athlète a couru. Une replanification part de la semaine
prochaine : dis ce qui est tombé et ce que la nouvelle version change.

Choisis-en UN, en tenant compte de ce que tu sais de ⟨athlète⟩ : son temps, ses douleurs,
sa motivation, ses courses B. Un pic plus bas que celui proposé se choisit avec
`--peak-km` ; plus haut, jamais. Un candidat qui commence plus tard se propose
à sa semaine, pas avant.

Écris la proposition : `redline cycle propose --candidate <slug> --decision '{"by":
"judgment", "alternatives": [{"option": "<candidat>", "rejected_because": "<raison>"}]}'`,
chaque candidat écarté avec sa raison. Le service
envoie ensuite à ⟨athlète⟩ le résumé et deux boutons : c'est lui qui la prend, jamais toi.
Dans ton message, dis en deux ou trois phrases pourquoi ce candidat, et ce qu'il demande.
