<script setup>
import "element-plus/es/components/alert/style/css";
import "element-plus/es/components/button/style/css";
import "element-plus/es/components/card/style/css";
import "element-plus/es/components/empty/style/css";
import "element-plus/es/components/input/style/css";
import "element-plus/es/components/option/style/css";
import "element-plus/es/components/select/style/css";
import "element-plus/es/components/switch/style/css";
import "element-plus/es/components/tag/style/css";
import {
  ElAlert,
  ElButton,
  ElCard,
  ElEmpty,
  ElInput,
  ElOption,
  ElSelect,
  ElSwitch,
  ElTag,
} from "element-plus";
import { computed, nextTick, onMounted, onUnmounted, ref } from "vue";
import { api, dateTime, download } from "../services/api.js";
import { useTask, confirmAction } from "../composables/useTask.js";
const { busy, error, run } = useTask();
const lines = ref([]),
  search = ref(""),
  level = ref("all"),
  paused = ref(false),
  autoScroll = ref(true),
  output = ref(null);
const filtered = computed(() =>
  lines.value.filter(
    (l) =>
      (level.value === "all" || l.level === level.value) &&
      (l.msg || "").toLowerCase().includes(search.value.toLowerCase()),
  ),
);
let timer;
async function load() {
  const data = await api("/log?since=0");
  lines.value = (data.lines || []).slice(-3000);
  await nextTick();
  if (autoScroll.value && output.value)
    output.value.scrollTop = output.value.scrollHeight;
}
onMounted(() => {
  run(load);
  timer = setInterval(() => {
    if (!paused.value) run(load, undefined, { quiet: true });
  }, 2500);
});
onUnmounted(() => clearInterval(timer));
function text() {
  return filtered.value
    .map((l) => `${dateTime(l.t)} [${l.level || "info"}] ${l.msg || ""}`)
    .join("\n");
}
</script>
<template>
  <el-card>
    <div class="toolbar">
      <el-input v-model="search" clearable placeholder="搜索日志内容" />
      <el-select v-model="level" style="width: 130px">
        <el-option label="全部级别" value="all" />
        <el-option
          v-for="l in ['info', 'warn', 'error', 'debug']"
          :key="l"
          :label="l"
          :value="l"
        />
      </el-select>
      <el-tag type="info">{{ filtered.length }} 行</el-tag>
      <el-switch v-model="autoScroll" active-text="自动滚动" />
      <el-button @click="paused = !paused">
        {{ paused ? "继续刷新" : "暂停刷新" }}
      </el-button>
      <el-button @click="download('运行日志.txt', text(), 'text/plain')">
        下载日志
      </el-button>
      <el-button
        :disabled="busy"
        @click="
          run(
            () => api('/log/export', { method: 'POST' }),
            '日志已保存到服务端',
          )
        "
      >
        保存到服务端
      </el-button>
      <el-button
        type="danger"
        plain
        :disabled="busy"
        @click="
          run(async () => {
            if (await confirmAction('清空服务端运行日志？', '清空日志')) {
              await api('/log/clear', { method: 'POST' });
              await load();
            }
          })
        "
      >
        清空日志
      </el-button>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <div ref="output" class="log-console mono" role="log" aria-label="运行日志">
      <p
        v-for="(line, index) in filtered"
        :key="line.id || index"
        :class="line.level"
      >
        <span>{{ dateTime(line.t) }}</span>
        [{{ line.level || "info" }}]
        {{ line.msg }}
      </p>
      <el-empty v-if="!filtered.length" description="暂无匹配日志" />
    </div>
  </el-card>
</template>
<style scoped>
.log-console {
  background: #151d2c;
  color: #d7e1f2;
  border-radius: 8px;
  height: min(65vh, 760px);
  overflow: auto;
  padding: 20px;
  font-size: 13px;
  line-height: 1.9;
}
.log-console p {
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.log-console span {
  color: #7e8fa9;
}
.error {
  color: #ff8c8c;
}
.warn {
  color: #e9c376;
}
.debug {
  color: #8aa7d4;
}
</style>
