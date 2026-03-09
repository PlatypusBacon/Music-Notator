#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# start.sh — Launch all ScoreScribe backend services
#
# Usage:
#   ./start.sh            # starts Redis, Celery worker, and FastAPI
#   ./start.sh api        # FastAPI only (if Redis/Celery already running)
#   ./start.sh worker     # Celery worker only
#   ./start.sh stop       # kill all background processes started by this script
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_DIR="$SCRIPT_DIR/.pids"
mkdir -p "$PID_DIR"

# ── Helpers ───────────────────────────────────────────────────────────────────

log()  { echo -e "\033[1;32m[ScoreScribe]\033[0m $*"; }
warn() { echo -e "\033[1;33m[ScoreScribe]\033[0m $*"; }
die()  { echo -e "\033[1;31m[ScoreScribe]\033[0m $*" >&2; exit 1; }

require() {
    command -v "$1" &>/dev/null || die "'$1' not found. Install it first."
}

stop_all() {
    log "Stopping services…"
    for pidfile in "$PID_DIR"/*.pid; do
        [ -f "$pidfile" ] || continue
        pid=$(cat "$pidfile")
        name=$(basename "$pidfile" .pid)
        if kill -0 "$pid" 2>/dev/null; then
            log "  Stopping $name (PID $pid)"
            kill "$pid" 2>/dev/null || true
        fi
        rm -f "$pidfile"
    done
    log "Done."
}

trap stop_all EXIT

# ── Commands ──────────────────────────────────────────────────────────────────

CMD="${1:-all}"

if [[ "$CMD" == "stop" ]]; then
    stop_all
    exit 0
fi

# ── Activate virtualenv if present ────────────────────────────────────────────
if [[ -f "$SCRIPT_DIR/.venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "$SCRIPT_DIR/.venv/bin/activate"
    log "Activated virtualenv: $SCRIPT_DIR/.venv"
fi

# ── Verify required tools ─────────────────────────────────────────────────────
require python
require redis-cli

# ── Redis ─────────────────────────────────────────────────────────────────────
start_redis() {
    if redis-cli ping &>/dev/null; then
        log "Redis already running — skipping start."
    else
        require redis-server
        log "Starting Redis…"
        redis-server --daemonize yes \
            --logfile "$SCRIPT_DIR/redis.log" \
            --loglevel notice
        sleep 1
        redis-cli ping &>/dev/null || die "Redis failed to start. Check redis.log."
        log "Redis started."
    fi
}

# ── Celery worker ─────────────────────────────────────────────────────────────
start_worker() {
    log "Starting Celery worker…"
    cd "$SCRIPT_DIR"
    celery -A workers.celery_app worker \
        --loglevel=info \
        --concurrency=1 \
        --logfile="$SCRIPT_DIR/celery.log" \
        --pidfile="$PID_DIR/celery.pid" \
        --detach
    sleep 2
    log "Celery worker started. Logs: $SCRIPT_DIR/celery.log"
}

# ── FastAPI via uvicorn ───────────────────────────────────────────────────────
start_api() {
    log "Starting FastAPI (uvicorn)…"
    cd "$SCRIPT_DIR"
    uvicorn main:app \
        --host 0.0.0.0 \
        --port 8000 \
        --reload \
        --log-level info &
    echo $! > "$PID_DIR/uvicorn.pid"
    log "FastAPI started at http://localhost:8000"
    log "  API docs: http://localhost:8000/docs"
}

# ── Run ───────────────────────────────────────────────────────────────────────
case "$CMD" in
    all)
        start_redis
        start_worker
        start_api
        log "All services running. Press Ctrl-C to stop."
        wait
        ;;
    api)
        start_api
        wait
        ;;
    worker)
        start_redis
        start_worker
        log "Worker running. Press Ctrl-C to stop."
        wait
        ;;
    *)
        die "Unknown command '$CMD'. Use: all | api | worker | stop"
        ;;
esac