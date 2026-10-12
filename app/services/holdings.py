"""Enrichment of unsold holdings for UI and reporting."""

from app.steam_market.pricing import batch_fetch_prices


def enrich_purchases_with_current_prices(transactions: list) -> None:
    """Fill current_market_price on unsold purchase records using shared batch_fetch_prices."""
    purchases = [t for t in transactions if t.get("type") == "purchase"]
    if not purchases:
        return
    names: set = set()
    for t in purchases:
        if t.get("sale_price") is not None:
            continue
        name = (t.get("name") or "").strip()
        if name:
            names.add(name)
    if not names:
        return
    prices = batch_fetch_prices(names)
    for t in purchases:
        name = (t.get("name") or "").strip()
        if name in prices and t.get("sale_price") is None:
            t["current_market_price"] = prices[name]
