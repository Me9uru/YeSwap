import { clone } from "./api.js";
export const buyRequired = [
  "buy.steamdt_top_n",
  "buy.basic_candidate_filter",
  "buy.buff_realtime_price",
  "buy.steam_sell_depth",
  "guard.target_balance",
  "action.buff_lock_pay",
];
export const sellRequired = [
  "sell.sellable_inventory_filter",
  "guard.max_listings_per_item",
  "action.steam_list",
];
export const pricing = ["pricing.steam_wall_price", "pricing.steam_wall_gap"];
export function fixedStep(step, draft) {
  if (draft.origin === "system") return true;
  if (draft.strategy_type === "buy")
    return buyRequired.includes(step.module_id);
  if (
    draft.steps.some(
      (s) => s.module_id === "action.steam_list" && s.enabled !== false,
    )
  )
    return (
      sellRequired.includes(step.module_id) || pricing.includes(step.module_id)
    );
  return step.module_id === "action.pause_auto_sell";
}
export function defaultStep(module) {
  return {
    module_id: module.id,
    enabled: true,
    params: Object.fromEntries(
      Object.entries(module.params_schema || {})
        .filter(([, s]) => s.default !== undefined)
        .map(([key, s]) => [key, clone(s.default)]),
    ),
  };
}
export function structureErrors(
  draft,
  modules,
  limits = { buy: 22, sell: 14 },
) {
  const errors = [],
    map = new Map(modules.map((m) => [m.id, m]));
  const steps = draft.steps || [],
    enabled = steps.filter((s) => s.enabled !== false),
    ids = enabled.map((s) => s.module_id);
  if (steps.length > (limits[draft.strategy_type] || 22))
    errors.push("模块数量超过限制");
  for (const [index, step] of enabled.entries()) {
    const mod = map.get(step.module_id);
    if (!mod) {
      errors.push(`未知模块 ${step.module_id}`);
      continue;
    }
    if (!mod.strategy_types?.includes(draft.strategy_type))
      errors.push(`${mod.name} 不支持此策略类型`);
    if (
      mod.origin === "user" &&
      (mod.module_kind !== "declarative" || mod.enabled === false)
    )
      errors.push(`${mod.name} 仅登记展示，不能执行`);
    if ((mod.requires || []).some((id) => !ids.slice(0, index).includes(id)))
      errors.push(`${mod.name} 缺少前置依赖或顺序错误`);
    if (
      (mod.requires_any || []).some(
        (group) => !group.some((id) => ids.slice(0, index).includes(id)),
      )
    )
      errors.push(`${mod.name} 缺少前置模块`);
    if ((mod.conflicts || []).some((id) => ids.includes(id)))
      errors.push(`${mod.name} 存在互斥模块`);
  }
  for (const mod of modules)
    if (
      steps.filter((s) => s.module_id === mod.id).length >
      (mod.max_instances || 1)
    )
      errors.push(`${mod.name} 已达到数量限制`);
  const required =
    draft.strategy_type === "buy"
      ? buyRequired
      : ids.includes("action.steam_list")
        ? sellRequired
        : ["action.pause_auto_sell"];
  if (required.some((id) => !ids.includes(id)))
    errors.push("缺少固定安全模块或交易动作");
  if (
    ids.includes("action.steam_list") &&
    !pricing.some((id) => ids.includes(id))
  )
    errors.push("出售策略必须保留定价核心");
  return [...new Set(errors)];
}
export function strategySignature(draft) {
  return JSON.stringify({
    name: draft?.name,
    description: draft?.description,
    strategy_type: draft?.strategy_type,
    steps: draft?.steps,
  });
}
export function strategyDiff(previous, next, modules) {
  const name = (id) => modules.find((m) => m.id === id)?.name || id;
  const old = previous?.steps || [],
    nextSteps = next?.steps || [],
    changes = [];
  for (const [index, step] of nextSteps.entries()) {
    const before = old[index];
    if (!before || before.module_id !== step.module_id)
      changes.push(
        `第 ${index + 1} 步：${before ? name(before.module_id) : "无"} → ${name(step.module_id)}`,
      );
    else {
      if ((before.enabled !== false) !== (step.enabled !== false))
        changes.push(
          `${name(step.module_id)}：${step.enabled === false ? "停用" : "启用"}`,
        );
      for (const key of new Set([
        ...Object.keys(before.params || {}),
        ...Object.keys(step.params || {}),
      ]))
        if (
          JSON.stringify(before.params?.[key]) !==
          JSON.stringify(step.params?.[key])
        )
          changes.push(
            `${name(step.module_id)} / ${key}：${JSON.stringify(before.params?.[key]) ?? "默认"} → ${JSON.stringify(step.params?.[key]) ?? "默认"}`,
          );
    }
  }
  for (const step of old.slice(nextSteps.length))
    changes.push(`移除：${name(step.module_id)}`);
  return changes;
}
