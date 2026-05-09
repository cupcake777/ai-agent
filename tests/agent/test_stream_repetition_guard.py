"""Tests for StreamingRepetitionGuard."""
import pytest
from agent.stream_repetition_guard import (
    StreamRepetitionGuardConfig,
    StreamingRepetitionGuard,
    _truncation_notice,
)


class TestStreamRepetitionGuardConfig:
    def test_defaults(self):
        cfg = StreamRepetitionGuardConfig()
        assert cfg.enabled is True
        assert cfg.chunk_repeat_max == 4
        assert cfg.window_chars == 4000
        assert cfg.min_pattern_len == 60
        assert cfg.window_repeats == 4

    def test_from_mapping_empty(self):
        cfg = StreamRepetitionGuardConfig.from_mapping(None)
        assert cfg.enabled is True
        assert cfg.chunk_repeat_max == 4

    def test_from_mapping_custom(self):
        cfg = StreamRepetitionGuardConfig.from_mapping({
            "enabled": False,
            "chunk_repeat_max": 6,
            "window_chars": 2000,
            "min_pattern_len": 30,
            "window_repeats": 3,
        })
        assert cfg.enabled is False
        assert cfg.chunk_repeat_max == 6
        assert cfg.window_chars == 2000

    def test_from_mapping_string_bools(self):
        cfg = StreamRepetitionGuardConfig.from_mapping({
            "enabled": "true",
        })
        assert cfg.enabled is True


class TestStreamingRepetitionGuardChunks:
    """Test heuristic 1: consecutive identical chunk detection."""

    def test_clean_text_passes_through(self):
        guard = StreamingRepetitionGuard()
        for word in ["Hello", " world", " this", " is", " fine"]:
            result = guard.feed(word)
            assert result is None, f"Clean text should pass through, got: {result}"

    def test_single_chunk_always_passes(self):
        guard = StreamingRepetitionGuard()
        result = guard.feed("test")
        assert result is None

    def test_two_identical_chunks_pass(self):
        """Below chunk_repeat_max=4, two identical chunks are fine."""
        guard = StreamingRepetitionGuard()
        assert guard.feed("same") is None
        assert guard.feed("same") is None
        assert guard.feed("same") is None
        # Three identical is still below default threshold of 4

    def test_consecutive_identical_chunks_trigger(self):
        """chunk_repeat_max=4: 4th consecutive identical chunk triggers."""
        guard = StreamingRepetitionGuard()
        assert guard.feed("xyz") is None   # count=0 (different from prior)
        assert guard.feed("x") is None      # count=0
        assert guard.feed("x") is None      # count=1
        assert guard.feed("x") is None      # count=2
        assert guard.feed("x") is None      # count=3
        # Next identical chunk makes count=4, which >= chunk_repeat_max=4
        result = guard.feed("x")
        assert result is not None
        assert "truncated" in result.lower() or "repetition" in result.lower()

    def test_different_chunk_resets_counter(self):
        """A different chunk resets the consecutive counter."""
        guard = StreamingRepetitionGuard()
        assert guard.feed("a") is None   # count=0
        assert guard.feed("a") is None   # count=1
        assert guard.feed("a") is None   # count=2
        assert guard.feed("b") is None   # reset count=0
        assert guard.feed("a") is None   # reset count=0
        assert guard.feed("a") is None   # count=1
        assert guard.feed("a") is None   # count=2 — still safe

    def test_custom_chunk_repeat_max(self):
        cfg = StreamRepetitionGuardConfig(chunk_repeat_max=2)
        guard = StreamingRepetitionGuard(cfg)
        assert guard.feed("x") is None    # count=0
        assert guard.feed("x") is None    # count=1
        result = guard.feed("x")          # count=2 >= threshold
        assert result is not None

    def test_triggered_guard_swallows_subsequent(self):
        """After triggering, the guard silently swallows all further deltas."""
        guard = StreamingRepetitionGuard(
            StreamRepetitionGuardConfig(chunk_repeat_max=2)
        )
        guard.feed("a")  # count=0
        guard.feed("a")  # count=1
        result = guard.feed("a")  # triggered
        assert result is not None
        # Subsequent different text should be swallowed
        result2 = guard.feed("completely different text")
        assert result2 == ""  # empty string = silently swallowed

    def test_guard_can_be_disabled(self):
        """Disabled guard should always return None."""
        cfg = StreamRepetitionGuardConfig(enabled=False, chunk_repeat_max=2)
        guard = StreamingRepetitionGuard(cfg)
        for _ in range(20):
            assert guard.feed("same") is None

    def test_empty_chunk_does_not_trigger(self):
        """Empty chunks should not affect the counter."""
        guard = StreamingRepetitionGuard()
        assert guard.feed("") is None
        assert guard.feed("text") is None
        assert guard.feed("") is None

    def test_triggered_property(self):
        guard = StreamingRepetitionGuard(
            StreamRepetitionGuardConfig(chunk_repeat_max=2)
        )
        assert guard.triggered is False
        guard.feed("x")
        guard.feed("x")
        guard.feed("x")
        assert guard.triggered is True

    def test_reset_clears_state(self):
        guard = StreamingRepetitionGuard(
            StreamRepetitionGuardConfig(chunk_repeat_max=2)
        )
        guard.feed("x")
        guard.feed("x")
        guard.feed("x")  # triggers
        assert guard.triggered is True
        guard.reset()
        assert guard.triggered is False
        assert guard.feed("x") is None  # fresh start


class TestStreamingRepetitionGuardWindow:
    """Test heuristic 2: window-level contiguous repeat detection."""

    def test_long_unique_text_passes(self):
        """Long unique text should not trigger window detection."""
        guard = StreamingRepetitionGuard(
            StreamRepetitionGuardConfig(
                min_pattern_len=20,
                window_repeats=4,
                window_chars=1000,
            )
        )
        # Feed Shakespeare — all unique
        lines = [
            "To be, or not to be, that is the question: ",
            "Whether 'tis nobler in the mind to suffer ",
            "The slings and arrows of outrageous fortune, ",
            "Or to take arms against a sea of troubles, ",
        ]
        for line in lines:
            result = guard.feed(line)
            assert result is None

    def test_contiguous_repeat_triggers(self):
        """A block repeated 4+ times contiguously should trigger."""
        cfg = StreamRepetitionGuardConfig(
            min_pattern_len=20,
            window_repeats=4,
            window_chars=5000,
        )
        guard = StreamingRepetitionGuard(cfg)
        block = "The quick brown fox jumps over the lazy dog. "
        # Feed the block 5 times
        for i in range(4):
            result = guard.feed(block)
            if result is not None:
                break  # triggered early
        # 5th repetition should definitely trigger
        result = guard.feed(block)
        assert result is not None, "Should detect repeated block"

    def test_noncontiguous_repeat_does_not_trigger(self):
        """Similar but not contiguous text should not false-positive."""
        cfg = StreamRepetitionGuardConfig(
            min_pattern_len=20,
            window_repeats=4,
            window_chars=5000,
        )
        guard = StreamingRepetitionGuard(cfg)
        # Numbered list where each line differs
        for i in range(20):
            line = f"Item number {i}: this is a distinct line with unique content. "
            result = guard.feed(line)
            assert result is None, f"False positive on line {i}"

    def test_varied_short_text_does_not_trigger(self):
        """Varied short text should not trigger either heuristic."""
        cfg = StreamRepetitionGuardConfig(
            min_pattern_len=100,
            window_repeats=4,
            window_chars=500,
        )
        guard = StreamingRepetitionGuard(cfg)
        # Feed varied short text — no consecutive identical chunks,
        # and no pattern long enough for window detection.
        for i in range(20):
            line = f"Line {i}: some unique content here. "
            result = guard.feed(line)
            assert result is None
        assert not guard.triggered

    def test_identical_short_chunks_do_trigger(self):
        """Even very short identical chunks trigger chunk-level detection."""
        cfg = StreamRepetitionGuardConfig(
            min_pattern_len=1000,  # window detection effectively disabled
            chunk_repeat_max=4,
        )
        guard = StreamingRepetitionGuard(cfg)
        block = "Short. "
        assert guard.feed(block) is None   # first occurrence (count=0)
        assert guard.feed(block) is None   # count=1
        assert guard.feed(block) is None   # count=2
        assert guard.feed(block) is None   # count=3
        # 5th call: count=4 >= chunk_repeat_max=4 → triggered
        result = guard.feed(block)
        assert result is not None


class TestTruncationNotice:
    def test_notice_content(self):
        notice = _truncation_notice()
        assert "truncated" in notice.lower() or "repetition" in notice.lower()
        assert len(notice) > 10  # meaningful message