"""Generate a minimal CycloneDX 1.7 SBOM from the project metadata.

This is intentionally dependency-free: release CI runs it before uploading
artifacts, so the SBOM cannot drift when the build environment lacks an SBOM
library. It records declared components, not resolved transitive hashes;
``uv.lock`` remains the authoritative dependency resolution.
"""

from __future__ import annotations

import argparse
import json
import re
import tomllib
import uuid
from pathlib import Path
from typing import Any

_NAME_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)")


def _requirement_component(requirement: str) -> tuple[str, str] | None:
    match = _NAME_RE.match(requirement)
    if not match:
        return None
    name = match.group(1)
    version = ""
    remainder = requirement[match.end() :].strip()
    if remainder.startswith("=="):
        version = re.split(r"[;,\s]", remainder[2:], maxsplit=1)[0]
    return name, version


def build_sbom(project_file: Path) -> dict[str, Any]:
    data = tomllib.loads(project_file.read_text(encoding="utf-8"))
    project = data.get("project", {})
    components: dict[tuple[str, str], dict[str, Any]] = {}
    groups = [project.get("dependencies", [])]
    groups.extend((project.get("optional-dependencies") or {}).values())
    for group in groups:
        for requirement in group:
            parsed = _requirement_component(str(requirement))
            if parsed is None:
                continue
            name, version = parsed
            purl = f"pkg:pypi/{name.lower()}" + (f"@{version}" if version else "")
            components[(name.lower(), version)] = {
                "type": "library",
                "name": name,
                "version": version or "unknown",
                "purl": purl,
                "bom-ref": purl,
            }
    name = str(project.get("name") or "machina")
    version = str(project.get("version") or "unknown")
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "serialNumber": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, f'machina-sbom:{name}:{version}')}",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": name,
                "version": version,
            }
        },
        "components": [components[key] for key in sorted(components)],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--output", type=Path, default=Path("dist/sbom.cdx.json"))
    args = parser.parse_args()
    document = build_sbom(args.project)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {args.output} ({len(document['components'])} components)")


if __name__ == "__main__":
    main()
