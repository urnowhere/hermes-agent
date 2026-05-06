#!/bin/bash
# Run Hermes gateway with MiniMax/OpenAI-compatible API streaming patch.
# Patches httpx-based OpenAI client to handle MiniMax's non-standard
# streaming (gzip + chunked Transfer-Encoding that breaks httpx streaming).
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH_FILE="${SCRIPT_DIR}/patch_openai_client.py"
exec python3 -c "
import sys
sys.path.insert(0, '${SCRIPT_DIR}')
exec(open('${PATCH_FILE}').read())
import gateway.run
gateway.run.main()
"
