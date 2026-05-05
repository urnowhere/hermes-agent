"""Tests for bundled skill devops/kubernetes-readonly/scripts."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest
from pydantic import ValidationError

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "devops" / "kubernetes-readonly" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import k8s_models  # noqa: E402
import k8s_readonly  # noqa: E402


class TestPydanticGuards:
    def test_rejects_shell_in_resource(self):
        with pytest.raises(ValidationError):
            k8s_models.OpGet.model_validate(
                {"op": "get", "resource": "pods;rm -rf /", "namespace": "default"}
            )

    def test_rejects_namespace_with_all_namespaces(self):
        with pytest.raises(ValidationError):
            k8s_models.OpGet.model_validate(
                {
                    "op": "get",
                    "resource": "pods",
                    "namespace": "kube-system",
                    "all_namespaces": True,
                }
            )

    def test_valid_get(self):
        m = k8s_models.OpGet.model_validate(
            {"op": "get", "resource": "deployments.apps", "namespace": "prod", "output": "yaml"}
        )
        assert m.resource == "deployments.apps"


@mock.patch("k8s_readonly.subprocess.run")
@mock.patch("k8s_readonly._kubectl_bin", return_value="/bin/kubectl")
def test_run_request_success(_mock_kubectl_bin, mock_subprocess_run):
    mock_subprocess_run.return_value = mock.Mock(returncode=0, stdout='{"clientVersion":{}}', stderr="")
    req = k8s_models.OpVersion(op="version")
    out = k8s_readonly.run_request(req)
    assert out["ok"] is True
    assert out["argv"][0] == "/bin/kubectl"
    assert out["argv"][1:] == ["version", "-o", "json"]
    mock_subprocess_run.assert_called_once()


@mock.patch("k8s_readonly._kubectl_bin", return_value=None)
def test_run_request_no_kubectl(mock_bin):
    req = k8s_models.OpClusterInfo(op="cluster_info")
    out = k8s_readonly.run_request(req)
    assert out["ok"] is False
    assert out["error"] == "kubectl_not_found"


@mock.patch("k8s_readonly.subprocess.run")
@mock.patch("k8s_readonly._kubectl_bin", return_value="/bin/kubectl")
def test_top_nodes_argv(_mock_kubectl_bin, mock_subprocess_run):
    mock_subprocess_run.return_value = mock.Mock(returncode=0, stdout="NAME CPU\n", stderr="")
    k8s_readonly.run_request(k8s_models.OpTopNodes(op="top_nodes"))
    argv = mock_subprocess_run.call_args[0][0]
    assert argv == ["/bin/kubectl", "top", "nodes"]


@pytest.mark.skipif(not __import__("shutil").which("kubectl"), reason="kubectl not installed")
def test_smoke_version_if_kubectl_present():
    """Optional integration: skipped in CI without kubectl."""
    out = k8s_readonly.run_request(k8s_models.OpVersion(op="version"))
    assert "returncode" in out
    assert "argv" in out
