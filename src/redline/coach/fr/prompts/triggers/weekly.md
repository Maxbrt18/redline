Fais le bilan du dimanche et prépare la semaine suivante.
1. Valide la semaine finie depuis les cycles. Aucune révision de clôture : le réalisé
   vient des cycles, le plan ne porte que l'intention.
2. Le DOSSIER DU BILAN en tête de ce message porte agrégats, deux séries de douleur,
   trajectoire de la sortie longue, signaux, prédictions et échéances échues : cite-le,
   ne le recalcule pas. Sans dossier, lance `scripts/ledger.py --semaine`,
   `scripts/signals.py` et `python3 scripts/fact-read.py predictions --due`. Rends un
   verdict sur CHAQUE prédiction échue, adossé au registre, avec
   `python3 scripts/fact-write.py prediction --key <clé> --outcome true|false|void`
   (`void` si condition non tenue ou observation absente) et son `--reason` quand
   elle est réfutée ou void. Plus de dix : les plus en retard d'abord, le reste
   dimanche prochain.
   Sans nouveau record de sortie longue depuis trois semaines closes, le protocole
   d'instruction des baisses de athlete/goals.md s'ouvre dans ce bilan. Écris la
   lecture dans le journal ; hot-cache seulement si son résumé change ; une décision
   va dans decisions.md et son propriétaire. Les 4 contrôles du § D de
   `coaching/protocols.md` se lisent chez Strava (`python3 scripts/strava.py
   activities`) : allure en descente sur ses segments habituels, parcours écourtés,
   pauses en fin de sortie, longueur de ses notes. Un écart ouvre une hypothèse, jamais
   une preuve : son silence ne rassure ni n'alarme.
3. Relis les six capacités, date leur évaluation et leur prochain test. Lis
   `coaching/adherence.md` : une consigne contournée appelle un changement de levier,
   pas plus de fermeté. Clôture les fils sans objet. Une échéance échue qui dépend de ⟨athlète⟩
   (mail, nom de médicament, rendez-vous) reçoit l'action prête à faire et un repli
   daté, une fois par semaine au plus, jamais en reproche.
4. Le service crée le journal de la semaine suivante ; écris-y l'intention et une
   révision complète (`volume_target_km` compris) via `scripts/week-plan.py
   revise/check`. Une condition du jour va dans la `note` de sa séance (trace nette :
   repos), jamais en `gate` ; au-delà,
   `scripts/fact-write.py deadline` (`--athlete-label` si ⟨athlète⟩ agit). Renfo
   selon `coaching/strength.md`. Une séance dont la nature sort des 30 derniers jours
   porte `transition`, une par semaine ; une hausse facile passe par la porte.
   Chaque SL et chaque hausse de la porte reçoit sa prédiction `corps`, probabilité
   explicite : `scripts/fact-write.py prediction --domain corps --resolves wake-trace
   --resolves-on <lendemain> --success-if '<=1'`. Elle se solde seule.
   Faisabilité du dossier
   INFAISABLE : présente ses leviers à ⟨athlète⟩, n'en choisis aucun en silence ;
   `python3 scripts/feasibility.py --libre <semaine>` explore.
   `start_time: HH:MM` si ⟨athlète⟩ a donné l'heure ; sinon laisse le champ absent.
5. Une preuve datée d'endurance mentale va avec ses mots dans
   `coaching/mental-bank.md`. Lance `scripts/vault-check.py`, corrige, puis
   `scripts/chart-week.py` avec `period` au format YYYY-WNN de la semaine close.
6. Envoie un bilan court qui fait ressortir ce qui change la semaine. Une question
   seulement si un choix utile reste ouvert, jamais une formule fixe ni un présupposé
   de défaut. Ne demande à ⟨athlète⟩ aucun engagement pour la semaine.
