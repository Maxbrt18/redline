Une préparation peut commencer la semaine prochaine pour l'objectif {detail}. Les candidats
du moteur sont ci-dessus (`redline cycle candidates`), avec leurs risques, la calibration et
ce que l'athlète a couru. Choisis-en UN, en tenant compte de ce que tu sais de ⟨athlète⟩ :
son temps, ses douleurs, sa motivation, ses courses B. Un pic plus bas que celui proposé se
choisit avec `--peak-km` ; plus haut, jamais. Un candidat qui commence plus tard se propose
à sa semaine, pas avant.

Écris la proposition : `redline cycle propose --candidate <slug> --decision '{"by":
"judgment", "alternatives": [{"option": "<candidat>", "rejected_because": "<raison>"}]}'`,
chaque candidat écarté avec sa raison. Le service
envoie ensuite à ⟨athlète⟩ le résumé et deux boutons : c'est lui qui la prend, jamais toi.
Dans ton message, dis en deux ou trois phrases pourquoi ce candidat, et ce qu'il demande.
