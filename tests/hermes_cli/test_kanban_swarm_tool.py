"""Tests for the kanban_swarm agent tool and the parentless-verifier
guardrail on kanban_create.

The swarm *topology* itself is covered by tests/hermes_cli/test_kanban_swarm.py;
this file exercises the tool plumbing (validation, statuses via the tool
surface) and the _handle_create warning.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Orchestrator context: swarm/list/unblock are hidden from
    # dispatcher-spawned workers via this env var.
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    # Keep auto-subscribe out of these tests (no session context anyway).
    monkeypatch.delenv("HERMES_SESSION_KEY", raising=False)
    kb.init_db()
    return home


# ---------------------------------------------------------------------------
# kanban_swarm tool
# ---------------------------------------------------------------------------

def test_swarm_tool_creates_correct_topology(kanban_home):
    from tools.kanban_tools import _handle_swarm

    out = json.loads(_handle_swarm({
        "goal": "Produce a market decision memo.",
        "workers": [
            {"profile": "researcher-a", "title": "Market scan", "body": "Find competitors"},
            {"profile": "researcher-b", "title": "Customer scan"},
        ],
        "verifier_assignee": "critic",
        "synthesizer_assignee": "writer",
    }))
    assert "error" not in out, out
    assert len(out["worker_ids"]) == 2

    conn = kb.connect()
    try:
        root = kb.get_task(conn, out["root_id"])
        workers = [kb.get_task(conn, wid) for wid in out["worker_ids"]]
        verifier = kb.get_task(conn, out["verifier_id"])
        synthesizer = kb.get_task(conn, out["synthesizer_id"])

        assert root.status == "done"
        assert [w.status for w in workers] == ["ready", "ready"]
        # Verifier waits for ALL workers; synthesizer waits for the verifier.
        assert verifier.status == "todo"
        assert sorted(kb.parent_ids(conn, verifier.id)) == sorted(out["worker_ids"])
        assert synthesizer.status == "todo"
        assert kb.parent_ids(conn, synthesizer.id) == [verifier.id]
    finally:
        conn.close()


def test_swarm_tool_validates_inputs(kanban_home):
    from tools.kanban_tools import _handle_swarm

    base = {
        "goal": "g",
        "workers": [{"profile": "p", "title": "t"}],
        "verifier_assignee": "v",
        "synthesizer_assignee": "s",
    }
    assert "error" in json.loads(_handle_swarm({**base, "goal": "  "}))
    assert "error" in json.loads(_handle_swarm({**base, "workers": []}))
    assert "error" in json.loads(_handle_swarm({**base, "workers": ["not-a-dict"]}))
    assert "error" in json.loads(
        _handle_swarm({**base, "workers": [{"profile": "p"}]})  # missing title
    )
    assert "error" in json.loads(_handle_swarm({**base, "verifier_assignee": ""}))


def test_swarm_tool_is_orchestrator_only(kanban_home, monkeypatch):
    from tools.kanban_tools import _handle_swarm

    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_worker123")
    out = json.loads(_handle_swarm({
        "goal": "g",
        "workers": [{"profile": "p", "title": "t"}],
        "verifier_assignee": "v",
        "synthesizer_assignee": "s",
    }))
    assert "error" in out
    assert "orchestrator-only" in out["error"]


# ---------------------------------------------------------------------------
# Parentless-verifier guardrail on kanban_create
# ---------------------------------------------------------------------------

def test_create_warns_on_parentless_verifier(kanban_home):
    from tools.kanban_tools import _handle_create

    out = json.loads(_handle_create({
        "title": "Verify swarm outputs",
        "assignee": "critic",
    }))
    assert "error" not in out, out
    assert "warning" in out
    assert "parents" in out["warning"]
    # Creation itself is NOT blocked.
    conn = kb.connect()
    try:
        assert kb.get_task(conn, out["task_id"]) is not None
    finally:
        conn.close()


def test_create_no_warning_with_parents(kanban_home):
    from tools.kanban_tools import _handle_create

    worker = json.loads(_handle_create({"title": "do work", "assignee": "w"}))
    out = json.loads(_handle_create({
        "title": "Review the work",
        "assignee": "critic",
        "parents": [worker["task_id"]],
    }))
    assert "error" not in out, out
    assert "warning" not in out


def test_create_no_warning_on_plain_title(kanban_home):
    from tools.kanban_tools import _handle_create

    out = json.loads(_handle_create({
        "title": "Implement the CSV exporter",
        "assignee": "coder",
    }))
    assert "error" not in out, out
    assert "warning" not in out
