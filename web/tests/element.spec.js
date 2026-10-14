import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { mount, flushPromises } from "@vue/test-utils";
import { nextTick } from "vue";
import { ElMessageBox } from "element-plus";
import App from "../src/App.vue";
import Dashboard from "../src/views/Dashboard.vue";
import PaymentDialog from "../src/widgets/PaymentDialog.vue";
import Settings from "../src/views/Settings.vue";
import ConfigViewer from "../src/views/ConfigViewer.vue";
import Strategies from "../src/views/Strategies.vue";
import Transactions from "../src/views/Transactions.vue";
import NumberInput from "../src/widgets/NumberInput.vue";
import StrategyParameterPane from "../src/widgets/StrategyParameterPane.vue";
import { appState } from "../src/state/appState.js";
import { api, clone, paymentPayload } from "../src/services/api.js";
import { configPatch } from "../src/services/configForm.js";
import { structureErrors } from "../src/services/strategyEditor.js";
import { fixtures, modules, strategy } from "./fixtures.js";
import {
  configureRefresh,
  startRefresh,
  stopRefresh,
} from "../src/state/refreshState.js";

vi.mock("qrcode", () => ({
  default: { toDataURL: async () => "data:image/png;base64,fixture" },
}));
let wrapper, calls, data;
function response(body, ok = true) {
  return { ok, status: ok ? 200 : 409, json: async () => clone(body) };
}
async function render(component, props = {}) {
  wrapper = mount(component, {
    props,
    attachTo: document.body,
  });
  await flushPromises();
  await nextTick();
  return wrapper;
}
function button(label, scope = wrapper) {
  return scope.findAll("button").find((b) => b.text() === label);
}
beforeEach(() => {
  data = clone(fixtures);
  data["/config/credentials"] = {
    steam: { cookies: "sessionid=old; steamLoginSecure=old" },
    buff: {
      cookies: "session=old; csrf_token=old",
      user_agent: "Test Browser",
    },
  };
  calls = [];
  appState.payment = null;
  appState.guard = null;
  appState.status = {};
  appState.connected = true;
  appState.error = "";
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url, options = {}) => {
      const path = new URL(url, "http://localhost").pathname.replace(
        /^\/api/,
        "",
      );
      calls.push({
        path,
        method: options.method || "GET",
        body: options.body ? JSON.parse(options.body) : undefined,
      });
      if (options.method && options.method !== "GET") {
        if (path === "/strategies/simulate")
          return response({
            ok: true,
            mode: "sample",
            results: [
              {
                module_name: "最高折扣",
                status: "reject",
                reason: "样例折扣未通过",
                output: {},
              },
            ],
          });
        if (path === "/config") {
          for (const [section, values] of Object.entries(
            JSON.parse(options.body).config,
          ))
            Object.assign((data["/config"].config[section] ||= {}), values);
        }
        return response({ ok: true });
      }
      if (!(path in data)) throw new Error(`未模拟的请求 ${path}`);
      return response(data[path]);
    }),
  );
});
afterEach(() => {
  wrapper?.unmount();
  wrapper = undefined;
  stopRefresh();
  document.body.innerHTML = "";
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("Element Plus 页面", () => {
  it("总览优先显示任务状态，待对账时阻止启动并保留人工处理入口", async () => {
    appState.status = { status: "stopped", step: "等待启动" };
    appState.guard = { intent_id: "intent-1", order_id: "order-1", stage: "待核对" };
    await render(Dashboard);
    expect(wrapper.find("#operation-title").text()).toBe("自动交易已停止");
    expect(wrapper.text()).toContain("购买已暂停，等待人工对账");
    expect(button("启动任务").attributes("disabled")).toBeDefined();
    expect(button("已核对：未付款 / 已取消").exists()).toBe(true);
    expect(calls.filter((call) => call.method !== "GET")).toEqual([]);
  });

  it("数字输入加载结束后同步真实禁用状态与可访问状态", async () => {
    await render(NumberInput, { modelValue: 10, disabled: true });
    expect(wrapper.find("input").element.disabled).toBe(true);
    await wrapper.setProps({ disabled: false });
    await flushPromises();
    expect(wrapper.find("input").element.disabled).toBe(false);
    expect(wrapper.find("input").attributes("aria-disabled")).toBe("false");
  });

  it("策略参数面板通过事件提交修改，保留草稿更新边界", async () => {
    await render(StrategyParameterPane, {
      step: { params: { max_discount: 0.9 } },
      module: modules.find((item) => item.id === "guard.max_discount"),
    });
    wrapper.findComponent(NumberInput).vm.$emit("update:modelValue", 0.8);
    await nextTick();
    expect(wrapper.emitted("update:param")).toEqual([["max_discount", 0.8]]);
  });

  it("取消离开脏草稿时导航高亮仍指向当前页面", async () => {
    vi.spyOn(ElMessageBox, "confirm").mockRejectedValue("cancel");
    await render(App);
    await wrapper
      .findAll(".el-menu-item")
      .find((i) => i.text().trim() === "策略中心")
      .trigger("click");
    await flushPromises();
    await button("复制为自定义").trigger("click");
    await wrapper
      .findAll(".el-menu-item")
      .find((i) => i.text().trim() === "总览")
      .trigger("click");
    await flushPromises();
    expect(wrapper.find("h1").text()).toBe("策略中心");
    expect(wrapper.find(".el-menu-item.is-active").text()).toBe("策略中心");
  });
  it("真实应用可切换全部页面，不在导航时产生写请求", async () => {
    const errors = [];
    const original = console.error;
    vi.spyOn(console, "error").mockImplementation((...args) => {
      errors.push(args.join(" "));
      original(...args);
    });
    await render(App);
    for (const name of [
      "Steam 库存",
      "持有饰品",
      "售出记录",
      "数据分析",
      "策略中心",
      "账号管理",
      "Steam 令牌",
      "代理网络",
      "系统设置",
      "配置查看",
      "运行日志",
      "总览",
    ]) {
      await wrapper
        .findAll(".el-menu-item")
        .find((i) => i.text().trim() === name)
        .trigger("click");
      await flushPromises();
      expect(wrapper.find("h1").text()).toBe(name);
    }
    expect(errors).toEqual([]);
    expect(calls.filter((c) => c.method !== "GET")).toEqual([]);
  });

  it("出现待付款订单时加载付款弹窗并显示订单号", async () => {
    await render(App);
    appState.payment = {
      order_id: "pending-42",
      name: "样例订单",
      pay_url: "https://example.com/pay/42",
    };
    await flushPromises();
    expect(document.body.textContent).toContain("pending-42");
  });

  it("设置只提交修改字段，并保留 false 和 0", async () => {
    await render(Settings);
    const switches = wrapper.findAllComponents({ name: "ElSwitch" });
    await switches[0].vm.$emit("update:modelValue", true);
    await nextTick();
    await button("保存设置").trigger("click");
    await flushPromises();
    const request = calls.find(
      (c) => c.path === "/config" && c.method === "POST",
    );
    expect(request.body.config.pipeline).toEqual({
      start_time_limit_enabled: true,
    });
    expect(request.body.config.strategies).toBeUndefined();
    expect(request.body.config.steam_guard).toBeUndefined();
    expect(
      configPatch(
        { system: { enabled: false, hours: 0 } },
        { system: { enabled: true, hours: 5 } },
      ),
    ).toEqual({ system: { enabled: false, hours: 0 } });
  });

  it("配置查看只提交修改的密钥，Cookie 走已有手动校验接口", async () => {
    await render(ConfigViewer);
    const inputs = wrapper.findAllComponents({ name: "ElInput" });
    await inputs[0].vm.$emit(
      "update:modelValue",
      "sessionid=new; steamLoginSecure=new",
    );
    await button("保存 Steam Cookie").trigger("click");
    await flushPromises();
    expect(
      calls.find((c) => c.path === "/auth/steam/manual_cookie")?.body,
    ).toEqual({
      cookies: "sessionid=new; steamLoginSecure=new",
    });
    await inputs[3].vm.$emit("update:modelValue", "new-shared-secret");
    await button("保存密钥").trigger("click");
    await flushPromises();
    expect(
      calls.find((c) => c.path === "/config" && c.method === "POST")?.body,
    ).toEqual({
      config: { steam_guard: { shared_secret: "new-shared-secret" } },
    });
  });

  it("策略修改后撤销模拟状态，固定模块不可移除，离开可取消", async () => {
    await render(Strategies);
    await button("复制为自定义").trigger("click");
    expect(
      wrapper.findAll(".step-row").filter((s) => !s.text().includes("移除"))
        .length,
    ).toBe(6);
    await button("样例模拟").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("样例折扣未通过");
    expect(button("启用策略").attributes("disabled")).toBeUndefined();
    const name = wrapper.findComponent({ name: "ElInput" });
    name.vm.$emit("update:modelValue", "修改后的草稿");
    await nextTick();
    expect(button("启用策略").attributes("disabled")).toBeDefined();
    expect(wrapper.text()).not.toContain("样例折扣未通过");
    vi.spyOn(ElMessageBox, "confirm").mockRejectedValue("cancel");
    expect(await wrapper.vm.beforeLeave()).toBe(false);
    expect(wrapper.vm.dirty).toBe(true);
  });

  it("模块参数就地展开并可收起，移动后跟随对应模块", async () => {
    await render(Strategies);
    await button("复制为自定义").trigger("click");
    const rows = () => wrapper.findAll(".step-row");
    expect(wrapper.find(".parameter-pane").exists()).toBe(false);
    await rows()[4].find(".step-select").trigger("click");
    expect(rows()[4].find(".step-select").attributes("aria-expanded")).toBe(
      "true",
    );
    expect(rows()[4].find(".parameter-pane input").exists()).toBe(true);
    await rows()[5].find(".step-select").trigger("click");
    expect(wrapper.findAll(".parameter-pane")).toHaveLength(1);
    expect(rows()[4].find(".parameter-pane").exists()).toBe(false);
    await rows()[5].find(".step-select").trigger("click");
    expect(wrapper.find(".parameter-pane").exists()).toBe(false);
    await rows()[1].find(".step-select").trigger("click");
    expect(rows()[1].find(".parameter-pane").text()).toContain(
      "没有可编辑参数",
    );
    await rows()[4].find(".step-select").trigger("click");
    await rows()[4]
      .findAll("button")
      .find((b) => b.text() === "↓")
      .trigger("click");
    expect(rows()[4].find(".parameter-pane").exists()).toBe(false);
    expect(rows()[5].text()).toContain("最高折扣");
    expect(rows()[5].find(".parameter-pane input").exists()).toBe(true);
  });

  it("添加模块后展开其参数，移除模块后面板跟随或收起", async () => {
    await render(Strategies);
    await button("复制为自定义").trigger("click");
    const rows = () => wrapper.findAll(".step-row");
    await rows()[5].find(".step-select").trigger("click");
    await rows()[4]
      .findAll("button")
      .find((b) => b.text() === "移除")
      .trigger("click");
    expect(rows()[4].text()).toContain("关键词排除");
    expect(rows()[4].find(".parameter-pane").text()).toContain("关键词");
    await rows()[4]
      .findAll("button")
      .find((b) => b.text() === "移除")
      .trigger("click");
    expect(wrapper.find(".parameter-pane").exists()).toBe(false);
    await button("添加模块").trigger("click");
    const option = [...document.querySelectorAll(".module-option")].find((el) =>
      el.textContent.includes("关键词排除"),
    );
    [...option.querySelectorAll("button")]
      .find((b) => b.textContent.trim() === "添加 / 替换")
      .click();
    await nextTick();
    expect(rows()[4].text()).toContain("关键词排除");
    expect(rows()[4].find(".parameter-pane .el-form-item").exists()).toBe(true);
  });

  it("实际到账按 sale_proceeds 提交，携带稳定记录 ID", async () => {
    data["/transactions"].transactions = [
      {
        type: "purchase",
        idx: 4,
        db_id: 42,
        name: "样例饰品",
        price: 10,
        at: 1,
      },
    ];
    vi.spyOn(ElMessageBox, "confirm").mockResolvedValue("confirm");
    await render(Transactions, { mode: "purchases" });
    await button("记录售出").trigger("click");
    await flushPromises();
    const input = document.querySelector(".el-dialog input");
    input.value = "15.25";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    await nextTick();
    const save = [...document.querySelectorAll(".el-dialog button")].find(
      (b) => b.textContent.trim() === "保存记录",
    );
    save.click();
    await flushPromises();
    expect(
      calls.find((c) => c.path === "/transaction" && c.method === "PUT").body,
    ).toEqual({ type: "purchase", idx: 4, db_id: 42, sale_proceeds: 15.25 });
  });

  it("挂单异常在状态列显示待修复，正常持仓显示持有", async () => {
    data["/transactions"].transactions = [
      {
        type: "purchase",
        idx: 0,
        db_id: 1,
        name: "异常挂单",
        price: 10,
        at: 2,
        listing: false,
        listing_status: "error",
      },
      { type: "purchase", idx: 1, db_id: 2, name: "正常持仓", price: 10, at: 1 },
    ];
    await render(Transactions, { mode: "purchases" });
    const tags = wrapper.findAll(".el-tag");
    const broken = tags.find((tag) => tag.text() === "待修复");
    expect(broken).toBeTruthy();
    expect(broken.classes()).toContain("el-tag--danger");
    expect(tags.some((tag) => tag.text() === "持有")).toBe(true);
  });

  it("含已售出开关并入历史视图，批量售出只提交未售出记录", async () => {
    data["/transactions"].transactions = [
      { type: "purchase", idx: 0, db_id: 41, name: "在持饰品", price: 10, at: 2 },
      {
        type: "purchase",
        idx: 1,
        db_id: 42,
        name: "已售饰品",
        price: 10,
        at: 1,
        sale_price: 15,
        sold_at: 5,
      },
    ];
    vi.spyOn(ElMessageBox, "confirm").mockResolvedValue("confirm");
    await render(Transactions, { mode: "purchases" });
    const body = () => wrapper.find(".el-table__body").text();
    expect(body()).toContain("在持饰品");
    expect(body()).not.toContain("已售饰品");
    wrapper
      .findComponent({ name: "ElSwitch" })
      .vm.$emit("update:modelValue", true);
    await nextTick();
    expect(body()).toContain("已售饰品");
    expect(wrapper.text()).toContain("售出价（税前 CNY）");
    expect(
      wrapper.findAll("button").filter((b) => b.text() === "记录售出"),
    ).toHaveLength(1);
    wrapper
      .findComponent({ name: "ElTable" })
      .vm.$emit("selection-change", data["/transactions"].transactions);
    await nextTick();
    await button("批量记录售出").trigger("click");
    await flushPromises();
    const input = document.querySelector(".el-dialog input");
    input.value = "15.25";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    await nextTick();
    [...document.querySelectorAll(".el-dialog button")]
      .find((b) => b.textContent.trim() === "保存记录")
      .click();
    await flushPromises();
    const puts = calls.filter(
      (c) => c.path === "/transaction" && c.method === "PUT",
    );
    expect(puts).toHaveLength(1);
    expect(puts[0].body).toEqual({
      type: "purchase",
      idx: 0,
      db_id: 41,
      sale_proceeds: 15.25,
    });
  });
});

describe("交易交互保护", () => {
  it("刷新任务重配置不会累积计时器，卸载后停止", async () => {
    vi.useFakeTimers();
    await startRefresh();
    expect(vi.getTimerCount()).toBe(2);
    configureRefresh({
      inventory: { refresh_seconds: 900 },
      pipeline: { current_price_refresh_minutes: 15 },
    });
    expect(vi.getTimerCount()).toBe(2);
    stopRefresh();
    expect(vi.getTimerCount()).toBe(0);
  });
  it("确认期间轮询换单仍提交用户核对的原订单，阻止重复点击", async () => {
    let confirm;
    vi.spyOn(ElMessageBox, "confirm").mockImplementation(
      () =>
        new Promise((resolve) => {
          confirm = resolve;
        }),
    );
    appState.payment = {
      order_id: "order-A",
      name: "样例订单 A",
      pay_url: "https://example.com/pay/A",
    };
    await render(PaymentDialog);
    const submit = [...document.querySelectorAll("button")].find(
      (b) => b.textContent.trim() === "我已完成付款",
    );
    submit.click();
    await nextTick();
    submit.click();
    appState.payment = {
      order_id: "order-B",
      name: "样例订单 B",
      pay_url: "https://example.com/pay/B",
    };
    await nextTick();
    expect(document.body.textContent).toContain("order-A");
    expect(document.body.textContent).not.toContain("order-B");
    confirm("confirm");
    await flushPromises();
    const requests = calls.filter((c) => c.path === "/confirm_payment");
    expect(requests).toHaveLength(1);
    expect(requests[0].body).toEqual({
      ok: true,
      expected_order_id: "order-A",
    });
  });

  it("缺少订单号时不能提交，付款失败保留弹窗且不重试", async () => {
    expect(() => paymentPayload({}, true)).toThrow(/订单号/);
    appState.payment = {
      name: "缺少订单号",
      pay_url: "https://example.com/pay",
    };
    await render(PaymentDialog);
    let submit = [...document.querySelectorAll("button")].find(
      (b) => b.textContent.trim() === "我已完成付款",
    );
    expect(submit.disabled).toBe(true);
    appState.payment = {
      order_id: "stale",
      name: "旧订单",
      pay_url: "https://example.com/pay",
    };
    await nextTick();
    vi.spyOn(ElMessageBox, "confirm").mockResolvedValue("confirm");
    fetch.mockImplementation(async () =>
      response({ detail: "待确认订单已变化，请重新核对" }, false),
    );
    submit.click();
    await flushPromises();
    expect(document.body.textContent).toContain("待确认订单已变化");
    expect(document.body.textContent).toContain("stale");
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("服务返回 ok:false 不视为成功，不重试写请求", async () => {
    fetch.mockResolvedValue(
      response({ ok: false, error: "结果未知，请人工核对" }),
    );
    await expect(
      api("/purchase", { method: "POST", body: {} }),
    ).rejects.toThrow("结果未知");
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("策略阻止依赖倒置、固定模块缺失、互斥和外部代码", () => {
    expect(structureErrors(strategy, modules)).toEqual([]);
    const draft = clone(strategy);
    draft.steps.reverse();
    expect(structureErrors(draft, modules).join()).toContain("前置");
    draft.steps = draft.steps.filter(
      (s) => s.module_id !== "action.buff_lock_pay",
    );
    expect(structureErrors(draft, modules).join()).toContain("固定安全模块");
    const customModules = [
      ...modules,
      {
        id: "unsafe",
        name: "外部代码",
        origin: "user",
        module_kind: "code",
        strategy_types: ["buy"],
      },
    ];
    draft.steps.push({ module_id: "unsafe", enabled: true, params: {} });
    expect(structureErrors(draft, customModules).join()).toContain(
      "仅登记展示",
    );
  });
});

describe("弹窗尺寸统一", () => {
  // jsdom 不计算布局，逐个渲染各页弹窗既慢又验证不到真实宽度，因此按文件
  // 核对：每个 el-dialog 都必须声明共享宽度变量，避免再次出现写死像素宽度。
  it("所有弹窗都声明共享宽度变量", () => {
    const root = resolve(import.meta.dirname, "../src");
    const files = readdirSync(root, { recursive: true, encoding: "utf8" })
      .filter((name) => name.endsWith(".vue"))
      .map((name) => resolve(root, name));
    let total = 0;
    const off = files.flatMap((file) => {
      const source = readFileSync(file, "utf8");
      const dialogs = source.match(/<el-dialog\b/g)?.length || 0;
      const widths =
        source.match(/width="var\(--dialog-width(?:-wide)?\)"/g)?.length || 0;
      total += dialogs;
      return dialogs === widths
        ? []
        : [`${file}: ${dialogs} 个弹窗 / ${widths} 处共享宽度`];
    });
    expect(total).toBeGreaterThan(0);
    expect(off).toEqual([]);
  });

  // jsdom 不计算布局，无法渲染出真实宽度，只能约束样式声明本身：
  // 全局样式必须保留 Element Plus 的 MessageBox 默认宽度上限，
  // 覆盖成 100vw 会让所有确认弹窗铺满屏幕。
  it("全局样式保留 MessageBox 的默认宽度上限", () => {
    const css = readFileSync(
      resolve(import.meta.dirname, "../src/styles/element.css"),
      "utf8",
    );
    const blocks = css.match(/\.el-message-box\s*\{[^}]*\}/g) || [];
    expect(blocks.length).toBeGreaterThan(0);
    expect(
      blocks.some((block) =>
        /max-width:[^;]*--el-messagebox-width/.test(block),
      ),
    ).toBe(true);
  });
});
