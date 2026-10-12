import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _NoProxyManager:
    def get_proxies_for_request(self, failed=False):
        return None

    def is_proxy_enabled(self):
        return False


def _reset_rate_limit(monkeypatch, module, *, now=100.0):
    monkeypatch.setattr(module.time, "monotonic", lambda: now)
    monkeypatch.setattr(module, "_history_rate_limit_until", 0.0)
    monkeypatch.setattr(module, "_history_rate_limit_streak", 0)


def test_pricehistory_429_starts_cooldown_and_skips_following_requests(monkeypatch):
    from app.services import steam_client
    import utils.proxy_manager as proxy_manager

    _reset_rate_limit(monkeypatch, steam_client)
    monkeypatch.setattr(steam_client, "get_steam_credentials", lambda: {})
    monkeypatch.setattr(steam_client, "load_app_config_validated", lambda: {"steam": {}})
    monkeypatch.setattr(proxy_manager, "get_proxy_manager", lambda: _NoProxyManager())
    calls = []

    def limited(*args, **kwargs):
        calls.append((args, kwargs))
        return None, 429, None

    monkeypatch.setattr(steam_client, "_fetch_history_with_meta", limited)
    client = steam_client.SteamClient()

    assert client.fetch_history("AK-47 | Test") is None
    assert steam_client._history_rate_limit_until == 130.0
    assert client.fetch_history("M4A1-S | Test") is None
    assert len(calls) == 1


def test_pricehistory_429_adopts_retry_after_and_success_resets_backoff(monkeypatch):
    from app.services import steam_client
    import utils.proxy_manager as proxy_manager

    _reset_rate_limit(monkeypatch, steam_client)
    monkeypatch.setattr(steam_client, "get_steam_credentials", lambda: {})
    monkeypatch.setattr(steam_client, "load_app_config_validated", lambda: {"steam": {}})
    monkeypatch.setattr(proxy_manager, "get_proxy_manager", lambda: _NoProxyManager())

    now = {"value": 100.0}
    monkeypatch.setattr(steam_client.time, "monotonic", lambda: now["value"])
    responses = []
    fetched = []

    def fake_fetch(market_hash_name, **_kwargs):
        fetched.append(market_hash_name)
        return responses.pop(0)

    monkeypatch.setattr(steam_client, "_fetch_history_with_meta", fake_fetch)
    client = steam_client.SteamClient()

    # 429 携带 Retry-After=90：冷却按 90 秒执行，而不是基础退避的 30 秒。
    responses.append((None, 429, 90.0))
    assert client.fetch_history("item-429") is None
    now["value"] = 185.0
    assert client.fetch_history("item-cooling") is None
    assert fetched == ["item-429"]

    # 冷却结束后请求成功，成功必须把退避清零。
    now["value"] = 191.0
    responses.append(([{"price": "1.00"}], 200, None))
    assert client.fetch_history("item-ok") == [{"price": "1.00"}]

    # 于是下一次 429 重新从基础退避开始（30 秒），而不是翻倍成 60 秒。
    responses.append((None, 429, None))
    assert client.fetch_history("item-429-again") is None
    now["value"] = 222.0
    responses.append(([{"price": "2.00"}], 200, None))
    assert client.fetch_history("item-after") == [{"price": "2.00"}]
