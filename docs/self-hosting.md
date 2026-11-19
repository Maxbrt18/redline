# Self-hosting

Redline runs on one Linux host. Two ways to install it:

- **Docker Compose** (`compose.yml` at the checkout root): PostgreSQL, the migrations,
  the service and Caddy, in containers. The simplest.
- **systemd**: the service runs on the host, PostgreSQL alone in a container. More
  control over the runtime, more to maintain.

Either way, the rule is the same: **the service never runs as the account that deploys
it**. A coaching turn can write files, so everything the service can write is something
a turn can write. The engine, its venv and the agent runtime belong to another account;
the service owns only its state (the vault, the outbox, the agent's sessions).

[Getting started](getting-started.md) covers the accounts and the first configuration.

## Docker Compose

The image (`Dockerfile`) holds the engine, its venv, Chromium and Claude Code, owned by
root; the service runs as uid 10001. What the host provides:

| On the host | Mounted as |
|---|---|
| `.env` (from `config/compose.env.example`) | the domain and the migrator's URL |
| `redline.env` (from `config/redline.env.example`) | the service's environment |
| `secrets/` | the three PostgreSQL passwords |
| `vault/`, `ssh/` | the vault clone and the key that pushes it, owned by uid 10001 |

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f redline
```

`migrate` runs `alembic upgrade head` with the migrator role before every start of the
service. **To update**: `git pull --ff-only` on `stable`, then the same `up -d --build`.
Operator commands run in the service container: `docker compose exec redline redline
<command>`.

Claude Code is pinned by the `CLAUDE_CODE_VERSION` build argument (2.1.296 or later:
`--restricted` is required). Its own updater is off; a new version is a rebuild.

## systemd

The reference layout, to adapt:

| Account | Owns | Writes |
|---|---|---|
| `ubuntu` (deploy, sudo) | the checkout `/home/ubuntu/redline`, the venv, the Claude runtime | deployments |
| `redline` (service, no sudo, no login) | `/var/lib/redline`: the vault, `state/`, `~/.claude` | notes, cards, sessions |

```bash
sudo useradd --system --home-dir /var/lib/redline --shell /usr/sbin/nologin redline
sudo install -d -o redline -g redline -m 0700 /var/lib/redline /var/lib/redline/state
sudo -u redline git clone <vault remote> /var/lib/redline/vault
```

**PostgreSQL**, alone in a container, shares its socket with the host:

```bash
sudo install -m 0644 deploy/systemd/redline-postgres.tmpfiles /etc/tmpfiles.d/redline-postgres.conf
sudo systemd-tmpfiles --create /etc/tmpfiles.d/redline-postgres.conf
sudo install -d -m 0750 -o root -g docker /etc/redline/secrets     # the three password files
sudo cp config/postgres.env.example /etc/redline/postgres.env      # then edit it
sudo docker compose --env-file /etc/redline/postgres.env -f deploy/postgres/compose.yml up -d --build
```

The image replaces PostgreSQL's default local `trust` with `deploy/postgres/pg_hba.conf`:
every role gives its password, and `postgres` is reserved to that account inside the
container, hence `--user postgres` in the commands below.

The `DATABASE_URL` of the service and of the migrator then uses the socket:
`postgresql+psycopg://redline_app:…@/redline?host=/run/redline-postgres`.

**The service and a release.** `deploy/release` moves the checkout to another commit
and back, from the deploy account:

```bash
cd /home/ubuntu/redline && deploy/release <tag or commit>    # e.g. a tag of `stable`
deploy/release <tag or commit> --declarations <dir>         # with the rulebook it needs
deploy/release --rollback                                    # back to the commit it replaced
```

It does, in this order:

1. **Preflight.** The target engine's `redline config-check` reads a copy of the vault's
   `redline.toml` and `rulebook.toml`, or the ones in `--declarations <dir>`. The service
   refuses to start on a key its engine requires or no longer knows; here that refusal
   stops the release before anything moves. The running engine refuses a key it does
   not know yet, so a declaration the new engine needs is never written to the vault
   ahead of time: put it in `<dir>`.
2. **Release.** Backup (`redline-backup.service`), checkout, the launcher, its allowlist
   and the unit installed as root, `pip install -e`, `alembic upgrade head` when
   `migrations/` changed (with `MIGRATOR_DATABASE_URL`), then, service stopped, the
   declarations of `<dir>` written and committed in the vault, and start.
3. **Check.** `/ready` answers and `runtime_tree` in `/health` is the target's: it
   fingerprints the engine's code, so a vault commit does not move it but different
   code does.

A failure in 2 or 3 puts the previous commit and declarations back and restarts it. After a migration it
does not: the schema belongs to the new code, and the script says which revision to
downgrade to by hand. A rollback across a migration is refused for the same reason.
Overrides (`REDLINE_CHECKOUT`, `REDLINE_VENV`, `REDLINE_VAULT`, `REDLINE_EXTRAS`…) are at
the top of the script; it targets this systemd layout, not Compose.

The unit (`deploy/systemd/coach-messenger.service`) makes the file system read-only
outside `/var/lib/redline` and reads its environment from
`/etc/redline/runtime/coach-messenger.env`, readable by root and the deploy account
only. **Caddy** terminates TLS in front of it: `deploy/caddy/Caddyfile`. Keep Claude
Code's own updater off (`DISABLE_AUTOUPDATER=1`, in the unit): install a new version
deliberately, check it with a real turn, never in the middle of one.

Never have these steps carried out by a message or by the agent.

## Backups

`deploy/postgres/backup.sh` takes a `pg_dump` (custom format), encrypts it with
[`age`](https://age-encryption.org) to a **public key**, writes a manifest (Alembic
revision, number of observations, latest one) and a SHA-256, and keeps
`REDLINE_BACKUP_RETENTION_DAYS` of them in `REDLINE_BACKUP_DIR`. The private key stays
off the server: a compromised host cannot read its own backups.

```bash
age-keygen -o backup-age-key.txt                  # on your workstation; keep it safe
age-keygen -y backup-age-key.txt                  # the public key, for the server
sudo install -m 0640 -o root -g docker <public key file> /etc/redline/secrets/backup-age-recipient.txt
sudo cp deploy/systemd/redline-backup.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now redline-backup.timer
```

The timer runs every hour. With Compose, run the same script with
`REDLINE_POSTGRES_COMPOSE=<checkout>/compose.yml` and `REDLINE_POSTGRES_ENV=<checkout>/.env`.
Copying the archives off the host is yours to arrange; they are encrypted, so any
storage will do.

**Restore** into a new, empty database of a PostgreSQL 17 container, then compare its
Alembic revision and its number of observations with the manifest:

```bash
docker exec --user postgres <container> createdb --username postgres redline_restore
age --decrypt --identity backup-age-key.txt redline-<stamp>.dump.age \
  | docker exec -i --user postgres <container> pg_restore --username postgres --dbname redline_restore \
      --exit-on-error --no-owner --no-privileges
docker exec --user postgres <container> psql --username postgres --dbname redline_restore \
  -c 'SELECT version_num FROM alembic_version' -c 'SELECT count(*) FROM redline.observations'
```

`REDLINE_BACKUP_PING_URL`, in the same environment file, names a healthchecks.io check
of its own, scheduled hourly: `backup.sh` pings it after each backup and pings its
`/fail` endpoint when one fails, and the check alerts when none reports at all.

A backup never restored is a hypothesis. `deploy/postgres/restore-check.sh` does the
restore above in a throwaway PostgreSQL 17 container, where the private key lives, and
holds the result to the manifest (revision, number of observations, latest day) and the
checksum; it prints how long the restore took and removes the container:

```bash
deploy/postgres/restore-check.sh redline-<stamp>.dump.age backup-age-key.txt
```

Run it on the latest archive once a month, and after any change to the chain.

### Recovery objectives

| | Target | Rests on |
|---|---|---|
| RPO, PostgreSQL | 1 hour | the hourly backup, once copied off the host |
| RPO, vault | 10 minutes | the push after each delivery and at maintenance |
| RTO | 2 hours (target, measured only by a rehearsal) | a new host from this page, the latest archive restored, the vault cloned |

The archives on the host cover a failed migration or a deleted row, not the loss of the
host: the RPO holds only as long as the copy off the host runs at least hourly.

## Strava

Strava does not sign its events, so the callback is authenticated only by the
`STRAVA_WEBHOOK_SECRET` segment of its URL. After setting or changing that secret, and
once the service runs with it:

```bash
redline strava-subscription renew     # replaces the subscription; prints no secret
redline strava-subscription status    # 0 when the one subscription is the expected one
```

The Strava tokens rotate: they live in `$REDLINE_STATE_DIR/strava-token.json`, written by
`redline strava login`, never in the environment.

## Endpoints

| Method | Route | Use |
|---|---|---|
| `GET`, `POST` | `/webhook` | the Meta challenge, signed Messenger events |
| `GET`, `POST` | `/strava-webhook/<secret>` | subscription and activity events |
| `GET`, `POST` | `/notebook-api/*` | the iOS app's API (device bearer token) |
| `POST` | `/strength-session`, `/strength-report` | the guided strength session, by opaque capability |
| `GET` | `/health` | liveness, deployed commit, `runtime_tree`, uptime |
| `GET` | `/ready` | worker running, PostgreSQL reachable, schema at the Alembic head: **local only** |

Bodies are capped at 1 MB, by the service and by Caddy. The tokens you choose
(`MESSENGER_VERIFY_TOKEN`, `STRAVA_VERIFY_TOKEN`, `STRAVA_WEBHOOK_SECRET`) must be 32
characters or more, or the service does not start.

## Operator commands

Run with the service's environment (`docker compose exec redline …`, or on the host
with the venv's `python` and the environment file loaded). They are outside the coach's
allowlist on purpose:

```bash
redline proactive-job status --job-key weekly:2030-W34    # an initiative and its last error
redline proactive-job reopen --job-key weekly:2030-W34    # after a repair
redline operator-message --key review-w34 --text "…"      # send a text written outside the service
redline vault-check                                       # the vault against the engine's contract
```

**Adding a command the coach may use**: a module under `src/redline/cli/` is refused by
default. List it in `deploy/agent-cli/agent-cli-tools.txt` and name it in the prompt
that needs it; the tests reject a missing, unsorted or unknown entry. The list is
installed outside the vault, owned by root: the coach cannot widen its own rights.

## Diagnostics

```bash
docker compose logs --tail 200 redline          # or: journalctl -u coach-messenger -n 200
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/ready
```

In order:

1. the configuration and the PostgreSQL connection at startup: an invalid value stops
   the service with its name;
2. the `redline.events` lines, one JSON object per step and never the text of a message:
   `triage`, `model`, `apply`, `shape` and `deliver` share the `turn_id` of a turn;
   initiatives carry `job_id`, `kind` and `outcome`;
3. `Internal incident` lines: a projection, a commit or a push that failed;
4. `redline week-plan check --week <week>` for the weeks involved;
5. `redline vault-check`;
6. read-only, `inbound_messages`, `proactive_jobs` (`failed` and `last_error`) and
   `projection_states`;
7. the age of the latest backup.
