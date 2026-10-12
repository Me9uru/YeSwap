"""Exchange rates."""

from app.services.lifecycle import wait, stoppable_worker, WorkerStopped
from utils.paths import DATA_DIR
import json
from typing import Optional
from app.runtime.state import log
from app.configuration.repository import load_app_config_validated


_EXCHANGE_RATE_FILE = DATA_DIR / "exchange_rate.json"


def _fetch_exchange_rates(base: str = "CNY", targets: Optional[list] = None) -> Optional[dict]:
    try:
        import requests
        from utils.proxy_manager import get_proxy_manager

        pm = get_proxy_manager()
        proxies = pm.get_proxies_for_request()
        url = f"https://open.er-api.com/v6/latest/{base}"
        from app.runtime.state import log

        if proxies:
            log(
                f"exchange_rate: 正在使用代理 {proxies.get('http')} 访问: {url}",
                "debug",
                category="exchange_rate",
            )
        r = requests.get(url, timeout=10, proxies=proxies)
        data = r.json()
        if data.get("result") != "success":
            return None
        all_rates = data.get("rates") or {}
        if not targets:
            return {}
        out = {}
        for code in targets:
            if code not in all_rates:
                continue
            rate_val = all_rates[code]
            if not rate_val:
                continue
            out[code] = 1.0 / float(rate_val)
        return out or None
    except WorkerStopped:
        raise
    except Exception:
        return None


def _save_exchange_rates(rates: dict, base: str = "CNY") -> None:
    try:
        from datetime import datetime, timezone

        _EXCHANGE_RATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "base": base,
            "rates": rates,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(_EXCHANGE_RATE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except WorkerStopped:
        raise
    except Exception:
        return


@stoppable_worker
def exchange_rate_worker() -> None:
    while True:
        try:
            cfg = load_app_config_validated()
            sys_cfg = cfg.get("system") or {}
            interval_h = float(sys_cfg.get("exchange_rate_refresh_hours") or 0)
            if interval_h <= 0:
                log(
                    "exchange_rate: 已关闭, system.exchange_rate_refresh_hours<=0",
                    "debug",
                    category="exchange_rate",
                )
                wait(3600)
                continue
            targets = [
                "USD",
                "INR",
                "RUB",
                "HKD",
                "EUR",
                "KZT",
                "UAH",
                "PKR",
                "TRY",
                "ARS",
                "AZN",
                "VND",
                "IDR",
                "BRL",
                "CLP",
                "JPY",
                "PHP",
            ]
            log(
                f"exchange_rate: 开始获取, 间隔={interval_h} 小时, 目标={','.join(targets)}",
                "debug",
                category="exchange_rate",
            )
            rates = _fetch_exchange_rates("CNY", targets)
            if rates is not None:
                _save_exchange_rates(rates, "CNY")
                preview = ", ".join(f"{k}={v:.4f}" for k, v in rates.items())
                log(
                    f"exchange_rate: 已更新 {len(rates)} 个币种: {preview}",
                    "debug",
                    category="exchange_rate",
                )
            else:
                log("exchange_rate: 获取失败或无有效结果", "error", category="exchange_rate")
            wait(max(1, int(interval_h * 3600)))
        except WorkerStopped:
            raise
        except Exception as e:
            log(
                f"exchange_rate: worker 异常 {type(e).__name__}: {e}, 5 分钟后重试",
                "error",
                category="exchange_rate",
            )
            wait(300)
