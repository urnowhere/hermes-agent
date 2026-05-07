# Vesta Chorus deep integration runbook

This records the Phase 1-4 activation path for Vesta von der Proto.

## Phase 1 — ambient Chorus memory

- Hermes memory provider: `plugins/memory/chorus`
- Shared JSON-RPC client: `plugins/chorus_common.py`
- Vesta profile uses `memory.provider: chorus`.
- Provider exposes compact tools:
  - `chorus_resume_context`
  - `chorus_memory_query`
  - `chorus_memory_store`
  - `chorus_emit_signal`
- Compression now threads `MemoryProvider.on_pre_compress()` return text into compression focus so provider preservation guidance reaches the summarizer.

## Phase 2 — lifecycle and gateway hooks

- Standalone plugin: `plugins/vesta-chorus`
- Registered hooks:
  - `on_session_start`
  - `on_session_end`
  - `on_session_finalize`
  - `pre_gateway_dispatch`
  - `pre_llm_call`
  - `post_tool_call`
- Gateway hook redacts and avoids storing credential-like inbound text.
- Risky tool audit redacts secret-like args before Chorus memory writes.

## Phase 3 — Vesta tools

Vesta plugin toolset: `vesta_chorus`.

Tools:
- `vesta_wake_briefing`
- `vesta_closeout`
- `vesta_worker_audit`
- `vesta_gate_check`
- `vesta_workstream_sweep`

Governance note: scoped `launch.agent_worker` is audit-only by Inu's explicit Vesta doctrine. Spend, production deploy, customer/legal/public, secret rotation, DNS change, open-source release, destructive memory/workstream operations remain approval-gated unless an active Circle authorizes them.

## Phase 4 — always-on posture

Vesta profile activation:
- `toolsets` includes `vesta_chorus`.
- Chorus MCP tree mode is `runtime`.
- Webhook platform enabled on local port `8644`.
- Dynamic Hermes webhook routes:
  - `chorus-alerts`
  - `chorus-briefings`
- Supervised WSL runner is systemd user service `vesta-gateway.service`.
- Reverse SSH tunnel from devops-hub to local Vesta gateway is systemd user service `vesta-webhook-tunnel.service`.
- devops-hub exposes the reverse tunnel to the Chorus Docker network through system service `vesta-webhook-socat.service` on host port `18645`.
- devops-hub firewall allows Docker-network sources (`10.0.0.0/8`) to reach port `18645`; the rule is persisted through `netfilter-persistent`.

Commands:

```bash
# Local WSL supervision
systemctl --user status vesta-gateway.service vesta-webhook-tunnel.service
systemctl --user restart vesta-gateway.service vesta-webhook-tunnel.service
curl -fsS http://127.0.0.1:8644/health

# devops-hub relay supervision
ssh root@91.99.210.40 'systemctl status vesta-webhook-socat.service --no-pager'
ssh root@91.99.210.40 'docker exec chorus-protocol node -e '\''fetch("http://10.0.3.1:18645/health").then(async r=>console.log(r.status, await r.text()))'\'''
```

Cron jobs created:
- `vesta-chorus-watch-briefing` — daily 08:15
- `vesta-stale-workstream-sweep` — daily 08:30
- `vesta-approval-webhook-health-sweep` — daily 08:45

Chorus webhook registry is now wired to Vesta's Hermes route:
- Live webhook id: `webhook:5fyyo26aqy3bju9oy6oq`.
- Registry URL: `http://10.0.3.1:18645/webhooks/chorus-alerts` (reachable from the `chorus-protocol` container through devops-hub host gateway).
- Filters: rings `agents-of-proto`, `ops`, `vesta-von-der-proto`; all signal types; `min_urgency: 0.6`.
- The Chorus-generated signing secret is installed only into Vesta's local `webhook_subscriptions.json`; it is not printed or stored in docs.
- Hermes webhook adapter now accepts `X-Chorus-Signature` and `X-Chorus-Event` on branch `feat/vesta-chorus-deep-integration` / PR #15245.
- Live schema drift found: `webhook.created_by` expected `string` while the adapter writes `identity:<id>` record ids. Live Surreal schema hotfixed to `record<identity>`; upstream PR #180 codifies the fix.
- Verification signals delivered with HTTP 202:
  - `signal:4odx0olkrli9o93dfq58` → `webhook_delivery:yukm82g7eyn4m5d5yjyr`.
  - `signal:w2udya80yfyeo5niu0rb` → `webhook_delivery:je5mqjpkz1zweyllu15b`.

Supervision:
- WSL local: `vesta-gateway.service`, `vesta-webhook-tunnel.service`.
- devops-hub: `vesta-webhook-socat.service` plus persisted iptables rule allowing Docker-network sources to port `18645`.


## Verification

Focused gates:

```bash
/home/inu/.hermes/hermes-agent/venv/bin/python -m pytest \
  tests/plugins/memory/test_chorus_provider.py \
  tests/plugins/test_vesta_chorus_plugin.py \
  tests/test_chorus_pre_compress_focus.py \
  -q -o 'addopts='

/home/inu/.hermes/hermes-agent/venv/bin/python -m py_compile \
  plugins/chorus_common.py \
  plugins/memory/chorus/__init__.py \
  plugins/vesta-chorus/__init__.py \
  run_agent.py
```

Observed focused result: `11 passed`.

Broader `tests/plugins` currently has one unrelated existing failure in Hindsight post-setup key preservation; Vesta/Chorus focused tests pass.
