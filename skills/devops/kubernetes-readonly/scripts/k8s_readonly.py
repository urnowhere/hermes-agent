#!/usr/bin/env python3
"""Read-only kubectl helper: validates JSON requests, runs allowlisted kubectl argv.

Prints a single JSON object to stdout (machine-readable for agents).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from typing import Any

from pydantic import TypeAdapter

from k8s_models import (
    K8sRequest,
    OpApiResources,
    OpClusterInfo,
    OpDescribe,
    OpExplain,
    OpGet,
    OpTopNodes,
    OpTopPods,
    OpVersion,
)

_ADAPTER = TypeAdapter(K8sRequest)
_MAX_CAPTURE = 2_000_000


def _kubectl_bin() -> str | None:
    return shutil.which("kubectl")


def _argv_for(req: K8sRequest) -> list[str]:
    k = "kubectl"
    if isinstance(req, OpVersion):
        return [k, "version", "-o", "json"]
    if isinstance(req, OpClusterInfo):
        return [k, "cluster-info"]
    if isinstance(req, OpApiResources):
        cmd = [k, "api-resources", "-o", "wide"]
        if req.api_group:
            cmd += ["--api-group", req.api_group]
        return cmd
    if isinstance(req, OpExplain):
        cmd = [k, "explain", req.resource]
        if req.recursive:
            cmd.append("--recursive")
        return cmd
    if isinstance(req, OpGet):
        cmd = [k, "get", req.resource]
        if req.name:
            cmd.append(req.name)
        if req.all_namespaces:
            cmd.append("-A")
        elif req.namespace:
            cmd.extend(["-n", req.namespace])
        if req.output != "default":
            cmd.extend(["-o", req.output])
        return cmd
    if isinstance(req, OpDescribe):
        cmd = [k, "describe", req.resource, req.name]
        if req.namespace:
            cmd.extend(["-n", req.namespace])
        return cmd
    if isinstance(req, OpTopPods):
        cmd = [k, "top", "pods"]
        if req.all_namespaces:
            cmd.append("-A")
        elif req.namespace:
            cmd.extend(["-n", req.namespace])
        return cmd
    if isinstance(req, OpTopNodes):
        return [k, "top", "nodes"]
    raise TypeError("unhandled request type")


def run_request(req: K8sRequest) -> dict[str, Any]:
    """Execute validated request; returns JSON-serializable result."""
    kb = _kubectl_bin()
    if not kb:
        return {"ok": False, "error": "kubectl_not_found", "hint": "Install kubectl and ensure it is on PATH."}
    argv = _argv_for(req)
    argv[0] = kb
    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    out, err = proc.stdout or "", proc.stderr or ""
    truncated = False
    if len(out) > _MAX_CAPTURE:
        out = out[:_MAX_CAPTURE]
        truncated = True
    if len(err) > _MAX_CAPTURE:
        err = err[:_MAX_CAPTURE]
        truncated = True
    return {
        "ok": proc.returncode == 0,
        "argv": argv,
        "returncode": proc.returncode,
        "stdout": out,
        "stderr": err,
        "truncated": truncated,
    }


def main() -> None:
    raw = sys.stdin.read()
    if not raw.strip():
        print(json.dumps({"ok": False, "error": "empty_stdin", "hint": "POST JSON request body."}))
        sys.exit(2)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"ok": False, "error": "invalid_json", "detail": str(e)}))
        sys.exit(2)
    try:
        req = _ADAPTER.validate_python(payload)
    except Exception as e:
        print(json.dumps({"ok": False, "error": "validation_failed", "detail": str(e)}))
        sys.exit(2)
    print(json.dumps(run_request(req)))


if __name__ == "__main__":
    main()
