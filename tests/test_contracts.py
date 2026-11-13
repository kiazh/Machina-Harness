from tools.contracts.export_contracts import ROOT, generated_contracts


def test_native_and_frontend_allowlists_match_the_actual_gateway_registry():
    for name, content in generated_contracts().items():
        assert (ROOT / "packages/contracts/src" / name).read_text(encoding="utf-8") == content, name
