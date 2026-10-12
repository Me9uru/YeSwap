"""Steam store authentication and wallet queries."""

import html
import json
import re
import time
from utils.cookies import parse_cookies
from .session import build_steam_session


def _build_cookies(cookies_raw: str) -> dict:
    """从完整 cookie 字符串构建 cookies dict，并确保必要字段存在。"""
    cookies = parse_cookies(cookies_raw)
    cookies.setdefault("birthtime", "283993201")
    cookies.setdefault("wants_mature_content", "1")
    return cookies


_CURRENCY_MAP = {
    1: ("USD", "$"),
    2: ("GBP", "£"),
    3: ("EUR", "€"),
    5: ("RUB", "₽"),
    6: ("PLN", "zł"),
    7: ("BRL", "R$"),
    8: ("JPY", "¥"),
    9: ("NOK", "kr"),
    10: ("IDR", "Rp"),
    11: ("MYR", "RM"),
    12: ("PHP", "₱"),
    13: ("SGD", "S$"),
    14: ("THB", "฿"),
    15: ("VND", "₫"),
    16: ("KRW", "₩"),
    17: ("TRY", "₺"),
    18: ("UAH", "₴"),
    19: ("MXN", "Mex$"),
    20: ("CAD", "C$"),
    21: ("AUD", "A$"),
    22: ("NZD", "NZ$"),
    23: ("CNY", "¥"),
    24: ("INR", "₹"),
    25: ("CLP", "CLP$"),
    26: ("PEN", "S/."),
    27: ("COP", "COL$"),
    28: ("ZAR", "R"),
    29: ("HKD", "HK$"),
    30: ("TWD", "NT$"),
    31: ("SAR", "SR"),
    32: ("AED", "AED"),
    34: ("ARS", "ARS$"),
    35: ("ILS", "₪"),
    37: ("KZT", "₸"),
    38: ("KWD", "KD"),
    39: ("QAR", "QR"),
}
_NO_DIVIDE_CURRENCIES = {8, 16, 15}

_COUNTRY_CODE_KEYS = (
    "country_code",
    "country",
    "store_country_code",
    "wallet_country",
    "user_country",
)


def _normalize_country_code(value) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip().upper()
    return value if re.fullmatch(r"[A-Z]{2}", value) else ""


def _find_country_code(value) -> str:
    if isinstance(value, dict):
        for key in _COUNTRY_CODE_KEYS:
            code = _normalize_country_code(value.get(key))
            if code:
                return code
        for item in value.values():
            code = _find_country_code(item)
            if code:
                return code
    elif isinstance(value, list):
        for item in value:
            code = _find_country_code(item)
            if code:
                return code
    return ""


def _extract_store_user_config(page_text: str) -> dict:
    for pattern in (
        r'data-store_user_config="([^"]+)"',
        r"data-store_user_config='([^']+)'",
    ):
        cfg = re.search(pattern, page_text or "")
        if not cfg:
            continue
        try:
            data = json.loads(html.unescape(cfg.group(1)))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def _extract_country_code(page_text: str, config_data: dict = None) -> str:
    code = _find_country_code(config_data or {})
    if code:
        return code

    for source in (page_text or "", html.unescape(page_text or "")):
        for key in _COUNTRY_CODE_KEYS:
            match = re.search(rf'"{re.escape(key)}"\s*:\s*"([A-Za-z]{{2}})"', source)
            if match:
                code = _normalize_country_code(match.group(1))
                if code:
                    return code
    return ""


def get_base_auth_status(cookies_raw: str, *, require_country: bool = False):
    url = f"https://store.steampowered.com/cart/?_t={int(time.time() * 1000)}"
    cookies = _build_cookies(cookies_raw)
    try:
        session = build_steam_session(cookies=cookies)
        resp = session.get(url, timeout=15)
        page_text = resp.text or ""
        jwt_token = ""
        config_data = _extract_store_user_config(page_text)
        if config_data:
            jwt_token = config_data.get("webapi_token", "")
        country_code = _extract_country_code(page_text, config_data)
        if require_country and not country_code:
            raise RuntimeError("无法从 Steam 商店页面解析账号地区")
        if not country_code:
            country_code = "CN"
        return jwt_token, country_code, config_data
    except Exception as e:
        raise RuntimeError(f"获取底层鉴权失败: {e}") from e


def get_wallet_balance(cookies_raw: str) -> dict:
    cookies = _build_cookies(cookies_raw)
    unknown_currency_id = None
    session = build_steam_session(
        cookies=cookies, headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    )
    try:
        resp = session.get("https://steamcommunity.com/market/", timeout=15)
        m = re.search(r"g_rgWalletInfo\s*=\s*(\{[^;]+\})", resp.text)
        if m:
            wallet = json.loads(m.group(1))
            if wallet.get("success") == 1 or "wallet_balance" in wallet:
                balance_raw = int(wallet.get("wallet_balance", 0))
                delayed_raw = int(wallet.get("wallet_delayed_balance", 0))
                total_raw = balance_raw + delayed_raw
                currency_id = int(wallet.get("wallet_currency", 0))
                wallet_country = _normalize_country_code(wallet.get("wallet_country"))
                currency_info = _CURRENCY_MAP.get(currency_id)
                if not currency_info:
                    if currency_id > 0:
                        unknown_currency_id = currency_id
                    raise RuntimeError(f"未知 Steam 钱包币种 ID: {currency_id}")
                code, symbol = currency_info
                display = (
                    f"{symbol}{total_raw:,}"
                    if currency_id in _NO_DIVIDE_CURRENCIES
                    else f"{symbol}{total_raw / 100.0:.2f}"
                )
                return {
                    "balance_raw": total_raw,
                    "balance_display": display,
                    "currency_code": code,
                    "currency_symbol": symbol,
                    "currency_id": currency_id,
                    "wallet_country": wallet_country,
                    "country_code": wallet_country,
                }
    except Exception:
        pass
    try:
        jwt_token, _, _ = get_base_auth_status(cookies_raw)
        if jwt_token:
            api_url = (
                "https://api.steampowered.com/IWalletService/GetWalletDetails/v1"
                f"?access_token={jwt_token}"
            )
            resp = session.get(api_url, timeout=15)
            if resp.status_code == 200:
                data = resp.json().get("response", {})
                balance_raw = int(data.get("balance", 0))
                delayed_raw = int(data.get("delayed_balance", 0))
                total_raw = balance_raw + delayed_raw
                currency_id = int(data.get("currency", 0))
                wallet_country = _normalize_country_code(
                    data.get("country") or data.get("wallet_country") or data.get("country_code")
                )
                if currency_id > 0:
                    currency_info = _CURRENCY_MAP.get(currency_id)
                    if not currency_info:
                        if currency_id > 0:
                            unknown_currency_id = currency_id
                        raise RuntimeError(f"未知 Steam 钱包币种 ID: {currency_id}")
                    code, symbol = currency_info
                    display = (
                        f"{symbol}{total_raw:,}"
                        if currency_id in _NO_DIVIDE_CURRENCIES
                        else f"{symbol}{total_raw / 100.0:.2f}"
                    )
                    return {
                        "balance_raw": total_raw,
                        "balance_display": display,
                        "currency_code": code,
                        "currency_symbol": symbol,
                        "currency_id": currency_id,
                        "wallet_country": wallet_country,
                        "country_code": wallet_country,
                    }
    except Exception:
        pass
    if unknown_currency_id is not None:
        raise RuntimeError(f"未知 Steam 钱包币种 ID: {unknown_currency_id}")
    raise RuntimeError("无法获取 Steam 钱包余额，请确认 Cookie 有效且账户已设置钱包")
