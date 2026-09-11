"""DeepSeek HTTP 400 ``Content Exists Risk`` → omit L1 MEMORY.md and retry once."""
from __future__ import annotations

from types import SimpleNamespace

from agent.error_classifier import FailoverReason
from agent.turn_recovery import (
    _L1_MEMORY_OMITTED_STUB,
    _recover_content_exists_risk_memory,
    _strip_l1_memory_block,
    _strip_l1_memory_from_api_messages,
)
from agent.turn_retry_state import TurnRetryState
from tools.memory_tool_store import MEMORY_BLOCK_HEADERS


def _memory_block(body: str = "secret-proxy-chain-should-not-go-to-deepseek") -> str:
    sep = "═" * 46
    header = MEMORY_BLOCK_HEADERS["memory"]
    return f"{sep}\n{header} [10%]\n{sep}\n{body}"


def _user_block() -> str:
    sep = "═" * 46
    header = MEMORY_BLOCK_HEADERS["user"]
    return f"{sep}\n{header} [10%]\n{sep}\nUser prefers concise replies."


def test_strip_replaces_snapshot_and_keeps_user_profile():
    mem = _memory_block()
    user = _user_block()
    text = f"You are Hermes.\n{mem}\n{user}\n# runtime"
    out = _strip_l1_memory_block(text, snapshot=mem)
    assert mem not in out
    assert "secret-proxy-chain" not in out
    assert _L1_MEMORY_OMITTED_STUB in out
    assert "User prefers concise replies." in out
    assert "You are Hermes." in out


def test_strip_header_fallback_without_snapshot():
    mem = _memory_block()
    user = _user_block()
    text = f"core\n{mem}\n{user}"
    out = _strip_l1_memory_block(text, snapshot="")
    assert "secret-proxy-chain" not in out
    assert _L1_MEMORY_OMITTED_STUB in out
    assert "User prefers concise replies." in out


def test_strip_api_messages_does_not_alias_canonical_history():
    mem = _memory_block()
    canonical = [{"role": "system", "content": f"core\n{mem}\n{_user_block()}"}]
    api_messages = [canonical[0]]  # same object, the real loop's shallow aliasing
    agent = SimpleNamespace(_memory_store=SimpleNamespace(format_for_system_prompt=lambda kind: mem if kind == "memory" else ""))
    assert _strip_l1_memory_from_api_messages(agent, api_messages) is True
    assert "secret-proxy-chain" not in api_messages[0]["content"]
    assert "secret-proxy-chain" in canonical[0]["content"], (
        "canonical history must keep MEMORY.md; only the per-call copy is omitted"
    )


class _Agent:
    log_prefix = ""
    _memory_store = None

    def _vprint(self, line, force=False):
        self.last = line


def _classified():
    return SimpleNamespace(
        reason=FailoverReason.content_policy_blocked,
        message="HTTP 400: Content Exists Risk",
    )


def test_recovery_omits_memory_once_then_stops():
    mem = _memory_block()
    api_messages = [{"role": "system", "content": f"core\n{mem}"}]
    agent = _Agent()
    agent._memory_store = SimpleNamespace(format_for_system_prompt=lambda kind: mem if kind == "memory" else "")
    err = Exception("Error code: 400 - Content Exists Risk")
    retry = TurnRetryState()
    classified = _classified()
    assert _recover_content_exists_risk_memory(agent, err, classified, retry, api_messages) is True
    assert retry.content_exists_risk_memory_retry_attempted is True
    assert _L1_MEMORY_OMITTED_STUB in api_messages[0]["content"]
    # Second hit must not loop.
    assert _recover_content_exists_risk_memory(agent, err, classified, retry, api_messages) is False


def test_recovery_ignores_other_policy_blocks():
    mem = _memory_block()
    api_messages = [{"role": "system", "content": mem}]
    agent = _Agent()
    retry = TurnRetryState()
    classified = SimpleNamespace(
        reason=FailoverReason.content_policy_blocked,
        message="This content was flagged for possible cybersecurity risk",
    )
    assert _recover_content_exists_risk_memory(
        agent, Exception("flagged for possible cybersecurity risk"), classified, retry, api_messages,
    ) is False
    assert "secret-proxy-chain" in api_messages[0]["content"]
