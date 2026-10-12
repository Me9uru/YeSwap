"""Strategy catalog responsibility."""

import re
from typing import Any, Dict


STRATEGY_TYPES = {"buy", "sell"}


SELL_LEGACY_TO_SYSTEM = {
    1: "system.sell.immediate",
    2: "system.sell.trend",
    3: "system.sell.profit_guard",
    4: "system.sell.pause",
}


SELL_SYSTEM_TO_LEGACY = {v: k for k, v in SELL_LEGACY_TO_SYSTEM.items()}


SENSITIVE_KEY_RE = re.compile(r"(cookie|token|secret|password|passwd|session|credential)", re.I)


DEFAULT_MODULE_MAX_INSTANCES = 1


STRATEGY_STEP_LIMITS = {"buy": 22, "sell": 14}


BUY_REQUIRED_MODULES = [
    "buy.steamdt_top_n",
    "buy.basic_candidate_filter",
    "buy.buff_realtime_price",
    "buy.steam_sell_depth",
    "guard.target_balance",
    "action.buff_lock_pay",
]


SELL_LIST_REQUIRED_MODULES = [
    "sell.sellable_inventory_filter",
    "guard.max_listings_per_item",
]


SELL_PRICING_CORE_MODULES = [
    "pricing.steam_wall_price",
    "pricing.steam_wall_gap",
]


MODULE_CONSTRAINTS: Dict[str, Dict[str, Any]] = {
    "guard.max_discount": {"requires": ["buy.steam_sell_depth"]},
    "guard.sell_pressure": {"requires": ["buy.steam_sell_depth"]},
    "guard.history_stability": {
        "conflicts": [
            "guard.history_data_window",
            "guard.volatility_cv",
            "guard.trend_quality",
            "guard.price_position",
        ],
    },
    "guard.history_data_window": {"conflicts": ["guard.history_stability"]},
    "guard.volatility_cv": {"conflicts": ["guard.history_stability"]},
    "guard.trend_quality": {"conflicts": ["guard.history_stability"]},
    "guard.price_position": {"conflicts": ["guard.history_stability"]},
    "guard.safe_purchase_limit": {
        "conflicts": [
            "guard.purchase_hard_cap",
            "guard.purchase_liquidity_cap",
            "guard.low_price_purchase_guard",
            "guard.held_same_item_guard",
        ],
    },
    "guard.purchase_hard_cap": {"conflicts": ["guard.safe_purchase_limit"]},
    "guard.purchase_liquidity_cap": {"conflicts": ["guard.safe_purchase_limit"]},
    "guard.low_price_purchase_guard": {"conflicts": ["guard.safe_purchase_limit"]},
    "guard.held_same_item_guard": {"conflicts": ["guard.safe_purchase_limit"]},
    "pricing.steam_wall_gap": {
        "conflicts": ["pricing.steam_wall_price", "pricing.price_offset"],
    },
    "pricing.steam_wall_price": {"conflicts": ["pricing.steam_wall_gap"]},
    "pricing.price_offset": {
        "conflicts": ["pricing.steam_wall_gap"],
        "requires_any": [["pricing.steam_wall_price", "pricing.steam_wall_gap"]],
    },
    "action.pause_auto_sell": {"conflicts": ["action.steam_list"]},
    "action.steam_list": {
        "conflicts": ["action.pause_auto_sell"],
        "requires": ["sell.sellable_inventory_filter", "guard.max_listings_per_item"],
        "requires_any": [["pricing.steam_wall_price", "pricing.steam_wall_gap"]],
    },
}


USER_MODULE_CODE_KEYS = {"code", "source", "script", "entrypoint", "package", "command"}


DECLARATIVE_MODULE_KINDS = {"declarative", "rule"}


USER_MODULE_EFFECTS = {"guard", "filter", "info"}


USER_MODULE_FAIL_STATUSES = {"reject", "wait", "pass", "error"}


USER_MODULE_STAGES = {
    "buy": {"buy.candidate_guard"},
    "sell": {"sell.listing_guard"},
}


CONDITION_OPERATORS = {
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "contains",
    "not_contains",
    "in",
    "not_in",
    "exists",
    "missing",
    "between",
}


MODULE_DATA_OUTPUTS: Dict[str, Dict[str, str]] = {
    "buy.buff_realtime_price": {
        "lowest_price": "Current Buff lowest sell order.",
        "order_count": "Number of visible Buff sell orders.",
        "available": "Whether a usable Buff order exists.",
    },
    "buy.steam_sell_depth": {
        "smart_price": "Steam smart reference price in CNY.",
        "sell_orders_count": "Number of Steam sell orders.",
        "sell_pressure": "Top order volume divided by daily volume.",
        "reference_price": "Reference price after daily-high adjustment.",
        "estimated_ratio": "(Buff price / Steam reference price) * fee factor.",
    },
    "guard.history_data_window": {
        "status": "History analyzer status.",
        "avg": "Average history price.",
        "cv": "Coefficient of variation.",
        "r_squared": "Trend fit quality.",
        "slope": "Trend slope.",
        "price_percentile": "Current price percentile in history window.",
        "ma7": "Short moving average.",
        "ma30": "Long moving average.",
        "is_stable": "Whether the history analyzer accepted the item.",
    },
    "guard.max_discount": {
        "estimated_ratio": "Computed discount ratio.",
        "limit": "Configured max discount threshold.",
    },
    "guard.sell_pressure": {
        "sell_pressure": "Computed sell pressure.",
        "limit": "Configured sell pressure threshold.",
    },
    "guard.max_listings_per_item": {
        "steam_same_name": "Already listed same-name count on Steam.",
        "round_same_name": "Same-name count planned in this run.",
        "max_per_item": "Configured same-name listing cap.",
    },
    "pricing.steam_wall_price": {
        "list_price": "Computed CNY listing price.",
        "reason": "Pricing explanation from Steam order depth.",
        "order_count": "Number of Steam sell orders used.",
    },
    "pricing.steam_wall_gap": {
        "list_price": "Computed CNY listing price.",
        "reason": "Pricing explanation from Steam order depth.",
        "order_count": "Number of Steam sell orders used.",
    },
    "pricing.price_offset": {
        "sell_price_offset": "Fixed listing price offset.",
    },
    "guard.rising_trend_wait": {
        "trend": "Recent trend value.",
        "trend_days": "Configured trend lookback days.",
    },
    "guard.profit_ratio": {
        "current_ratio": "Current buy/listing ratio.",
        "original_ratio": "Buy-time ratio.",
        "ratio_limit": "Configured allowed ratio limit.",
    },
}


MODULE_SAMPLE_OUTPUTS: Dict[str, Dict[str, Any]] = {
    "buy.buff_realtime_price": {"lowest_price": 9.8, "order_count": 6, "available": True},
    "buy.steam_sell_depth": {
        "smart_price": 13.2,
        "sell_orders_count": 42,
        "sell_pressure": 1.1,
        "reference_price": 13.2,
        "estimated_ratio": 0.85,
    },
    "guard.history_data_window": {
        "status": "STABLE",
        "avg": 12.4,
        "cv": 0.032,
        "r_squared": 0.72,
        "slope": 0.001,
        "price_percentile": 0.48,
        "ma7": 12.6,
        "ma30": 12.2,
        "is_stable": True,
    },
    "guard.volatility_cv": {"cv": 0.032, "limit": 0.05},
    "guard.trend_quality": {"r_squared": 0.72, "slope": 0.001, "status": "STABLE"},
    "guard.price_position": {"price_percentile": 0.48, "ma7": 12.6, "ma30": 12.2},
    "guard.max_discount": {"estimated_ratio": 0.85, "limit": 0.9},
    "guard.sell_pressure": {"sell_pressure": 1.1, "limit": 2.0},
    "guard.purchase_hard_cap": {"hard_cap": 50},
    "guard.purchase_liquidity_cap": {"liquidity_cap": 5},
    "guard.low_price_purchase_guard": {"low_price_adjustment": 1.0},
    "guard.held_same_item_guard": {"held_same_name": 0},
    "guard.target_balance": {"target_balance": 100, "projected_spend": 9.8},
    "sell.sellable_inventory_filter": {"sellable": True},
    "guard.max_listings_per_item": {"steam_same_name": 1, "round_same_name": 0, "max_per_item": 5},
    "pricing.steam_wall_price": {
        "list_price": 12.8,
        "reason": "sample wall price",
        "order_count": 30,
    },
    "pricing.steam_wall_gap": {
        "list_price": 12.8,
        "reason": "sample wall-gap price",
        "order_count": 30,
    },
    "pricing.price_offset": {"sell_price_offset": 0},
    "guard.rising_trend_wait": {"trend": -0.01, "trend_days": 7},
    "guard.profit_ratio": {"current_ratio": 0.72, "original_ratio": 0.75, "ratio_limit": 0.7875},
}


STRATEGY_CONTEXT_FIELDS: Dict[str, Dict[str, str]] = {
    "buy": {
        "item.name": "Candidate item name.",
        "item.goods_id": "Buff goods id.",
        "item.min_price": "Candidate Buff price.",
        "item.daily_volume": "SteamDT daily volume.",
        "item.ratio": "Candidate ratio from source data.",
        "item.steam_market_name": "Steam market hash name.",
        "outputs.buy.steam_sell_depth.smart_price": "Steam smart reference price.",
        "outputs.buy.steam_sell_depth.estimated_ratio": "Estimated buy discount ratio.",
        "outputs.guard.history_data_window.cv": "History CV.",
        "outputs.guard.history_data_window.price_percentile": "Current price percentile.",
    },
    "sell": {
        "item.name": "Inventory item name.",
        "item.assetid": "Steam asset id.",
        "item.market_hash_name": "Steam market hash name.",
        "buy_record.price": "Recorded buy price.",
        "buy_record.market_price": "Buy-time market price.",
        "listing.list_price": "Computed CNY listing price.",
        "outputs.guard.max_listings_per_item.steam_same_name": "Already listed same-name count.",
        "outputs.pricing.steam_wall_price.list_price": "Computed CNY listing price.",
        "outputs.pricing.steam_wall_gap.list_price": "Computed CNY listing price from the one-piece pricing module.",
        "outputs.guard.rising_trend_wait.trend": "Recent Steam price trend.",
        "outputs.guard.profit_ratio.current_ratio": "Current buy/listing ratio.",
    },
}


class StrategyError(ValueError):
    pass
