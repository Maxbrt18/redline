Le programme de la semaine {detail} est absent ou invalide :
aucune séance ne peut être prescrite tant qu'il ne l'est pas.
1. Lis `redline week-plan show --week {detail}` puis `check`, le registre récent avec
   `redline ledger --week {detail}`, les décisions en vigueur et
   `coaching/current-plan.md` comme entrée du recalcul, jamais comme programme.
2. Écris une révision complète et prudente des jours restants avec
   `redline week-plan revise --week {detail} --input`, puis relis-la avec `check`. Sans
   bilan disponible, reste au plus près de la dernière semaine valide : aucune hausse,
   aucune séance de transition, les contrôles datés en vigueur conservés. Une séance
   retirée ou réduite porte sa `restriction` (`rule` et `fact`), sinon elle reste.
   La révision porte sa `decision` : ce qui a tranché et les options écartées.
3. Écris un message court : la suite de la semaine est prête, en une ou deux phrases.
   Ne raconte ni panne ni outil ; le service joint lui-même les séances.
