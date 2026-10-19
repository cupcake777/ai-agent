"""LOCAL sessions must not collide with the unified messaging DM route."""

from gateway.config import Platform
from gateway.session import SessionSource, build_session_key


def _cron_source(session_id="local-fire-1"):
    return SessionSource(
        platform=Platform.LOCAL,
        chat_id=session_id,
        chat_type="dm",
        user_id="cron-owner",
    )


def test_cron_local_dm_keeps_a_per_execution_route(monkeypatch):
    monkeypatch.setenv("UNIFIED_DM_SESSION", "true")

    key = build_session_key(_cron_source())

    assert key == "agent:main:local:dm:local-fire-1"
    assert key != "agent:main:unified:dm"


def test_interactive_local_dm_is_separate_from_messaging(monkeypatch):
    monkeypatch.setenv("UNIFIED_DM_SESSION", "true")
    source = SessionSource(
        platform=Platform.LOCAL,
        chat_id="interactive-cli",
        chat_type="dm",
        user_id="human",
    )

    assert build_session_key(source) == "agent:main:local:dm:interactive-cli"


def test_telegram_and_weixin_still_share_the_unified_route(monkeypatch):
    monkeypatch.setenv("UNIFIED_DM_SESSION", "true")
    telegram = SessionSource(
        platform=Platform.TELEGRAM, chat_id="telegram-user", chat_type="dm", user_id="human"
    )
    weixin = SessionSource(
        platform=Platform.WEIXIN, chat_id="weixin-user", chat_type="dm", user_id="human"
    )

    assert build_session_key(telegram) == "agent:main:unified:dm"
    assert build_session_key(weixin) == "agent:main:unified:dm"
