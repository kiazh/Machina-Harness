"""ECC integrity remains valid when Git checks out text files with CRLF."""

from pathlib import Path

from machina.skills.bundle import PIN, ECCBundle


def test_ecc_bundle_accepts_windows_checkout(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "skills" / "ecc"
    original = (source / "manifest.json").read_bytes()
    manifest = original.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    (tmp_path / "manifest.json").write_bytes(manifest)

    bundle = ECCBundle(root=tmp_path)
    assert bundle.manifest["revision"] == PIN
    resource = next(path for path in bundle.manifest["files"] if path.endswith("SKILL.md"))
    original_resource = (source / resource).read_bytes()
    target = tmp_path / resource
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(original_resource.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert bundle.read_resource(resource)
