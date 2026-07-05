"""Fallback activation must downshift oversized history before retrying.

A large primary model (for example GPT-5.5 with ~1M context) can fail after a
conversation has grown to hundreds of thousands of tokens. A smaller fallback
provider (for example DigitalOcean deepseek-v4-pro at ~87K) is useless if Hermes
retries the exact same oversized message list. The loop must compress against
the fallback model's context window before sending the fallback request.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent.conversation_loop import _downshift_context_for_fallback_if_needed


class DummyCompressor:
    def __init__(self, context_length=300_000):
        self.context_length = context_length

    def update_model(self, *, context_length, **_kwargs):
        self.context_length = context_length


class DummyAgent:
    def __init__(self):
        self.model = "gpt-5.5"
        self.provider = "custom"
        self.base_url = "https://primary.example/v1"
        self.api_key = "primary-key"
        self.api_mode = "chat_completions"
        self.context_compressor = DummyCompressor()
        self.compression_enabled = True
        self.tools = []
        self._custom_providers = None
        self.statuses = []
        self.warnings = []
        self.log_prefix = ""

    def _try_activate_fallback(self, reason=None):
        self.model = "small-fallback"
        self.provider = "fallback"
        self.base_url = "https://fallback.example/v1"
        self.api_key = "fallback-key"
        self.context_compressor.update_model(
            model=self.model,
            context_length=64_000,
            base_url=self.base_url,
            api_key=self.api_key,
            provider=self.provider,
            api_mode=self.api_mode,
        )
        return True

    def _compress_context(self, messages, system_message, *, approx_tokens=None, task_id=None, focus_topic=None):
        self.compress_called = {
            "approx_tokens": approx_tokens,
            "focus_topic": focus_topic,
            "task_id": task_id,
        }
        return ([{"role": "system", "content": "summary"}], "new system prompt")

    def _buffer_status(self, msg):
        self.statuses.append(msg)

    def _emit_warning(self, msg):
        self.warnings.append(msg)


def test_fallback_downshift_compresses_when_request_exceeds_fallback_window():
    agent = DummyAgent()
    messages = [{"role": "user", "content": "x" * 1000} for _ in range(20)]
    api_messages = list(messages)
    retry = SimpleNamespace(
        restart_with_compressed_messages=False,
        primary_recovery_attempted=True,
    )

    with patch(
        "agent.conversation_loop.estimate_request_tokens_rough",
        return_value=120_000,
    ):
        activated, new_messages, new_prompt = _downshift_context_for_fallback_if_needed(
            agent,
            messages,
            api_messages,
            "old system prompt",
            "system override",
            "task-1",
            retry,
            reason=None,
        )

    assert activated is True
    assert new_messages == [{"role": "system", "content": "summary"}]
    assert new_prompt == "new system prompt"
    assert retry.restart_with_compressed_messages is True
    assert retry.primary_recovery_attempted is False
    assert agent.compress_called["focus_topic"]
    assert "fallback model" in agent.compress_called["focus_topic"]
    assert any("Downshifting context for fallback model" in s for s in agent.statuses)


def test_fallback_downshift_skips_compression_when_request_fits():
    agent = DummyAgent()
    messages = [{"role": "user", "content": "small"}]
    api_messages = list(messages)
    retry = SimpleNamespace(
        restart_with_compressed_messages=False,
        primary_recovery_attempted=True,
    )

    with patch(
        "agent.conversation_loop.estimate_request_tokens_rough",
        return_value=10_000,
    ):
        activated, new_messages, new_prompt = _downshift_context_for_fallback_if_needed(
            agent,
            messages,
            api_messages,
            "old system prompt",
            None,
            "task-1",
            retry,
            reason=None,
        )

    assert activated is True
    assert new_messages is messages
    assert new_prompt == "old system prompt"
    assert retry.restart_with_compressed_messages is False
    assert not hasattr(agent, "compress_called")


def test_fallback_downshift_does_not_force_when_compression_disabled():
    agent = DummyAgent()
    agent.compression_enabled = False
    messages = [{"role": "user", "content": "x" * 1000}]
    api_messages = list(messages)
    retry = SimpleNamespace(
        restart_with_compressed_messages=False,
        primary_recovery_attempted=True,
    )

    with patch(
        "agent.conversation_loop.estimate_request_tokens_rough",
        return_value=120_000,
    ):
        activated, new_messages, _ = _downshift_context_for_fallback_if_needed(
            agent,
            messages,
            api_messages,
            "old system prompt",
            None,
            "task-1",
            retry,
            reason=None,
        )

    assert activated is True
    assert new_messages is messages
    assert retry.restart_with_compressed_messages is False
    assert agent.warnings
    assert "too large for fallback" in agent.warnings[0]
