"""Legacy Steam history display-currency detection."""


def currency_code_from_price_text(text: str) -> str:
    s = text or ""
    if "¥" in s or "￥" in s or "CNY" in s or "RMB" in s:
        return "CNY"
    if "HK" in s and "$" in s:
        return "HKD"
    if "₹" in s:
        return "INR"
    if "₽" in s:
        return "RUB"
    if "€" in s:
        return "EUR"
    if "USD" in s or "US$" in s:
        return "USD"
    if "$" in s:
        return "USD"
    return "CNY"
