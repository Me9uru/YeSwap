"""Full backup serialization and multi-store restore implementation."""

from typing import Optional
from app.runtime.state import get_log, get_purchases, get_sales, replace_log, replace_transactions
from app.configuration.repository import save_app_config_validated
from app.configuration.schema import strip_removed_feature_config
from config import load_app_config, save_credentials, get_all_credentials
from app.accounts.repository import list_accounts, replace_all as accounts_replace_all


def api_export_full():
    from datetime import datetime, timezone
    from app.accounts.repository import get_current_id

    data = {
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "app_config": strip_removed_feature_config(load_app_config()),
        "credentials": get_all_credentials(),
        "transactions": {"purchases": get_purchases(), "sales": get_sales()},
        "accounts": {"accounts": list_accounts(), "current_id": get_current_id()},
        "log": get_log(0),
    }
    return data


def restore_full_backup_unlocked(
    *, app_config: Optional[dict] = None, credentials: Optional[dict] = None,
    transactions: Optional[dict] = None, accounts: Optional[dict] = None,
    log: Optional[list] = None,
):
    """Restore stores while the caller holds the purchase maintenance guard."""
    from app.accounts.repository import get_current_id
    try:
        snapshot = {
            "app_config": load_app_config(),
            "credentials": get_all_credentials(),
            "transactions": {
                "purchases": get_purchases(),
                "sales": get_sales(),
            },
            "accounts": {
                "accounts": list_accounts(),
                "current_id": get_current_id(),
            },
            "log": get_log(0),
        }
        try:
            if app_config is not None:
                save_app_config_validated(app_config)
            if credentials is not None:
                save_credentials(credentials)
            if transactions is not None:
                tx = transactions
                replace_transactions(
                    tx.get("purchases", []),
                    tx.get("sales", []),
                )
            if accounts is not None:
                accounts_replace_all(accounts)
            if log is not None:
                replace_log(log)
        except Exception as import_exc:
            # The stores are heterogeneous (JSON + SQLite + memory), so a
            # best-effort rollback is required while all exclusion locks
            # are still held.
            restorations = [
                (
                    "app_config",
                    lambda: save_app_config_validated(snapshot["app_config"]),
                ),
                (
                    "credentials",
                    lambda: save_credentials(snapshot["credentials"]),
                ),
                (
                    "transactions",
                    lambda: replace_transactions(
                        snapshot["transactions"]["purchases"],
                        snapshot["transactions"]["sales"],
                    ),
                ),
                (
                    "accounts",
                    lambda: accounts_replace_all(snapshot["accounts"]),
                ),
                ("log", lambda: replace_log(snapshot["log"])),
            ]
            rollback_errors = []
            for label, restore in restorations:
                try:
                    restore()
                except Exception as rollback_exc:
                    rollback_errors.append(
                        f"{label}: {type(rollback_exc).__name__}: {rollback_exc}"
                    )
            if rollback_errors:
                raise RuntimeError(
                    f"完整导入失败 ({type(import_exc).__name__}: "
                    f"{import_exc})；部分回滚失败: " + "; ".join(rollback_errors)
                ) from import_exc
            raise
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}
