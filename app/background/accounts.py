"""Accounts."""

from app.services.lifecycle import stoppable_worker, WorkerStopped
from app.runtime.state import log
from app.configuration.repository import get_steam_credentials
from app.accounts.repository import get_current_account, update_account


def _sync_account_profile_and_region(acc: dict) -> None:
    from app.services.steam_auth import fetch_steam_profile_via_api

    account_id = acc.get("id")
    cred = get_steam_credentials()
    cred_steam_id = (cred.get("steam_id") or "").strip()
    acc_steam_id = (acc.get("steam_id") or "").strip()
    cookies_str = cred.get("cookies") or ""
    if cred_steam_id and not acc_steam_id:
        update_account(account_id, steam_id=cred_steam_id)
        acc_steam_id = cred_steam_id
        log(
            f"account_sync: 从 credentials 同步 steam_id={cred_steam_id}",
            "info",
            category="account",
        )
    steam_id = acc_steam_id or cred_steam_id
    if steam_id and cookies_str and (not acc.get("display_name") or not acc.get("avatar_url")):
        display_name, avatar_url = fetch_steam_profile_via_api(steam_id, cookies_str)
        if display_name or avatar_url:
            updates = {}
            if display_name and not acc.get("display_name"):
                updates["display_name"] = display_name
            if avatar_url and not acc.get("avatar_url"):
                updates["avatar_url"] = avatar_url
            if updates:
                update_account(account_id, **updates)
                log(
                    f"account_sync: 已获取 Steam 资料 name={display_name or '(无)'} avatar={'(有)' if avatar_url else '(无)'}",
                    "info",
                    category="account",
                )
        else:
            log(
                "account_sync: 未能获取 Steam 资料（网络异常或 Cookie 失效）",
                "debug",
                category="account",
            )


@stoppable_worker
def sync_account_region_worker() -> None:
    try:
        acc = get_current_account()
        if not acc:
            log("account_region: 无当前账号，跳过同步", "debug", category="account")
            return
        log(
            f"account_region: 开始同步 account_id={acc.get('id')} username={acc.get('username') or ''} steam_id={acc.get('steam_id') or ''}",
            "debug",
            category="account",
        )
        _sync_account_profile_and_region(acc)

        from app.services.account_region import refresh_account_region_currency

        result = refresh_account_region_currency(acc.get("id"), skip_unconfigured=True)
        if not result.get("ok"):
            if result.get("skipped"):
                log(
                    f"account_region: {result.get('error') or '尚未完成 Steam 登录配置'}",
                    "debug",
                    category="account",
                )
                return
            log(
                f"account_region: 同步失败，已暂停自动出售安全许可: {result.get('error') or '未知原因'}",
                "error",
                category="account",
            )
            return
        log(
            f"account_region: 同步完成 account_id={acc.get('id')} "
            f"币种={result.get('currency_code')} 派生地区={result.get('region_code')}",
            "debug",
            category="account",
        )
    except WorkerStopped:
        raise
    except Exception as e:
        log(f"account_region: 同步异常 {str(e)[:120]}", "error", category="account")
        return
