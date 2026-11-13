"""Export method names from literal gateway registries without importing services."""

import argparse
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def registered_methods() -> list[str]:
    names: set[str] = set()
    sources = [
        ROOT / "machina/gateway/server.py",
        ROOT / "machina/gateway/features/__init__.py",
    ]
    for source in sources:
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.AnnAssign) or not isinstance(node.value, ast.Dict):
                continue
            target = node.target
            if not (
                (isinstance(target, ast.Attribute) and target.attr == "_methods")
                or (isinstance(target, ast.Name) and target.id == "METHODS")
            ):
                continue
            names.update(
                key.value
                for key in node.value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            )
    return sorted(names)


def generated_contracts() -> dict[str, str]:
    methods = registered_methods()
    serialized = json.dumps(methods, indent=2)
    return {
        "rpc-methods.json": serialized + "\n",
        "rpc-methods.ts": "// Generated from the Python gateway registries by tools/contracts/export_contracts.py.\n"
        + "export const RPC_METHODS = "
        + serialized
        + " as const;\n"
        + "export type RpcMethod = typeof RPC_METHODS[number];\n",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail on generated contract drift without writing files",
    )
    args = parser.parse_args()
    destination = ROOT / "packages/contracts/src"
    for name, content in generated_contracts().items():
        target = destination / name
        if args.check:
            if not target.exists() or target.read_text(encoding="utf-8") != content:
                parser.exit(
                    1,
                    f"Generated RPC contract is stale: {name}. Run tools/contracts/export_contracts.py.\n",
                )
        else:
            destination.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
