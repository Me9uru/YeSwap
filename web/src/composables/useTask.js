import "element-plus/es/components/message/style/css";
import "element-plus/es/components/message-box/style/css";
import { ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
export function useTask() {
  const busy = ref(false);
  const error = ref("");
  async function run(action, success, { quiet = false } = {}) {
    if (busy.value) return;
    busy.value = true;
    error.value = "";
    try {
      const value = await action();
      if (success) ElMessage.success(success);
      return value;
    } catch (e) {
      if (e === "cancel" || e === "close") return;
      error.value = e.message || "操作失败，请检查连接";
      if (!quiet) ElMessage.error(error.value);
    } finally {
      busy.value = false;
    }
  }
  return { busy, error, run };
}
export async function confirmAction(message, title = "确认操作") {
  try {
    await ElMessageBox.confirm(message, title, {
      type: "warning",
      confirmButtonText: "确认",
      cancelButtonText: "取消",
      closeOnClickModal: false,
    });
    return true;
  } catch {
    return false;
  }
}
