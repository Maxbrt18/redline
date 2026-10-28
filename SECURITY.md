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
- **The agent cannot run arbitrary code.** The coach runs commands only through
  `deploy/redline-agent-cli`, which executes the deployed commit of the engine (never a
  file a turn could have edited) and only the commands listed in
  `deploy/agent-cli-tools.txt`.
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
repository made public by its owner, or a model that writes something wrong in the
athlete's own notes. See [`docs/llm-data.md`](docs/llm-data.md) for what reaches the
model provider.

## Out of scope

Findings that require root on the host, a leaked `.env`, or a modified deployment;
denial of service by volume; missing hardening headers without a demonstrated impact.
