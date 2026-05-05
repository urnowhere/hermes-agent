---
name: kubernetes-readonly
description: Run strictly read-only kubectl inspections (get, describe, explain, api-resources, version, cluster-info, top) with JSON I/O for agents. Use when the user needs cluster visibility without mutating state.
version: 1.0.0
metadata:
  hermes:
    tags: [kubernetes, k8s, kubectl, devops, observability, metrics]
    related_skills: [webhook-subscriptions]
---

# Kubernetes (read-only)

Bundled helper under `scripts/` that accepts **one JSON object on stdin**, validates it with **Pydantic**, then runs an **allowlisted** `kubectl` argv list (never `shell=True`).

## Safety model

- **No** `apply`, `delete`, `create`, `patch`, `exec`, `port-forward`, `run`, `scale`, or other mutating verbs.
- Resource names, namespaces, and API groups are pattern-checked to reduce injection risk.
- Large stdout/stderr is **truncated** at 2 MiB per stream; the JSON result sets `"truncated": true` when this happens.

## Prerequisites

- `kubectl` installed and on `PATH`.
- A valid kubeconfig (same rules as normal kubectl). In remote sandboxes the cluster may be unreachable; the tool still returns structured errors.

## Usage

From the skill directory:

```bash
python scripts/k8s_readonly.py <<'EOF'
{"op": "get", "resource": "pods", "namespace": "kube-system", "output": "json"}
EOF
```

### Supported `op` values

| `op` | Purpose |
|------|---------|
| `version` | `kubectl version -o json` |
| `cluster_info` | `kubectl cluster-info` |
| `api_resources` | `kubectl api-resources -o wide` (optional `api_group`) |
| `explain` | `kubectl explain` (`resource`, optional `recursive`) |
| `get` | `kubectl get` (`resource`, optional `name`, `namespace`, `all_namespaces`, `output`) |
| `describe` | `kubectl describe` (`resource`, `name`, optional `namespace`) |
| `top_pods` | `kubectl top pods` (optional `namespace` or `all_namespaces`) |
| `top_nodes` | `kubectl top nodes` |

`output` for `get` must be one of: `json`, `yaml`, `wide`, `name`, `default`.

## Response shape

```json
{
  "ok": true,
  "argv": ["/usr/bin/kubectl", "get", "pods", "-n", "default", "-o", "json"],
  "returncode": 0,
  "stdout": "...",
  "stderr": "",
  "truncated": false
}
```

On missing kubectl:

```json
{"ok": false, "error": "kubectl_not_found", "hint": "Install kubectl and ensure it is on PATH."}
```

## When to load this skill

- Inspect workloads, services, events, or metrics in a cluster the user already trusts you to read.
- Pair with incident analysis or infra questions where **read-only** inspection is enough.

## When **not** to use it

- Any operation that changes cluster state (use human-approved workflows outside this skill).
- Untrusted multi-tenant clusters where even read access is sensitive: confirm with the user first.
