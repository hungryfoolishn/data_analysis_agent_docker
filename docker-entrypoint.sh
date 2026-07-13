#!/bin/bash
set -e

# Entrypoint runs as root (container default; see Dockerfile). Bind-mounted
# workspace/ and temp_uploads/ come from the host and may be root-owned, which
# the non-root app user (telebi) cannot write to. Fix ownership, then re-exec
# the whole script as telebi so the app never runs as root.
if [ "$(id -u)" = "0" ]; then
    chown -R telebi:telebi /app/workspace /app/temp_uploads 2>/dev/null || true
    exec gosu telebi "$0" "$@"
fi

# --- below runs as telebi (uid 999) ---

# Activate conda env (in case PATH is overridden)
source /opt/conda/etc/profile.d/conda.sh
conda activate smolagents

# Validate required env
if [ -z "$DEEPSEEK_API_KEY" ]; then
    echo "[entrypoint] WARNING: DEEPSEEK_API_KEY is empty. Backend will fail to call DeepSeek." >&2
fi

# Default: start both backend + frontend. Override with START_MODE=backend|frontend|both.
START_MODE="${START_MODE:-both}"

start_backend() {
    echo "[entrypoint] Starting backend (FastAPI) on 0.0.0.0:8888 ..."
    exec_python() { python -m langgraph_langchain.api_server_langgraph; }
    exec_python &
    BACKEND_PID=$!
}

start_frontend() {
    echo "[entrypoint] Starting frontend (Streamlit) on 0.0.0.0:8501 ..."
    streamlit run webui/app.py \
        --server.port=8501 \
        --server.address=0.0.0.0 \
        --server.headless=true \
        --browser.gatherUsageStats=false &
    FRONTEND_PID=$!
}

cleanup() {
    echo "[entrypoint] Shutting down ..."
    [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null || true
    [ -n "$FRONTEND_PID" ] && kill "$FRONTEND_PID" 2>/dev/null || true
    wait 2>/dev/null || true
}
trap cleanup SIGINT SIGTERM

case "$START_MODE" in
    backend)  start_backend ;;
    frontend) start_frontend ;;
    both)     start_backend; start_frontend ;;
    *)
        echo "[entrypoint] Unknown START_MODE=$START_MODE (expected: both|backend|frontend)" >&2
        exit 1
        ;;
esac

# Wait for either child to exit, then cleanup the other
wait -n "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || wait
EXIT_CODE=$?
cleanup
exit $EXIT_CODE
