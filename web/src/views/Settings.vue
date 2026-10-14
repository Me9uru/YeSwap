<script setup>
import "element-plus/es/components/alert/style/css";
import "element-plus/es/components/button/style/css";
import "element-plus/es/components/card/style/css";
import "element-plus/es/components/form/style/css";
import "element-plus/es/components/form-item/style/css";
import "element-plus/es/components/input/style/css";
import "element-plus/es/components/loading/style/css";
import "element-plus/es/components/message/style/css";
import "element-plus/es/components/message-box/style/css";
import "element-plus/es/components/option/style/css";
import "element-plus/es/components/select/style/css";
import "element-plus/es/components/switch/style/css";
import "element-plus/es/components/tag/style/css";
import NumberInput from "../widgets/NumberInput.vue";
import { computed, onMounted, ref } from "vue";
import {
  ElAlert,
  ElButton,
  ElCard,
  ElForm,
  ElFormItem,
  ElInput,
  ElLoading,
  ElMessage,
  ElMessageBox,
  ElOption,
  ElSelect,
  ElSwitch,
  ElTag,
} from "element-plus";
import { api, clone } from "../services/api.js";
import { settingsGroups, configPatch } from "../services/configForm.js";
import { configureRefresh } from "../state/refreshState.js";
import { useTask, confirmAction } from "../composables/useTask.js";
const { busy, error, run } = useTask();
const form = ref({}),
  baseline = ref({}),
  loaded = ref(false),
  input = ref(null);
const dirty = computed(
  () =>
    loaded.value &&
    Object.keys(configPatch(form.value, baseline.value)).length > 0,
);
async function beforeLeave() {
  if (busy.value) return false;
  return (
    !dirty.value ||
    (await confirmAction("设置尚未保存，离开将丢弃修改。", "未保存的设置"))
  );
}
defineExpose({ beforeLeave, dirty });
async function load() {
  const d = await api("/config");
  form.value = clone(d.config || {});
  for (const g of settingsGroups)
    for (const f of g.fields) form.value[f[0]] ||= {};
  baseline.value = clone(form.value);
  configureRefresh(form.value);
  loaded.value = true;
}
onMounted(() => run(load));
function save() {
  return run(async () => {
    const patch = configPatch(form.value, baseline.value);
    if (
      patch.pipeline?.start_time_hour !== undefined ||
      patch.pipeline?.end_time_hour !== undefined ||
      patch.pipeline?.start_time_limit_enabled !== undefined
    ) {
      patch.system = {
        ...patch.system,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        timezone_offset_minutes: -new Date().getTimezoneOffset(),
      };
    }
    for (const g of settingsGroups)
      for (const [section, key, label, type, min, max] of g.fields) {
        if (patch[section]?.[key] === undefined) continue;
        const value = patch[section][key];
        if (
          ["number", "integer"].includes(type) &&
          (!Number.isFinite(value) ||
            value < min ||
            (max != null && value > max) ||
            (type === "integer" && !Number.isInteger(value)))
        )
          throw new Error(`请检查「${label}」的有效范围`);
      }
    await api("/config", { method: "POST", body: { config: patch } });
    await load();
    ElMessage.success("设置已保存");
  });
}
function restore(event) {
  const file = event.target.files[0];
  event.target.value = "";
  if (!file) return;
  run(async () => {
    const value = JSON.parse(await file.text());
    if (
      !(await confirmAction(
        "导入会写入配置；完整备份还会恢复账号、凭证与交易数据。请确认备份来源并先导出现有数据。",
        "恢复备份",
      ))
    )
      return;
    const full =
      value.version != null ||
      value.app_config != null ||
      value.credentials != null ||
      value.transactions != null ||
      value.accounts != null;
    await api(full ? "/import_full" : "/config", {
      method: "POST",
      body: full ? value : { config: value },
    });
    await load();
    ElMessage.success("数据已恢复");
  });
}
function reset() {
  return run(async () => {
    const result = await ElMessageBox.prompt(
      "此操作会清除账号、凭证、交易记录和配置。请输入“清除全部数据”确认。",
      "恢复出厂设置",
      {
        inputValidator: (value) =>
          value === "清除全部数据" || "请输入完整确认文字",
        confirmButtonText: "清除",
        cancelButtonText: "取消",
        type: "warning",
      },
    );
    if (result.value === "清除全部数据") {
      await api("/data/init", { method: "POST" });
      location.reload();
    }
  });
}
function maintain(path, title) {
  return run(async () => {
    if (
      !(await confirmAction(
        `${title}将核对平台信息并更新本地记录，是否继续？`,
        title,
      ))
    )
      return;
    const result = await api(path, { method: "POST" });
    ElMessage.success(result.message || `${title}已完成`);
  });
}
const vLoading = ElLoading.directive;
</script>
<template>
  <div v-loading="busy && !loaded" class="stack">
    <div class="toolbar">
      <el-tag v-if="dirty" type="warning">有未保存的修改</el-tag>
      <el-button
        type="primary"
        :loading="busy"
        :disabled="!loaded || !dirty"
        @click="save"
      >
        保存设置
      </el-button>
      <span class="muted">交易规则请在策略中心调整</span>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <template v-if="loaded">
      <el-card v-for="group in settingsGroups" :key="group.title">
        <template #header>
          <h2>{{ group.title }}</h2>
        </template>
        <el-form label-position="top" :disabled="busy">
          <div class="form-grid">
            <el-form-item
              v-for="field in group.fields"
              :key="field[0] + field[1]"
              :label="field[2]"
            >
              <el-switch
                v-if="field[3] === 'boolean'"
                v-model="form[field[0]][field[1]]"
              />
              <NumberInput
                v-else-if="['number', 'integer'].includes(field[3])"
                v-model="form[field[0]][field[1]]"
                :min="field[4]"
                :max="field[5]"
                :precision="field[3] === 'integer' ? 0 : undefined"
                :step="field[6] ?? (field[3] === 'integer' ? 1 : 0.5)"
              />
              <el-select
                v-else-if="field[3] === 'select'"
                v-model="form[field[0]][field[1]]"
              >
                <el-option
                  v-for="option in field[4]"
                  :key="option[0]"
                  :label="option[1]"
                  :value="option[0]"
                />
              </el-select>
              <el-input
                v-else
                v-model="form[field[0]][field[1]]"
                :type="field[3] === 'password' ? 'password' : 'text'"
                :show-password="field[3] === 'password'"
                autocomplete="new-password"
              />
            </el-form-item>
          </div>
        </el-form>
      </el-card>
      <el-card>
        <template #header><h2>数据与维护</h2></template>
        <p class="muted">
          完整备份包含账号凭证，请妥善保管。恢复或清除数据前请先备份。
        </p>
        <div class="actions">
          <el-button
            tag="a"
            href="/api/export_full/download"
            target="_blank"
            rel="noopener"
          >
            导出完整备份
          </el-button>
          <el-button :disabled="busy" @click="input.click()">
            恢复备份 / 配置
          </el-button>
          <el-button
            :disabled="busy"
            @click="maintain('/sync_sold_from_history', '同步售出与持有')"
          >
            同步售出 / 持有
          </el-button>
          <el-button
            type="warning"
            plain
            :disabled="busy"
            @click="maintain('/repair_error_records', '修复记录')"
          >
            修复记录
          </el-button>
          <el-button type="danger" plain :disabled="busy" @click="reset">
            恢复出厂设置
          </el-button>
        </div>
        <input
          ref="input"
          class="file-input"
          type="file"
          accept=".json"
          @change="restore"
        />
      </el-card>
    </template>
  </div>
</template>
