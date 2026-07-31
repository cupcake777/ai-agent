"""xAI Web Search — plugin form.

Routes ``web_search`` tool calls through xAI's agentic Web Search tool
(server-side ``web_search`` on the Responses API). Grok runs the actual
searching and page-browsing server-side; we ask it to return the top
results as structured JSON so we can hand back the same
``{title, url, description, position}`` rows every other Hermes web
provider produces.

Reference: https://docs.x.ai/developers/tools/web-search

Config keys this provider responds to::

    web:
      search_backend: "xai"           # explicit per-capability
      backend: "xai"                  # shared fallback

Optional knobs (under ``web.xai`` in ``config.yaml``)::

    web:
      xai:
        mode: "responses"          # official xAI Responses web_search
        model: "grok-build-0.1"    # reasoning model required by web_search
        provider: "custom:grok_self" # used by chat_completions mode
        allowed_domains: ["x.ai"]     # max 5 — mutually exclusive with excluded_domains
        excluded_domains: ["bad.com"] # max 5 — mutually exclusive with allowed_domains
        timeout: 90                   # seconds (default 90)

``chat_completions`` mode posts to the configured custom provider's
OpenAI-compatible ``/chat/completions`` endpoint. It is intended for relays
where the selected Grok model performs current, source-linked retrieval in a
normal chat response; it does not claim to provide xAI's native
``web_search_call`` events.

Auth: official ``responses`` mode reuses :func:`tools.xai_http.resolve_xai_http_credentials`, which
prefers Hermes-managed xAI Grok OAuth (via ``hermes auth``) and falls back
 to ``XAI_API_KEY`` (resolved through ``~/.hermes/.env``, then
``os.environ``). ``chat_completions`` mode resolves the named ``custom:*``
credential pool instead.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from agent.web_search_provider import WebSearchProvider
from tools.xai_http import (
    has_xai_credentials,
    hermes_xai_user_agent,
    resolve_xai_http_credentials,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "grok-build-0.1"
DEFAULT_TIMEOUT = 90
DEFAULT_CUSTOM_PROVIDER = "custom:grok_self"
_MAX_DOMAIN_FILTERS = 5  # xAI hard cap on allowed_domains / excluded_domains

# Match the JSON object Grok is asked to emit. Tolerates leading/trailing
# prose since reasoning models occasionally narrate before the JSON block
# even when explicitly asked not to.
_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)
_URL_RE = re.compile(r"https?://[^\s<>\]\)\"']+")


def _configured_mode(cfg: Dict[str, Any]) -> str:
    """Return the configured xAI search mode, defaulting to official xAI."""
    mode = cfg.get("mode") if isinstance(cfg.get("mode"), str) else "responses"
    return mode.strip().lower() or "responses"


def _resolve_custom_chat_credentials(provider_name: str) -> Dict[str, str]:
    """Resolve a named OpenAI-compatible custom endpoint from its credential pool."""
    pool_key = str(provider_name or DEFAULT_CUSTOM_PROVIDER).strip().lower()
    if not pool_key.startswith("custom:"):
        pool_key = f"custom:{pool_key}"
    try:
        from agent.credential_pool import load_pool

        pool = load_pool(pool_key)
        entry = pool.select() if pool is not None else None
        if entry is None:
            return {"provider": pool_key, "api_key": "", "base_url": ""}
        return {
            "provider": pool_key,
            "api_key": str(entry.runtime_api_key or "").strip(),
            "base_url": str(entry.runtime_base_url or "").strip().rstrip("/"),
        }
    except Exception as exc:  # noqa: BLE001 — provider errors become tool errors
        logger.warning("Could not resolve custom web-search credentials: %s", exc)
        return {"provider": pool_key, "api_key": "", "base_url": ""}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _load_xai_web_config() -> Dict[str, Any]:
    """Read ``web.xai`` from config.yaml (returns {} on miss)."""
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        web_section = cfg.get("web") if isinstance(cfg, dict) else None
        xai_section = web_section.get("xai") if isinstance(web_section, dict) else None
        return xai_section if isinstance(xai_section, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not load web.xai config: %s", exc)
        return {}


def _coerce_domain_list(value: Any) -> List[str]:
    """Coerce a config value to a clean list of <=5 domain strings."""
    if not isinstance(value, list):
        return []
    cleaned: List[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            cleaned.append(item.strip())
        if len(cleaned) >= _MAX_DOMAIN_FILTERS:
            break
    return cleaned


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class XAIWebSearchProvider(WebSearchProvider):
    """Search-only provider backed by xAI's agentic Web Search tool.

    Sends a structured prompt to Grok with ``tools=[{"type": "web_search"}]``
    enabled and asks it to return the top *limit* results as JSON. Falls
    back to the Responses API ``citations`` list if Grok ignores the JSON
    schema instruction (rare for grok-4.3 but cheap insurance).

    No extract capability — pair with Firecrawl / Tavily / Exa for
    ``web_extract`` if you need page content.

    Trust model
    -----------
    Unlike index-backed providers (Brave / Tavily / Exa) which return
    verbatim search-engine results, this backend is an LLM in a trench
    coat: Grok decides which URLs to surface, generates the titles and
    descriptions itself, and is influenced by the *content of the query*.
    A maliciously crafted query (e.g. injected via untrusted upstream
    input the agent picked up) can in principle steer Grok into emitting
    attacker-chosen URLs. Callers that pipe untrusted text directly into
    ``web_search`` should treat returned URLs the same way they would
    treat any model-generated link — validate before fetching.
    """

    @property
    def name(self) -> str:
        return "xai"

    @property
    def display_name(self) -> str:
        return "xAI Web Search (Grok)"

    def is_available(self) -> bool:
        """Cheap availability probe — env var OR auth-store has OAuth tokens,
        OR custom provider pool has credentials for chat_completions mode.

        Delegates to :func:`tools.xai_http.has_xai_credentials` for the
        default ``responses`` mode.  In ``chat_completions`` mode, checks
        the custom credential pool instead.  Must never trigger OAuth refresh
        or acquire the auth-store lock — the ABC contract requires this
        method to be safe to call on every ``hermes tools`` repaint and at
        tool-registration time.
        """
        cfg = _load_xai_web_config()
        mode = _configured_mode(cfg)
        if mode == "chat_completions":
            provider_name = cfg.get("provider") or DEFAULT_CUSTOM_PROVIDER
            creds = _resolve_custom_chat_credentials(provider_name)
            return bool(creds.get("api_key") and creds.get("base_url"))
        return has_xai_credentials()

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return False

    # -- Search -----------------------------------------------------------

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        """Execute a Grok-backed web search.

        Returns ``{"success": True, "data": {"web": [{title, url, description, position}, ...]}}``
        on success, ``{"success": False, "error": str}`` on failure.
        """
        try:
            from tools.interrupt import is_interrupted

            if is_interrupted():
                return {"success": False, "error": "Interrupted"}
        except Exception:  # noqa: BLE001 — interrupt module is best-effort
            pass

        # Clamp limit to the same range the caller (web_search_tool) accepts,
        # so we don't silently downgrade explicit limits. Grok happily
        # produces longer lists; cost scales linearly with the requested
        # count via reasoning tokens, but that's the caller's call to make.
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 5
        limit = max(1, min(limit, 100))

        cfg = _load_xai_web_config()
        model = cfg.get("model") if isinstance(cfg.get("model"), str) else DEFAULT_MODEL
        model = model.strip() or DEFAULT_MODEL

        # Route based on configured mode. Custom chat-completions mode must
        # not require official xAI credentials.
        mode = _configured_mode(cfg)
        if mode == "chat_completions":
            return self._chat_completions_search(query, limit, cfg, model)

        creds = resolve_xai_http_credentials()
        api_key = str(creds.get("api_key") or "").strip()
        base_url = str(creds.get("base_url") or "https://api.x.ai/v1").strip().rstrip("/")
        if not api_key:
            return {
                "success": False,
                "error": (
                    "No xAI credentials found. Run `hermes auth` to sign in with "
                    "xAI Grok OAuth, or set XAI_API_KEY."
                ),
            }

        try:
            timeout = float(cfg.get("timeout", DEFAULT_TIMEOUT))
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT

        allowed = _coerce_domain_list(cfg.get("allowed_domains"))
        excluded = _coerce_domain_list(cfg.get("excluded_domains"))
        if allowed and excluded:
            # xAI explicitly rejects this combo — surface a clear error
            # rather than a 400 from the API.
            return {
                "success": False,
                "error": (
                    "web.xai.allowed_domains and web.xai.excluded_domains "
                    "cannot both be set (xAI restriction)."
                ),
            }

        web_search_tool: Dict[str, Any] = {"type": "web_search"}
        if allowed:
            web_search_tool["filters"] = {"allowed_domains": allowed}
        elif excluded:
            web_search_tool["filters"] = {"excluded_domains": excluded}

        prompt = self._build_prompt(query, limit)

        payload: Dict[str, Any] = {
            "model": model,
            "input": [{"role": "user", "content": prompt}],
            "tools": [web_search_tool],
            # Drop inline citation markdown — we want the JSON block clean,
            # and we read URLs from annotations / citations separately.
            "include": ["no_inline_citations"],
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": hermes_xai_user_agent(),
        }

        try:
            import httpx
        except ImportError:
            return {
                "success": False,
                "error": "httpx is not installed (required for xAI web search)",
            }

        logger.info(
            "xAI web search via %s: '%s' (limit=%d, model=%s)",
            base_url, query, limit, model,
        )

        # Two-attempt loop: if the first call returns 401 and our creds came
        # from the OAuth path, force-refresh the token once and retry. This
        # closes two gaps the proactive resolver check doesn't cover:
        # (1) opaque (non-JWT) access tokens — `_xai_access_token_is_expiring`
        #     can't decode them and returns False, so refresh never fires
        #     until the server hands us a 401.
        # (2) mid-window revocation — admin revoke, refresh-token rotation,
        #     or clock skew can produce 401s on a token whose JWT `exp` claim
        #     is still in the future.
        # Env-var (`XAI_API_KEY`) credentials skip the retry entirely — we
        # can't refresh those and an immediate retry would just burn quota.
        is_oauth_path = (creds.get("provider") == "xai-oauth")
        resp = None
        for attempt in range(2):
            try:
                resp = httpx.post(
                    f"{base_url}/responses",
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                )
                resp.raise_for_status()
                break
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                if status == 401 and attempt == 0 and is_oauth_path:
                    logger.info(
                        "xAI web search got 401 on first attempt; forcing OAuth "
                        "refresh and retrying once.",
                    )
                    try:
                        refreshed = resolve_xai_http_credentials(
                            force_refresh=True,
                            api_key_hint=api_key,
                        )
                        refreshed_key = str(refreshed.get("api_key") or "").strip()
                        if refreshed_key and refreshed_key != api_key:
                            api_key = refreshed_key
                            headers["Authorization"] = f"Bearer {api_key}"
                            continue
                        # Refresh returned the same (or empty) token — no point
                        # in retrying. Fall through to the error return below.
                    except Exception as refresh_exc:  # noqa: BLE001
                        logger.warning(
                            "xAI web search OAuth refresh after 401 failed: %s",
                            refresh_exc,
                        )
                body = ""
                try:
                    body = exc.response.text[:300] if exc.response is not None else ""
                except Exception:
                    body = ""
                logger.warning("xAI web search HTTP %d: %s", status, body)
                return {
                    "success": False,
                    "error": f"xAI web search returned HTTP {status}: {body}".rstrip(),
                }
            except httpx.RequestError as exc:
                logger.warning("xAI web search request error: %s", exc)
                return {"success": False, "error": f"Could not reach xAI: {exc}"}

        if resp is None:
            # Defensive — both attempts somehow exited the loop without resp.
            return {"success": False, "error": "xAI web search produced no response"}

        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("xAI web search bad JSON: %s", exc)
            return {
                "success": False,
                "error": "Could not parse xAI Responses API reply as JSON",
            }

        # xAI's Responses surface sometimes returns HTTP 200 with an error
        # envelope (model overloaded, content-policy refusal, etc.). Without
        # this check, ``_extract_results`` would silently produce an empty
        # list and we'd report success-with-no-rows — masking a real failure
        # the agent should see and decide whether to retry.
        api_error = data.get("error") if isinstance(data, dict) else None
        if isinstance(api_error, dict):
            err_msg = (
                api_error.get("message")
                or api_error.get("code")
                or "unknown error"
            )
            logger.warning("xAI web search returned error envelope: %s", err_msg)
            return {"success": False, "error": f"xAI returned an error: {err_msg}"}

        web_results = self._extract_results(data, limit=limit)
        if not web_results:
            # Successful call, just no usable rows — return success with an
            # empty list so the model can decide whether to retry. Matches
            # what brave-free / exa do when the upstream API returns 0 hits.
            return {"success": True, "data": {"web": []}}

        return {"success": True, "data": {"web": web_results}}

    def _chat_completions_search(
        self, query: str, limit: int, cfg: Dict[str, Any], model: str,
    ) -> Dict[str, Any]:
        """Search via a custom OpenAI-compatible chat completions endpoint.

        Posts a prompt that asks the model to act as a search engine, then
        extracts URLs and descriptions from the free-form answer.  This is
        intended for Grok relays whose model already provides current,
        encyclopedia-like responses with source links — it does not invoke
        the native xAI ``web_search`` tool.
        """
        provider_name = cfg.get("provider") or DEFAULT_CUSTOM_PROVIDER
        creds = _resolve_custom_chat_credentials(provider_name)
        api_key = creds.get("api_key", "")
        base_url = creds.get("base_url", "")
        if not api_key or not base_url:
            return {
                "success": False,
                "error": (
                    f"No credentials found for {provider_name}. "
                    "Check web.xai.provider in config.yaml."
                ),
            }

        try:
            timeout = float(cfg.get("timeout", DEFAULT_TIMEOUT))
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT

        prompt = (
            "You are a web search engine. Find current information for the "
            f"following query and respond with a concise answer that includes "
            f"relevant source URLs. Return at most {limit} sources, each with "
            f"a short description. Format: for each source, include the URL "
            f"and a 1-2 sentence summary of what it says. Always include the "
            f"full https:// URL. If no usable results exist, say so.\n\n"
            f"Query: {query}"
        )

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 2048,
            # Prefer a normal JSON response; some relays ignore this and
            # force SSE, which _chat_completion_content also supports.
            "stream": False,
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "hermes-agent/1.0",
        }

        try:
            import httpx
        except ImportError:
            return {
                "success": False,
                "error": "httpx is not installed (required for custom web search)",
            }

        logger.info(
            "Custom chat-completions web search via %s: '%s' (limit=%d, model=%s, provider=%s)",
            base_url, query, limit, model, provider_name,
        )

        try:
            resp = httpx.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=timeout,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            body = ""
            try:
                body = exc.response.text[:300] if exc.response is not None else ""
            except Exception:
                body = ""
            logger.warning("Custom web search HTTP %d: %s", status, body)
            return {
                "success": False,
                "error": f"Custom web search returned HTTP {status}: {body}".rstrip(),
            }
        except httpx.RequestError as exc:
            logger.warning("Custom web search request error: %s", exc)
            return {"success": False, "error": f"Could not reach endpoint: {exc}"}

        content = self._chat_completion_content(resp)
        if not content:
            return {"success": True, "data": {"web": []}}

        web_results = self._chat_results_from_text(content, limit=limit)
        if not web_results:
            return {"success": True, "data": {"web": []}}
        return {"success": True, "data": {"web": web_results}}

    @staticmethod
    def _chat_completion_content(resp: Any) -> str:
        """Extract assistant text from JSON or forced SSE chat responses."""
        content_type = str(getattr(resp, "headers", {}).get("content-type", "")).lower()
        raw = getattr(resp, "content", b"")

        # Normal OpenAI-compatible response: one JSON object.
        if "text/event-stream" not in content_type:
            try:
                data = resp.json()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Custom web search bad JSON: %s", exc)
                return ""
            choices = data.get("choices") if isinstance(data, dict) else None
            if not isinstance(choices, list) or not choices:
                return ""
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            content = message.get("content") if isinstance(message, dict) else ""
            return content.strip() if isinstance(content, str) else ""

        # Some relays return SSE regardless of the request's stream flag.
        # Aggregate delta.content from each data line and ignore heartbeats.
        if isinstance(raw, bytes):
            text = raw.decode("utf-8", errors="replace")
        else:
            text = str(raw)
        parts: List[str] = []
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                chunk = json.loads(payload)
            except (json.JSONDecodeError, ValueError):
                continue
            choices = chunk.get("choices") if isinstance(chunk, dict) else None
            if not isinstance(choices, list) or not choices:
                continue
            delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
            piece = delta.get("content") if isinstance(delta, dict) else ""
            if isinstance(piece, str):
                parts.append(piece)
        return "".join(parts).strip()

    @staticmethod
    def _chat_results_from_text(text: str, *, limit: int) -> List[Dict[str, Any]]:
        """Extract search results from a free-form chat-completions answer.

        Uses two strategies:
        1. Try to parse a JSON ``results`` array embedded in the text
           (same as the official Responses path).
        2. Fall back to extracting all https:// URLs from the text with
           surrounding context as descriptions.
        """
        # Primary: JSON object with a results array
        parsed = XAIWebSearchProvider._try_parse_json_results(text, limit=limit)
        if parsed:
            return parsed

        # Fallback: extract URLs and use surrounding lines as descriptions
        seen: set[str] = set()
        results: List[Dict[str, Any]] = []
        lines = text.splitlines()
        for idx, line in enumerate(lines):
            if len(results) >= limit:
                break
            urls = _URL_RE.findall(line)
            for url in urls:
                url = url.rstrip(".,;:!?\"')]>*")
                if not url or url in seen:
                    continue
                seen.add(url)
                # Use the line itself plus next line as description
                desc_parts = [line.strip()]
                if idx + 1 < len(lines):
                    next_line = lines[idx + 1].strip()
                    if next_line:
                        desc_parts.append(next_line)
                description = " ".join(desc_parts)
                if len(description) > 300:
                    description = description[:300] + "…"
                results.append({
                    "title": "",
                    "url": url,
                    "description": description,
                    "position": len(results) + 1,
                })
        return results

    # -- Prompt + parsing -------------------------------------------------

    @staticmethod
    def _build_prompt(query: str, limit: int) -> str:
        """Compose the prompt that asks Grok to act as a search engine.

        We deliberately ask for a JSON object (not bare array) so we can
        match it cheaply with ``_JSON_BLOCK_RE``; we explicitly forbid
        prose, markdown fences, and inline-citation links to keep the
        payload parseable.
        """
        return (
            "Use the web_search tool to find current information for the query below, "
            "then respond with ONLY a single JSON object — no prose, no markdown "
            "fences, no inline citation links — matching this exact schema:\n\n"
            '{"results": [{"title": "string", "url": "string", '
            '"description": "1-2 sentence summary"}]}\n\n'
            f'Return at most {limit} results, ordered by relevance, with absolute '
            "https:// URLs. If no usable results exist, return "
            '{"results": []}.\n\n'
            f"Query: {query}"
        )

    @classmethod
    def _extract_results(
        cls,
        response_data: Dict[str, Any],
        *,
        limit: int,
    ) -> List[Dict[str, Any]]:
        """Pull a ``[{title, url, description, position}, ...]`` list out of a
        Responses-API reply.

        Strategy:

        1. Walk ``output[*].content[*].text`` for ``output_text`` blocks and
           try to parse the first JSON object that has a ``results`` list.
        2. If the JSON path fails, fall back to the message annotations
           (``url_citation`` entries) — every annotation carries a URL and
           a ``title`` (citation number); we pair those URLs with surrounding
           text from the message body as a best-effort description.
        """
        text_blocks, annotations = cls._collect_output_text(response_data)

        # Primary path: parse the JSON object Grok was asked for.
        for block in text_blocks:
            parsed = cls._try_parse_json_results(block, limit=limit)
            if parsed:
                return parsed

        # Secondary path: derive results from message annotations + raw text.
        # Only short-circuit when annotations actually yielded usable rows;
        # otherwise fall through to the citations list. (xAI currently only
        # emits ``url_citation`` annotations, but future annotation types
        # would silently produce an empty result set if we returned here
        # unconditionally — masking real data in ``citations``.)
        if annotations:
            joined_text = "\n".join(text_blocks)
            annotation_results = cls._results_from_annotations(
                annotations, joined_text, limit=limit,
            )
            if annotation_results:
                return annotation_results

        # Last-ditch: raw citations list (no titles or descriptions).
        citations = response_data.get("citations") or []
        if isinstance(citations, list):
            return [
                {
                    "title": "",
                    "url": str(u),
                    "description": "",
                    "position": i + 1,
                }
                for i, u in enumerate(citations[:limit])
                if isinstance(u, str) and u.strip()
            ]

        return []

    @staticmethod
    def _collect_output_text(
        response_data: Dict[str, Any],
    ) -> tuple[List[str], List[Dict[str, Any]]]:
        """Return (text_blocks, annotations) extracted from ``response.output``."""
        text_blocks: List[str] = []
        annotations: List[Dict[str, Any]] = []
        output = response_data.get("output")
        if not isinstance(output, list):
            return text_blocks, annotations

        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for chunk in content:
                if not isinstance(chunk, dict) or chunk.get("type") != "output_text":
                    continue
                text = chunk.get("text")
                if isinstance(text, str) and text.strip():
                    text_blocks.append(text)
                chunk_annotations = chunk.get("annotations")
                if isinstance(chunk_annotations, list):
                    for ann in chunk_annotations:
                        if isinstance(ann, dict):
                            annotations.append(ann)
        return text_blocks, annotations

    @staticmethod
    def _try_parse_json_results(
        text: str,
        *,
        limit: int,
    ) -> Optional[List[Dict[str, Any]]]:
        """Parse a JSON object with a ``results`` array out of ``text``.

        Returns the normalized result list on success, ``None`` when the
        block has no valid JSON object or no ``results`` key. Tolerates
        leading/trailing prose because reasoning models sometimes prefix a
        short narration even when told not to.
        """
        # Try the whole string first — cheapest path when Grok obeys.
        candidates = [text]
        match = _JSON_BLOCK_RE.search(text)
        if match and match.group(0) != text:
            candidates.append(match.group(0))

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(parsed, dict):
                continue
            results = parsed.get("results")
            if not isinstance(results, list):
                continue
            normalized: List[Dict[str, Any]] = []
            for row in results[:limit]:
                if not isinstance(row, dict):
                    continue
                url = str(row.get("url", "")).strip()
                if not url:
                    continue
                normalized.append(
                    {
                        "title": str(row.get("title", "")).strip(),
                        "url": url,
                        "description": str(row.get("description", "")).strip(),
                        # Renumber from the kept results, not the raw input
                        # index, so a dropped malformed row doesn't leave a
                        # gap in the positions handed back to the agent.
                        "position": len(normalized) + 1,
                    }
                )
            if normalized:
                return normalized
        return None

    @staticmethod
    def _results_from_annotations(
        annotations: List[Dict[str, Any]],
        joined_text: str,
        *,
        limit: int,
    ) -> List[Dict[str, Any]]:
        """Best-effort fallback when JSON parsing fails.

        Uses each ``url_citation`` annotation's ``url`` (the citation
        title is just the integer label, so we don't surface it) and
        slices ~200 characters of surrounding text as the description.
        """
        seen: set[str] = set()
        results: List[Dict[str, Any]] = []
        for ann in annotations:
            if ann.get("type") != "url_citation":
                continue
            url = str(ann.get("url", "")).strip()
            if not url or url in seen:
                continue
            seen.add(url)

            description = ""
            start = ann.get("start_index")
            end = ann.get("end_index")
            if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(joined_text):
                window_start = max(0, start - 200)
                description = joined_text[window_start:start].strip()
                if len(description) > 200:
                    description = description[-200:].strip()

            results.append(
                {
                    "title": "",
                    "url": url,
                    "description": description,
                    "position": len(results) + 1,
                }
            )
            if len(results) >= limit:
                break
        return results

    # -- Setup picker -----------------------------------------------------

    def get_setup_schema(self) -> Dict[str, Any]:
        # Auth resolution is delegated to the shared ``xai_grok`` post_setup
        # hook (same one image_gen.xai and tts.xai use) so users see the
        # familiar OAuth-or-API-key prompt for every xAI service.
        return {
            "name": "xAI Web Search (Grok)",
            "badge": "paid",
            "tag": (
                "Agentic web search via Grok's web_search tool — uses xAI "
                "Grok OAuth or XAI_API_KEY."
            ),
            "env_vars": [],
            "post_setup": "xai_grok",
        }
