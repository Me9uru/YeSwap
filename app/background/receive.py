"""Receive."""

from app.services.lifecycle import wait, stoppable_worker, WorkerStopped
from app.runtime.state import (
    get_purchases,
    is_steam_background_allowed,
    log,
    set_buff_auth_expired,
    set_buff_verification_required,
    update_purchase,
    update_purchase_by_id,
)
from app.configuration.repository import get_buff_credentials, get_steam_credentials, load_app_config_validated
from app.steam_market.inventory import scan_cs2_inventory
from app.background.safety import (
    _buff_background_request_is_safe,
)


@stoppable_worker
def receive_worker() -> None:
    from app.workflows.receiving import (
        reconcile_delivered_purchases,
        try_receive_once,
    )
    from app.services.buff_auth import get_buff_auth_lock
    from app.services.buff_client import create_buff_client_from_config
    from app.services.buff_checkout_guard import buff_activity_guard
    from buff import (
        BuffAuthExpired,
        BuffRateLimited,
        BuffRequestBlocked,
        BuffVerificationRequired,
    )

    buff_client = None
    while True:
        try:
            cfg = load_app_config_validated()
            interval = max(
                10, int(cfg.get("pipeline", {}).get("receive_poll_interval_seconds", 30) or 30)
            )
            wait(interval)
            if not is_steam_background_allowed() or not _buff_background_request_is_safe():
                continue
            # Hold the complete receive transaction (BUFF task lookup, Steam
            # offer acceptance and DB update) outside pipeline start/import/
            # data reset.  The inner BuffClient calls are re-entrant.
            with get_buff_auth_lock():
                with buff_activity_guard():
                    if not is_steam_background_allowed() or not _buff_background_request_is_safe():
                        continue
                    purchases = get_purchases()
                    if not any(
                        p.get("pending_receipt") and not p.get("assetid") for p in purchases
                    ):
                        continue
                    credentials = get_buff_credentials() or {}
                    if not credentials.get("cookies"):
                        continue
                    if buff_client is None:
                        buff_client = create_buff_client_from_config(
                            credentials,
                            cfg,
                        )
                    n = try_receive_once(
                        get_purchases,
                        update_purchase,
                        lambda: buff_client,
                        get_steam_credentials,
                        scan_inventory=scan_cs2_inventory,
                        update_purchase_by_id=update_purchase_by_id,
                    )
                    # 买家发起报价（mode 5/8）的订单不会出现在待收货报价
                    # 列表里，需要按订单交付状态 + 库存名字单独对账。
                    m = reconcile_delivered_purchases(
                        get_purchases,
                        lambda: buff_client,
                        scan_inventory=scan_cs2_inventory,
                        update_purchase_by_id=update_purchase_by_id,
                    )
            if n > 0:
                log(f"receive_worker: 本轮收取到 {n} 件物品", "info", category="receive")
            if m > 0:
                log(
                    f"receive_worker: 交付对账补全 {m} 件待收货记录"
                    "（买家发起报价 / 已在别处完成交付）",
                    "info",
                    category="receive",
                )
        except BuffRateLimited as e:
            log(f"receive_worker: Buff 请求处于限流冷却: {e}", "warn", category="receive")
            wait(min(60, max(1, int(e.retry_after))))
        except BuffVerificationRequired as e:
            set_buff_verification_required(True, str(e))
            log(f"receive_worker: Buff 需要安全验证: {e}", "warn", category="receive")
            wait(60)
        except BuffAuthExpired:
            set_buff_auth_expired(True)
            log("receive_worker: Buff 登录已失效，暂停待收货查询", "warn", category="receive")
            wait(60)
        except BuffRequestBlocked as e:
            log(f"receive_worker: Buff 请求策略已阻止后台查询: {e}", "warn", category="receive")
            wait(60)
        except WorkerStopped:
            raise
        except Exception as e:
            log(f"receive_worker 异常 {type(e).__name__}: {e}", "error", category="receive")
            wait(60)
