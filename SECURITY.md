# Security

Redline is a self-hosted, single-athlete service published as is, without support. It
still handles health-adjacent data (pain, sleep, heart rate) and secrets for Meta,
Strava and an LLM provider, so security reports are welcome.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting: **Security → Report a vulnerability** on
this repository. Please do not open a public issue. Include the affected file or
endpoint, how to reproduce, and what an attacker gains. Expect an answer on a
best-effort basis; there is no bounty.

Only the latest `main` is maintained.

## Security model

What the engine is built to guarantee:

- **One athlete.** Messenger messages from any sender other than
  `MESSENGER_ALLOWED_PSID` are dropped.
- **Signed webhooks.** Every Messenger call is checked against `X-Hub-Signature-256`
  with `MESSENGER_APP_SECRET`; the service refuses to start without the Messenger
  secrets. The Strava callback is authenticated by a secret URL segment
  (`STRAVA_WEBHOOK_SECRET`); without it, the Strava webhook stays disabled.
- **The agent is confined to the vault and its commands.** A turn may edit notes in
  the vault and run the commands listed in `deploy/agent-cli/agent-cli-tools.txt`, through
  `deploy/agent-cli/redline-agent-cli`, which executes the deployed commit of the engine and
  refuses to run if the service account can write the engine's code or its venv.
  - Claude Code runs every call with `--restricted --strict-mcp-config`: no user,
    project or vault settings file, hook or MCP server is loaded, the file tools stay
    in the vault, and writes to settings, Git and tool configuration are refused. A
    turn cannot plant something the next call would execute.
  - Codex runs its commands in its workspace sandbox (bubblewrap on Linux): writes are
    limited to the vault and `REDLINE_STATE_DIR`; `.git` and `.codex` stay read-only.
  - Nothing in the vault is executed. The engine starts its commands from the vault
    with `python -P`, so a `redline/` folder there never shadows the engine, and the
    views run without the service secrets. Code a turn writes anyway (Python, a script,
    the vault's Git hooks or CI) is removed before the notes are committed.
  - The reference unit runs the service as a dedicated account without sudo, with a
    read-only file system outside its state directory.
- **A local account is not a database account.** The PostgreSQL image of `deploy/postgres/`
  asks every role for its password, on the shared socket too; `postgres` is reachable
  only from that account inside the container. The service connects as `redline_app`,
  which cannot alter the schema nor rewrite the append-only tables.
- **Received content is data.** Messages and attachments are never treated as
  instructions to the service. Attachments are downloaded by the worker into a private,
  size-bounded, temporary file that is deleted after the turn.
- **Private notebook.** The notebook PWA serves only its shell and static resources
  publicly; every data route needs a device cookie (`Secure`, `HttpOnly`,
  `SameSite=Strict`) obtained by exchanging a one-time token sent over Messenger, and
  revocable.
- **Secrets stay out of Git.** They live in the service environment file
  (`/etc/redline/runtime/coach-messenger.env` in the reference deployment) and in
  `REDLINE_STATE_DIR` (`var/`, ignored).

What it does not protect against: a compromised host or LLM provider account, a vault
repository made public by its owner, a model that writes something wrong in the
athlete's own notes, or a deployment that runs the service as the account owning the
engine (the confinement then rests on the agent runtime alone). The agent can read
what the service account can read: keep the environment file and other secrets out of
its reach. See [`docs/llm-data.md`](docs/llm-data.md) for what reaches the
model provider.

## Out of scope

Findings that require root on the host, a leaked `.env`, or a modified deployment;
denial of service by volume; missing hardening headers without a demonstrated impact.
