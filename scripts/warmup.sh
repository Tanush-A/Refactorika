#!/usr/bin/env bash
# warmup.sh — idempotent dev bootstrap for Refactorika.
#
# Brings a fresh clone (or a new session) up to "works the same" state:
#   1. Python venv (.venv)
#   2. deps installed with the [semantic] extra (openai + redisvl)
#   3. .env scaffolded (never overwritten)
#   4. a reachable Redis — uses yours if up, else starts a local redis:8 in Docker
#
# Safe to run on every session: each step checks state first and skips work
# already done, so re-runs are a fast status check. It NEVER overwrites your
# .env, never touches secrets, and never fails the session — anything it can't
# do is reported in the summary, and Refactorika still runs offline without it.

set -u

# ── locate repo root (this script lives in scripts/) ───────────────────────────
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

VENV="$ROOT/.venv"
PY="$VENV/bin/python"
PIP="$VENV/bin/pip"

# status accumulators for the final summary
S_DEPS="skipped"; S_ENV="skipped"; S_REDIS="offline-fallback"; S_OPENAI="local-fallback"

say()  { printf '\033[1;36m▸ %s\033[0m\n' "$1"; }
ok()   { printf '  \033[1;32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[1;33m!\033[0m %s\n' "$1"; }

# ── 1. venv ────────────────────────────────────────────────────────────────────
say "Python virtualenv"
if [ ! -x "$PY" ]; then
  python3 -m venv "$VENV" && ok "created .venv" || { warn "could not create .venv — is python3 installed?"; }
else
  ok ".venv present"
fi

# ── 2. dependencies (editable install + semantic extra) ────────────────────────
say "Dependencies (.[semantic] → openai + redisvl)"
if [ -x "$PY" ]; then
  if "$PY" -c "import refactorika, redisvl" >/dev/null 2>&1; then
    ok "already installed"; S_DEPS="ok"
  else
    warn "installing — first run can take a minute…"
    if "$PIP" install -q -e ".[semantic]" >/dev/null 2>&1; then
      ok "installed refactorika + semantic extra"; S_DEPS="ok"
    elif "$PIP" install -q -e "." >/dev/null 2>&1; then
      warn "semantic extra failed; installed base only (structural analysis works, no embeddings)"; S_DEPS="base-only"
    else
      warn "pip install failed — check network / Python version (needs 3.11+)"; S_DEPS="failed"
    fi
  fi
else
  warn "no venv python — skipping install"; S_DEPS="failed"
fi

# ── 3. .env scaffold (never overwrite) ─────────────────────────────────────────
say ".env"
if [ -f "$ROOT/.env" ]; then
  ok ".env present (left untouched)"; S_ENV="present"
else
  cat > "$ROOT/.env" <<'ENVEOF'
# Refactorika local config. Gitignored — yours alone.
# Optional: embeddings provider. Leave blank to use the local sentence-transformers
# fallback (no key, offline). Set your OWN key to use OpenAI embeddings (costs money).
OPENAI_API_KEY=
# Optional — falls back to local JSON if unset or unreachable.
REDIS_URL=redis://localhost:6379/0
# Where the local-JSON fallback (edit log + analysis cache) lives.
REFACTORIKA_STATE=.refactorika/state.json
ENVEOF
  ok "wrote .env scaffold — add your own OPENAI_API_KEY to enable embeddings"; S_ENV="scaffolded"
fi

# OPENAI key present?
OPENAI_KEY="$(grep -E '^OPENAI_API_KEY=' "$ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2-)"
[ -n "$OPENAI_KEY" ] && S_OPENAI="ok"

# ── 4. Local Redis ─────────────────────────────────────────────────────────────
# We always stand up a LOCAL Redis on localhost:6379 — never depend on a remote/
# cloud Redis. The .env points REDIS_URL at localhost so the app connects here.
LOCAL_URL="redis://localhost:6379/0"
say "Local Redis ($LOCAL_URL)"
ping_redis() {
  [ -x "$PY" ] || return 1
  RU="$LOCAL_URL" "$PY" - <<'PYEOF' >/dev/null 2>&1
import os
import redis
redis.Redis.from_url(os.environ["RU"], socket_connect_timeout=0.6).ping()
PYEOF
}

if ping_redis; then
  ok "already running on localhost:6379"; S_REDIS="ok"
elif command -v docker >/dev/null 2>&1; then
  warn "not running — starting a local redis:8 in Docker"
  if docker ps -a --format '{{.Names}}' | grep -qx "refactorika-redis"; then
    docker start refactorika-redis >/dev/null 2>&1
  else
    docker run -d --name refactorika-redis --restart=always -p 6379:6379 redis:8 >/dev/null 2>&1
  fi
  sleep 1
  if ping_redis; then ok "local Redis up (container: refactorika-redis)"; S_REDIS="ok-docker"
  else warn "Docker start failed — running on local-JSON fallback"; S_REDIS="offline-fallback"; fi
elif command -v redis-server >/dev/null 2>&1; then
  warn "not running — starting local redis-server in background"
  redis-server --daemonize yes >/dev/null 2>&1
  sleep 1
  if ping_redis; then ok "local Redis up (redis-server daemon)"; S_REDIS="ok-local"
  else warn "redis-server start failed — running on local-JSON fallback"; S_REDIS="offline-fallback"; fi
else
  warn "no Redis, no Docker, no redis-server — running on local-JSON fallback"
  warn "install one: 'brew install redis' (then re-run) or start Docker"
  S_REDIS="offline-fallback"
fi

# ── summary ────────────────────────────────────────────────────────────────────
printf '\n\033[1mWarmup summary\033[0m\n'
printf '  deps    : %s\n' "$S_DEPS"
printf '  .env    : %s\n' "$S_ENV"
printf '  redis   : %s\n' "$S_REDIS"
printf '  openai  : %s (embeddings)\n' "$S_OPENAI"

cat <<'NOTE'

What warmup CANNOT do for you (per-dev, by design):
  • Supply OPENAI_API_KEY — it's secret, costs money, and is yours. Add it to .env
    yourself; until then embeddings use the local sentence-transformers fallback.
  • Use a SHARED / cloud Redis. By design it only stands up a LOCAL Redis
    (Docker redis:8, else a redis-server daemon). Redis here is per-instance backend
    state, not shared infra — another dev runs their own local one.
  • Register the MCP server in your client. Do that once, yourself, e.g.:
      claude mcp add refactorika -- .venv/bin/python -m refactorika.mcp_server
    (auto-connects to whatever REDIS_URL your .env points at).
NOTE
