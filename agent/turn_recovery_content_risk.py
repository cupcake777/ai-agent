"""DeepSeek "Content Exists Risk" recovery: retry once with L1 MEMORY.md omitted.

The hosted API's serving-side input scan rejects a request (HTTP 400, no completion) most
often because of the frozen MEMORY.md block, not the user turn. Sibling of
``agent.turn_recovery``, which calls :func:`recover_content_exists_risk_memory` from its
classified-error ladder.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from agent.turn_retry_state import TurnRetryState

logger = logging.getLogger("agent.conversation_loop")

_CONTENT_EXISTS_RISK_NEEDLE = "content exists risk"
_L1_MEMORY_OMITTED_STUB = (
    "[L1 MEMORY.md omitted this request: the provider content filter rejected it. "
    "Facts remain on disk; use memory/session_search if needed.]"
)


def _is_content_exists_risk(api_error: Exception, classified: Any) -> bool:
    """DeepSeek hosted API's serving-side input scan (HTTP 400, no completion)."""
    blob = f"{getattr(classified, 'message', '')} {api_error}".lower()
    return _CONTENT_EXISTS_RISK_NEEDLE in blob


def _l1_memory_snapshot(agent: Any) -> str:
    store = getattr(agent, "_memory_store", None)
    if store is None:
        return ""
    try:
        return store.format_for_system_prompt("memory") or ""
    except Exception:
        return ""


def _strip_l1_memory_block(text: str, snapshot: str = "") -> str:
    """Replace the frozen MEMORY.md system-prompt block; leave USER.md / the rest intact."""
    if not text:
        return text
    if snapshot and snapshot in text:
        return text.replace(snapshot, _L1_MEMORY_OMITTED_STUB, 1)
    try:
        from tools.memory_tool_store import MEMORY_BLOCK_HEADERS
    except Exception:
        return text
    header = MEMORY_BLOCK_HEADERS["memory"]
    start = text.find(header)
    if start < 0:
        return text
    block_start = text.rfind("═", 0, start)
    if block_start >= 0:
        line_start = text.rfind("\n", 0, block_start)
        block_start = 0 if line_start < 0 else line_start + 1
    else:
        block_start = start
    user_h = MEMORY_BLOCK_HEADERS["user"]
    end = text.find(user_h, start + len(header))
    if end < 0:
        end = len(text)
    else:
        user_sep = text.rfind("═", block_start, end)
        if user_sep >= 0:
            line_start = text.rfind("\n", block_start, user_sep)
            end = block_start if line_start < 0 else line_start + 1
    return text[:block_start] + _L1_MEMORY_OMITTED_STUB + "\n" + text[end:]


def _omit_l1_memory_in_message(msg: dict[str, Any], snapshot: str) -> Optional[dict[str, Any]]:
    """Return a replaced system message dict, or None if there was nothing to strip."""
    content = msg.get("content")
    if isinstance(content, str):
        new = _strip_l1_memory_block(content, snapshot)
        return {**msg, "content": new} if new != content else None
    if isinstance(content, list):
        changed = False
        new_parts: list[Any] = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                new_text = _strip_l1_memory_block(part["text"], snapshot)
                if new_text != part["text"]:
                    new_parts.append({**part, "text": new_text})
                    changed = True
                    continue
            new_parts.append(part)
        return {**msg, "content": new_parts} if changed else None
    return None


def _strip_l1_memory_from_api_messages(agent: Any, api_messages: Any) -> bool:
    """Rewrite system rows on the per-call copy only — never canonical history or disk."""
    if not isinstance(api_messages, list):
        return False
    snapshot = _l1_memory_snapshot(agent)
    changed = False
    for i, msg in enumerate(api_messages):
        if not isinstance(msg, dict) or msg.get("role") != "system":
            continue
        replaced = _omit_l1_memory_in_message(msg, snapshot)
        if replaced is None:
            continue
        api_messages[i] = replaced
        changed = True
    return changed


def recover_content_exists_risk_memory(
    agent: Any, api_error: Exception, classified: Any, _retry: TurnRetryState, api_messages: Any,
) -> bool:
    """One-shot: DeepSeek Content Exists Risk is usually L1 MEMORY.md, not the user turn.

    Retry the same call with MEMORY.md omitted from the system prompt. USER.md and
    conversation history stay. If the filter still fires, fall through to fallback/abort.
    """
    if _retry.content_exists_risk_memory_retry_attempted:
        return False
    if not _is_content_exists_risk(api_error, classified):
        return False
    _retry.content_exists_risk_memory_retry_attempted = True
    if not _strip_l1_memory_from_api_messages(agent, api_messages):
        logger.info(
            "%sContent Exists Risk recovery: no L1 MEMORY.md block in the payload; surfacing original error.",
            agent.log_prefix,
        )
        return False
    from agent.turn_recovery import _vlines

    _vlines(
        agent,
        "⚠️  Provider content filter rejected L1 MEMORY.md — omitted it for this request and retrying...",
    )
    logger.warning(
        "%sContent Exists Risk recovery: omitted L1 MEMORY.md from the retry payload",
        agent.log_prefix,
    )
    return True
