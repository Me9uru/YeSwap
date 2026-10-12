import time
from typing import Optional

from app.accounts.repository import get_account, get_current_account, update_account
from app.configuration.repository import get_steam_credentials
from app.runtime.state import log
from app.services.steam_auth import steam_id_from_cookie_str

_SETTLEMENT_CURRENCY_REGION = {
    "USD": "US",
    "GBP": "GB",
    "EUR": "EU",
    "RUB": "RU",
    "PLN": "PL",
    "BRL": "BR",
    "JPY": "JP",
    "NOK": "NO",
    "IDR": "ID",
    "MYR": "MY",
    "PHP": "PH",
    "SGD": "SG",
    "THB": "TH",
    "VND": "VN",
    "KRW": "KR",
    "TRY": "TR",
    "UAH": "UA",
    "MXN": "MX",
    "CAD": "CA",
    "AUD": "AU",
    "NZD": "NZ",
    "CNY": "CN",
    "INR": "IN",
    "CLP": "CL",
    "PEN": "PE",
    "COP": "CO",
    "ZAR": "ZA",
    "HKD": "HK",
    "TWD": "TW",
    "SAR": "SA",
    "AED": "AE",
    "ARS": "AR",
    "ILS": "IL",
    "KZT": "KZ",
    "KWD": "KW",
    "QAR": "QA",
}


def _short_error(exc: Exception, limit: int = 180) -> str:
    msg = str(exc).strip() or type(exc).__name__
    msg = " ".join(msg.split())
    return msg[:limit]


def _region_from_settlement_currency(currency_code: str, wallet_country: str = "") -> str:
    currency = (currency_code or "").strip().upper()
    country = (wallet_country or "").strip().upper()
    return _SETTLEMENT_CURRENCY_REGION.get(currency) or country


def _recover_steam_session(failed_cookies: str) -> Optional[str]:
    """钱包查询失败后尝试恢复登录态，返回重试用的 Cookie；无法恢复时返回 None。

    steamLoginSecure 是约 24 小时过期的 JWT，应用离线一段时间后启动时必然可能
    撞上失效凭据。只有探测结果明确为"未登录"才触发一次自动重登——网络异常等
    无法判定（None）的情况不启动密码登录。其他流程可能已重登并更新了存储凭据，
    此时直接复用，避免重复登录。
    """
    from steam.session import build_steam_session, check_login_state

    def _probe(cookie_str: str) -> Optional[bool]:
        if not cookie_str or "steamLoginSecure" not in cookie_str:
            return None
        try:
            return check_login_state(build_steam_session(cookies=cookie_str))
        except Exception:
            return None

    failed = (failed_cookies or "").strip()
    stored = (get_steam_credentials().get("cookies") or "").strip()
    if stored and stored != failed and _probe(stored) is True:
        return stored
    if _probe(failed) is not False:
        return None
    log("account_region: 检测到 Steam 登录态已失效，先自动重登再重试", "info", category="steam")
    from app.services.steam_auth import try_steam_auto_relogin

    ok, _status, _message = try_steam_auto_relogin()
    if not ok:
        return None
    refreshed = (get_steam_credentials().get("cookies") or "").strip()
    return refreshed or None


def refresh_account_region_currency(
    account_id: Optional[str] = None,
    *,
    cookies_raw: Optional[str] = None,
    skip_unconfigured: bool = False,
) -> dict:
    """Refresh Steam wallet currency and persist the matching display region.

    Sale code treats a failed currency refresh as unsafe and refuses listing.
    The settlement currency is the source of truth; region_code is derived from
    that same currency so stale store-country parsing cannot diverge from price
    calculation.
    """
    acc = get_account(account_id) if account_id else get_current_account()
    if not acc:
        return {"ok": False, "skipped": True, "status": "no_account", "error": "未设置当前 Steam 账号"}
    aid = acc.get("id")
    checked_at = time.time()
    try:
        cookies = cookies_raw if cookies_raw is not None else get_steam_credentials().get("cookies", "")
        if not cookies or "steamLoginSecure" not in cookies:
            if skip_unconfigured:
                return {
                    "ok": False,
                    "skipped": True,
                    "status": "missing_cookie",
                    "error": "尚未配置有效 Steam Cookie，跳过结算币种检查",
                    "checked_at": checked_at,
                }
            raise RuntimeError("缺少有效 Steam Cookie，无法确认结算币种")
        expected_steam_id = str(acc.get("steam_id") or "").strip()
        saved_steam_id = str(get_steam_credentials().get("steam_id") or "").strip()
        actual_steam_id = steam_id_from_cookie_str(cookies) or saved_steam_id
        if expected_steam_id and actual_steam_id and expected_steam_id != actual_steam_id:
            raise RuntimeError("当前账号与已保存的 Steam Cookie 不一致，请重新验证当前账号或重新登录 Steam")

        from steam.account import get_wallet_balance

        try:
            wallet = get_wallet_balance(cookies)
        except Exception:
            recovered = _recover_steam_session(cookies)
            if not recovered:
                raise
            cookies = recovered
            wallet = get_wallet_balance(cookies)
        currency_code = (wallet.get("currency_code") or "").strip().upper()
        if not currency_code:
            raise RuntimeError("Steam 未返回钱包结算币种")
        region_code = _region_from_settlement_currency(
            currency_code,
            wallet.get("country_code") or wallet.get("wallet_country") or "",
        )

        updated = update_account(
            aid,
            region_code=region_code,
            currency_code=currency_code,
            region_check_ok=True,
            region_check_error="",
            region_checked_at=checked_at,
            currency_checked_at=checked_at,
            wallet_currency_id=wallet.get("currency_id"),
        )
        return {
            "ok": True,
            "account": updated,
            "region_code": region_code,
            "currency_code": currency_code,
            "wallet_currency_id": wallet.get("currency_id"),
            "checked_at": checked_at,
        }
    except Exception as exc:
        error = _short_error(exc)
        if aid:
            update_account(
                aid,
                region_check_ok=False,
                region_check_error=error,
                region_checked_at=checked_at,
            )
        return {"ok": False, "error": error, "checked_at": checked_at}
