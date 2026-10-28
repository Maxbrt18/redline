# What reaches the LLM provider

Redline runs its coach through an agent CLI (`AGENT_PROVIDER=claude` for Claude Code,
or `codex`). Everything the model reads is sent to that provider under the operator's
own account and the provider's data terms. The engine adds no telemetry of its own.

## Sent on every coaching turn

- **The instructions**: the vault's `CLAUDE.md` (or `AGENTS.md`), `redline.toml`, and the
  engine's prompts from `src/redline/coach/fr/prompts/`.
- **The conversation**: the athlete's Messenger messages since the session started,
  and the coach's replies.
- **A typed context packet** built by the service: the current week's plan, recent
  sessions from the ledger (distance, pace, heart rate, cadence, elevation, weather,
  RPE, pain scores), the day's declared state (sleep, life load), open threads and
  deadlines.
- **Attachments** the athlete sends (JPEG, PNG, WebP images, PDF), within
  `ATTACHMENT_MAX_BYTES` and `ATTACHMENT_TOTAL_BYTES`.

## Read by the agent on demand

During a turn the agent works in a checkout of the vault with `Read`, `Glob`, `Grep`,
`Edit` and `Write`, and may run the commands of `deploy/agent-cli-tools.txt`. Whatever it
opens is sent to the provider:

- any note of the vault (athlete profile, injury history, goals, decisions, journal);
- PostgreSQL facts returned by the CLIs (`redline ledger`, `redline fact-read`, `redline week-plan`…);
- Strava activities, laps and streams fetched by `redline strava`;
- weather at the start of a run (`redline weather`, which sends the route's coordinates and
  time to Open-Meteo).

## Smaller calls

- **Triage**: each incoming message is classified by a small model with no tools and
  an empty working directory; it receives the message text only.
- **Proactive messages** (morning, weekly and monthly reviews, debrief after a Strava
  upload) are ordinary turns with the same inputs as above.

## Not sent

- Messages from other Messenger users: they are dropped before any turn.
- Raw attachments after their turn: the temporary file is deleted.
- Secrets: the service environment (tokens, `DATABASE_URL`) and `REDLINE_STATE_DIR` (the
  Strava token cache) are outside the vault checkout and never put in a prompt.
  Claude's file tools are confined to the vault (`--restricted`), and the attachment
  folder during an attachment turn; Codex reads with the service account's rights.
  Keep the environment file readable only by root and the deploy account.

## Reducing what is sent

Keep in the vault only what changes a prescription (the vault template says so), lower
`ATTACHMENT_MAX_BYTES` and `ATTACHMENT_TOTAL_BYTES`, and choose a provider whose data
terms fit you.
