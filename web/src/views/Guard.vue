<script setup>
import "element-plus/es/components/alert/style/css";
import "element-plus/es/components/button/style/css";
import "element-plus/es/components/card/style/css";
import "element-plus/es/components/progress/style/css";
import { ElAlert, ElButton, ElCard, ElProgress } from "element-plus";
import { onUnmounted, ref } from "vue";
import { api } from "../services/api.js";
import { useTask } from "../composables/useTask.js";
const { busy, error, run } = useTask();
const code = ref(""),
  remaining = ref(0),
  period = ref(30);
let timer,
  hideTimer,
  expires = 0,
  disposed = false;
async function refresh() {
  const d = await api("/steam_guard");
  if (disposed) return;
  code.value = d.code;
  period.value = d.period || 30;
  remaining.value =
    period.value -
    ((d.server_time || Math.floor(Date.now() / 1000)) % period.value);
  expires = Date.now() + remaining.value * 1000;
}
function hide() {
  clearInterval(timer);
  clearTimeout(hideTimer);
  code.value = "";
}
function show() {
  return run(async () => {
    await refresh();
    if (disposed) return;
    clearInterval(timer);
    timer = setInterval(() => {
      remaining.value = Math.max(0, Math.ceil((expires - Date.now()) / 1000));
      if (!remaining.value && !busy.value) {
        code.value = "";
        run(refresh);
      }
    }, 1000);
    clearTimeout(hideTimer);
    hideTimer = setTimeout(hide, 60000);
  });
}
onUnmounted(() => {
  disposed = true;
  hide();
});
</script>
<template>
  <el-card class="guard-card">
    <div class="empty-tip">
      <h2>动态验证码</h2>
      <p class="muted">验证码默认隐藏，显示一分钟后自动隐藏。</p>
      <div class="guard-code mono">{{ code || "•••••" }}</div>
      <el-progress
        v-if="code"
        :percentage="(remaining / period) * 100"
        :show-text="false"
      />
      <p class="muted">
        {{
          code ? `${remaining} 秒后刷新` : "请先在系统设置中配置 shared_secret"
        }}
      </p>
      <div class="guard-actions">
        <el-button type="primary" :loading="busy" @click="show">
          显示令牌
        </el-button>
        <el-button
          :disabled="!code"
          @click="
            run(() => navigator.clipboard.writeText(code), '已复制验证码')
          "
        >
          复制
        </el-button>
        <el-button v-if="code" @click="hide">隐藏</el-button>
      </div>
      <el-alert
        v-if="error"
        :title="error"
        type="error"
        :closable="false"
        class="space-bottom"
      />
    </div>
  </el-card>
</template>
<style scoped>
.guard-card {
  max-width: 620px;
  margin: 30px auto;
}
.guard-code {
  font-size: 48px;
  letter-spacing: 12px;
  color: var(--el-color-primary);
  margin: 45px 0;
}
.guard-actions {
  margin: 28px 0;
}
</style>
