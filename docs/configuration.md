# Configuration

Three kinds of configuration, with three owners:

| What | Where | Owner |
|---|---|---|
| the instance: who the athlete is, what is watched | `redline.toml` at the vault root | you, in the vault |
| the coaching rules and their thresholds | `rulebook.toml` at the vault root, optional | you, in the vault |
| how the service runs: secrets, timings, models | the environment, and the files below | the operator, outside Git |

The vault's two files are described in [vault-format.md](vault-format.md) and, field by
field, in `config/redline.example.toml` and `src/redline/model/rulebook.default.toml`.
This page covers the service. An invalid value stops the service at startup with its
name; it never falls back silently to a default.

## Files

| File | Read by | Example |
|---|---|---|
| the environment file (`redline.env` with Compose, `/etc/redline/runtime/coach-messenger.env` with systemd) | the service | `config/redline.env.example` |
| the routing file, named by `REDLINE_ROUTING` | the service, at startup | `config/routing.example.toml` |
| `.env` at the checkout root | Docker Compose | `config/compose.env.example` |
| `/etc/redline/postgres.env` | the PostgreSQL Compose and `backup.sh` (systemd setup) | `config/postgres.env.example` |

Locally, a `.env` at the engine root is also read by the service. Every file holding a
secret stays out of Git and readable only by root and the deploy account.

## Environment variables

A test checks that every variable the service reads is in this table.

### Messenger

| Variable | Default | Meaning |
|---|---|---|
| `MESSENGER_PAGE_TOKEN` | required | the Page access token |
| `MESSENGER_APP_SECRET` | required | the Meta app secret: checks the webhook signatures |
| `MESSENGER_VERIFY_TOKEN` | required | the webhook verify token you choose, 32 characters or more |
| `MESSENGER_ALLOWED_PSID` | required | the athlete's page-scoped id; anyone else is ignored |
| `GRAPH_API_VERSION` | `v21.0` | the Graph API version |

### Service

| Variable | Default | Meaning |
|---|---|---|
| `VAULT_PATH` | required | the vault clone; never the engine checkout |
| `DATABASE_URL` | required | PostgreSQL, with the `redline_app` role |
| `REDLINE_STATE_DIR` | `var/` in the checkout | what the service writes outside PostgreSQL: card outbox, attachments, Strava tokens |
| `WEBHOOK_HOST` | `127.0.0.1` | the address the HTTP service listens on; `0.0.0.0` in a container |
| `WEBHOOK_PORT` | `8000` | its port, the one the proxy forwards to |
| `GIT_REMOTE` | `origin` | the vault's remote |
| `GIT_BRANCH` | `main` | the vault's branch |
| `COACH_BASE_URL` | empty | the public origin, `https://<domain>`; empty disables the strength page and its link on the card |
| `HEALTHCHECK_PING_URL` | empty | a URL pinged every 5 minutes (healthchecks.io, for instance) |

### The agent

| Variable | Default | Meaning |
|---|---|---|
| `AGENT_PROVIDER` | required | `claude` or `codex` |
| `CLAUDE_CODE_OAUTH_TOKEN` | empty | Claude's credentials, from `claude setup-token` |
| `CLAUDE_BIN` | `claude` | the Claude Code binary, 2.1.296 or later |
| `CODEX_BIN` | the SDK's | a Codex binary other than the SDK's pinned one |
| `REDLINE_ROUTING` | empty | the routing file (below) |
| `AGENT_TIMEOUT` | `600` | the watchdog of an N0 or N1 turn, in seconds |
| `AGENT_TIMEOUT_DEEP` | `1800` | the watchdog of N2, N3 and `/deep` |
| `AGENT_TIMEOUT_HEAVY` | `5400` | the watchdog of a heavy review |
| `TRIAGE_ENABLED` | `true` | without triage, every message is N1 |
| `TRIAGE_TIMEOUT` | `10` | beyond it, the message is N1 |
| `SESSION_TTL_HOURS` | `6` | the life of an agent session |
| `DEBOUNCE_SECONDS` | `5` | the wait after the last message of a burst |
| `DEBOUNCE_MAX_SECONDS` | `20` | the longest a burst is held, from its first message |

### Initiatives

| Variable | Default | Meaning |
|---|---|---|
| `PROACTIVE_ENABLED` | `true` | the coach may write first |
| `PROACTIVE_ACTIVE_START` | `07:30` | the window opens at this local time and runs to midnight |
| `PROACTIVE_MIN_GAP_HOURS` | `4` | the spacing of ordinary initiatives |
| `PROACTIVE_SILENCE_DAYS` | `3` | days without activity or message before checking in |
| `PROACTIVE_MORNING` | `07:45` | the morning message; empty disables it |
| `PROACTIVE_WEEKLY` | `19:00` | the weekly review, on Sunday; empty disables it |
| `PROACTIVE_MONTHLY` | `18:30` | the monthly review, on the 1st; empty disables it |
| `STRAVA_DEBRIEF_DELAY` | `300` | the longest wait before a debrief, in seconds |

### Strava

| Variable | Default | Meaning |
|---|---|---|
| `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET` | empty | the Strava application; the tokens live in `$REDLINE_STATE_DIR/strava-token.json` |
| `STRAVA_TOKEN_CACHE` | `$REDLINE_STATE_DIR/strava-token.json` | another path for that file |
| `STRAVA_OWNER_ID` | empty | the athlete's Strava id; empty disables the webhook |
| `STRAVA_VERIFY_TOKEN` | empty | the subscription's verify token, 32 characters or more; empty disables the webhook |
| `STRAVA_WEBHOOK_SECRET` | empty | the secret segment of the callback URL, 32 characters or more; empty disables the webhook |

### Attachments and cards

| Variable | Default | Meaning |
|---|---|---|
| `ATTACHMENT_DIR` | `$REDLINE_STATE_DIR/attachments` | where an attachment waits for its turn |
| `ATTACHMENT_MAX_BYTES` | 12 MiB | the largest attachment |
| `ATTACHMENT_TOTAL_BYTES` | 20 MiB | the largest batch |
| `STRENGTH_REPORTS_PER_DAY` | `8` | strength reports accepted per day |
| `CHROMIUM_PATH` | Playwright's | a system Chromium, where Playwright ships none |

## The routing file

`REDLINE_ROUTING` names a TOML file that overrides the [routes](architecture.md#routing).
Every key is optional; a route or a field left out keeps the engine's default.

```toml
[routing.N2]
model = "sonnet"
effort = "high"
fallback = { model = "opus", effort = "high" }
```

The routes are `TRIAGE`, `N0`, `N1`, `N2`, `N3`, `DEEP` and `HEAVY`. A fallback on
`TRIAGE` is refused, and an effort unknown to the provider stops the service at
startup. The engine forbids no model: what a route may cost is your choice.
