#!/usr/bin/env bash
#
# Detect a wedged Ollama and restart it.
#
# `Restart=always` only covers a process that dies. Ollama can stay alive while
# no longer serving generations: during one incident here it accepted and
# answered GET /api/ps normally for eleven minutes while POST /api/chat never
# reached its handler at all. So probing a cheap endpoint would have reported
# healthy throughout -- the probe has to exercise generation.
#
# Guards against restarting a merely busy server:
#   * a generous per-probe timeout, so queueing behind the parallel slots under
#     real load does not read as a failure
#   * consecutive failures required, not a single one
#   * a minimum interval between restarts, so a crash-loop cannot form
#
set -uo pipefail

OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"
MODEL="${OLLAMA_HEALTH_MODEL:-gemma4:e4b}"
PROBE_TIMEOUT="${OLLAMA_HEALTH_TIMEOUT:-90}"
FAILURES_BEFORE_RESTART="${OLLAMA_HEALTH_FAILURES:-3}"
MIN_RESTART_INTERVAL="${OLLAMA_HEALTH_MIN_INTERVAL:-900}"   # 15 minutes

STATE_DIR="${OLLAMA_HEALTH_STATE_DIR:-/var/lib/ollama-healthcheck}"
FAIL_FILE="$STATE_DIR/consecutive_failures"
LAST_RESTART_FILE="$STATE_DIR/last_restart"

mkdir -p "$STATE_DIR" 2>/dev/null || true

log() { echo "[ollama-health] $*"; }

# A one-token generation. Cheap, but it goes through the scheduler, the slot
# allocation, and the runner -- the parts that actually wedge.
probe() {
    local code
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time "$PROBE_TIMEOUT" \
        "$OLLAMA_URL/api/chat" \
        -H 'Content-Type: application/json' \
        -d "{\"model\":\"$MODEL\",
             \"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],
             \"stream\":false,\"think\":false,
             \"options\":{\"num_predict\":1},\"keep_alive\":\"45m\"}" 2>/dev/null)
    [[ "$code" == "200" ]]
}

read_count() { cat "$FAIL_FILE" 2>/dev/null || echo 0; }

if probe; then
    if [[ "$(read_count)" != "0" ]]; then
        log "recovered after $(read_count) consecutive failure(s)"
    fi
    echo 0 > "$FAIL_FILE"
    exit 0
fi

failures=$(( $(read_count) + 1 ))
echo "$failures" > "$FAIL_FILE"
log "generation probe failed ($failures/$FAILURES_BEFORE_RESTART)"

if (( failures < FAILURES_BEFORE_RESTART )); then
    exit 0
fi

now=$(date +%s)
last=$(cat "$LAST_RESTART_FILE" 2>/dev/null || echo 0)
if (( now - last < MIN_RESTART_INTERVAL )); then
    log "restarted $(( now - last ))s ago; holding off to avoid a restart loop"
    exit 0
fi

# Record the established connection count first: a pile of half-open client
# connections is what preceded the incident, and it is worth having in the
# journal when working out whether this recurs.
conns=$(ss -tn state established "( sport = :11434 )" 2>/dev/null | wc -l)
log "restarting ollama after $failures failed probes (established connections: $conns)"
echo "$now" > "$LAST_RESTART_FILE"
systemctl restart ollama
echo 0 > "$FAIL_FILE"
