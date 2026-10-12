import { reactive } from "vue";
import { api } from "../services/api.js";
export const appState = reactive({
  status: {},
  stats: {},
  payment: null,
  guard: null,
  connected: false,
  updated: "",
  error: "",
});
let refreshing = false;
export async function refreshOverview() {
  if (refreshing) return;
  refreshing = true;
  try {
    const results = await Promise.allSettled([
      api("/status"),
      api("/stats"),
      api("/pending_payment"),
      api("/pipeline/buff_checkout_guard"),
    ]);
    const keys = ["status", "stats", "payment", "guard"];
    results.forEach((result, index) => {
      if (result.status !== "fulfilled") return;
      const value = result.value;
      appState[keys[index]] =
        index === 2
          ? value.pending
          : index === 3
            ? value.reconciliation_required
              ? value.checkout || {}
              : null
            : value;
    });
    appState.connected = results.every((r) => r.status === "fulfilled");
    appState.error = appState.connected
      ? ""
      : "部分状态刷新失败，当前显示可能已过期；请恢复连接后再操作。";
    if (appState.connected)
      appState.updated = new Date().toLocaleTimeString("zh-CN");
  } finally {
    refreshing = false;
  }
}
