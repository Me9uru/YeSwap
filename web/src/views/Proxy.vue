<script setup>
import "element-plus/es/components/alert/style/css";
import "element-plus/es/components/button/style/css";
import "element-plus/es/components/card/style/css";
import "element-plus/es/components/collapse/style/css";
import "element-plus/es/components/collapse-item/style/css";
import "element-plus/es/components/dialog/style/css";
import "element-plus/es/components/form/style/css";
import "element-plus/es/components/form-item/style/css";
import "element-plus/es/components/input/style/css";
import "element-plus/es/components/radio-button/style/css";
import "element-plus/es/components/radio-group/style/css";
import "element-plus/es/components/table/style/css";
import "element-plus/es/components/table-column/style/css";
import "element-plus/es/components/tag/style/css";
import {
  ElAlert,
  ElButton,
  ElCard,
  ElCollapse,
  ElCollapseItem,
  ElDialog,
  ElForm,
  ElFormItem,
  ElInput,
  ElRadioButton,
  ElRadioGroup,
  ElTable,
  ElTableColumn,
  ElTag,
} from "element-plus";
import NumberInput from "../widgets/NumberInput.vue";
import { computed, onMounted, ref } from "vue";
import { api, clone } from "../services/api.js";
import { useTask, confirmAction } from "../composables/useTask.js";
const { busy, error, run } = useTask();
const config = ref({
    strategy: 1,
    proxies: [],
    timeout_seconds: 10,
    test_url: "https://ipv4.webshare.io/",
    webshare_api_key: "",
  }),
  snapshot = ref(""),
  loaded = ref(false),
  results = ref([]),
  dialog = ref(false),
  bulk = ref("");
const entry = ref({ host: "", port: 8080, username: "", password: "" });
const dirty = computed(
  () => loaded.value && JSON.stringify(config.value) !== snapshot.value,
);
async function beforeLeave() {
  if (busy.value) return false;
  return (
    !dirty.value ||
    (await confirmAction("代理配置尚未保存，离开将丢弃修改。", "未保存的配置"))
  );
}
defineExpose({ beforeLeave, dirty });
async function load() {
  config.value = {
    ...config.value,
    ...(await api("/proxy/config")).proxy_pool,
  };
  config.value.proxies ||= [];
  snapshot.value = JSON.stringify(config.value);
  loaded.value = true;
}
onMounted(() => run(load));
async function save() {
  const body = clone(config.value);
  body.enabled = body.strategy !== 3;
  await api("/proxy/config", { method: "POST", body: { proxy_pool: body } });
  snapshot.value = JSON.stringify(config.value);
}
function add() {
  run(async () => {
    if (
      !entry.value.host.trim() ||
      !(entry.value.port > 0 && entry.value.port <= 65535)
    )
      throw new Error("请输入有效主机和端口");
    config.value.proxies.push(clone(entry.value));
    dialog.value = false;
    entry.value = { host: "", port: 8080, username: "", password: "" };
  });
}
function importBulk() {
  run(async () => {
    const entries = bulk.value
      .split(/\r?\n/)
      .filter((x) => x.trim())
      .map((line, index) => {
        const [host, port, username = "", ...password] = line.trim().split(":");
        if (
          !host ||
          !/^\d+$/.test(port || "") ||
          Number(port) < 1 ||
          Number(port) > 65535
        )
          throw new Error(`第 ${index + 1} 行格式错误`);
        return {
          host,
          port: Number(port),
          username,
          password: password.join(":"),
        };
      });
    for (const p of entries)
      if (
        !config.value.proxies.some(
          (x) => x.host === p.host && x.port === p.port,
        )
      )
        config.value.proxies.push(p);
    bulk.value = "";
  });
}
function test() {
  return run(async () => {
    await save();
    results.value =
      (await api("/proxy/test", { method: "POST" })).results || [];
  });
}
function result(row) {
  return results.value.find((r) => r.host === row.host && r.port === row.port);
}
</script>
<template>
  <div class="stack">
    <el-card>
      <div class="toolbar">
        <h2>连接策略</h2>
        <el-tag v-if="dirty" type="warning">未保存</el-tag>
        <el-button
          type="primary"
          :loading="busy"
          :disabled="!loaded"
          @click="run(save, '代理配置已保存')"
        >
          保存配置
        </el-button>
      </div>
      <el-form label-position="top" :disabled="busy || !loaded">
        <el-form-item label="代理模式">
          <el-radio-group v-model="config.strategy">
            <el-radio-button :value="1">本机优先，失败切换代理</el-radio-button>
            <el-radio-button :value="2">全部经代理池</el-radio-button>
            <el-radio-button :value="3">直连</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <div class="form-grid">
          <el-form-item label="测试 URL">
            <el-input v-model="config.test_url" />
          </el-form-item>
          <el-form-item label="超时（秒）">
            <NumberInput v-model="config.timeout_seconds" :min="1" />
          </el-form-item>
          <el-form-item label="Webshare API Key">
            <el-input
              v-model="config.webshare_api_key"
              type="password"
              show-password
              autocomplete="new-password"
            />
          </el-form-item>
        </div>
        <el-button
          :disabled="!config.webshare_api_key"
          @click="
            run(async () => {
              await save();
              await api('/proxy/webshare', { method: 'POST' });
              await load();
            }, '订阅已更新')
          "
        >
          获取 Webshare 订阅
        </el-button>
      </el-form>
      <el-alert
        v-if="error"
        :title="error"
        type="error"
        :closable="false"
        class="space-bottom"
      />
    </el-card>
    <el-card>
      <div class="toolbar">
        <h2>代理列表</h2>
        <el-button :disabled="busy || !loaded" @click="dialog = true">
          添加代理
        </el-button>
        <el-button
          :loading="busy"
          :disabled="!loaded || !config.proxies.length"
          @click="test"
        >
          保存并测试连通性
        </el-button>
        <el-button
          type="danger"
          plain
          :disabled="busy || !loaded"
          @click="
            run(async () => {
              if (await confirmAction('清空并保存全部代理？', '清空代理')) {
                await api('/proxy/clear', { method: 'POST' });
                await load();
              }
            })
          "
        >
          清空
        </el-button>
      </div>
      <el-table :data="config.proxies" empty-text="暂无代理">
        <el-table-column prop="host" label="主机" min-width="170" />
        <el-table-column prop="port" label="端口" width="90" />
        <el-table-column prop="username" label="用户名" min-width="120" />
        <el-table-column label="状态" min-width="130">
          <template #default="{ row }">
            <el-tag
              :type="
                result(row)?.status === 'ok'
                  ? 'success'
                  : result(row)
                    ? 'danger'
                    : 'info'
              "
            >
              {{
                result(row)?.status === "ok"
                  ? `${result(row).latency_ms} ms`
                  : result(row)
                    ? "连接失败"
                    : "未测试"
              }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="出口 IP" min-width="150">
          <template #default="{ row }">
            {{ result(row)?.ip_detected || "—" }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80">
          <template #default="{ $index }">
            <el-button
              link
              type="danger"
              :disabled="busy"
              @click="config.proxies.splice($index, 1)"
            >
              移除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-collapse>
        <el-collapse-item title="批量导入代理">
          <el-input
            v-model="bulk"
            type="textarea"
            :rows="4"
            placeholder="每行 host:port 或 host:port:username:password"
          />
          <el-button
            class="import-button"
            :disabled="busy || !loaded || !bulk.trim()"
            @click="importBulk"
          >
            加入列表（保存后生效）
          </el-button>
        </el-collapse-item>
      </el-collapse>
    </el-card>
    <el-dialog
      v-model="dialog"
      title="添加代理"
      width="var(--dialog-width)"
      :close-on-click-modal="false"
    >
      <el-form label-position="top">
        <el-form-item label="主机">
          <el-input v-model="entry.host" />
        </el-form-item>
        <el-form-item label="端口">
          <NumberInput
            v-model="entry.port"
            :min="1"
            :max="65535"
            :precision="0"
          />
        </el-form-item>
        <el-form-item label="用户名">
          <el-input v-model="entry.username" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="entry.password"
            type="password"
            show-password
            autocomplete="new-password"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" @click="add">添加到列表</el-button>
      </template>
    </el-dialog>
  </div>
</template>
<style scoped>
.import-button {
  margin-top: 12px;
}
.el-collapse {
  margin-top: 24px;
}
</style>
