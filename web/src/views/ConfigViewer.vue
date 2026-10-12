<script setup>
import "element-plus/es/components/alert/style/css";
import "element-plus/es/components/button/style/css";
import "element-plus/es/components/card/style/css";
import "element-plus/es/components/divider/style/css";
import "element-plus/es/components/form/style/css";
import "element-plus/es/components/form-item/style/css";
import "element-plus/es/components/input/style/css";
import "element-plus/es/components/loading/style/css";
import "element-plus/es/components/message/style/css";
import { computed, onMounted, ref } from "vue";
import {
  ElAlert,
  ElButton,
  ElCard,
  ElDivider,
  ElForm,
  ElFormItem,
  ElInput,
  ElLoading,
  ElMessage,
} from "element-plus";
import { api, clone } from "../services/api.js";
import { useTask, confirmAction } from "../composables/useTask.js";

const { busy, error, run } = useTask();
const loaded = ref(false);
const form = ref({ steam: {}, buff: {}, steam_guard: {}, steam_confirm: {} });
const baseline = ref({});
const fields = [
  ["steam_guard", "shared_secret", "shared_secret"],
  ["steam_confirm", "identity_secret", "identity_secret"],
  ["steam_confirm", "device_id", "device_id"],
];
const changed = (section, key) =>
  form.value[section]?.[key] !== baseline.value[section]?.[key];
const dirty = computed(
  () =>
    ["steam", "buff"].some((section) => changed(section, "cookies")) ||
    changed("buff", "user_agent") ||
    fields.some(([section, key]) => changed(section, key)),
);
async function beforeLeave() {
  if (busy.value) return false;
  return (
    !dirty.value ||
    (await confirmAction("凭证修改尚未保存，离开会丢弃输入。", "未保存的凭证"))
  );
}
defineExpose({ beforeLeave, dirty });

async function load() {
  const [config, credentials] = await Promise.all([
    api("/config"),
    api("/config/credentials"),
  ]);
  form.value = {
    steam: { cookies: credentials.steam?.cookies || "" },
    buff: {
      cookies: credentials.buff?.cookies || "",
      user_agent: credentials.buff?.user_agent || "",
    },
    steam_guard: {
      shared_secret: config.config?.steam_guard?.shared_secret || "",
    },
    steam_confirm: {
      identity_secret: config.config?.steam_confirm?.identity_secret || "",
      device_id: config.config?.steam_confirm?.device_id || "",
    },
  };
  baseline.value = clone(form.value);
  loaded.value = true;
}
onMounted(() => run(load));

function saveSecrets() {
  return run(async () => {
    const patch = {};
    for (const [section, key] of fields) {
      if (changed(section, key))
        (patch[section] ||= {})[key] = form.value[section][key];
    }
    if (!Object.keys(patch).length) return;
    await api("/config", { method: "POST", body: { config: patch } });
    for (const [section, key] of fields)
      baseline.value[section][key] = form.value[section][key];
    ElMessage.success("密钥已保存");
  });
}
function saveCookie(platform) {
  return run(async () => {
    const cookies = form.value[platform].cookies.trim();
    if (!cookies) throw new Error("Cookie 不能为空");
    await api(`/auth/${platform}/manual_cookie`, {
      method: "POST",
      body: {
        cookies,
        ...(platform === "buff"
          ? { user_agent: form.value.buff.user_agent.trim() }
          : {}),
      },
    });
    await load();
    ElMessage.success("Cookie 已验证并保存");
  });
}
const vLoading = ElLoading.directive;
</script>

<template>
  <div v-loading="busy && !loaded" class="stack">
    <el-alert
      title="此页包含完整登录 Cookie 和令牌密钥。点击眼睛图标可查看原值；修改 Cookie 时会按平台现有校验流程保存。"
      type="warning"
      :closable="false"
    />
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <template v-if="loaded">
      <el-card>
        <template #header><h2>平台 Cookie</h2></template>
        <el-form label-position="top" :disabled="busy">
          <el-form-item label="Steam Cookie">
            <el-input
              v-model="form.steam.cookies"
              type="password"
              show-password
              autocomplete="new-password"
            />
          </el-form-item>
          <el-button
            type="primary"
            :disabled="!changed('steam', 'cookies')"
            :loading="busy"
            @click="saveCookie('steam')"
            >保存 Steam Cookie</el-button
          >
          <el-divider />
          <el-form-item label="BUFF Cookie">
            <el-input
              v-model="form.buff.cookies"
              type="password"
              show-password
              autocomplete="new-password"
            />
          </el-form-item>
          <el-form-item label="BUFF User-Agent">
            <el-input v-model="form.buff.user_agent" autocomplete="off" />
          </el-form-item>
          <el-button
            type="primary"
            :disabled="
              !changed('buff', 'cookies') && !changed('buff', 'user_agent')
            "
            :loading="busy"
            @click="saveCookie('buff')"
            >验证并保存 BUFF Cookie</el-button
          >
        </el-form>
      </el-card>
      <el-card>
        <template #header><h2>Steam 令牌密钥</h2></template>
        <el-form label-position="top" :disabled="busy">
          <el-form-item
            v-for="[section, key, label] in fields"
            :key="key"
            :label="label"
          >
            <el-input
              v-model="form[section][key]"
              type="password"
              show-password
              autocomplete="new-password"
            />
          </el-form-item>
          <el-button
            type="primary"
            :disabled="!fields.some(([section, key]) => changed(section, key))"
            :loading="busy"
            @click="saveSecrets"
            >保存密钥</el-button
          >
        </el-form>
      </el-card>
    </template>
  </div>
</template>
