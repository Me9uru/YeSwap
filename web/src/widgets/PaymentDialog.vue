<script setup>
import "element-plus/es/components/alert/style/css";
import "element-plus/es/components/button/style/css";
import "element-plus/es/components/descriptions/style/css";
import "element-plus/es/components/descriptions-item/style/css";
import "element-plus/es/components/dialog/style/css";
import "element-plus/es/components/link/style/css";
import {
  ElAlert,
  ElButton,
  ElDescriptions,
  ElDescriptionsItem,
  ElDialog,
  ElLink,
} from "element-plus";
import { computed, ref, watch } from "vue";
import QRCode from "qrcode";
import { appState, refreshOverview } from "../state/appState.js";
import { api, clone, paymentPayload } from "../services/api.js";
import { useTask, confirmAction } from "../composables/useTask.js";
const payment = ref(null),
  qr = ref("");
const { busy, error, run } = useTask();
// Polling cannot swap the order being reviewed while a confirmation is open.
watch(
  () => appState.payment,
  (value) => {
    if (!busy.value) payment.value = value ? clone(value) : null;
  },
  { immediate: true },
);
watch(
  () => payment.value?.pay_url,
  async (value) => {
    qr.value = "";
    if (!value) return;
    try {
      const image = await QRCode.toDataURL(value, { width: 220, margin: 2 });
      if (payment.value?.pay_url === value) qr.value = image;
    } catch {
      /* 链接仍可使用 */
    }
  },
);
const link = computed(() =>
  /^https?:\/\//i.test(payment.value?.pay_url || "")
    ? payment.value.pay_url
    : null,
);
function submit(ok) {
  return run(async () => {
    const payload = paymentPayload(payment.value, ok);
    if (
      !(await confirmAction(
        `订单 ${payload.expected_order_id}：${ok ? "确认已经完成付款？" : "确认未付款或付款失败？"}请与平台订单核对。`,
        "核对付款结果",
      ))
    )
      return;
    await api("/confirm_payment", { method: "POST", body: payload });
    payment.value = null;
    await refreshOverview();
  });
}
</script>
<template>
  <el-dialog
    :model-value="!!payment"
    title="待确认付款"
    width="var(--dialog-width)"
    :show-close="false"
    :close-on-click-modal="false"
    :close-on-press-escape="false"
  >
    <template v-if="payment">
      <el-alert
        title="请核对订单号与平台付款结果"
        type="warning"
        :closable="false"
        show-icon
        class="space-bottom"
      />
      <el-descriptions :column="1" border>
        <el-descriptions-item label="商品">
          {{ payment.name || "—" }}
        </el-descriptions-item>
        <el-descriptions-item label="订单号">
          <span class="mono">
            {{ payment.order_id || "缺少订单号，无法确认" }}
          </span>
        </el-descriptions-item>
      </el-descriptions>
      <div class="empty-tip">
        <img
          v-if="qr"
          :src="qr"
          width="220"
          height="220"
          alt="当前订单付款二维码"
        />
        <p>
          <el-link
            v-if="link"
            :href="link"
            target="_blank"
            rel="noopener noreferrer"
            type="primary"
          >
            打开支付链接
          </el-link>
        </p>
        <el-button
          v-if="payment.pay_url"
          @click="
            run(
              () => navigator.clipboard.writeText(payment.pay_url),
              '已复制支付链接',
            )
          "
        >
          复制支付链接
        </el-button>
      </div>
      <el-alert v-if="error" :title="error" type="error" :closable="false" />
    </template>
    <template #footer>
      <el-button
        :disabled="busy || !payment?.order_id || !appState.connected"
        @click="submit(false)"
      >
        未付款 / 失败
      </el-button>
      <el-button
        type="primary"
        :loading="busy"
        :disabled="!payment?.order_id || !appState.connected"
        @click="submit(true)"
      >
        我已完成付款
      </el-button>
    </template>
  </el-dialog>
</template>
