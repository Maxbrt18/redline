# Stockage opérationnel

## Décision

PostgreSQL 17 est l'unique propriétaire des faits opérationnels et de l'état durable
du service. Git reste le journal du code et du savoir humain ; les faits se lisent
directement avec `redline fact-read`, `redline ledger` et `redline week-plan`.
Obsidian ne conserve que le raisonnement humain et quelques vues réellement utiles
(`hot-cache`, plans hebdomadaires, tables de renforcement et CSV d'export).

Il n'existe aucun repli de production vers un fichier Markdown, CSV ou JSON. Le
service refuse de démarrer sans `DATABASE_URL`.

## Modèle, nommage et garanties

Le schéma applicatif s'appelle `redline`. Les conventions sont uniformes :

- tables au pluriel et identifiants SQL en `snake_case` ;
- clé primaire technique `id` en UUID ; clés naturelles explicites en `*_key` ;
- clés étrangères en `*_id`, dates civiles en `*_on`, instants UTC en `*_at` ;
- clés primaires `pk_*`, étrangères `fk_*`, contraintes `ck_*`/`uq_*` et index `ix_*` ;
- aucune table EAV `metric/state/value` et aucune donnée structurée encodée avec des
  pipes ; les métriques sportives ont chacune une colonne et un état contraint ;
- `JSONB` est réservé aux enveloppes validées dont les sous-champs ne sont pas
  interrogés transversalement : commandes de tour, audit, distillat Strava et
  prescriptions imbriquées d'une séance (renfo, météo). Les faits requêtables ont
  des colonnes relationnelles typées.

Les garanties principales sont portées par PostgreSQL :

- `athlete_id` référence toujours `athletes` ;
- une observation possède un UUID et une `idempotency_key` stable ; Strava est unique
  par athlète et identifiant d'activité ;
- chaque métrique porte explicitement `pending`, `known`, `unavailable` ou
  `not_applicable` ; une valeur `known` est bornée et immuable par trigger ;
- renforcement, séries, bilans quotidiens, instruments, plans, capacités, fils,
  échéances et messages proactifs sont relationnels et contraints ;
- `session_exposures` distingue une mesure d'une exposition déclarée, avec origine,
  dose, tolérance et statut `candidate|confirmed|dismissed` ; les précisions et corrections
  sont auditées sur la même exposition, sans créer de doublon ;
- les programmes sont append-only, avec révisions strictement contiguës ;
- chaque changement de prescription future relie les champs réellement touchés à une
  preuve typée de la révision et à la règle de coaching appliquée ;
- chaque révision possède un `persisted_at` donné par PostgreSQL. Une semaine demande
  un nouvel arbitrage seulement si une activité d'effort encore sans cycle est entrée
  après ce watermark ; changer le format d'une empreinte technique ne périme pas un
  programme ;
- `planned_sessions` porte l'intention (`planned` ou `cancelled`) ; l'exécution et
  son état appartiennent à `session_cycles`. Le read model peut afficher `done`, mais
  aucune nouvelle révision ne peut l'écrire ;
- sessions agent, quotas, tâches proactives, dettes de livraison, claims et marqueurs
  ont chacun une table dédiée ;
- `outbound_deliveries` journalise la frontière non transactionnelle de Messenger :
  `sending`, `delivered` ou `failed`, avec empreinte du contenu et tentatives ;
- `notebook_entries` conserve les cartes et leur texte comme archives immuables ;
  `notebook_credentials` porte les empreintes des accès privés révocables. Les images
  sont sauvegardées avec la base, indépendamment de la purge du dossier d’envoi ;
- audit et marqueur de projection sale sont écrits dans la même transaction que le fait ;
- une même clé d'idempotence avec un contenu différent est une erreur, pas un succès
  silencieux.

Le schéma courant est dans `src/redline/storage/schema.py`. Alembic est l'autorité de
version : une révision unique crée le stockage au complet, et toute évolution
ultérieure crée une nouvelle révision.

## Frontière transactionnelle

Les commandes produites pendant un tour sont d'abord enregistrées dans
`turn_commands`. `apply_turn` verrouille le tour, applique tous ses faits, écrit audit
et état de projection, puis commit en **une transaction PostgreSQL**. Une commande
invalide annule tout ce lot. La frontière du service relit ensuite les semaines dont
la réponse dépend avant toute livraison.

La CLI `redline week-plan preview --week …` accepte le même JSON que `revise`. Dans un
tour du coach, ce JSON arrive par `--input .agent-tmp/<turn_id>.json`, jamais par
une redirection ; stdin reste la voie des lancements opérateur, hors du tour.
Elle appelle le même moteur transactionnel avec les commandes déjà préparées dans
le tour, puis annule le SAVEPOINT entier : aucune commande de simulation, aucun fait,
audit ou marqueur de projection ne subsiste. Le résultat décrit une possibilité à
l'état courant, sans réserver le programme ni autoriser son application ultérieure.
`revise` distingue sa préparation dans un tour de son application directe hors tour.

La persistance rend aussi le nombre de commandes réellement appliquées et leurs reçus.
Elle ne déclenche aucun second appel de modèle : la réponse livrée est celle du tour qui
a travaillé, que des commandes aient été appliquées ou non. Une panne transitoire de
PostgreSQL (connexion perdue) est retentée trois fois avant de refuser le lot ; un refus
définitif nomme les types de commandes perdus, et la réponse l'annonce à l'athlète dans ses
mots. Un fichier de code ou une projection modifiés pendant le tour sont restaurés
fichier par fichier, sans abandonner les notes ni les commandes valides.

Les reçus comportent la clé, le type, le contenu et l'instant d'application de chaque
commande, relus dans la transaction qui les applique. Les semaines réellement révisées
sont contrôlées même si le triage ne les avait pas identifiées. Une panne de publication
après commit conserve ces reçus et le statut enregistré.

Le contrôle sémantique probabiliste ne participe plus au transport Messenger. Il vit dans
le paquet `src/redline/evaluation/`, hors du paquet `redline` et du protocole des
adaptateurs, comme banc d'évaluation explicite. Les réponses réactives et proactives
partent après les seules portes déterministes : résultat de persistance, validité et
fraîcheur du programme, contrôles datés et rendu canonique. Une panne d'un second modèle
ne peut donc plus jeter une réponse déjà produite.

L'atomicité reste celle du lot de commandes ; l'autorisation métier indépendante du
modèle n'est pas encore implémentée. Le triage aide à charger le contexte : les
salutations fermées sont résolues localement, les confirmations comme « ok » passent au
classifieur avec l'historique borné et le message cité. Aucun drapeau ne force une
révision ou une question.

Les appels tiers suivent la même séparation. L'activité Strava est distillée avec ses
tours puis capturée avant D- et WBGT ; chaque enrichissement possède un état durable et
peut échouer sans effacer la capture. Le débrief reçoit un instantané borné de cette
capture, sans polyline, et ne rappelle jamais Strava depuis le bac à sable du modèle.
Après redémarrage, le service relit d'abord ce distillat PostgreSQL : une activité déjà
acquise ne redevient pas une dépendance réseau. Pour Messenger, l'enveloppe complète
(texte et boutons) est enregistrée dans `outbound_deliveries.payload` avant l'appel
réseau : calculée une fois, elle peut être livrée plusieurs fois. Une enveloppe `failed`
sans fragment livré, ou `prepared` (une initiative prête retenue pendant une
conversation), est renvoyée telle quelle sans nouveau tour de modèle. Un état `sending`
retrouvé après redémarrage, comme un délai de lecture dépassé sur la Send API, est
intrinsèquement ambigu, car Meta ne fournit pas de clé d'idempotence : le service choisit
l'at-most-once et émet un incident au lieu de risquer un doublon.

Les quelques vues Obsidian et Git sont volontairement en cohérence éventuelle : elles ne
peuvent pas participer à une transaction ACID avec PostgreSQL. Elles sont régénérées,
committées et poussées après la livraison, jamais sur le chemin d'une réponse. Si leur
génération ou leur push échoue, le fait canonique reste enregistré et `projection_states` demeure sale
pour reprise. Le service ne prétend donc jamais avoir annulé une transaction déjà
commitée.

Les écritures passent uniquement par `redline ledger-add`, `redline ledger-complete`,
`redline week-plan revise`, `redline fact-write` et la route renfo signée. Le projecteur est
sérialisé par un verrou consultatif PostgreSQL et n'acquitte l'état sale qu'après le
succès de tous les générateurs.

## Rôles PostgreSQL

Trois rôles séparent les responsabilités :

- `redline_owner`, sans login, possède le schéma et les objets ;
- `redline_migrator`, sans privilège système, peut prendre le rôle owner pour Alembic ;
- `redline_app` peut seulement se connecter, utiliser le schéma et lire/écrire les
  tables métier. Il ne peut ni créer une table, ni créer une base ou un rôle ; le
  journal d'audit est append-only. Il peut marquer et acquitter l'état de projection,
  qui n'est pas un journal mais un singleton de reprise.

Le bootstrap est dans `server/postgres/init-roles.sh`. Les trois mots de passe sont des
secrets Docker hors Git :

```text
/etc/redline/secrets/postgres_password
/etc/redline/secrets/postgres_app_password
/etc/redline/secrets/postgres_migrator_password
```

## Installation du serveur PostgreSQL

```bash
cd /home/ubuntu/redline/server/postgres
sudo install -d -m 0750 -o root -g docker /etc/redline/secrets
# Créer les trois fichiers root:docker en mode 0640.
sudo docker compose --env-file /etc/redline/postgres.env up -d --build
```

Le service tourne ensuite avec le rôle restreint `redline_app` ; `redline_migrator`
n'est employé que par Alembic (voir la section suivante).

Les secrets du service Messenger vivent dans
`/etc/redline/runtime/coach-messenger.env` (`root:ubuntu`, mode `0640`), hors du
checkout que les agents peuvent lire et modifier. `server/.env.example` documente les
clés sans porter de valeur. Une copie `server/.env` n'est admise qu'en développement
local et ne doit jamais subsister sur le VPS.

## Sauvegarde gratuite, chiffrée et hors VPS

Il n'y a ni stockage objet payant, ni abonnement de sauvegarde, ni service permanent
sur le Mac. Toutes les heures, le VPS produit un `pg_dump` au format custom et le
chiffre avec `age` grâce à une **clé publique**, écrit le SHA-256 puis conserve trois
jours de tampon. Ce dépôt porte l'export (`server/postgres/backup.sh`, les unités
systemd, le drill de restauration) ; la collecte hors VPS est, dans le déploiement de
référence, un workflow GitHub Actions privé qui récupère la dernière archive toutes
les deux heures avec une clé SSH dédiée et la conserve comme artifact : le fichier
n'entre jamais dans Git.

La clé SSH est enfermée côté VPS dans une commande forcée qui ne sait que rendre la
dernière paire archive/checksum. Elle ne donne ni shell, ni transfert arbitraire, ni
port forwarding. GitHub ne voit que du ciphertext ; la clé privée `age` reste hors du
VPS et hors de GitHub, idéalement dans le gestionnaire de mots de passe. Un compromis
du VPS ou du compte GitHub ne suffit donc pas à déchiffrer les sauvegardes sorties.

### Installation

Sur le poste d'administration, une seule fois :

```bash
brew install age
install -d -m 0700 ~/.config/redline
age-keygen -o ~/.config/redline/backup-age-key.txt
chmod 0600 ~/.config/redline/backup-age-key.txt
age-keygen -y ~/.config/redline/backup-age-key.txt > /tmp/redline-age-recipient.txt
scp /tmp/redline-age-recipient.txt ubuntu@VPS:/tmp/
```

Sur le VPS :

```bash
sudo apt-get install age
sudo install -m 0640 -o root -g docker /tmp/redline-age-recipient.txt \
  /etc/redline/secrets/backup-age-recipient.txt
sudo cp server/postgres/redline-backup.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now redline-backup.timer
sudo systemctl start redline-backup.service
```

Le socket PostgreSQL local est créé avant le service :

```bash
sudo install -m 0644 server/postgres/redline-postgres.tmpfiles \
  /etc/tmpfiles.d/redline-postgres.conf
sudo systemd-tmpfiles --create /etc/tmpfiles.d/redline-postgres.conf
```

Le `DATABASE_URL` du service et du migrateur utilise le socket, par exemple
`postgresql+psycopg://redline_app:…@/redline?host=/run/redline-postgres`.

Claude et Codex exécutent exactement les mêmes CLI Python avec les variables du service.
Les lectures interrogent PostgreSQL directement. Les écritures qui portent
`REDLINE_TURN_ID` sont préparées dans `redline.turn_commands`, puis appliquées ensemble
par la transaction du tour ; un échec les supprime. Il n'existe ni snapshot JSON ni
outbox de commandes sur disque.

Codex tourne actuellement en `full_access` : bubblewrap échoue sur le VPS
(`loopback: Failed RTM_NEWADDR`). La lecture seule de `TurnExecutor` est donc une
frontière de persistance applicative, pas un isolement système : elle abandonne les
commandes préparées et les changements Git. Elle ne protège pas d'un effet externe
produit par un outil. Le triage désactive le shell et ne charge aucune documentation
projet ; les tours du coach chargent `CLAUDE.md` comme constitution, pour Codex comme pour
Claude. Tant que Codex garde un shell complet sous le même utilisateur Unix, il peut
contourner le lanceur (`python scripts/…`, `psql`) : la frontière d'écriture reste
applicative, et seule une séparation d'utilisateur système la rendrait étanche.
Claude Code n'utilise plus `--dangerously-skip-permissions` ; son
mode `dontAsk` autorise les outils de lecture/écriture du vault et borne Bash aux CLI
exposées par `/usr/local/bin/redline-agent-cli`. Ce lanceur root-owned refuse toute CLI
hors du manifeste root-owned `/usr/local/share/redline/agent-cli-tools.txt`, toute
exécution sans `REDLINE_TURN_ID` valide (une écriture hors tour échapperait à l'abandon
transactionnel) et tout écart entre le runtime du checkout et son commit : éditer
`scripts/` puis l'exécuter dans le même tour n'est donc pas un contournement.

La politique est **fermée par défaut** : tout nouveau script reste inaccessible. Un
script destiné au coach doit entrer explicitement dans `server/agent-cli-tools.txt` ;
la CI relie ce manifeste aux CLI citées par `CLAUDE.md` et les prompts. Ainsi, l'ajout
d'un outil ne casse pas silencieusement le coach et n'élargit jamais ses droits par
simple présence dans `scripts/`.

Cette frontière limite les dégâts d'une instruction malveillante. Elle ne constitue
pas encore un broker de capacités : les secrets PostgreSQL et Strava nécessaires au
service restent présents dans l'environnement du processus et font l'objet d'une dette
résiduelle explicite dans `src/redline/service/config.py`. Le PSID unique, les signatures
webhook, l'utilisateur PostgreSQL applicatif, le sandbox du moteur et systemd restent
les autres frontières de sécurité.

Créer ensuite une paire SSH **dédiée**, installer
`server/postgres/export-latest-backup.sh` sous
`/usr/local/bin/redline-backup-export`, puis ajouter la clé publique à
`/home/ubuntu/.ssh/authorized_keys` avec ces options :

```text
restrict,command="/usr/local/bin/redline-backup-export" ssh-ed25519 AAAA… github-actions-redline-backup
```

Les trois secrets de la collecte GitHub Actions (workflow privé, hors de cette
édition) sont :

```bash
gh secret set REDLINE_BACKUP_SSH_KEY < cle-privee-dediee
gh secret set REDLINE_BACKUP_KNOWN_HOSTS < known-hosts-verifie
gh secret set REDLINE_BACKUP_SSH_TARGET --body 'ubuntu@VPS'
```

### Objectifs et exercice de restauration

Le pire intervalle théorique entre une écriture et sa copie hors VPS est inférieur à
trois heures : export horaire, puis collecte toutes les deux heures. Un retard du
planificateur GitHub allonge ce RPO ; le tampon VPS de trois jours permet le rattrapage.
Ce compromis est explicite et mesurable, sans dépendre de l'état d'un Mac.

Une fois par mois, dans le déploiement de référence : télécharger le dernier artifact
puis le restaurer réellement :

```bash
gh run list --workflow database-backup.yml
gh run download RUN_ID --name redline-postgres-HORODATAGE --dir /tmp/redline-restore
server/postgres/macos-restore-drill.sh \
  "/tmp/redline-restore/redline-….dump.age" \
  "$HOME/.config/redline/backup-age-key.txt"
```

Le script crée un PostgreSQL 17 Docker éphémère, déchiffre en flux, restaure avec
`--exit-on-error`, puis vérifie la révision Alembic et le nombre d'observations. Le
conteneur de test est supprimé dans tous les cas. Une sauvegarde jamais restaurée
reste une hypothèse.
