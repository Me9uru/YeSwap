"""内置策略模块的参数与展示定义。"""

from typing import Any, Dict


BUILTIN_MODULES: Dict[str, Dict[str, Any]] = {
    "buy.steamdt_top_n": {
        "id": "buy.steamdt_top_n",
        "name": "SteamDT 取前 N 条",
        "category": "buy.source",
        "strategy_types": ["buy"],
        "description": "限制本轮从 SteamDT 候选中检查的最大数量。",
        "params_schema": {
            "iflow_top_n": {
                "type": "integer",
                "min": 0,
                "default": 50,
                "label": "SteamDT 取前 N 条",
            }
        },
    },
    "buy.exclude_keywords": {
        "id": "buy.exclude_keywords",
        "name": "关键词排除",
        "category": "buy.filter",
        "strategy_types": ["buy"],
        "description": "排除名称命中关键词的候选饰品。",
        "params_schema": {
            "exclude_keywords": {"type": "array", "default": ["印花"], "label": "关键词排除"}
        },
    },
    "buy.basic_candidate_filter": {
        "id": "buy.basic_candidate_filter",
        "name": "基础候选校验",
        "category": "buy.filter",
        "strategy_types": ["buy"],
        "description": "过滤无效价格、无 Buff 链接或 goods_id 缺失的候选。",
        "params_schema": {},
    },
    "buy.buff_realtime_price": {
        "id": "buy.buff_realtime_price",
        "name": "Buff 实时卖单",
        "category": "buy.data",
        "strategy_types": ["buy"],
        "description": "购买前刷新 Buff 卖单，避免按过期候选价锁单。",
        "params_schema": {},
    },
    "buy.steam_sell_depth": {
        "id": "buy.steam_sell_depth",
        "name": "Steam 卖单深度",
        "category": "buy.data",
        "strategy_types": ["buy"],
        "description": "拉取 Steam 卖单并计算智能参考价。",
        "params_schema": {},
    },
    "guard.max_discount": {
        "id": "guard.max_discount",
        "name": "最高折扣",
        "category": "guard",
        "strategy_types": ["buy"],
        "description": "要求 (Buff 价格 / Steam 参考价) × 1.15 低于阈值。",
        "params_schema": {
            "max_discount": {
                "type": "number",
                "min": 0.001,
                "max": 1,
                "default": 0.9,
                "label": "最高折扣",
            }
        },
    },
    "guard.sell_pressure": {
        "id": "guard.sell_pressure",
        "name": "卖压保护",
        "category": "guard",
        "strategy_types": ["buy"],
        "description": "用 Steam 前 N 档卖单总量与日销量比值过滤卖压过高的饰品。",
        "params_schema": {
            "sell_pressure_orders_n": {
                "type": "integer",
                "min": 1,
                "default": 5,
                "label": "卖压前 N 档",
            },
            "sell_pressure_threshold": {
                "type": "number",
                "min": 0,
                "default": 2.0,
                "label": "卖压阈值",
            },
        },
    },
    "guard.history_stability": {
        "id": "guard.history_stability",
        "name": "历史稳定性（兼容）",
        "category": "guard.legacy",
        "strategy_types": ["buy"],
        "description": "旧版合并模块，仅用于兼容已保存策略；新策略请使用拆分后的历史模块。",
        "hidden": True,
        "params_schema": {
            "days": {"type": "integer", "min": 1, "default": 30, "label": "统计天数"},
            "cv_threshold": {
                "type": "number",
                "min": 0.001,
                "max": 0.999,
                "default": 0.05,
                "label": "CV 阈值",
            },
            "r2_threshold": {
                "type": "number",
                "min": 0.001,
                "max": 0.999,
                "default": 0.6,
                "label": "R² 趋势阈值",
            },
            "min_daily_trades": {"type": "number", "min": 0, "default": 5, "label": "最低日成交数"},
            "price_percentile_ceil": {
                "type": "number",
                "min": 0.001,
                "max": 1,
                "default": 0.8,
                "label": "价格分位上限",
            },
            "r2_rising_threshold": {
                "type": "number",
                "min": 0.001,
                "max": 0.999,
                "default": 0.8,
                "label": "RISING R² 审慎阈值",
            },
            "slope_pct_ceil": {
                "type": "number",
                "min": 0,
                "default": 0.01,
                "label": "RISING 日斜率上限",
            },
            "ma_deviation_ceil": {
                "type": "number",
                "min": 1,
                "default": 1.1,
                "label": "MA7/MA30 偏离上限",
            },
            "last_price_ma30_ceil": {
                "type": "number",
                "min": 1,
                "default": 1.05,
                "label": "last_price/MA30 上限",
            },
            "slope_stable_floor": {
                "type": "number",
                "default": -0.005,
                "label": "STABLE slope 下限",
            },
            "price_percentile_ceil_rising": {
                "type": "number",
                "min": 0.001,
                "max": 1,
                "default": 0.5,
                "label": "RISING 分位上限",
            },
            "use_vwap": {"type": "boolean", "default": True, "label": "使用 VWAP"},
        },
    },
    "guard.history_data_window": {
        "id": "guard.history_data_window",
        "name": "历史数据窗口",
        "category": "guard.history",
        "strategy_types": ["buy"],
        "description": "控制历史分析使用的天数、最低成交活跃度和参考均价方式。",
        "params_schema": {
            "days": {"type": "integer", "min": 1, "default": 30, "label": "统计天数"},
            "min_daily_trades": {"type": "number", "min": 0, "default": 5, "label": "最低日成交数"},
            "use_vwap": {"type": "boolean", "default": True, "label": "使用 VWAP"},
        },
    },
    "guard.volatility_cv": {
        "id": "guard.volatility_cv",
        "name": "波动率过滤",
        "category": "guard.history",
        "strategy_types": ["buy"],
        "description": "按历史价格 CV 波动率过滤不稳定饰品。",
        "params_schema": {
            "cv_threshold": {
                "type": "number",
                "min": 0.001,
                "max": 0.999,
                "default": 0.05,
                "label": "CV 阈值",
            },
        },
    },
    "guard.trend_quality": {
        "id": "guard.trend_quality",
        "name": "趋势质量过滤",
        "category": "guard.history",
        "strategy_types": ["buy"],
        "description": "控制趋势拟合、上涨趋势审慎阈值和稳定趋势斜率下限。",
        "params_schema": {
            "r2_threshold": {
                "type": "number",
                "min": 0.001,
                "max": 0.999,
                "default": 0.6,
                "label": "R² 趋势阈值",
            },
            "r2_rising_threshold": {
                "type": "number",
                "min": 0.001,
                "max": 0.999,
                "default": 0.8,
                "label": "RISING R² 审慎阈值",
            },
            "slope_pct_ceil": {
                "type": "number",
                "min": 0,
                "default": 0.01,
                "label": "RISING 日斜率上限",
            },
            "slope_stable_floor": {
                "type": "number",
                "default": -0.005,
                "label": "STABLE slope 下限",
            },
        },
    },
    "guard.price_position": {
        "id": "guard.price_position",
        "name": "价格位置过滤",
        "category": "guard.history",
        "strategy_types": ["buy"],
        "description": "按价格分位、均线偏离和最后成交价位置过滤追高风险。",
        "params_schema": {
            "price_percentile_ceil": {
                "type": "number",
                "min": 0.001,
                "max": 1,
                "default": 0.8,
                "label": "价格分位上限",
            },
            "price_percentile_ceil_rising": {
                "type": "number",
                "min": 0.001,
                "max": 1,
                "default": 0.5,
                "label": "RISING 分位上限",
            },
            "ma_deviation_ceil": {
                "type": "number",
                "min": 1,
                "default": 1.1,
                "label": "MA7/MA30 偏离上限",
            },
            "last_price_ma30_ceil": {
                "type": "number",
                "min": 1,
                "default": 1.05,
                "label": "last_price/MA30 上限",
            },
        },
    },
    "guard.safe_purchase_limit": {
        "id": "guard.safe_purchase_limit",
        "name": "安全采购上限（兼容）",
        "category": "guard.legacy",
        "strategy_types": ["buy"],
        "description": "旧版合并模块，仅用于兼容已保存策略；新策略请使用拆分后的采购上限模块。",
        "hidden": True,
        "params_schema": {
            "safe_purchase_hard_qty_cap": {
                "type": "integer",
                "min": 1,
                "default": 50,
                "label": "硬上限",
            },
            "safe_purchase_liquidity_ratio": {
                "type": "number",
                "min": 0,
                "default": 0.05,
                "label": "流动性比例",
            },
            "safe_purchase_low_price_threshold": {
                "type": "number",
                "min": 0,
                "default": 5.0,
                "label": "低价阈值",
            },
            "safe_purchase_low_price_penalty": {
                "type": "number",
                "min": 0,
                "default": 0.5,
                "label": "低价惩罚系数",
            },
            "safe_purchase_low_price_hard_cap": {
                "type": "integer",
                "min": 1,
                "default": 30,
                "label": "低价硬上限",
            },
        },
    },
    "guard.purchase_hard_cap": {
        "id": "guard.purchase_hard_cap",
        "name": "单品硬数量上限",
        "category": "guard.purchase_limit",
        "strategy_types": ["buy"],
        "description": "给单个饰品设置固定采购数量上限，避免单品仓位过大。",
        "params_schema": {
            "safe_purchase_hard_qty_cap": {
                "type": "integer",
                "min": 1,
                "default": 50,
                "label": "硬上限",
            },
        },
    },
    "guard.purchase_liquidity_cap": {
        "id": "guard.purchase_liquidity_cap",
        "name": "流动性采购上限",
        "category": "guard.purchase_limit",
        "strategy_types": ["buy"],
        "description": "按日销量比例限制采购数量，避免一次买掉过多流动性。",
        "params_schema": {
            "safe_purchase_liquidity_ratio": {
                "type": "number",
                "min": 0,
                "default": 0.05,
                "label": "日销量比例",
            },
        },
    },
    "guard.low_price_purchase_guard": {
        "id": "guard.low_price_purchase_guard",
        "name": "低价采购惩罚",
        "category": "guard.purchase_limit",
        "strategy_types": ["buy"],
        "description": "低于指定价格时收紧采购数量，降低低价垃圾单品堆仓风险。",
        "params_schema": {
            "safe_purchase_low_price_threshold": {
                "type": "number",
                "min": 0,
                "default": 5.0,
                "label": "低价阈值",
            },
            "safe_purchase_low_price_penalty": {
                "type": "number",
                "min": 0,
                "default": 0.5,
                "label": "流动性惩罚系数",
            },
            "safe_purchase_low_price_hard_cap": {
                "type": "integer",
                "min": 1,
                "default": 30,
                "label": "低价硬上限",
            },
        },
    },
    "guard.held_same_item_guard": {
        "id": "guard.held_same_item_guard",
        "name": "同名持仓扣减",
        "category": "guard.purchase_limit",
        "strategy_types": ["buy"],
        "description": "采购上限会扣除当前已持有的同名饰品数量，避免继续加仓同一品种。",
        "params_schema": {},
    },
    "guard.target_balance": {
        "id": "guard.target_balance",
        "name": "目标余额保护",
        "category": "guard",
        "strategy_types": ["buy"],
        "description": "限制本轮买入累计金额不超过目标余额。",
        "params_schema": {
            "target_balance": {"type": "number", "min": 0, "default": 100, "label": "目标余额"}
        },
        "safety_required": True,
    },
    "action.buff_lock_pay": {
        "id": "action.buff_lock_pay",
        "name": "Buff 锁单付款",
        "category": "action",
        "strategy_types": ["buy"],
        "description": "执行 Buff 锁单、付款等待、记录购买和催发货。",
        "params_schema": {},
    },
    "sell.sellable_inventory_filter": {
        "id": "sell.sellable_inventory_filter",
        "name": "可出售库存",
        "category": "sell.filter",
        "strategy_types": ["sell"],
        "description": "仅处理 Steam 标记为可出售的库存物品。",
        "params_schema": {},
    },
    "guard.max_listings_per_item": {
        "id": "guard.max_listings_per_item",
        "name": "同名在售上限",
        "category": "guard",
        "strategy_types": ["sell"],
        "description": "同一饰品名的在售数量达到上限后不再继续上架。",
        "params_schema": {
            "max_listings_per_item": {
                "type": "integer",
                "min": 1,
                "default": 5,
                "label": "同名在售上限",
            }
        },
    },
    "pricing.steam_wall_gap": {
        "id": "pricing.steam_wall_gap",
        "name": "墙+断层一体定价",
        "category": "pricing",
        "strategy_types": ["sell"],
        "description": "一体化计算基础上架价和价格补偿，可替换拆分后的价格墙定价模块。",
        "params_schema": {
            "sell_price_wall_volume": {
                "type": "integer",
                "min": 1,
                "default": 20,
                "label": "价格墙数量阈值",
            },
            "sell_price_max_ignore_volume": {
                "type": "integer",
                "min": 0,
                "default": 4,
                "label": "断层跳跃容忍量",
            },
            "sell_price_offset": {"type": "number", "default": 0, "label": "价格补偿"},
        },
    },
    "pricing.steam_wall_price": {
        "id": "pricing.steam_wall_price",
        "name": "价格墙定价",
        "category": "pricing",
        "strategy_types": ["sell"],
        "description": "基于 Steam 卖单墙和价格断层计算基础上架价。",
        "params_schema": {
            "sell_price_wall_volume": {
                "type": "integer",
                "min": 1,
                "default": 20,
                "label": "价格墙数量阈值",
            },
            "sell_price_max_ignore_volume": {
                "type": "integer",
                "min": 0,
                "default": 4,
                "label": "断层跳跃容忍量",
            },
        },
    },
    "pricing.price_offset": {
        "id": "pricing.price_offset",
        "name": "上架价补偿",
        "category": "pricing",
        "strategy_types": ["sell"],
        "description": "在智能定价结果上追加固定补偿，可用于略高或略低挂牌。",
        "params_schema": {
            "sell_price_offset": {"type": "number", "default": 0, "label": "价格补偿"},
        },
    },
    "guard.rising_trend_wait": {
        "id": "guard.rising_trend_wait",
        "name": "上涨趋势等待",
        "category": "guard",
        "strategy_types": ["sell"],
        "description": "近 N 天价格上涨时暂缓出售。",
        "params_schema": {
            "sell_trend_days": {"type": "integer", "min": 1, "default": 7, "label": "趋势判断天数"}
        },
    },
    "guard.profit_ratio": {
        "id": "guard.profit_ratio",
        "name": "利润比例保护",
        "category": "guard",
        "strategy_types": ["sell"],
        "description": "当前买入/挂刀价比例不能高于购入时比例的指定倍数。",
        "params_schema": {
            "profit_ratio_multiplier": {
                "type": "number",
                "min": 1,
                "default": 1.05,
                "label": "利润比例倍数",
            }
        },
    },
    "action.pause_auto_sell": {
        "id": "action.pause_auto_sell",
        "name": "暂停自动出售",
        "category": "action",
        "strategy_types": ["sell"],
        "description": "出售阶段直接跳过，保留手动出售。",
        "params_schema": {},
    },
    "action.steam_list": {
        "id": "action.steam_list",
        "name": "Steam 上架",
        "category": "action",
        "strategy_types": ["sell"],
        "description": "按策略计算价格后提交 Steam 上架。",
        "params_schema": {},
    },
}
