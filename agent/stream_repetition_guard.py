"""
StreamingRepetitionGuard — detect and truncate degenerate model output.

Models (notably glm-5.x) can fall into token-level repetition loops where the
same text block is generated over and over.  The tool_loop_guardrails only
catch tool-call loops, not *text* repetition in streaming responses.

This guard monitors the accumulated streamed text and, when a repeating
pattern is detected beyond a configurable threshold, signals that output
should be truncated.

Detection heuristics
--------------------
1. **Chunk-level repeat**: The same delta text appears N+ consecutive times.
   Catches the degenerate case where the model emits the exact same token
   sequence in a tight loop (most common pattern for glm-5.x).

2. **Window-level repeat**: A non-trivial substring (>min_len chars) of the
   accumulated output appears 3+ times in the last `window_size` chars, with
   each repetition starting at the boundary of the previous one (contiguous
   repeat).  Catches longer blocks that get repeated verbatim.

Both heuristics use a sliding window to bound memory and avoid false positives
on legitimate repeating content (numbered lists, code patterns, etc.).

Config (config.yaml → stream_repetition_guard)
------------------------------------------------
enabled          bool    True    Master switch
chunk_repeat_max int     4       Max consecutive identical deltas before halt
window_chars     int     4000    Rolling window size for pattern detection
min_pattern_len  int     60      Shortest pattern to consider for window detection
window_repeats   int     4       Repetitions within window to trigger halt
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class StreamRepetitionGuardConfig:
    """Configuration for the streaming repetition guard."""

    enabled: bool = True
    chunk_repeat_max: int = 4       # consecutive identical deltas → halt
    window_chars: int = 4000       # rolling window size
    min_pattern_len: int = 60       # minimum pattern length for window detection
    window_repeats: int = 4         # repetitions within window → halt

    @classmethod
    def from_mapping(cls, data: dict | None) -> "StreamRepetitionGuardConfig":
        if not data:
            return cls()
        return cls(
            enabled=_as_bool(data.get("enabled", True), True),
            chunk_repeat_max=int(data.get("chunk_repeat_max", 4)),
            window_chars=int(data.get("window_chars", 4000)),
            min_pattern_len=int(data.get("min_pattern_len", 60)),
            window_repeats=int(data.get("window_repeats", 4)),
        )


class StreamingRepetitionGuard:
    """Stateful guard that monitors streamed text deltas for degenerate repetition.

    Call ``feed(delta_text)`` for each streaming chunk.  The method returns:
    - ``None``      → text is fine, pass it through
    - ``<str>``     → truncation message; stop streaming and display this
    """

    def __init__(self, config: StreamRepetitionGuardConfig | None = None):
        self.config = config or StreamRepetitionGuardConfig()
        self._accumulated: str = ""
        self._last_chunk: str = ""
        self._consecutive_repeat_count: int = 0
        self._triggered: bool = False

    def reset(self) -> None:
        """Reset state for a new streaming response."""
        self._accumulated = ""
        self._last_chunk = ""
        self._consecutive_repeat_count = 0
        self._triggered = False

    def feed(self, delta: str) -> Optional[str]:
        """Process a streaming delta.

        Returns:
            None  — delta is clean, deliver it normally
            str   — repetition detected; this is the truncation notice to
                    deliver instead of further deltas
        """
        if not self.config.enabled:
            return None

        if self._triggered:
            # Already halted — swallow all further deltas silently
            return ""

        # --- Heuristic 1: consecutive identical chunk -----------------------
        if delta and delta == self._last_chunk:
            self._consecutive_repeat_count += 1
            if self._consecutive_repeat_count >= self.config.chunk_repeat_max:
                self._triggered = True
                logger.warning(
                    "Stream repetition guard: %d consecutive identical chunks "
                    "(len=%d), halting output.",
                    self._consecutive_repeat_count,
                    len(delta),
                )
                return _truncation_notice()
        else:
            self._consecutive_repeat_count = 0

        self._last_chunk = delta

        # --- Accumulate for window detection --------------------------------
        self._accumulated += delta

        # Trim to window size to bound memory and search space
        if len(self._accumulated) > self.config.window_chars * 2:
            self._accumulated = self._accumulated[-self.config.window_chars:]

        # --- Heuristic 2: window-level contiguous repeat ---------------------
        # Only check if enough text has accumulated (skip short outputs)
        if len(self._accumulated) >= self.config.min_pattern_len * 3:
            result = self._detect_window_repeat()
            if result is not None:
                return result

        # Clean — pass through
        return None

    def _detect_window_repeat(self) -> Optional[str]:
        """Check if the tail of _accumulated contains a repeated block.

        Uses a suffix-matching approach: take progressively longer suffix
        strings and count how many times they repeat contiguously at the end.
        """
        text = self._accumulated
        min_len = self.config.min_pattern_len
        max_repeats = self.config.window_repeats
        # Only search in the last portion of accumulated text for performance
        tail = text[-self.config.window_chars:] if len(text) > self.config.window_chars else text

        # Try candidate pattern lengths.  Start from min_len, cap at 1/4 of
        # the tail so we need at least 4 copies to fill the tail.
        max_pattern_len = min(len(tail) // 4, 2000)
        if max_pattern_len < min_len:
            return None

        # Sample a few pattern lengths rather than trying every one.
        # Logarithmic sampling: min_len, min_len*2, min_len*4, ...
        pattern_lengths = []
        pl = min_len
        while pl <= max_pattern_len:
            pattern_lengths.append(pl)
            pl *= 2
        # Always try the most recent window boundary sizes
        for extra in [100, 200, 500, 1000]:
            if min_len < extra <= max_pattern_len:
                pattern_lengths.append(extra)
        pattern_lengths = sorted(set(pattern_lengths))[-6:]  # try at most 6 lengths

        for plen in pattern_lengths:
            candidate = tail[-plen:]
            # Don't waste time on whitespace-only or near-whitespace patterns
            stripped = candidate.strip()
            if len(stripped) < plen * 0.3:
                continue
            # Count contiguous repetitions of candidate at the end of tail
            repeats = 0
            pos = len(tail)
            while pos >= plen:
                if tail[pos - plen:pos] == candidate:
                    repeats += 1
                    pos -= plen
                else:
                    break
            if repeats >= max_repeats:
                self._triggered = True
                logger.warning(
                    "Stream repetition guard: pattern of %d chars repeated "
                    "%d times contiguously, halting output.",
                    plen,
                    repeats,
                )
                return _truncation_notice()

        return None

    @property
    def triggered(self) -> bool:
        return self._triggered


def _truncation_notice() -> str:
    """Return a user-visible notice when repetition is detected."""
    return "\n\n[Output truncated: degenerate repetition detected]"


def _as_bool(value, default: bool = False) -> bool:
    """Coerce common YAML bool representations to Python bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes", "on")
    if isinstance(value, (int, float)):
        return bool(value)
    return default