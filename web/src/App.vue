<script setup>
import "element-plus/es/components/alert/style/css";
import "element-plus/es/components/breadcrumb/style/css";
import "element-plus/es/components/breadcrumb-item/style/css";
import "element-plus/es/components/button/style/css";
import "element-plus/es/components/config-provider/style/css";
import "element-plus/es/components/icon/style/css";
import "element-plus/es/components/menu/style/css";
import "element-plus/es/components/menu-item/style/css";
import "element-plus/es/components/scrollbar/style/css";
import "element-plus/es/components/tag/style/css";
import {
  ElAlert,
  ElBreadcrumb,
  ElBreadcrumbItem,
  ElButton,
  ElConfigProvider,
  ElIcon,
  ElMenu,
  ElMenuItem,
  ElScrollbar,
  ElTag,
} from "element-plus";
import {
  computed,
  defineAsyncComponent,
  onMounted,
  onUnmounted,
  ref,
  watch,
} from "vue";
import {
  DataAnalysis,
  Box,
  Tickets,
  TrendCharts,
  User,
  Key,
  Connection,
  Setting,
  Document,
  Operation,
  Fold,
  Sunny,
  Moon,
} from "@element-plus/icons-vue";
import Dashboard from "./views/Dashboard.vue";
import zhCn from "element-plus/es/locale/lang/zh-cn";
const Inventory = defineAsyncComponent(() => import("./views/Inventory.vue"));
const Transactions = defineAsyncComponent(
  () => import("./views/Transactions.vue"),
);
const Analytics = defineAsyncComponent(() => import("./views/Analytics.vue"));
const Accounts = defineAsyncComponent(() => import("./views/Accounts.vue"));
const Strategies = defineAsyncComponent(() => import("./views/Strategies.vue"));
const Settings = defineAsyncComponent(() => import("./views/Settings.vue"));
const ConfigViewer = defineAsyncComponent(
  () => import("./views/ConfigViewer.vue"),
);
const Proxy = defineAsyncComponent(() => import("./views/Proxy.vue"));
const Guard = defineAsyncComponent(() => import("./views/Guard.vue"));
const Logs = defineAsyncComponent(() => import("./views/Logs.vue"));
const PaymentDialog = defineAsyncComponent(
  () => import("./widgets/PaymentDialog.vue"),
);
import { appState, refreshOverview } from "./state/appState.js";
import { startRefresh, stopRefresh } from "./state/refreshState.js";
const sections = [
  {
    label: "交易工作台",
    items: [
      ["dashboard", "总览", DataAnalysis, Dashboard, "交易运行状态与资金概览"],
      ["inventory", "Steam 库存", Box, Inventory, "查看当前账号资产与交易冷却"],
      [
        "purchases",
        "持有饰品",
        Tickets,
        Transactions,
        "跟踪持仓、上架与实际到账",
      ],
      ["sales", "售出记录", TrendCharts, Transactions, "查看已完成的售出记录"],
      [
        "analytics",
        "数据分析",
        DataAnalysis,
        Analytics,
        "按饰品复盘成本与余额转化",
      ],
      ["strategies", "策略中心", Operation, Strategies, "配置购入与出售流程"],
    ],
  },
  {
    label: "管理与配置",
    items: [
      ["accounts", "账号管理", User, Accounts, "管理账号与平台登录状态"],
      ["guard", "Steam 令牌", Key, Guard, "查看动态验证码"],
      ["proxy", "代理网络", Connection, Proxy, "配置网络出口与连通性"],
      ["settings", "系统设置", Setting, Settings, "全局配置、通知与数据管理"],
      [
        "config-viewer",
        "配置查看",
        Key,
        ConfigViewer,
        "查看和修改 Cookie 与令牌密钥",
      ],
      ["logs", "运行日志", Document, Logs, "查看执行过程与异常信息"],
    ],
  },
];
const pages = sections.flatMap((s) => s.items);
const active = ref("dashboard");
const current = computed(() => pages.find((p) => p[0] === active.value));
const page = ref(null),
  collapsed = ref(false),
  mobile = ref(false);
const menu = ref(null);
const dark = ref(localStorage.getItem("aetherswap-element-theme") === "dark");
const paymentActivated = ref(!!appState.payment);
watch(
  () => appState.payment,
  (payment) => {
    if (payment) paymentActivated.value = true;
  },
);
function applyTheme() {
  document.documentElement.classList.toggle("dark", dark.value);
  localStorage.setItem(
    "aetherswap-element-theme",
    dark.value ? "dark" : "light",
  );
}
async function navigate(name) {
  if (name === active.value || !pages.some((p) => p[0] === name)) return;
  if (page.value?.beforeLeave && !(await page.value.beforeLeave())) {
    menu.value?.updateActiveIndex(active.value);
    return;
  }
  active.value = name;
  mobile.value = false;
}
function beforeUnload(event) {
  if (page.value?.dirty) {
    event.preventDefault();
    event.returnValue = "";
  }
}
let timer;
onMounted(() => {
  applyTheme();
  refreshOverview();
  startRefresh();
  timer = setInterval(refreshOverview, 5000);
  window.addEventListener("beforeunload", beforeUnload);
});
onUnmounted(() => {
  clearInterval(timer);
  stopRefresh();
  window.removeEventListener("beforeunload", beforeUnload);
});
</script>
<template>
  <el-config-provider :locale="zhCn">
    <div class="workspace" :class="{ collapsed, 'mobile-open': mobile }">
      <div v-if="mobile" class="nav-scrim" @click="mobile = false"></div>
      <aside class="sidebar">
        <a class="brand" href="#" @click.prevent="navigate('dashboard')">
          <span class="brand-mark">A</span>
          <span v-if="!collapsed || mobile">
            AetherSwap
            <small>交易管理工作台</small>
          </span>
        </a>
        <el-scrollbar class="nav-scroll">
          <el-menu
            ref="menu"
            :default-active="active"
            :collapse="collapsed && !mobile"
            @select="navigate"
          >
            <template v-for="section in sections" :key="section.label">
              <p v-if="!collapsed || mobile" class="nav-label">
                {{ section.label }}
              </p>
              <el-menu-item
                v-for="item in section.items"
                :key="item[0]"
                :index="item[0]"
              >
                <el-icon><component :is="item[2]" /></el-icon>
                <template #title>{{ item[1] }}</template>
              </el-menu-item>
            </template>
          </el-menu>
        </el-scrollbar>
        <div class="sidebar-footer">
          <span
            class="connection-dot"
            :class="{ online: appState.connected }"
          ></span>
          <span v-if="!collapsed || mobile">
            {{ appState.connected ? "本地服务已连接" : "本地服务未连接" }}
          </span>
        </div>
      </aside>
      <div class="main-shell">
        <header class="topbar">
          <el-button
            class="desktop-toggle"
            text
            :icon="Fold"
            aria-label="折叠导航"
            @click="collapsed = !collapsed"
          />
          <el-button
            class="mobile-toggle"
            text
            :icon="Fold"
            aria-label="打开导航"
            @click="mobile = !mobile"
          />
          <el-breadcrumb>
            <el-breadcrumb-item>AetherSwap</el-breadcrumb-item>
            <el-breadcrumb-item>{{ current[1] }}</el-breadcrumb-item>
          </el-breadcrumb>
          <div class="topbar-right">
            <span class="muted updated">
              {{
                appState.updated ? `更新于 ${appState.updated}` : "等待状态更新"
              }}
            </span>
            <el-tag
              :type="appState.connected ? 'success' : 'info'"
              effect="plain"
            >
              {{ appState.connected ? "在线" : "离线" }}
            </el-tag>
            <el-button
              text
              :icon="dark ? Sunny : Moon"
              aria-label="切换主题"
              @click="
                dark = !dark;
                applyTheme();
              "
            />
          </div>
        </header>
        <main class="page-content">
          <div class="page-heading">
            <div>
              <h1>{{ current[1] }}</h1>
              <p class="muted">{{ current[4] }}</p>
            </div>
          </div>
          <el-alert
            v-if="appState.error"
            :title="appState.error"
            type="error"
            show-icon
            :closable="false"
            class="space-bottom"
          />
          <component
            :is="current[3]"
            :key="active"
            ref="page"
            v-bind="
              ['purchases', 'sales'].includes(active)
                ? { mode: active }
                : {}
            "
            v-on="active === 'dashboard' ? { navigate } : {}"
          />
        </main>
      </div>
      <PaymentDialog v-if="paymentActivated" />
    </div>
  </el-config-provider>
</template>
