from pathlib import Path
import tomllib

from packaging.requirements import Requirement
from packaging.version import Version


REPO_ROOT = Path(__file__).resolve().parents[1]


def _find_requirement(requirements: list[str], name: str) -> Requirement:
    for requirement in requirements:
        parsed = Requirement(requirement)
        if parsed.name.lower() == name.lower():
            return parsed
    raise AssertionError(f"Missing requirement for {name}")


def _minimum_version(requirement: Requirement) -> Version:
    mins = [
        Version(spec.version)
        for spec in requirement.specifier
        if spec.operator in {">=", "==", "==="}
    ]
    if not mins:
        raise AssertionError(f"No minimum version found for {requirement}")
    return max(mins)


def test_faster_whisper_is_not_a_base_dependency():
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]

    assert not any(dep.startswith("faster-whisper") for dep in deps)

    voice_extra = data["project"]["optional-dependencies"]["voice"]
    assert any(dep.startswith("faster-whisper") for dep in voice_extra)


def test_manifest_includes_bundled_skills():
    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")

    assert "graft skills" in manifest
    assert "graft optional-skills" in manifest


def test_security_sensitive_dependency_floors():
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]
    extras = data["project"]["optional-dependencies"]

    anthropic_req = _find_requirement(deps, "anthropic")
    cryptography_req = _find_requirement(deps, "cryptography")

    assert _minimum_version(anthropic_req) >= Version("0.87.0")
    assert _minimum_version(cryptography_req) >= Version("46.0.7")

    for extra_name in ("messaging", "homeassistant", "sms"):
        aiohttp_req = _find_requirement(extras[extra_name], "aiohttp")
        assert _minimum_version(aiohttp_req) >= Version("3.13.4")

    assert _minimum_version(_find_requirement(extras["modal"], "cbor2")) >= Version("5.9.0")
    assert _minimum_version(_find_requirement(extras["daytona"], "python-multipart")) >= Version("0.0.26")
    assert _minimum_version(_find_requirement(extras["dev"], "pytest")) >= Version("9.0.3")
    assert _minimum_version(_find_requirement(extras["mcp"], "python-multipart")) >= Version("0.0.26")
    assert _minimum_version(_find_requirement(extras["yc-bench"], "litellm")) >= Version("1.83.0")
    assert _minimum_version(_find_requirement(extras["yc-bench"], "pillow")) >= Version("12.2.0")
