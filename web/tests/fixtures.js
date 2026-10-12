export const modules = [
  [
    "buy.steamdt_top_n",
    "SteamDT 取前 N 条",
    {
      iflow_top_n: { type: "integer", default: 50, min: 0, label: "候选数量" },
    },
  ],
  ["buy.basic_candidate_filter", "基础候选校验"],
  ["buy.buff_realtime_price", "BUFF 实时卖单"],
  ["buy.steam_sell_depth", "Steam 卖单深度"],
  [
    "guard.max_discount",
    "最高折扣",
    {
      max_discount: {
        type: "number",
        default: 0.9,
        min: 0.001,
        max: 1,
        label: "最高折扣",
      },
    },
  ],
  [
    "buy.exclude_keywords",
    "关键词排除",
    { exclude_keywords: { type: "array", default: ["印花"], label: "关键词" } },
  ],
  [
    "guard.target_balance",
    "目标余额",
    {
      target_balance: {
        type: "number",
        default: 100,
        min: 0,
        label: "目标余额",
      },
    },
  ],
  ["action.buff_lock_pay", "BUFF 锁单付款"],
].map(([id, name, params_schema = {}]) => ({
  id,
  name,
  params_schema,
  strategy_types: ["buy"],
  description: "用于界面验收的样例模块",
  max_instances: 1,
  ...(id === "guard.max_discount"
    ? { requires: ["buy.steam_sell_depth"] }
    : {}),
}));
export const strategy = {
  id: "system.buy.default",
  name: "系统默认购入",
  origin: "system",
  readonly: true,
  strategy_type: "buy",
  description: "默认保护规则",
  steps: modules.map((m) => ({
    module_id: m.id,
    enabled: true,
    params: Object.fromEntries(
      Object.entries(m.params_schema).map(([k, v]) => [k, v.default]),
    ),
  })),
};
export const fixtures = {
  "/status": { status: "idle", step: "", buff_no_cookie: true },
  "/stats": {
    total_purchased: 0,
    total_sold: 0,
    total_profit: 0,
    discount_ratio: null,
  },
  "/pending_payment": { pending: null },
  "/pipeline/buff_checkout_guard": { reconciliation_required: false },
  "/accounts": { accounts: [], current_id: null },
  "/config": {
    config: {
      iflow: {
        type: "swap",
        platforms: "buff",
        sort_by: "sell",
        min_price: 2,
        max_price: 5000,
        min_volume: 200,
      },
      buff: { pay_method: "wechat" },
      pipeline: {
        start_time_limit_enabled: false,
        start_time_hour: 8,
        end_time_hour: 22,
        retry_interval_seconds: 300,
        current_price_refresh_minutes: 10,
      },
      inventory: { refresh_seconds: 600 },
      steam_guard: {},
      steam_confirm: { enabled: false },
      notify: {},
      system: {
        ui_scale: "1.0",
        session_keepalive_hours: 4,
        exchange_rate_refresh_hours: 24,
      },
      strategies: { active_buy_strategy_id: strategy.id },
    },
  },
  "/inventory": { items: [] },
  "/transactions": { transactions: [], resell_ratio: 0.85 },
  "/log": { lines: [] },
  "/strategies": {
    ok: true,
    strategies: [strategy],
    modules,
    active: { buy: strategy.id, sell: "" },
    limits: { buy: 22, sell: 14 },
    available_data: { item: ["min_price", "daily_volume"] },
  },
  "/proxy/config": {
    proxy_pool: {
      strategy: 3,
      proxies: [],
      timeout_seconds: 10,
      test_url: "https://ipv4.webshare.io/",
      webshare_api_key: "",
    },
  },
};
