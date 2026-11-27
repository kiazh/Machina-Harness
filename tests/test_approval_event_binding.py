from machina.gateway.server import _approval_event_target


def test_approval_event_uses_the_pending_rows_owning_child_session():
    card = {"session_id": "child-session", "turn_id": "child-turn"}
    assert _approval_event_target(card, "parent-session", "parent-turn") == (
        "child-session",
        "child-turn",
    )


def test_approval_event_falls_back_for_legacy_cards_without_a_binding():
    assert _approval_event_target({}, "parent-session", "parent-turn") == (
        "parent-session",
        "parent-turn",
    )
