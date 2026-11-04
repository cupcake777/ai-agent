from tools.environments import local


def test_gateway_foreground_command_uses_bounded_worker_scope(monkeypatch):
    from tools.process_registry import GatewayChildDispatch

    monkeypatch.setenv("_HERMES_GATEWAY", "1")
    monkeypatch.setenv("HERMES_SUPERVISED_CHILD", "1")
    monkeypatch.setattr(
        "tools.process_registry.restart_safe_gateway_child_argv",
        lambda argv, unit_suffix, **kwargs: GatewayChildDispatch(
            "scoped", ["scoped", unit_suffix, *argv]
        ),
    )
    monkeypatch.setattr(
        "tools.process_registry.systemd_user_bus_env",
        lambda env: {**env, "DBUS_SESSION_BUS_ADDRESS": "test-bus"},
    )

    argv, env, unit = local._isolate_gateway_foreground_command(
        ["/bin/bash", "-c", "true"], {"PATH": "/bin"}
    )

    assert argv[0] == "scoped"
    assert argv[1].startswith("terminal-")
    assert argv[-3:] == ["/bin/bash", "-c", "true"]
    assert env["DBUS_SESSION_BUS_ADDRESS"] == "test-bus"
    assert unit == f"hermes-worker-{argv[1]}.scope"


def test_non_gateway_foreground_command_stays_in_process(monkeypatch):
    monkeypatch.delenv("_HERMES_GATEWAY", raising=False)

    original = ["/bin/bash", "-c", "true"]
    argv, env, unit = local._isolate_gateway_foreground_command(original, {"PATH": "/bin"})

    assert argv == original
    assert env == {"PATH": "/bin"}
    assert unit is None