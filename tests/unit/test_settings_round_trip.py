"""Unit tests for settings load/save round-trip (task 2.4)."""
from __future__ import annotations
import json
import pytest
from pathlib import Path
from pa_agent.config.settings import Settings, load_settings, save_settings


@pytest.fixture()
def fake_secret_store(monkeypatch):
    """Replace SecretStore with a trivial ROT0 (identity) cipher for tests."""
    import pa_agent.config.settings as settings_mod
    import pa_agent.security.secret_store as ss_mod

    class _FakeStore:
        @staticmethod
        def encrypt(s: str) -> str:
            return f"ENC:{s[::-1]}"

        @staticmethod
        def decrypt(s: str) -> str:
            if not s.startswith("ENC:"):
                raise ValueError("bad ciphertext")
            return s[4:][::-1]

    monkeypatch.setattr(ss_mod, "SecretStore", _FakeStore)
    return _FakeStore


def test_defaults(tmp_path, fake_secret_store):
    """load_settings on a missing file returns defaults and creates the file."""
    p = tmp_path / "settings.json"
    s = load_settings(p)
    assert s.provider.model == "claude-sonnet-4-6"
    assert s.provider.base_url == "https://www.packyapi.com/v1"
    assert s.provider.thinking is True
    assert s.provider.reasoning_effort == "max"
    assert s.provider.context_window == 2_000_000
    assert s.general.default_bar_count == 100
    assert s.general.data_source_kind == "akshare_a_share"
    assert s.general.refresh_interval_ms == 5000
    assert s.general.last_symbol == "600519"
    assert s.general.last_timeframe == "15m"
    assert s.general.market_data_use_proxy is False
    assert s.general.market_data_timeout_sec == 8.0
    assert s.general.market_data_retry == 1
    assert s.general.decision_stance == "balanced"
    assert s.general.decision_flow_auto_play is True
    assert p.exists(), "defaults should be written to disk"


def test_round_trip(tmp_path, fake_secret_store):
    """save → load preserves all fields."""
    p = tmp_path / "settings.json"
    original = Settings()
    original.provider.api_key = "sk-test-1234"
    original.general.last_symbol = "BTCUSDT"
    save_settings(original, p)
    loaded = load_settings(p)
    assert loaded.provider.api_key == "sk-test-1234"
    assert loaded.general.last_symbol == "BTCUSDT"
    assert loaded.provider.model == original.provider.model


def test_no_plaintext_key_on_disk(tmp_path, fake_secret_store):
    """The saved JSON must not contain the plaintext API key."""
    p = tmp_path / "settings.json"
    s = Settings()
    s.provider.api_key = "sk-super-secret-key"
    save_settings(s, p)
    raw = p.read_text(encoding="utf-8")
    assert "sk-super-secret-key" not in raw, "plaintext key must not appear on disk"
    assert "api_key_encrypted" in raw, "encrypted key must be present"
    assert "ENC:yek-terces-repus-ks" in raw, "fake-encrypted value must be present"


def test_corrupt_json_returns_defaults(tmp_path, fake_secret_store):
    """Corrupt settings.json falls back to defaults without raising."""
    p = tmp_path / "settings.json"
    p.write_text("{not valid json", encoding="utf-8")
    s = load_settings(p)
    assert s.provider.model == "claude-sonnet-4-6"


def test_missing_encrypted_key_leaves_api_key_blank(tmp_path, fake_secret_store):
    """If api_key_encrypted is absent, api_key stays empty string."""
    p = tmp_path / "settings.json"
    data = Settings().model_dump()
    data["provider"].pop("api_key_encrypted", None)
    data["provider"].pop("api_key", None)
    p.write_text(json.dumps(data), encoding="utf-8")
    s = load_settings(p)
    assert s.provider.api_key == ""
