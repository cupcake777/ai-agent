"""An approval answered outside the session's chat tells the chat's prompt who answered.

``resolve_gateway_approval(source=..., actor=...)`` is how a surface other than the chat (a desktop
approval plugin) answers. The settle hook registered ``with_outcome`` receives that, so the turn runner
can edit the chat card instead of leaving live buttons; ``post_approval_response`` gets ``decided_by``.
"""

from tools import approval as mod
from tools import approval_gateway_wait as wait_mod

SESSION_KEY = "approval-resolved-elsewhere"
APPROVAL = {"command": "rm -rf build", "description": "d", "pattern_key": "dangerous", "pattern_keys": ["dangerous"]}


def _clear():
    mod._gateway_queues.clear()
    mod._gateway_notify_cbs.clear()


def _answer_while_waiting(monkeypatch, *, with_outcome, **resolve_kwargs):
    """Run one gateway wait whose prompt is answered by ``resolve_gateway_approval(**resolve_kwargs)``."""
    _clear()
    hooks: list[tuple[str, dict]] = []
    settled: list[tuple] = []
    monkeypatch.setattr(wait_mod._ctx, "_fire_approval_hook", lambda name, **kw: hooks.append((name, kw)))

    def answered(event, session_key, *, interrupt_log):
        request_id = mod.list_gateway_approvals(session_key)[0]["request_id"]
        assert mod.register_gateway_settle(session_key, request_id, lambda *args: settled.append(args),
                                           with_outcome=with_outcome)
        assert mod.resolve_gateway_approval(session_key, "session", request_id=request_id, **resolve_kwargs) == 1
        return "set"

    monkeypatch.setattr(wait_mod, "_poll_event", answered)
    decision = wait_mod._await_gateway_decision(SESSION_KEY, lambda data: None, APPROVAL)
    return decision, settled, hooks[-1][1]


def test_an_answer_from_another_surface_reaches_the_prompt_and_observers(monkeypatch):
    decision, settled, post = _answer_while_waiting(
        monkeypatch, with_outcome=True, source="anotify", actor="anotify desktop · MacBook")

    assert decision == {"resolved": True, "choice": "session", "reason": None}
    assert settled == [("resolved", {"choice": "session", "source": "anotify", "actor": "anotify desktop · MacBook"})]
    assert post["decided_by"] == "anotify"


def test_an_answer_from_the_chat_itself_carries_no_source(monkeypatch):
    _, settled, post = _answer_while_waiting(monkeypatch, with_outcome=True)

    assert settled == [("resolved", {"choice": "session", "source": None, "actor": None})]
    assert "decided_by" not in post


def test_settle_hooks_registered_without_outcome_keep_their_one_argument(monkeypatch):
    """The TUI's settle callable takes only the cancel reason."""
    _, settled, _ = _answer_while_waiting(monkeypatch, with_outcome=False, source="anotify")

    assert settled == [("resolved",)]
