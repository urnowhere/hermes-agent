"""Kubernetes execution environment for sandboxed command execution via kubectl."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from tools.environments.base import BaseEnvironment, _popen_bash
from tools.environments.local import _HERMES_PROVIDER_ENV_BLOCKLIST

logger = logging.getLogger(__name__)

_DNS_LABEL_RE = re.compile(r"[^a-z0-9-]+")


def _normalize_dns_label(value: str, *, max_len: int = 63) -> str:
    v = (value or "").lower().strip()
    v = _DNS_LABEL_RE.sub("-", v)
    v = v.strip("-")
    if not v:
        v = "hermes"
    if len(v) > max_len:
        v = v[:max_len].rstrip("-")
    return v or "hermes"


def _cpu_quantity(cpu: float | int) -> str:
    try:
        cpu_f = float(cpu)
    except Exception:
        return ""
    if cpu_f <= 0:
        return ""
    if cpu_f.is_integer():
        return str(int(cpu_f))
    return str(cpu_f)


def _mi_quantity(mb: int | float) -> str:
    try:
        mb_i = int(float(mb))
    except Exception:
        return ""
    if mb_i <= 0:
        return ""
    return f"{mb_i}Mi"


def _normalize_forward_env_names(names: list[str] | None) -> list[str]:
    result: list[str] = []
    for item in names or []:
        if not isinstance(item, str):
            continue
        name = item.strip()
        if name and name not in result:
            result.append(name)
    return result


def _build_pod_env(forward_env: list[str]) -> list[dict]:
    exec_env: dict[str, str] = {}

    passthrough: set[str] = set()
    try:
        from tools.env_passthrough import get_all_passthrough

        passthrough = set(get_all_passthrough())
    except Exception:
        pass

    forward_keys = set(forward_env) | (passthrough - _HERMES_PROVIDER_ENV_BLOCKLIST)

    hermes_env: dict[str, str] = {}
    try:
        from hermes_constants import get_hermes_home

        env_path = Path(get_hermes_home()) / ".env"
        if env_path.is_file():
            for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                if k:
                    hermes_env[k] = v.strip()
    except Exception:
        pass

    for key in sorted(forward_keys):
        value = os.getenv(key)
        if value is None:
            value = hermes_env.get(key)
        if value is not None:
            exec_env[key] = value

    return [{"name": k, "value": exec_env[k]} for k in sorted(exec_env)]


class KubernetesEnvironment(BaseEnvironment):
    def __init__(
        self,
        *,
        image: str,
        cwd: str,
        timeout: int,
        task_id: str = "default",
        namespace: str = "default",
        context: str | None = None,
        kubeconfig: str | None = None,
        forward_env: list[str] | None = None,
        cpu: float | int = 1,
        memory: int = 5120,
        disk: int = 51200,
        service_account: str | None = None,
        pod_prefix: str = "hermes",
        image_pull_policy: str = "IfNotPresent",
    ):
        if cwd == "~":
            cwd = "/root"
        super().__init__(cwd=cwd, timeout=timeout)

        self._task_id = task_id
        self._namespace = namespace or "default"
        self._context = context or ""
        self._kubeconfig = kubeconfig or ""
        self._service_account = service_account or ""
        self._image_pull_policy = image_pull_policy or "IfNotPresent"
        self._container_name = "hermes"
        self._kubectl = shutil.which("kubectl") or "kubectl"
        self._forward_env = _normalize_forward_env_names(forward_env)

        self._pod_name = _normalize_dns_label(
            f"{pod_prefix}-{task_id[:16]}-{uuid.uuid4().hex[:6]}",
            max_len=63,
        )

        self._create_pod(
            image=image,
            cpu=cpu,
            memory=memory,
            disk=disk,
        )
        self._wait_ready()
        self._seed_files()
        self.init_session()

    def _kubectl_base(self) -> list[str]:
        cmd = [self._kubectl]
        if self._context:
            cmd.extend(["--context", self._context])
        if self._kubeconfig:
            cmd.extend(["--kubeconfig", self._kubeconfig])
        if self._namespace:
            cmd.extend(["-n", self._namespace])
        return cmd

    def _create_pod(self, *, image: str, cpu: float | int, memory: int, disk: int) -> None:
        resources: dict = {}
        req: dict[str, str] = {}
        lim: dict[str, str] = {}

        cpu_q = _cpu_quantity(cpu)
        mem_q = _mi_quantity(memory)
        disk_q = _mi_quantity(disk)

        if cpu_q:
            req["cpu"] = cpu_q
            lim["cpu"] = cpu_q
        if mem_q:
            req["memory"] = mem_q
            lim["memory"] = mem_q
        if disk_q:
            req["ephemeral-storage"] = disk_q
            lim["ephemeral-storage"] = disk_q

        if req or lim:
            resources = {"requests": req, "limits": lim}

        pod_env = _build_pod_env(self._forward_env)

        container: dict = {
            "name": self._container_name,
            "image": image,
            "imagePullPolicy": self._image_pull_policy,
            "command": ["bash", "-lc", "sleep infinity"],
            "workingDir": self.cwd,
            "securityContext": {
                "allowPrivilegeEscalation": False,
                "capabilities": {"drop": ["ALL"]},
            },
        }
        if pod_env:
            container["env"] = pod_env
        if resources:
            container["resources"] = resources

        spec: dict = {
            "restartPolicy": "Never",
            "containers": [container],
        }
        if self._service_account:
            spec["serviceAccountName"] = self._service_account

        manifest = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": self._pod_name,
                "labels": {
                    "app": "hermes-agent",
                    "hermes-task": _normalize_dns_label(self._task_id, max_len=63),
                },
            },
            "spec": spec,
        }

        cmd = [*self._kubectl_base(), "apply", "-f", "-"]
        result = subprocess.run(
            cmd,
            input=json.dumps(manifest),
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            msg = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"kubectl apply failed: {msg}")

    def _wait_ready(self) -> None:
        cmd = [
            *self._kubectl_base(),
            "wait",
            "--for=condition=Ready",
            f"pod/{self._pod_name}",
            "--timeout=180s",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=200)
        if result.returncode == 0:
            return
        desc = subprocess.run(
            [*self._kubectl_base(), "describe", f"pod/{self._pod_name}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        msg = (result.stderr or result.stdout or "").strip()
        details = (desc.stdout or desc.stderr or "").strip()
        raise RuntimeError(f"Kubernetes pod not ready: {msg}\n{details}")

    def _seed_files(self) -> None:
        try:
            from tools.credential_files import get_credential_file_mounts, get_skills_directory_mount
        except Exception:
            return

        mounts: list[dict[str, str]] = []
        try:
            mounts.extend(get_credential_file_mounts())
        except Exception:
            pass

        try:
            mounts.extend(get_skills_directory_mount())
        except Exception:
            pass

        for entry in mounts:
            host_path = entry.get("host_path") or ""
            container_path = entry.get("container_path") or ""
            if not host_path or not container_path:
                continue
            hp = Path(host_path)
            if not hp.exists():
                continue

            if hp.is_dir():
                if Path(container_path).name != hp.name:
                    continue
                dest_parent = str(Path(container_path).parent)
                mkdir_cmd = [
                    *self._kubectl_base(),
                    "exec",
                    self._pod_name,
                    "-c",
                    self._container_name,
                    "--",
                    "sh",
                    "-c",
                    f"mkdir -p {json.dumps(dest_parent)}",
                ]
                subprocess.run(mkdir_cmd, capture_output=True, text=True, timeout=20)
                cp_cmd = [*self._kubectl_base(), "cp", str(hp), f"{self._pod_name}:{dest_parent}"]
                subprocess.run(cp_cmd, capture_output=True, text=True, timeout=180)
                continue

            dest_parent = str(Path(container_path).parent)
            mkdir_cmd = [
                *self._kubectl_base(),
                "exec",
                self._pod_name,
                "-c",
                self._container_name,
                "--",
                "sh",
                "-c",
                f"mkdir -p {json.dumps(dest_parent)}",
            ]
            subprocess.run(mkdir_cmd, capture_output=True, text=True, timeout=20)
            cp_cmd = [*self._kubectl_base(), "cp", str(hp), f"{self._pod_name}:{container_path}"]
            subprocess.run(cp_cmd, capture_output=True, text=True, timeout=120)

    def _run_bash(
        self,
        cmd_string: str,
        *,
        login: bool = False,
        timeout: int = 120,
        stdin_data: str | None = None,
    ) -> subprocess.Popen:
        cmd = [
            *self._kubectl_base(),
            "exec",
            self._pod_name,
            "-c",
            self._container_name,
        ]
        if stdin_data is not None:
            cmd.append("-i")
        cmd.append("--")
        if login:
            cmd.extend(["bash", "-l", "-c", cmd_string])
        else:
            cmd.extend(["bash", "-c", cmd_string])
        return _popen_bash(cmd, stdin_data)

    def cleanup(self):
        cmd = [*self._kubectl_base(), "delete", "pod", self._pod_name, "--ignore-not-found=true", "--wait=false"]
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
