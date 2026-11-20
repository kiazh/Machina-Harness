"""The release SBOM is deterministic CycloneDX 1.7 metadata."""

from __future__ import annotations

from pathlib import Path

from tools.generate_sbom import _requirement_component, build_sbom


def test_sbom_is_cyclonedx_1_7_and_deterministic(tmp_path: Path) -> None:
    project = tmp_path / "pyproject.toml"
    project.write_text(
        """
[project]
name = "demo"
version = "1.2.3"
dependencies = ["httpx>=0.27", "rich==13.9.4"]
[project.optional-dependencies]
dev = ["pytest>=8"]
""",
        encoding="utf-8",
    )
    first = build_sbom(project)
    second = build_sbom(project)
    assert first == second
    assert first["bomFormat"] == "CycloneDX"
    assert first["specVersion"] == "1.7"
    assert first["serialNumber"].startswith("urn:uuid:")
    names = {component["name"] for component in first["components"]}
    assert names == {"httpx", "rich", "pytest"}
    assert next(c for c in first["components"] if c["name"] == "rich")["version"] == "13.9.4"


def test_requirement_parser_handles_extras_markers_and_versions() -> None:
    assert _requirement_component("httpx[http2]>=0.27; python_version >= '3.11'") == ("httpx", "")
    assert _requirement_component("mcp==2.3.0") == ("mcp", "2.3.0")
