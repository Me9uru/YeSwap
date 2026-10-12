<script setup>
import "element-plus/es/components/alert/style/css";
import "element-plus/es/components/avatar/style/css";
import "element-plus/es/components/button/style/css";
import "element-plus/es/components/card/style/css";
import "element-plus/es/components/dialog/style/css";
import "element-plus/es/components/divider/style/css";
import "element-plus/es/components/form/style/css";
import "element-plus/es/components/form-item/style/css";
import "element-plus/es/components/input/style/css";
import "element-plus/es/components/loading/style/css";
import "element-plus/es/components/message/style/css";
import "element-plus/es/components/table/style/css";
import "element-plus/es/components/table-column/style/css";
import "element-plus/es/components/tag/style/css";
import { computed, onMounted, ref } from "vue";
import {
  ElAlert,
  ElAvatar,
  ElButton,
  ElCard,
  ElDialog,
  ElDivider,
  ElForm,
  ElFormItem,
  ElInput,
  ElLoading,
  ElMessage,
  ElTable,
  ElTableColumn,
  ElTag,
} from "element-plus";
import { api, clone } from "../services/api.js";
import { setAccountPresent } from "../state/refreshState.js";
import { useTask, confirmAction } from "../composables/useTask.js";
const { busy, error, run } = useTask();
const accounts = ref([]),
  current = ref(""),
  search = ref(""),
  edit = ref(false),
  editId = ref(""),
  form = ref({});
const login = ref(""),
  started = ref(false),
  cookies = ref("");
async function beforeLeave() {
  if (busy.value) return false;
  if (started.value) {
    ElMessage.warning("请先完成或取消正在进行的浏览器登录");
    return false;
  }
  return (
    !edit.value ||
    (await confirmAction("账号编辑尚未保存，离开会丢弃输入。", "离开账号编辑"))
  );
}
defineExpose({ beforeLeave });
const filtered = computed(() =>
  accounts.value.filter((a) =>
    [a.display_name, a.username, a.steam_id]
      .join(" ")
      .toLowerCase()
      .includes(search.value.toLowerCase()),
  ),
);
async function load() {
  const d = await api("/accounts");
  accounts.value = d.accounts || [];
  setAccountPresent(accounts.value.length > 0);
  current.value = d.current_id;
}
onMounted(() => run(load));
function open(account) {
  editId.value = account?.id || "";
  form.value = {
    username: account?.username || "",
    steam_id: account?.steam_id || "",
    display_name: account?.display_name || "",
    password: "",
  };
  edit.value = true;
}
function save() {
  return run(async () => {
    const body = clone(form.value);
    if (!body.username.trim() && !body.steam_id.trim())
      throw new Error("请填写用户名或 Steam ID");
    if (editId.value && !body.password) delete body.password;
    await api(
      editId.value
        ? `/accounts/${encodeURIComponent(editId.value)}`
        : "/accounts",
      { method: editId.value ? "PUT" : "POST", body },
    );
    form.value.password = "";
    edit.value = false;
    await load();
    ElMessage.success("账号已保存");
  });
}
function accountAction(account, action) {
  return run(async () => {
    const name = account.display_name || account.username || account.steam_id;
    if (
      !(await confirmAction(
        `${action === "delete" ? "删除" : action === "verify" ? "验证登录" : "切换当前"}账号「${name}」？`,
        "账号操作",
      ))
    )
      return;
    const result = await api(
      `/accounts/${encodeURIComponent(account.id)}${action === "delete" ? "" : `/${action}`}`,
      { method: action === "delete" ? "DELETE" : "POST" },
    );
    ElMessage.success(result.message || "账号操作已完成");
    await load();
  });
}
function openLogin(type) {
  login.value = type;
  started.value = false;
  cookies.value = "";
}
function startLogin() {
  return run(async () => {
    await api(`/auth/${login.value}/relogin_start`, { method: "POST" });
    started.value = true;
    ElMessage.info("请在打开的浏览器中完成登录");
  });
}
function finish(success) {
  return run(async () => {
    await api(`/auth/${login.value}/relogin_finish`, {
      method: "POST",
      body: { success },
    });
    login.value = "";
    started.value = false;
    await load();
  });
}
function manual() {
  return run(async () => {
    if (!cookies.value.trim()) throw new Error("请填写 Cookie");
    await api(`/auth/${login.value}/manual_cookie`, {
      method: "POST",
      body: {
        cookies: cookies.value.trim(),
        ...(login.value === "buff" ? { user_agent: navigator.userAgent } : {}),
      },
    });
    cookies.value = "";
    login.value = "";
    await load();
    ElMessage.success("Cookie 已验证并保存");
  });
}
const vLoading = ElLoading.directive;
</script>
<template>
  <div class="stack">
    <el-card>
      <div class="toolbar">
        <h2>平台登录</h2>
        <el-button :disabled="busy" @click="openLogin('steam')">
          登录 Steam
        </el-button>
        <el-button
          type="primary"
          plain
          :disabled="busy"
          @click="openLogin('buff')"
        >
          登录 BUFF
        </el-button>
      </div>
      <p class="muted">
        使用本机浏览器完成授权，也可手动更新 Cookie。BUFF
        会话将进行在线校验，受限会话不会自动解除交易保护。
      </p>
    </el-card>
    <el-card>
      <div class="toolbar">
        <h2>Steam 账号</h2>
        <el-input
          v-model="search"
          placeholder="搜索名称、用户名或 Steam ID"
          clearable
        />
        <el-button :loading="busy" @click="run(load)">刷新</el-button>
        <el-button type="primary" :disabled="busy" @click="open()">
          添加账号
        </el-button>
      </div>
      <el-alert
        v-if="error"
        :title="error"
        type="error"
        :closable="false"
        class="space-bottom"
      />
      <el-table
        v-loading="busy"
        :data="filtered"
        empty-text="暂无账号，点击添加账号开始配置"
      >
        <el-table-column label="账号" min-width="210">
          <template #default="{ row }">
            <div class="account-name">
              <el-avatar :src="row.avatar_url">
                {{ (row.display_name || row.username || "A")[0] }}
              </el-avatar>
              <div>
                {{ row.display_name || row.username || "未命名" }}
                <p class="muted">{{ row.username }}</p>
              </div>
              <el-tag v-if="row.id === current" size="small">当前</el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="steam_id" label="Steam ID" min-width="175" />
        <el-table-column prop="region_code" label="地区" width="90" />
        <el-table-column prop="currency_code" label="结算币种" width="110" />
        <el-table-column label="操作" min-width="250">
          <template #default="{ row }">
            <el-button link type="primary" :disabled="busy" @click="open(row)">
              编辑
            </el-button>
            <el-button
              link
              type="primary"
              :disabled="busy"
              @click="accountAction(row, 'verify')"
            >
              验证
            </el-button>
            <el-button
              v-if="row.id !== current"
              link
              type="primary"
              :disabled="busy"
              @click="accountAction(row, 'set_current')"
            >
              设为当前
            </el-button>
            <el-button
              link
              type="danger"
              :disabled="busy"
              @click="accountAction(row, 'delete')"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
    <el-dialog
      v-model="edit"
      :title="editId ? '编辑账号' : '添加账号'"
      width="var(--dialog-width)"
      :close-on-click-modal="false"
      :before-close="
        (done) => {
          if (!busy) done();
        }
      "
      @closed="form.password = ''"
    >
      <el-form label-position="top" :disabled="busy">
        <el-form-item label="Steam 用户名">
          <el-input v-model="form.username" autocomplete="off" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            autocomplete="new-password"
            :placeholder="
              editId ? '留空保留现有密码' : '可选，用于本机登录填充'
            "
          />
        </el-form-item>
        <el-form-item label="Steam ID">
          <el-input v-model="form.steam_id" />
        </el-form-item>
        <el-form-item label="显示名称">
          <el-input v-model="form.display_name" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :disabled="busy" @click="edit = false">取消</el-button>
        <el-button type="primary" :loading="busy" @click="save">
          保存账号
        </el-button>
      </template>
    </el-dialog>
    <el-dialog
      :model-value="!!login"
      :title="login === 'steam' ? 'Steam 登录' : 'BUFF 登录'"
      width="var(--dialog-width)"
      :show-close="!started && !busy"
      :close-on-click-modal="false"
      :close-on-press-escape="false"
      @close="
        login = '';
        cookies = '';
      "
    >
      <div class="stack">
        <el-alert
          title="先打开浏览器，完成登录后再提取登录信息"
          type="info"
          :closable="false"
        />
        <div class="actions">
          <el-button
            type="primary"
            :loading="busy"
            :disabled="started"
            @click="startLogin"
          >
            打开登录浏览器
          </el-button>
          <el-button :disabled="!started || busy" @click="finish(true)">
            已完成登录
          </el-button>
          <el-button v-if="started" :disabled="busy" @click="finish(false)">
            取消登录
          </el-button>
        </div>
        <el-divider>手动 Cookie</el-divider>
        <el-input
          v-model="cookies"
          type="password"
          show-password
          autocomplete="new-password"
          placeholder="粘贴完整 Cookie"
          :disabled="started || busy"
        />
        <el-button
          :disabled="started || busy || !cookies.trim()"
          @click="manual"
        >
          验证并保存 Cookie
        </el-button>
        <el-alert v-if="error" :title="error" type="error" :closable="false" />
      </div>
    </el-dialog>
  </div>
</template>
<style scoped>
.account-name {
  display: flex;
  align-items: center;
  gap: 12px;
}
.account-name p {
  margin: 3px 0 0;
}
</style>
