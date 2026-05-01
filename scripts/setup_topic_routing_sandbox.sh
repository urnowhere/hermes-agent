#!/usr/bin/env bash
# Create a disposable Hermes topic-routing sandbox.
#
# This script never writes into ~/.hermes. It prepares a gateway home plus two
# profile homes with non-sensitive marker data so forum-topic routing can be
# tested against isolated SOUL/config/memory/session trees.

set -euo pipefail

GATEWAY_HOME="${HERMES_TOPIC_ROUTING_GATEWAY_HOME:-/tmp/hermes-topic-routing-gateway}"
PROFILES_ROOT="${HERMES_TOPIC_ROUTING_PROFILES_ROOT:-/tmp/hermes-topic-routing-profiles}"
CYBREL_HOME="$PROFILES_ROOT/cybrel-test"
VAULT_HOME="$PROFILES_ROOT/vault-test"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

case "$GATEWAY_HOME:$PROFILES_ROOT" in
  *"$HOME/.hermes"*|*"~/.hermes"*)
    echo "Refusing to use ~/.hermes for a sandbox." >&2
    exit 1
    ;;
esac

for path in "$GATEWAY_HOME" "$CYBREL_HOME" "$VAULT_HOME"; do
  if [ -L "$path" ]; then
    echo "Refusing symlink sandbox path: $path" >&2
    exit 1
  fi
done

if [ "${1:-}" = "--clean" ]; then
  if command -v trash >/dev/null 2>&1; then
    [ ! -e "$GATEWAY_HOME" ] || trash "$GATEWAY_HOME"
    [ ! -e "$PROFILES_ROOT" ] || trash "$PROFILES_ROOT"
  else
    echo "Refusing to delete without the 'trash' command installed." >&2
    exit 1
  fi
fi

mkdir -p \
  "$GATEWAY_HOME/sessions" \
  "$CYBREL_HOME/memories" "$CYBREL_HOME/sessions" "$CYBREL_HOME/logs" \
  "$VAULT_HOME/memories" "$VAULT_HOME/sessions" "$VAULT_HOME/logs"

cat > "$GATEWAY_HOME/config.yaml" <<EOF
model:
  provider: openai-compatible
  default: sandbox-fake-model
  base_url: http://127.0.0.1:18099/v1
telegram:
  enabled: true
  topic_profiles_safe_root: "$PROFILES_ROOT"
  topic_profiles:
    - match:
        chat_id: "-1000000000000"
        thread_id: 101
      profile: cybrel-test
      profile_home: "$CYBREL_HOME"
    - match:
        chat_id: "-1000000000000"
        thread_id: 202
      profile: vault-test
      profile_home: "$VAULT_HOME"
EOF

cat > "$GATEWAY_HOME/.env" <<'EOF'
# Sandbox only. Do not put production tokens here.
OPENAI_API_KEY=test-key-only
TELEGRAM_BOT_TOKEN=
EOF

cat > "$CYBREL_HOME/config.yaml" <<'EOF'
model:
  provider: openai-compatible
  default: sandbox-cybrel-model
  base_url: http://127.0.0.1:18099/v1
EOF
cat > "$CYBREL_HOME/.env" <<'EOF'
OPENAI_API_KEY=test-key-only
EOF
cat > "$CYBREL_HOME/SOUL.md" <<'EOF'
# Cybrel Test Soul

SANDBOX_PROFILE_MARKER=cybrel-test
EOF
cat > "$CYBREL_HOME/memories/MEMORY.md" <<'EOF'
SANDBOX_MEMORY_MARKER=cybrel-test
EOF

cat > "$VAULT_HOME/config.yaml" <<'EOF'
model:
  provider: openai-compatible
  default: sandbox-vault-model
  base_url: http://127.0.0.1:18099/v1
EOF
cat > "$VAULT_HOME/.env" <<'EOF'
OPENAI_API_KEY=test-key-only
EOF
cat > "$VAULT_HOME/SOUL.md" <<'EOF'
# Vault Test Soul

SANDBOX_PROFILE_MARKER=vault-test
EOF
cat > "$VAULT_HOME/memories/MEMORY.md" <<'EOF'
SANDBOX_MEMORY_MARKER=vault-test
EOF

cat <<EOF
Sandbox ready:
  Gateway home: $GATEWAY_HOME
  Cybrel profile: $CYBREL_HOME
  Vault profile: $VAULT_HOME

Offline tests:
  HERMES_HOME=$GATEWAY_HOME PYTHONPATH=$REPO_ROOT scripts/run_tests.sh tests/gateway/test_topic_profile_routing.py

Manual gateway sandbox:
  HERMES_HOME=$GATEWAY_HOME PYTHONPATH=$REPO_ROOT python -m hermes_cli.main gateway run
EOF
