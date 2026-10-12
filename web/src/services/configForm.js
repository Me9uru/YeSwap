// 仅发送修改字段，避免全量旧快照覆盖策略中心等页面的新配置。
export function configPatch(current, baseline) {
  const patch = {};
  for (const [section, values] of Object.entries(current)) {
    if (!values || typeof values !== "object" || Array.isArray(values))
      continue;
    for (const [key, value] of Object.entries(values)) {
      if (JSON.stringify(value) !== JSON.stringify(baseline[section]?.[key])) {
        (patch[section] ||= {})[key] = value;
      }
    }
  }
  return patch;
}
export const settingsGroups = [
  {
    title: "SteamDT 数据源",
    fields: [
      ["iflow", "type", "数据类型", "text"],
      ["iflow", "platforms", "目标平台", "text"],
      [
        "iflow",
        "sort_by",
        "排序方式",
        "select",
        [
          ["sell", "最优寄售"],
          ["buy", "最优求购"],
        ],
      ],
      ["iflow", "min_price", "最低价（CNY）", "number", 0],
      ["iflow", "max_price", "最高价（CNY）", "number", 0],
      ["iflow", "min_volume", "最低日成交量", "integer", 0],
    ],
  },
  {
    title: "支付与任务时段",
    fields: [
      [
        "buff",
        "pay_method",
        "支付方式",
        "select",
        [
          ["wechat", "微信支付"],
          ["alipay", "支付宝（兼容配置）"],
        ],
      ],
      ["pipeline", "start_time_limit_enabled", "启用运行时段限制", "boolean"],
      ["pipeline", "start_time_hour", "开始时间（小时）", "integer", 0, 23],
      ["pipeline", "end_time_hour", "结束时间（小时）", "integer", 0, 23],
    ],
  },
  {
    title: "Steam 令牌与确认",
    fields: [
      [
        "steam_guard",
        "shared_secret",
        "动态令牌密钥 shared_secret",
        "password",
      ],
      ["steam_confirm", "enabled", "自动确认上架", "boolean"],
      [
        "steam_confirm",
        "identity_secret",
        "确认密钥 identity_secret",
        "password",
      ],
      ["steam_confirm", "device_id", "设备标识 device_id", "password"],
    ],
  },
  {
    title: "消息推送与持仓报告",
    fields: [
      ["notify", "pushplus_token", "PushPlus Token", "password"],
      [
        "notify",
        "holdings_report_interval_hours",
        "定时报告间隔（小时，0 关闭）",
        "integer",
        0,
        720,
      ],
      ["notify", "holdings_report_drop_enabled", "启用持仓跌幅提醒", "boolean"],
      [
        "notify",
        "holdings_report_change_threshold_pct",
        "跌幅提醒阈值（%）",
        "number",
        1,
        100,
      ],
    ],
  },
  {
    title: "邮箱付款确认",
    fields: [
      ["notify", "email_user", "邮箱账号", "text"],
      ["notify", "email_pass", "邮箱授权码", "password"],
      ["notify", "imap_server", "IMAP 服务器", "text"],
      ["notify", "target_sender", "指令发件人（昵称）", "text"],
      ["notify", "allowed_sender", "允许的发件人邮箱", "text"],
      ["notify", "subject_success", "付款成功邮件标题", "text"],
      ["notify", "subject_fail", "付款失败邮件标题", "text"],
      ["notify", "email_timeout_seconds", "等待超时（秒）", "integer", 60],
    ],
  },
  {
    title: "刷新与系统",
    fields: [
      [
        "pipeline",
        "retry_interval_seconds",
        "候选重试间隔（秒）",
        "integer",
        60,
      ],
      ["inventory", "refresh_seconds", "库存刷新间隔（秒）", "integer", 600],
      [
        "pipeline",
        "current_price_refresh_minutes",
        "市场价刷新间隔（分钟）",
        "integer",
        1,
      ],
      [
        "system",
        "exchange_rate_refresh_hours",
        "汇率刷新间隔（小时）",
        "number",
        0,
      ],
      [
        "system",
        "buff_session_keepalive_enabled",
        "BUFF 浏览器会话保活",
        "boolean",
      ],
      [
        "system",
        "session_keepalive_hours",
        "会话保活间隔（小时）",
        "number",
        0.5,
      ],
      ["pipeline", "verbose_debug", "详细日志", "boolean"],
      ["pipeline", "steam_listings_debug", "Steam 解析调试", "boolean"],
    ],
  },
];
