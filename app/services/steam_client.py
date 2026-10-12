import threading
import time
from typing import Dict, List, Optional, Union
from app.configuration.repository import get_steam_credentials, load_app_config_validated
from steam.client import (
    fetch_history_with_meta as _fetch_history_with_meta,
    market_hash_name_from_listing_url as _market_hash_name_from_listing_url,
)
steam_timeout = 15
steam_retry_attempts = 2
_history_cache: Dict[str, tuple] = {}
_history_cache_ttl = 300
_history_cache_max = 200
# Shared across all SteamClient instances: pricehistory is rate-limited by
# Steam per account/IP, so per-item retries must not keep hitting the endpoint.
_history_rate_limit_lock = threading.Lock()
_history_rate_limit_until = 0.0
_history_rate_limit_streak = 0
_history_rate_limit_base_seconds = 30.0
_history_rate_limit_max_seconds = 300.0


def _history_cooldown_remaining() -> float:
    with _history_rate_limit_lock:
        return max(0.0, _history_rate_limit_until - time.monotonic())


def _record_history_rate_limit(retry_after: Optional[float] = None) -> tuple[float, int]:
    global _history_rate_limit_streak, _history_rate_limit_until
    with _history_rate_limit_lock:
        _history_rate_limit_streak += 1
        backoff = min(
            _history_rate_limit_max_seconds,
            _history_rate_limit_base_seconds * (2 ** (_history_rate_limit_streak - 1)),
        )
        cooldown = max(backoff, retry_after or 0.0)
        _history_rate_limit_until = time.monotonic() + cooldown
        return cooldown, _history_rate_limit_streak


def _record_history_success() -> None:
    global _history_rate_limit_streak, _history_rate_limit_until
    with _history_rate_limit_lock:
        _history_rate_limit_streak = 0
        _history_rate_limit_until = 0.0
class SteamClient:
    def __init__(
        self,
        timeout_sec: int = steam_timeout,
        cache_ttl: int = _history_cache_ttl,
        cache_max: int = _history_cache_max,
    ) -> None:
        self._timeout = timeout_sec
        self._cache_ttl = cache_ttl
        self._cache_max = cache_max
    def fetch_history(
        self,
        market_hash_name: str,
        app_id: int = 730,
        return_currency: bool = False,
    ) -> Union[Optional[List], Optional[Dict]]:
        key = f"{market_hash_name}:{app_id}:{return_currency}"
        now = time.time()
        if key in _history_cache:
            data, ts = _history_cache[key]
            if now - ts < self._cache_ttl:
                return data
        result = self._fetch_history_impl(market_hash_name, app_id, return_currency)
        if result is not None:
            if len(_history_cache) >= self._cache_max:
                oldest = min(_history_cache.items(), key=lambda x: x[1][1])
                del _history_cache[oldest[0]]
            _history_cache[key] = (result, now)
        return result
    def _fetch_history_impl(
        self,
        market_hash_name: str,
        app_id: int,
        return_currency: bool,
    ) -> Union[Optional[List], Optional[Dict]]:
        cooldown_remaining = _history_cooldown_remaining()
        if cooldown_remaining > 0:
            from app.runtime.state import log
            log(
                f"[SteamClient] 历史价格接口限流冷却中，约 {int(cooldown_remaining + 0.999)} 秒后恢复；跳过请求",
                "warn",
                category="proxy",
            )
            return None
        cred = get_steam_credentials()
        cookies = (cred.get("cookies") or "").strip() or None
        cfg = load_app_config_validated()
        from utils.proxy_manager import get_proxy_manager
        pm = get_proxy_manager()
        for attempt in range(steam_retry_attempts):
            failed = (attempt > 0)
            proxies = pm.get_proxies_for_request(failed=failed)
            if proxies is None and not pm.is_proxy_enabled():
                proxy_url = cfg.get("steam", {}).get("proxy")
                proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
            result, status_code, retry_after = _fetch_history_with_meta(
                market_hash_name,
                app_id=app_id,
                timeout=self._timeout,
                return_currency=return_currency,
                cookies=cookies,
                proxies=proxies,
            )
            if result is not None:
                _record_history_success()
                return result
            from app.runtime.state import log
            if status_code == 429:
                cooldown, streak = _record_history_rate_limit(retry_after)
                log(
                    f"[SteamClient] 历史价格接口 HTTP 429，已启动指数退避冷却 {int(cooldown)} 秒 "
                    f"(连续限流 {streak} 次)；不再立即重试",
                    "warn",
                    category="proxy",
                )
                return None
            log(
                f"[SteamClient] 历史数据请求失败 (attempt={attempt+1}/{steam_retry_attempts}) "
                f"status={status_code} proxies={proxies}",
                "debug",
                category="proxy",
            )
            if attempt < steam_retry_attempts - 1:
                from app.services.delay import jittered_sleep
                jittered_sleep(1.0)
        return None
    @staticmethod
    def market_hash_name_from_listing_url(url: str) -> Optional[str]:
        return _market_hash_name_from_listing_url(url)
