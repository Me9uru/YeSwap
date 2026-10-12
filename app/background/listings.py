"""Listings."""

from app.services.lifecycle import wait, stoppable_worker, WorkerStopped
import time
from app.runtime.state import (
    get_purchases,
    is_steam_background_allowed,
    log,
    set_inventory,
    update_purchase,
    update_purchase_by_id,
)
from app.configuration.repository import get_steam_credentials, load_app_config_validated
from app.steam_market.inventory import scan_cs2_inventory
from app.background.alerts import (
    _worker_alert,
)


@stoppable_worker
def listing_check_worker() -> None:
    from app.steam_market.listings import fetch_my_listings, fetch_my_history_sold

    while True:
        try:
            cfg = load_app_config_validated()
            interval = max(
                60, int(cfg.get("pipeline", {}).get("listing_check_interval_seconds", 600) or 600)
            )
            wait(interval)
            if not is_steam_background_allowed():
                continue
            purchases = get_purchases()
            listing_idx = [
                (i, p) for i, p in enumerate(purchases) if p.get("listing") and p.get("assetid")
            ]
            if not listing_idx:
                continue
            cred = get_steam_credentials()
            cookies = cred.get("cookies") or ""
            if not cookies:
                continue
            pipeline_cfg = cfg.get("pipeline") or {}
            steam_debug = bool(
                pipeline_cfg.get("steam_listings_debug") or pipeline_cfg.get("verbose_debug")
            )
            debug_fn = (lambda m: log(m, "debug", category="steam")) if steam_debug else None
            ok, active_ids, err, _ = fetch_my_listings(cookies, debug_fn=debug_fn)
            if not ok:
                continue
            not_in_active = [
                (i, p)
                for i, p in listing_idx
                if str(p.get("assetid") or "") and str(p.get("assetid") or "") not in active_ids
            ]
            if steam_debug and not_in_active:
                log(
                    f"[listing_check] 本地 {len(listing_idx)} 条在售, Steam 活跃 {len(active_ids)}, 可能已售 {len(not_in_active)} 条",
                    "debug",
                    category="steam",
                )
            if not not_in_active:
                continue
            ok2, sold_map, _ = fetch_my_history_sold(cookies, debug_fn=debug_fn)
            seen_aids = set()
            sold_updates = 0
            for _i, p in not_in_active:
                aid = str(p.get("assetid") or "")
                if not aid or aid in seen_aids:
                    continue
                seen_aids.add(aid)
                db_id = p.get("_db_id")
                sale_price_rounded = round(sold_map[aid], 2) if (ok2 and aid in sold_map) else None
                if db_id:
                    if sale_price_rounded is not None:
                        sold_at = time.time()
                        update_purchase_by_id(
                            db_id,
                            {
                                "sale_price": sale_price_rounded,
                                "sold_at": sold_at,
                                "listing": False,
                                "listing_status": None,
                            },
                        )
                        sold_updates += 1
                    else:
                        update_purchase_by_id(db_id, {"listing": False, "listing_status": "error"})
                else:
                    current = get_purchases()
                    matched = [
                        j for j, q in enumerate(current) if str(q.get("assetid") or "") == aid
                    ]
                    if sale_price_rounded is not None:
                        sold_at = time.time()
                        for idx in matched:
                            update_purchase(
                                idx,
                                {
                                    "sale_price": sale_price_rounded,
                                    "sold_at": sold_at,
                                    "listing": False,
                                    "listing_status": None,
                                },
                            )
                        if matched:
                            sold_updates += 1
                    else:
                        for idx in matched:
                            update_purchase(idx, {"listing": False, "listing_status": "error"})
            if sold_updates > 0:
                log(
                    f"[listing_check] 确认售出 {sold_updates} 件，刷新库存并触发自动补挂",
                    "info",
                    category="steam",
                )
                ok_inv, inv_items, inv_err = scan_cs2_inventory()
                if ok_inv:
                    set_inventory(inv_items)
                    from app.workflows.sale import run_sell_phase_on_inventory_update

                    run_sell_phase_on_inventory_update(inv_items)
                else:
                    log(
                        f"[listing_check] 售出后刷新库存失败，暂不补挂: {inv_err}",
                        "warn",
                        category="steam",
                    )
        except WorkerStopped:
            raise
        except Exception as e:
            log(f"listing_check_worker 异常 {type(e).__name__}: {e}", "error", category="steam")
            _worker_alert("listing_check_worker", e)
            wait(60)
