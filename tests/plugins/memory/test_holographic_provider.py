from plugins.memory.holographic import (
    HolographicMemoryProvider,
    _auto_extract_facts_from_text,
)


def _provider(tmp_path, **config):
    p = HolographicMemoryProvider(
        config={
            "auto_extract": True,
            "db_path": str(tmp_path / "memory_store.db"),
            "hrr_weight": 0.0,
            **config,
        }
    )
    p.initialize(session_id="test-session")
    return p


def _stored_facts(provider):
    return [
        row["content"]
        for row in provider._store.list_facts(min_trust=0.0, limit=50)
    ]


def test_auto_extract_distills_preference_instead_of_storing_raw_turn(tmp_path):
    raw = "I prefer concise direct answers."
    p = _provider(tmp_path)

    p.on_session_end([{"role": "user", "content": raw}])

    facts = _stored_facts(p)
    assert facts == ["User prefers concise direct answers."]
    assert raw not in facts


def test_auto_extract_distills_project_facts(tmp_path):
    p = _provider(tmp_path)

    p.on_session_end(
        [
            {"role": "user", "content": "We decided to use broker transport for Claude CLI."},
            {"role": "user", "content": "The project uses pytest for regression tests."},
        ]
    )

    assert _stored_facts(p) == [
        "Project decision: use broker transport for Claude CLI.",
        "The project uses pytest for regression tests.",
    ]


def test_auto_extract_rejects_task_requests_and_questions():
    raw_turns = [
        "can you install it for me? I want to try using enhanced mode?",
        "what are you talking about? I need to pay for it?",
        "lets do serious stuff here: I want you to fully research the terminal issue",
    ]

    for raw in raw_turns:
        assert _auto_extract_facts_from_text(raw) == []

