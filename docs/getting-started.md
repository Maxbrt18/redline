# Getting started

This page takes a new instance from nothing to a first message. It assumes the Docker
Compose setup of [self-hosting](self-hosting.md); the systemd setup differs only in
where the files live.

Redline serves **one athlete**, over **Messenger**, with a vault written in **French**.
Budget an afternoon: most of it is spent in the Meta and Strava consoles.

## What you need

- A server reachable from the internet, a domain pointing at it, ports 80 and 443 open.
- A **Facebook Page** and a **Meta app** with the Messenger product (it can stay in
  development mode: only you will talk to it).
- A **Strava API application** (<https://www.strava.com/settings/api>).
- A **model account**: a Claude Pro or Max subscription (`claude setup-token`), or an
  OpenAI account for Codex.
- A private **Git repository** for the vault, and a deploy key with write access to it.

## 1. The vault

The vault is the coach's memory: Markdown notes in a Git repository, which you can open
in Obsidian. Start from the fictional one the tests use, then make it yours:

```bash
git clone -b stable https://github.com/Maxbrt18/redline.git && cd redline
cp -R tests/fixtures/vault vault
cp templates/vault/CLAUDE.md vault/CLAUDE.md
cp -R templates/vault/.githooks vault/
mkdir -p vault/templates && cp templates/*.md vault/templates/
cd vault && git init && git remote add origin <your vault repository> && cd ..
```

`stable` points at the latest release, tagged `v0.x.y`; `main` is where pull requests
land, and is not meant to be run by an instance.

Then edit, in `vault/`:

- **`redline.toml`**, the instance: the athlete's first name, resting and maximum heart
  rate, time zone, the place used for the weather, the watched injuries and the tracked
  capabilities. `config/redline.example.toml` documents every field; the service refuses
  to start while a `[placeholder]` remains.
- **`CLAUDE.md`**, the coach's instructions. The template keeps the structure that
  matters (the reading ladder, the calibration rule, one owner per fact); replace the
  bracketed passages.
- **`athlete/`** and **`coaching/`**: the profile, the goals, the method, the workouts and
  exercises. [vault-format.md](vault-format.md) says what the engine reads and how.
- **`rulebook.toml`**, optional: the coaching rules in force and their thresholds. Without
  it the engine applies `src/redline/model/rulebook.default.toml`; copy that file to
  change it, since a vault's rulebook replaces the default entirely.

Remove the test athlete's notes you do not keep, commit, and push. The vault directory
and its SSH key must be readable by uid 10001, the service account of the image:

```bash
mkdir -p ssh && cp <deploy key> ssh/id_ed25519 && ssh-keyscan github.com > ssh/known_hosts
sudo chown -R 10001 vault ssh && chmod 600 ssh/id_ed25519
```

## 2. The configuration

Three files, all ignored by Git:

```bash
cp config/compose.env.example .env          # the domain, the migrator's URL
cp config/redline.env.example redline.env   # the service
mkdir -p secrets                            # one PostgreSQL password per file
for name in postgres_password postgres_app_password postgres_migrator_password; do
  python3 -c "import secrets; print(secrets.token_urlsafe(32), end='')" > secrets/$name
done
```

In `redline.env`, set at least:

| Variable | Value |
|---|---|
| `MESSENGER_PAGE_TOKEN` | the Page access token (step 3) |
| `MESSENGER_APP_SECRET` | the app secret (Meta → App settings → Basic) |
| `MESSENGER_VERIFY_TOKEN` | a random string of 32 characters or more, that you choose |
| `MESSENGER_ALLOWED_PSID` | the athlete's page-scoped id (step 3) |
| `DATABASE_URL` | `postgresql+psycopg://redline_app:<postgres_app_password>@postgres:5432/redline` |
| `AGENT_PROVIDER` | `claude` or `codex` |
| `CLAUDE_CODE_OAUTH_TOKEN` | for Claude: the output of `claude setup-token` |
| `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET` | the Strava application |
| `STRAVA_OWNER_ID` | the athlete's Strava id (the number in their profile URL) |
| `STRAVA_VERIFY_TOKEN`, `STRAVA_WEBHOOK_SECRET` | two more random strings of 32 characters or more |
| `COACH_BASE_URL` | `https://<your domain>` |

`MIGRATOR_DATABASE_URL` in `.env` uses the `redline_migrator` password. Every variable
is in [configuration.md](configuration.md).

## 3. Messenger

In the Meta app, Messenger product:

1. Generate a **Page access token** for your Page; put it in `MESSENGER_PAGE_TOKEN`.
2. Find the athlete's **PSID**, the id the Page knows them by: send any message to the
   Page from their account, then read the conversation's participants. The id that is
   not the Page's is the PSID; put it in `MESSENGER_ALLOWED_PSID`. The service refuses
   to start without it, and answers no one else.

   ```bash
   curl -s "https://graph.facebook.com/v21.0/me/conversations?fields=participants&access_token=$MESSENGER_PAGE_TOKEN"
   ```
3. After the service is up (step 4), add the **webhook**: callback
   `https://<your domain>/webhook`, verify token `MESSENGER_VERIFY_TOKEN`, fields
   `messages` and `messaging_postbacks`. Subscribe the Page.
4. Optionally, install the menu and the conversation starters:
   `docker compose exec redline redline setup-messenger-profile`.

## 4. Start

```bash
docker compose up -d --build
docker compose logs -f redline       # "Redline started"
curl -fsS https://<your domain>/health
```

`migrate` creates the schema before the service starts. Then authorize Strava once, in
an interactive terminal (the code is read from the keyboard, never from an argument):

```bash
docker compose exec -it redline redline strava login
docker compose exec redline redline strava check
docker compose exec redline redline strava-subscription renew
```

## 5. The first message

Once the webhook is subscribed, send a message to the Page from the athlete's account:
the coach answers. From there:

- `/carnet` in Messenger gives a link to the notebook; add it to the home screen.
- `/help` lists the commands; `/status` says what the coach is doing.
- The first week has no plan: ask the coach for one. Until a valid plan exists, it
  prescribes nothing.
- `redline vault-check` checks the vault against what the engine expects:
  `docker compose exec redline redline vault-check`.
