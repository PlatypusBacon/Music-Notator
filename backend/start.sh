#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# start.sh — Launch all ScoreScribe backend services
#
# Usage:
#   ./start.sh            starts Redis + Celery worker + FastAPI
#   ./start.sh api        FastAPI only
#   ./start.sh worker     Celery worker only
#   ./start.sh stop       kill everything started by this script
#   ./start.sh status     show what's running
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_DIR="$SCRIPT_DIR/.pids"
PORT=8000
mkdir -p "$PID_DIR"

log()  { echo -e "\033[1;32m[ScoreScribe]\033[0m $*"; }
warn() { echo -e "\033[1;33m[ScoreScribe]\033[0m $*"; }
die()  { echo -e "\033[1;31m[ScoreScribe]\033[0m $*" >&2; exit 1; }

require() { command -v "$1" &>/dev/null || die "'$1' not found. Install it first."; }

# ── Stop all managed processes ────────────────────────────────────────────────

stop_all() {
    log "Stopping services..."
    for pidfile in "$PID_DIR"/*.pid; do
        [ -f "$pidfile" ] || continue
        pid=$(cat "$pidfile")
        name=$(basename "$pidfile" .pid)
        if kill -0 "$pid" 2>/dev/null; then
            log "  Stopping $name (PID $pid)"
            kill "$pid" 2>/dev/null || true
            sleep 1
            kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
        fi
        rm -f "$pidfile"
    done
    log "Done."
}

# ── Kill whatever is currently on PORT ───────────────────────────────────────

free_port() {
    local pid
    pid=$(lsof -ti :"$PORT" 2>/dev/null || true)
    if [ -n "$pid" ]; then
        warn "Port $PORT in use by PID $pid — killing it"
        kill -9 "$pid" 2>/dev/null || true
        sleep 1
        log "Port $PORT freed."
    fi
}

# ── Status ────────────────────────────────────────────────────────────────────

show_status() {
    log "Service status:"
    redis-cli ping &>/dev/null \
        && log "  Redis    : running" \
        || warn "  Redis    : not responding"

    local cpid="$PID_DIR/celery.pid"
    if [ -f "$cpid" ] && kill -0 "$(cat "$cpid")" 2>/dev/null; then
        log "  Celery   : running (PID $(cat "$cpid"))"
    else
        warn "  Celery   : not running"
    fi

    local upid="$PID_DIR/uvicorn.pid"
    if [ -f "$upid" ] && kill -0 "$(cat "$upid")" 2>/dev/null; then
        log "  FastAPI  : running (PID $(cat "$upid"))  http://localhost:$PORT"
    else
        warn "  FastAPI  : not running"
    fi
}

# ── Activate virtualenv ───────────────────────────────────────────────────────

if [[ -f "$SCRIPT_DIR/.venv/bin/activate" ]]; then
    source "$SCRIPT_DIR/.venv/bin/activate"
    log "Activated virtualenv: $SCRIPT_DIR/.venv"
fi

# ── Redis ─────────────────────────────────────────────────────────────────────

start_redis() {
    if redis-cli ping &>/dev/null; then
        log "Redis: already running"
    else
        require redis-server
        log "Starting Redis..."
        redis-server --daemonize yes \
            --logfile "$SCRIPT_DIR/redis.log" \
            --loglevel notice
        sleep 1
        redis-cli ping &>/dev/null || die "Redis failed to start — check redis.log"
        log "Redis: started"
    fi
}

# ── Celery worker ─────────────────────────────────────────────────────────────

start_worker() {
    local cpid="$PID_DIR/celery.pid"
    if [ -f "$cpid" ] && kill -0 "$(cat "$cpid")" 2>/dev/null; then
        warn "Celery: already running (PID $(cat "$cpid")) — skipping"
        return
    fi
    log "Starting Celery worker..."
    cd "$SCRIPT_DIR"
    celery -A workers.celery_app worker \
        --loglevel=debug \
        --concurrency=1 \
        --logfile="$SCRIPT_DIR/celery.log" \
        --pidfile="$cpid" \
        --detach
    sleep 2
    if [ -f "$cpid" ] && kill -0 "$(cat "$cpid")" 2>/dev/null; then
        log "Celery: started (PID $(cat "$cpid"))  logs -> celery.log"
    else
        die "Celery worker failed to start — check celery.log"
    fi
}

# ── FastAPI ───────────────────────────────────────────────────────────────────

start_api() {
    free_port
    log "Starting FastAPI on port $PORT..."
    cd "$SCRIPT_DIR"
    uvicorn main:app \
        --host 0.0.0.0 \
        --port "$PORT" \
        --reload \
        --log-level debug &
    local pid=$!
    echo "$pid" > "$PID_DIR/uvicorn.pid"
    sleep 1
    if kill -0 "$pid" 2>/dev/null; then
        log "FastAPI: started (PID $pid)"
        log "  http://localhost:$PORT"
        log "  http://localhost:$PORT/docs  (Swagger UI)"
    else
        die "FastAPI failed to start — check output above"
    fi
}

# ── Entrypoint ────────────────────────────────────────────────────────────────

CMD="${1:-all}"

case "$CMD" in
    all)
        require redis-cli
        start_redis
        start_worker
        start_api
        echo ""
        show_status
        echo ""
        log "Ctrl-C stops uvicorn. To stop everything: ./start.sh stop"
        log ""
        log "Tail logs in separate terminals:"
        log "  Celery pipeline : tail -f celery_pipeline.log"
        log "  Celery worker   : tail -f celery.log"
        log "  API             : tail -f scorescribe.log"
        wait
        ;;
    api)
        start_api
        wait
        ;;
    worker)
        start_redis
        start_worker
        log "Worker running. tail -f celery.log"
        ;;
    stop)
        stop_all
        ;;
    status)
        show_status
        ;;
    *)
        die "Unknown command '$CMD'. Usage: all | api | worker | stop | status"
        ;;
esac