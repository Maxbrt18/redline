# The Redline service, its agent CLI and Claude Code, for compose.yml.
#
# The engine and its venv belong to root: the service runs as `redline` and a turn can
# never rewrite the code it runs, which `redline-agent-cli` checks before every command.
FROM node:22-bookworm-slim AS node

FROM python:3.12-slim-bookworm

ARG CLAUDE_CODE_VERSION=2.1.296
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git openssh-client \
    && rm -rf /var/lib/apt/lists/*

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    && npm cache clean --force

# The checkout comes with its Git metadata: the agent CLI refuses to run code that
# differs from the commit.
COPY . /opt/redline
WORKDIR /opt/redline
RUN python -m venv /opt/.venvs/redline \
    && /opt/.venvs/redline/bin/pip install -c requirements.txt -e '.[cards]' \
    && /opt/.venvs/redline/bin/python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && install -m 0755 deploy/redline-agent-cli /usr/local/bin/redline-agent-cli \
    && install -D -m 0644 deploy/agent-cli-tools.txt /usr/local/share/redline/agent-cli-tools.txt \
    && useradd --system --uid 10001 --home-dir /var/lib/redline --create-home redline \
    && install -d -o redline -g redline -m 0700 /var/lib/redline/state

USER redline
ENV PATH=/opt/.venvs/redline/bin:/usr/local/bin:/usr/bin:/bin \
    HOME=/var/lib/redline \
    REDLINE_REPO=/opt/redline \
    REDLINE_STATE_DIR=/var/lib/redline/state \
    VAULT_PATH=/var/lib/redline/vault \
    WEBHOOK_HOST=0.0.0.0 \
    DISABLE_AUTOUPDATER=1
EXPOSE 8000
CMD ["python", "-m", "redline"]
