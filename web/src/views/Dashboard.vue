<script setup>
import "element-plus/es/components/alert/style/css";
import "element-plus/es/components/button/style/css";
import "element-plus/es/components/descriptions/style/css";
import "element-plus/es/components/descriptions-item/style/css";
import "element-plus/es/components/icon/style/css";
import "element-plus/es/components/tag/style/css";
import {
  ElAlert,
  ElButton,
  ElDescriptions,
  ElDescriptionsItem,
  ElIcon,
  ElTag,
} from "element-plus";
import { computed, onMounted, ref } from "vue";
import {
  VideoPlay,
  VideoPause,
  RefreshRight,
  ArrowRight,
  Warning,
} from "@element-plus/icons-vue";
import { appState, refreshOverview } from "../state/appState.js";
import { api, amount, clone } from "../services/api.js";
import { useTask, confirmAction } from "../composables/useTask.js";
defineEmits(["navigate"]);
const { busy, error, run } = useTask();
const accounts = ref([]),
  loaded = ref(false);
const statusText = computed(
  () =>
    ({
      running: "运行中",
      stopped: "已停止",
      error: "运行异常",
      idle: "待机中",
    })[appState.status.status] || "等待状态",
);
const stats = computed(() => [
  [
    "已售饰品成本",
    amount(appState.stats.total_purchased),
    "人民币 · 已售出记录的购入成本",
  ],
  [
    "累计售出",
    amount(appState.stats.total_sold),
    "人民币 · 买家支付金额（税前）",
  ],
  [
    "累计收益",
    amount(appState.stats.total_profit),
    "人民币 · 税后到账减购入成本",
  ],
  [
    "余额折扣率",
    appState.stats.discount_ratio == null
      ? "—"
      : Number(appState.stats.discount_ratio).toFixed(4),
    "逐笔购入成本 / 实际到账的平均值",
  ],
]);
onMounted(() =>
  run(async () => {
    accounts.value = (await api("/accounts")).accounts || [];
    loaded.value = true;
  }),
);
function start() {
  return run(async () => {
    if (
      !(await confirmAction(
        "启动后将按已保存策略执行真实交易流程。请确认账号、策略和资金限额。",
        "启动自动任务",
      ))
    )
      return;
    const data = await api("/config");
    const result = await api("/pipeline/start", {
      method: "POST",
      body: { config: data.config || {} },
    });
    if (result.reconciliation_required) appState.guard = result.checkout || {};
    await refreshOverview();
  });
}
function reconcile(outcome) {
  return run(async () => {
    const checkout = clone(appState.guard || {});
    if (!checkout.intent_id)
      throw new Error("无法识别待对账记录，请先备份并人工检查门禁文件");
    if (
      !(await confirmAction(
        `订单 ${checkout.order_id || checkout.batch_id || "未取得订单号"}：请先在 BUFF 核对${outcome === "cancelled_or_unpaid" ? "未付款或已取消" : "已人工处理完成；已付款但未记账时还需补录购入"}。解除后可继续下单。`,
        "解除 BUFF 对账门禁",
      ))
    )
      return;
    await api("/pipeline/buff_checkout_guard/reconcile", {
      method: "POST",
      body: { intent_id: checkout.intent_id, outcome },
    });
    await refreshOverview();
  });
}
</script>
<template>
  <div class="dashboard">
    <section class="operation-panel" aria-labelledby="operation-title">
      <div class="operation-topline">
        <div>
          <p class="section-kicker">任务状态</p>
          <h2 id="operation-title">自动交易{{ statusText }}</h2>
          <p class="operation-detail">
            {{
              appState.status.step ||
              (appState.connected ? "当前没有运行中的任务" : "正在获取任务状态")
            }}
          </p>
          <p
            v-if="appState.status.progress_item"
            class="muted operation-progress"
          >
            {{ appState.status.progress_item }}
          </p>
        </div>
        <span
          class="status-badge"
          :class="`status-${appState.status.status || 'unknown'}`"
        >
          <span class="status-dot"></span>{{ statusText }}
        </span>
      </div>
      <div class="operation-actions">
        <el-button
          type="primary"
          :icon="VideoPlay"
          :loading="busy"
          :disabled="
            !appState.connected ||
            !!appState.guard ||
            appState.status.status === 'running'
          "
          @click="start"
          >启动任务</el-button
        >
        <el-button
          :icon="VideoPause"
          :disabled="
            busy || !appState.connected || appState.status.status !== 'running'
          "
          @click="
            run(async () => {
              await api('/pipeline/stop', { method: 'POST' });
              await refreshOverview();
            }, '停止请求已发送')
          "
          >停止任务</el-button
        >
        <el-button
          :icon="RefreshRight"
          :disabled="busy"
          @click="refreshOverview"
          >刷新状态</el-button
        >
      </div>
      <p class="operation-hint">
        启动后将按已保存策略执行真实交易。请先核对账号、策略和资金限额。
      </p>
      <el-alert
        v-if="error"
        :title="error"
        type="error"
        :closable="false"
        class="operation-error"
      />
    </section>

    <section
      v-if="appState.guard"
      class="guard-panel"
      aria-labelledby="guard-title"
    >
      <div class="guard-heading">
        <el-icon><Warning /></el-icon>
        <div>
          <h2 id="guard-title">购买已暂停，等待人工对账</h2>
          <p>
            {{
              appState.guard.reason ||
              "请先在 BUFF 核对订单结果，避免重复购买。"
            }}
          </p>
        </div>
      </div>
      <el-descriptions :column="2" border class="guard-details">
        <el-descriptions-item label="阶段">{{
          appState.guard.stage || "—"
        }}</el-descriptions-item>
        <el-descriptions-item label="订单号">{{
          appState.guard.order_id || "—"
        }}</el-descriptions-item>
        <el-descriptions-item label="批次号">{{
          appState.guard.batch_id || "—"
        }}</el-descriptions-item>
        <el-descriptions-item label="数量 / 金额"
          >{{ appState.guard.quantity ?? "—" }} 件 / 人民币
          {{ amount(appState.guard.price) }}</el-descriptions-item
        >
      </el-descriptions>
      <div class="actions guard-actions">
        <el-button
          :loading="busy"
          :disabled="busy || !appState.connected"
          @click="reconcile('cancelled_or_unpaid')"
          >已核对：未付款 / 已取消</el-button
        >
        <el-button
          :loading="busy"
          :disabled="busy || !appState.connected"
          @click="reconcile('handled_externally')"
          >已核对：人工处理完成</el-button
        >
      </div>
    </section>

    <el-alert
      v-if="
        appState.status.buff_auth_expired ||
        appState.status.buff_verification_required ||
        appState.status.buff_no_cookie
      "
      title="BUFF 登录需要处理"
      :description="
        appState.status.buff_verification_reason ||
        '请在账号管理中完成 BUFF 登录或更新 Cookie。'
      "
      type="warning"
      :closable="false"
      show-icon
    />

    <section class="overview-section" aria-labelledby="overview-title">
      <div class="section-heading">
        <h2 id="overview-title">交易概览</h2>
        <span>按已记录的交易计算</span>
      </div>
      <div class="stats-grid">
        <div v-for="s in stats" :key="s[0]" class="stat-item">
          <div class="stat-label">{{ s[0] }}</div>
          <div class="stat-value">{{ s[1] }}</div>
          <p class="stat-note">{{ s[2] }}</p>
        </div>
      </div>
    </section>

    <section class="setup-section" aria-labelledby="setup-title">
      <div class="section-heading">
        <h2 id="setup-title">开始前检查</h2>
        <span>依次完成后再启动任务</span>
      </div>
      <div class="setup-list">
        <div class="setup-row">
          <span class="setup-number">1</span>
          <div class="setup-copy">
            <h3>交易账号</h3>
            <p v-if="loaded">
              {{
                accounts.length
                  ? `已添加 ${accounts.length} 个账号，请确认登录状态与结算币种。`
                  : "尚未添加 Steam 账号，请先完成账号配置。"
              }}
            </p>
            <p v-else>正在读取账号信息…</p>
          </div>
          <el-button
            text
            :icon="ArrowRight"
            :disabled="!loaded"
            @click="$emit('navigate', 'accounts')"
            >管理账号</el-button
          >
        </div>
        <div class="setup-row">
          <span class="setup-number">2</span>
          <div class="setup-copy">
            <h3>令牌与通知</h3>
            <p>检查交易确认凭据、付款确认方式和消息提醒。</p>
          </div>
          <el-button
            text
            :icon="ArrowRight"
            @click="$emit('navigate', 'settings')"
            >系统设置</el-button
          >
        </div>
        <div class="setup-row">
          <span class="setup-number">3</span>
          <div class="setup-copy">
            <h3>交易策略</h3>
            <p>检查筛选、定价和保护规则。模拟仅使用样例，不能代表实盘结果。</p>
          </div>
          <el-button
            text
            :icon="ArrowRight"
            @click="$emit('navigate', 'strategies')"
            >策略中心</el-button
          >
        </div>
      </div>
    </section>
  </div>
</template>
<style scoped>
.dashboard {
  display: grid;
  gap: 32px;
}
.operation-panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 28px 32px;
}
.operation-topline {
  display: flex;
  justify-content: space-between;
  gap: 24px;
}
.section-kicker {
  color: var(--muted);
  font-size: 13px;
  font-weight: 600;
  margin: 0 0 8px;
}
.operation-panel h2 {
  font-size: 20px;
  margin: 0 0 8px;
}
.operation-detail {
  margin: 0;
  color: var(--text);
}
.operation-progress {
  margin: 8px 0 0;
}
.status-badge {
  display: inline-flex;
  align-items: center;
  align-self: flex-start;
  gap: 8px;
  font-size: 13px;
  color: var(--muted);
  white-space: nowrap;
}
.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--muted);
}
.status-running .status-dot {
  background: var(--success);
}
.status-error .status-dot {
  background: var(--danger);
}
.operation-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 28px;
}
.operation-actions .el-button + .el-button {
  margin-left: 0;
}
.operation-hint {
  color: var(--muted);
  font-size: 13px;
  line-height: 1.6;
  margin: 16px 0 0;
}
.operation-error {
  margin-top: 16px;
}
.guard-panel {
  border: 1px solid var(--warning-border);
  border-radius: var(--radius);
  background: var(--warning-bg);
  padding: 24px;
}
.guard-heading {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}
.guard-heading .el-icon {
  color: var(--warning);
  font-size: 20px;
  margin-top: 2px;
}
.guard-heading h2 {
  margin: 0 0 6px;
}
.guard-heading p {
  margin: 0;
}
.guard-details {
  margin: 20px 0;
}
.section-heading {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 12px;
  border-bottom: 1px solid var(--border);
  padding-bottom: 12px;
  margin-bottom: 0;
}
.section-heading h2 {
  margin: 0;
}
.section-heading span {
  color: var(--muted);
  font-size: 13px;
}
.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 0;
  margin: 0;
}
.stat-item {
  padding: 24px 18px 0 0;
  min-width: 0;
}
.stat-item + .stat-item {
  border-left: 1px solid var(--border);
  padding-left: 18px;
}
.stat-label {
  font-size: 14px;
  margin-bottom: 10px;
}
.stat-value {
  font-size: 27px;
}
.stat-note {
  font-size: 13px;
  line-height: 1.5;
  margin-top: 8px;
}
.setup-list {
  display: grid;
}
.setup-row {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 18px 0;
  border-bottom: 1px solid var(--border);
}
.setup-number {
  width: 28px;
  height: 28px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  display: grid;
  place-items: center;
  color: var(--muted);
  font-size: 13px;
  flex: none;
}
.setup-copy {
  flex: 1;
  min-width: 0;
}
.setup-copy h3 {
  margin: 0 0 4px;
  font-size: 15px;
}
.setup-copy p {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.5;
}
.setup-row .el-button {
  flex: none;
}
@media (max-width: 900px) {
  .stats-grid {
    grid-template-columns: repeat(2, 1fr);
  }
  .stat-item:nth-child(3) {
    border-left: 0;
    padding-left: 0;
  }
}
@media (max-width: 600px) {
  .dashboard {
    gap: 28px;
  }
  .operation-panel {
    padding: 20px;
  }
  .operation-topline {
    gap: 8px;
  }
  .operation-panel h2 {
    font-size: 18px;
  }
  .operation-actions {
    display: grid;
    grid-template-columns: 1fr 1fr;
  }
  .operation-actions .el-button:first-child {
    grid-column: 1 / -1;
  }
  .operation-actions .el-button {
    width: 100%;
  }
  .section-heading {
    align-items: flex-start;
    flex-direction: column;
    gap: 2px;
  }
  .stats-grid {
    grid-template-columns: 1fr 1fr;
  }
  .stat-item {
    padding: 16px 8px 14px 0;
    border-bottom: 1px solid var(--border);
  }
  .stat-item + .stat-item {
    padding-left: 12px;
  }
  .stat-item:nth-child(3) {
    padding-left: 0;
  }
  .stat-value {
    font-size: 23px;
  }
  .setup-row {
    align-items: flex-start;
    gap: 10px;
    flex-wrap: wrap;
  }
  .setup-row .el-button {
    margin-left: 38px;
    padding-left: 0;
  }
  .guard-panel {
    padding: 18px;
  }
  .guard-actions .el-button {
    margin-left: 0;
    width: 100%;
  }
}
</style>
