import json
from typing import Optional, Union
SELL_ITEM_URL = "https://steamcommunity.com/market/sellitem/"


def parse_sell_response(text: object) -> tuple[bool, Optional[dict]]:
    """Parse a Steam listing response without assuming a JSON object.

    Steam, a CDN, or an intermediary may occasionally return valid JSON such
    as ``null`` or a non-object payload.  Callers use mapping keys like
    ``success`` and ``message``, so only a JSON object is a usable response.
    """
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            ok = data.get("success") is True
            return ok, data
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
        pass
    return False, None


def list_item(
    session,
    session_id: str,
    appid: int,
    contextid: Union[str, int],
    assetid: str,
    price: Union[int, float],
    amount: int = 1,
) -> Optional[dict]:
    payload = {
        "sessionid": session_id,
        "appid": str(appid),
        "contextid": str(contextid),
        "assetid": str(assetid),
        "amount": str(amount),
        "price": str(int(price) if isinstance(price, float) and price == int(price) else price),
    }
    try:
        r = session.post(SELL_ITEM_URL, data=payload, timeout=15)
        return {"status_code": r.status_code, "text": r.text}
    except Exception:
        return None
