def test_code_map_config_defaults_to_disabled_and_validates_intervals(monkeypatch):
    from antisentinel.code_map.config import CodeMapConfig
    from antisentinel.domain.errors import InvalidInputError

    monkeypatch.delenv("ANTISENTINEL_CODE_MAP_ENABLED", raising=False)
    assert CodeMapConfig.from_environment().enabled is False

    try:
        CodeMapConfig(min_interval_seconds=10, max_interval_seconds=5)
    except InvalidInputError:
        pass
    else:
        raise AssertionError("invalid intervals must be rejected")


def test_code_map_module_entrypoint_is_callable():
    from antisentinel.code_map.__main__ import main

    assert callable(main)
