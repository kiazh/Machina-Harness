import pytest

from machina.core.config import UI_PREFERENCES_MAX_BYTES, validate_value


def test_keybindings_config_accepts_bounded_json_text() -> None:
    value = '{"restoreSplit":"f7"}'
    assert validate_value("keybindings", value) == value


def test_keybindings_config_rejects_oversized_input() -> None:
    with pytest.raises(ValueError, match="keybindings is too long"):
        validate_value("keybindings", "a" * (UI_PREFERENCES_MAX_BYTES + 1))


def test_validate_value_reports_typed_failure() -> None:
    from machina.core.exceptions import ValidationError

    with pytest.raises(ValidationError, match="max_tokens"):
        validate_value("max_tokens", "not-a-number")
