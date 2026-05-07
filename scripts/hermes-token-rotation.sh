#!/usr/bin/env bash
# hermes-token-rotation.sh — Proactive token rotation daemon for Hermes Agent
#
# Checks the current Hermes Copilot auth token's quota status via the Copilot
# Account Manager every INTERVAL seconds. If the token is exhausted, critical
# (<=5%), or invalid, swaps it with the best available token from the manager.
#
# Usage:
#   hermes-token-rotation.sh              # Run as daemon (loop forever)
#   hermes-token-rotation.sh --once       # Run a single check and exit
#   hermes-token-rotation.sh --status     # Show current token status
#
# Requires: curl, python3

set -euo pipefail

# --- Configuration ---
INTERVAL="${HERMES_ROTATION_INTERVAL:-300}"  # seconds between checks (default: 5 min)
ENV_FILE="/root/.hermes/.env"
MANAGER_URL="http://localhost:5111"
MANAGER_USER="759641"
MANAGER_PASS="Kapuma@23"
TOOL_NAME="Hermes-Agent"
LOG_FILE="/var/log/hermes-token-rotation.log"
LOCK_FILE="/var/run/hermes-token-rotation.lock"

# Telegram notifications
TG_SECRETS="/root/.hermes/.env"

# --- Helpers ---

log() {
    echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') $1" | tee -a "$LOG_FILE"
}

notify_telegram() {
    local msg="$1"
    # Read Telegram bot token and chat ID from Hermes .env
    local TG_BOT_TOKEN=""
    local TG_CHAT_ID=""
    if [[ -f "$TG_SECRETS" ]]; then
        TG_BOT_TOKEN=$(grep '^TELEGRAM_BOT_TOKEN=' "$TG_SECRETS" | head -1 | cut -d= -f2- | tr -d '"' || true)
        TG_CHAT_ID=$(grep '^TELEGRAM_HOME_CHANNEL=' "$TG_SECRETS" | head -1 | cut -d= -f2- | tr -d '"' || true)
    fi
    if [[ -n "$TG_BOT_TOKEN" && -n "$TG_CHAT_ID" ]]; then
        curl -s -x "http://127.0.0.1:18080" \
            -X POST "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
            -d "chat_id=${TG_CHAT_ID}" \
            -d "text=${msg}" \
            -d "parse_mode=Markdown" \
            --max-time 10 >/dev/null 2>&1 || true
    fi
}

# Call manager API with Basic Auth
manager_api() {
    local method="$1" endpoint="$2"
    shift 2
    curl -s -u "${MANAGER_USER}:${MANAGER_PASS}" \
        -X "$method" \
        "${MANAGER_URL}${endpoint}" \
        --max-time 15 \
        "$@"
}

# Parse JSON fields in one python3 call
parse_json() {
    local json_str="$1"
    shift
    local fields="$*"
    python3 -c "
import json, sys
try:
    data = json.loads(sys.stdin.read())
except Exception:
    for _ in '$fields'.split():
        print('')
    sys.exit(0)
for f in '$fields'.split():
    parts = f.split('.')
    val = data
    for p in parts:
        if isinstance(val, dict) and p in val:
            val = val[p]
        else:
            val = ''
            break
    print(val if val is not None else '')
" <<< "$json_str"
}

# Get current token from .env
get_current_token() {
    if [[ -f "$ENV_FILE" ]]; then
        grep '^COPILOT_GITHUB_TOKEN=' "$ENV_FILE" | head -1 | cut -d= -f2- || true
    fi
}

# Update token in .env
update_token() {
    local new_token="$1"
    if [[ -f "$ENV_FILE" ]]; then
        if grep -q '^COPILOT_GITHUB_TOKEN=' "$ENV_FILE"; then
            sed -i "s|^COPILOT_GITHUB_TOKEN=.*|COPILOT_GITHUB_TOKEN=${new_token}|" "$ENV_FILE"
        else
            echo "COPILOT_GITHUB_TOKEN=${new_token}" >> "$ENV_FILE"
        fi
    fi
}

# Signal gateway to reload (USR1 causes credential re-resolution)
reload_gateway() {
    local gw_pid
    gw_pid=$(cat /root/.hermes/gateway.pid 2>/dev/null || true)
    if [[ -n "$gw_pid" ]] && kill -0 "$gw_pid" 2>/dev/null; then
        kill -USR1 "$gw_pid" 2>/dev/null || true
        log "Sent USR1 to gateway PID $gw_pid"
    else
        # Fallback: find gateway process
        gw_pid=$(pgrep -f "hermes.*gateway.*run" | head -1 || true)
        if [[ -n "$gw_pid" ]]; then
            kill -USR1 "$gw_pid" 2>/dev/null || true
            log "Sent USR1 to gateway PID $gw_pid"
        fi
    fi
}

# --- Main Logic ---

do_rotation_check() {
    local current_token
    current_token=$(get_current_token)

    if [[ -z "$current_token" ]]; then
        log "WARNING: No COPILOT_GITHUB_TOKEN found in $ENV_FILE"
        notify_telegram "⚠️ *Hermes Token Rotation*\n\nNo Copilot token configured in .env file"
        return 1
    fi

    # Ask manager for best token
    local response
    response=$(manager_api GET "/api/best-token?tool=${TOOL_NAME}&reason=rotation-check") || {
        log "ERROR: Copilot Manager unreachable"
        notify_telegram "⚠️ *Hermes Token Rotation*\n\nCopilot Manager unreachable — cannot check token status"
        return 1
    }

    local ghu_token github_username premium_pct rotated should_rotate
    ghu_token=$(parse_json "$response" ghu_token)
    github_username=$(parse_json "$response" github_username)
    premium_pct=$(parse_json "$response" premium_percent_remaining)
    rotated=$(parse_json "$response" rotated)

    if [[ -z "$ghu_token" ]]; then
        log "ERROR: No valid token available from manager"
        notify_telegram "❌ *Hermes Token Rotation*\n\nNo valid Copilot token available"
        return 1
    fi

    # Check if token differs from current
    local current_prefix="${current_token:0:10}"
    local new_prefix="${ghu_token:0:10}"

    if [[ "$current_prefix" != "$new_prefix" ]]; then
        # Token changed — rotation happened
        update_token "$ghu_token"
        log "Token rotated: → @${github_username} (${premium_pct}% remaining)"
        notify_telegram "🔄 *Hermes Token Rotation*\n\nSwitched to: @${github_username}\nPremium remaining: ${premium_pct}%"
        reload_gateway
    else
        log "Token OK: @${github_username} (${premium_pct}% remaining) — no rotation needed"
    fi

    return 0
}

do_status() {
    local current_token
    current_token=$(get_current_token)
    if [[ -z "$current_token" ]]; then
        echo "No COPILOT_GITHUB_TOKEN configured"
        return 1
    fi

    local masked="${current_token:0:8}...${current_token: -4}"
    echo "Current token: $masked"

    local response
    response=$(manager_api GET "/api/best-token?tool=${TOOL_NAME}") || {
        echo "Copilot Manager unreachable"
        return 1
    }

    local github_username premium_pct
    github_username=$(parse_json "$response" github_username)
    premium_pct=$(parse_json "$response" premium_percent_remaining)

    echo "Best available: @${github_username} (${premium_pct}% remaining)"
}

# --- Lock file management ---

acquire_lock() {
    if [[ -f "$LOCK_FILE" ]]; then
        local lock_pid
        lock_pid=$(cat "$LOCK_FILE" 2>/dev/null || true)
        if [[ -n "$lock_pid" ]] && kill -0 "$lock_pid" 2>/dev/null; then
            log "Another instance running (PID $lock_pid), skipping"
            return 1
        fi
    fi
    echo $$ > "$LOCK_FILE"
    return 0
}

release_lock() {
    rm -f "$LOCK_FILE"
}

trap release_lock EXIT

# --- Entry Point ---

case "${1:-}" in
    --once)
        acquire_lock || exit 0
        do_rotation_check
        ;;
    --status)
        do_status
        ;;
    --help|-h)
        echo "Usage: $0 [--once|--status|--help]"
        echo "  --once    Run single rotation check and exit"
        echo "  --status  Show current token status"
        echo "  --help    Show this help"
        echo ""
        echo "  Without flags: run as daemon (loop every ${INTERVAL}s)"
        ;;
    "")
        # Daemon mode
        log "Starting Hermes token rotation daemon (interval: ${INTERVAL}s)"
        while true; do
            if acquire_lock; then
                do_rotation_check || true
                release_lock
            fi
            sleep "$INTERVAL"
        done
        ;;
    *)
        echo "Unknown option: $1"
        echo "Use --help for usage information"
        exit 1
        ;;
esac
