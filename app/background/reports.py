"""Reports."""

from app.services.lifecycle import wait, stoppable_worker, WorkerStopped
from utils.paths import DATA_DIR
import json
from typing import Optional
from app.runtime.state import get_purchases, is_steam_background_allowed
from app.configuration.repository import load_app_config_validated
from app.notifications.service import send_pushplus, build_holdings_report_content, compute_holdings_stats
from app.services.holdings import (
    enrich_purchases_with_current_prices as _enrich_purchases_with_current_prices,
)


_HOLDINGS_REPORT_LAST_FILE = DATA_DIR / "holdings_report_last.json"


_HOLDINGS_REPORT_WAIT_INTERVAL = 60


_HOLDINGS_REPORT_WAIT_MAX = 30 * 60


def _load_last_pl_pct() -> Optional[float]:
    try:
        if _HOLDINGS_REPORT_LAST_FILE.exists():
            with open(_HOLDINGS_REPORT_LAST_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                v = data.get("pl_pct")
                return float(v) if v is not None else None
    except WorkerStopped:
        raise
    except Exception:
        pass
    return None


def _save_last_pl_pct(pl_pct: float) -> None:
    try:
        _HOLDINGS_REPORT_LAST_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_HOLDINGS_REPORT_LAST_FILE, "w", encoding="utf-8") as f:
            json.dump({"pl_pct": pl_pct}, f)
    except WorkerStopped:
        raise
    except Exception:
        pass


def run_holdings_report_once(force: bool = False) -> bool:
    if not force and not is_steam_background_allowed():
        return False
    cfg = load_app_config_validated()
    notify_cfg = cfg.get("notify") or {}
    token = (notify_cfg.get("pushplus_token") or "").strip()
    if not token:
        return False
    resell_ratio = float(cfg.get("pipeline", {}).get("resell_ratio", 0.85))
    if resell_ratio <= 0:
        resell_ratio = 0.85

    def _build_out():
        purchases = get_purchases()
        out = []
        for i, p in enumerate(purchases):
            row = {
                "type": "purchase",
                "idx": i,
                "name": p.get("name", ""),
                "price": float(p.get("price", 0)),
                "market_price": p.get("market_price"),
                "sale_price": p.get("sale_price"),
            }
            if row["market_price"] is not None:
                row["market_price"] = round(float(row["market_price"]), 2)
            out.append(row)
        return out, purchases

    def _enriched_holdings(out):
        _enrich_purchases_with_current_prices(out)
        return [
            t
            for t in out
            if t.get("type") == "purchase"
            and (t.get("sale_price") is None or float(t.get("sale_price", 0) or 0) <= 0)
        ]

    out, purchases = _build_out()
    holdings = [
        p
        for p in purchases
        if not (p.get("sale_price") is not None and float(p.get("sale_price", 0) or 0) > 0)
    ]
    if not holdings:
        return False
    holdings_enriched = _enriched_holdings(out)
    if not holdings_enriched:
        return False
    all_have_price = all(t.get("current_market_price") is not None for t in holdings_enriched)
    if not all_have_price:
        return False
    _, total_mp, _, _, pl_pct, _ = compute_holdings_stats(holdings_enriched, resell_ratio)
    if not force:
        drop_threshold_pct = float(notify_cfg.get("holdings_report_change_threshold_pct", 20) or 20)
        last_pl_pct = _load_last_pl_pct()
        if last_pl_pct is None:
            if pl_pct is not None:
                _save_last_pl_pct(pl_pct)
            return False
        if pl_pct is None:
            return False
        drop = last_pl_pct - pl_pct
        if drop < drop_threshold_pct:
            return False
    content = build_holdings_report_content(holdings_enriched, resell_ratio)
    ok = send_pushplus(token, "持有饰品紧急回报" if not force else "持有饰品定时回报", content)
    if ok and pl_pct is not None:
        _save_last_pl_pct(pl_pct)
    return ok


@stoppable_worker
def holdings_report_worker() -> None:
    """定时回报 worker（holdings_report_interval_hours > 0 才运行）.
    定时回报不受跌幅限制，每隔设定小时强制推送一次。
    紧急回报另由 run_holdings_report_once(force=False) 负责（在有新市场价时自动触发）。
    """
    first_run = True
    while True:
        try:
            cfg = load_app_config_validated()
            n = cfg.get("notify") or {}
            interval_h = int(n.get("holdings_report_interval_hours", 0) or 0)
            if interval_h <= 0:
                first_run = True
                wait(3600)
                continue
            if first_run:
                first_run = False
                wait(60)
            else:
                wait(interval_h * 3600)
            while not is_steam_background_allowed():
                wait(60)
            run_holdings_report_once(force=True)
        except WorkerStopped:
            raise
        except Exception:
            wait(60)
