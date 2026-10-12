import { reactive } from "vue";
import { api } from "../services/api.js";

// 自动刷新属于应用生命周期，切换页面不会中止库存扫描或重复创建计时器。
export const refreshState = reactive({
  items: [],
  prices: {},
  inventoryError: "",
  inventoryBusy: false,
  inventorySeconds: 600,
  priceMinutes: 10,
});
let inventoryTimer,
  priceTimer,
  accountPresent = false,
  stopped = true,
  priceBusy = false;
export async function refreshInventory(force = false) {
  if (refreshState.inventoryBusy) return;
  refreshState.inventoryBusy = true;
  try {
    const data = await api(`/inventory${force ? "?refresh=1" : ""}`);
    refreshState.inventoryError =
      data.error ||
      (data.auth_expired ? "Steam 登录已过期，请前往账号管理" : "");
    refreshState.items = data.items || [];
  } catch (error) {
    refreshState.inventoryError = error.message;
  } finally {
    refreshState.inventoryBusy = false;
  }
}
export async function refreshPrices() {
  if (priceBusy) return;
  priceBusy = true;
  try {
    const data = await api("/market-prices");
    if (data.error) throw new Error(data.error);
    refreshState.prices = data.prices || {};
  } finally {
    priceBusy = false;
  }
}
export function configureRefresh(config) {
  refreshState.inventorySeconds = Math.max(
    600,
    Number(config.inventory?.refresh_seconds) || 600,
  );
  refreshState.priceMinutes = Math.max(
    1,
    Number(config.pipeline?.current_price_refresh_minutes) || 10,
  );
  clearInterval(inventoryTimer);
  clearInterval(priceTimer);
  if (stopped) return;
  inventoryTimer = setInterval(() => {
    if (accountPresent) refreshInventory(true);
  }, refreshState.inventorySeconds * 1000);
  priceTimer = setInterval(() => {
    if (accountPresent) refreshPrices().catch(() => {});
  }, refreshState.priceMinutes * 60000);
}
export function setAccountPresent(value) {
  accountPresent = value;
}
export async function startRefresh() {
  stopped = false;
  try {
    const [config, accounts] = await Promise.all([
      api("/config"),
      api("/accounts"),
    ]);
    if (stopped) return;
    accountPresent = !!accounts.accounts?.length;
    configureRefresh(config.config || {});
    if (accountPresent) {
      refreshInventory(true);
      refreshPrices().catch(() => {});
    }
  } catch {
    /* 状态栏和设置页分别展示服务错误；不以失败配置启动刷新。 */
  }
}
export function stopRefresh() {
  stopped = true;
  clearInterval(inventoryTimer);
  clearInterval(priceTimer);
}
