<script setup>
import "element-plus/es/components/alert/style/css";
import "element-plus/es/components/button/style/css";
import "element-plus/es/components/card/style/css";
import "element-plus/es/components/dialog/style/css";
import "element-plus/es/components/drawer/style/css";
import "element-plus/es/components/empty/style/css";
import "element-plus/es/components/form/style/css";
import "element-plus/es/components/form-item/style/css";
import "element-plus/es/components/input/style/css";
import "element-plus/es/components/loading/style/css";
import "element-plus/es/components/message/style/css";
import "element-plus/es/components/radio-button/style/css";
import "element-plus/es/components/radio-group/style/css";
import "element-plus/es/components/switch/style/css";
import "element-plus/es/components/table/style/css";
import "element-plus/es/components/table-column/style/css";
import "element-plus/es/components/tag/style/css";
import StrategyModuleCatalog from "../widgets/StrategyModuleCatalog.vue";
import StrategyParameterPane from "../widgets/StrategyParameterPane.vue";
import { computed, onMounted, ref, watch } from "vue";
import {
  ElAlert,
  ElButton,
  ElCard,
  ElDialog,
  ElDrawer,
  ElEmpty,
  ElForm,
  ElFormItem,
  ElInput,
  ElLoading,
  ElMessage,
  ElRadioButton,
  ElRadioGroup,
  ElSwitch,
  ElTable,
  ElTableColumn,
  ElTag,
} from "element-plus";
import { api, clone, download } from "../services/api.js";
import {
  defaultStep,
  fixedStep,
  structureErrors,
  strategySignature,
  strategyDiff,
} from "../services/strategyEditor.js";
import { useTask, confirmAction } from "../composables/useTask.js";
const { busy, error, run } = useTask();
const catalog = ref({ strategies: [], modules: [], active: {}, limits: {} });
const view = ref("buy"),
  draft = ref(null),
  snapshot = ref(""),
  expanded = ref(-1),
  results = ref([]),
  simulated = ref("");
const picker = ref(false),
  query = ref(""),
  importInput = ref(null),
  importKind = ref("strategy"),
  activation = ref(false);
const strategies = computed(() =>
  catalog.value.strategies.filter((s) => s.strategy_type === view.value),
);
const modules = computed(() =>
  catalog.value.modules.filter(
    (m) =>
      !m.hidden &&
      (view.value === "modules" || m.strategy_types?.includes(view.value)) &&
      `${m.name} ${m.description}`
        .toLowerCase()
        .includes(query.value.toLowerCase()),
  ),
);
const dirty = computed(
  () => !!draft.value && strategySignature(draft.value) !== snapshot.value,
);
const errors = computed(() =>
  draft.value
    ? structureErrors(draft.value, catalog.value.modules, catalog.value.limits)
    : [],
);
const diff = computed(() =>
  strategyDiff(
    catalog.value.strategies.find(
      (s) => s.id === catalog.value.active[draft.value?.strategy_type],
    ),
    draft.value,
    catalog.value.modules,
  ),
);
function moduleFor(id) {
  return (
    catalog.value.modules.find((m) => m.id === id) || {
      name: id,
      params_schema: {},
    }
  );
}
function setDraft(strategy) {
  draft.value = strategy ? clone(strategy) : null;
  expanded.value = -1;
  snapshot.value = strategySignature(draft.value);
  results.value = [];
  simulated.value = "";
}
function toggleStep(index) {
  expanded.value = expanded.value === index ? -1 : index;
}
async function beforeLeave() {
  return (
    !busy.value &&
    (!dirty.value ||
      (await confirmAction(
        "策略修改尚未保存，离开会丢弃这些修改。",
        "未保存的策略",
      )))
  );
}
defineExpose({ beforeLeave, dirty });
async function load(id) {
  catalog.value = await api("/strategies");
  if (view.value !== "modules")
    setDraft(
      strategies.value.find((s) => s.id === id) ||
        strategies.value.find(
          (s) => s.id === catalog.value.active[view.value],
        ) ||
        strategies.value[0],
    );
}
onMounted(() => run(() => load()));
watch(
  () => strategySignature(draft.value),
  () => {
    if (simulated.value && simulated.value !== strategySignature(draft.value)) {
      simulated.value = "";
      results.value = [];
    }
  },
);
async function selectView(value) {
  if (value === view.value || !(await beforeLeave())) return;
  view.value = value;
  query.value = "";
  setDraft(
    strategies.value.find((s) => s.id === catalog.value.active[value]) ||
      strategies.value[0],
  );
}
async function selectStrategy(value) {
  if (await beforeLeave()) setDraft(value);
}
async function save() {
  if (errors.value.length) throw new Error(errors.value.join("；"));
  const saved = await api("/strategies", {
    method: "POST",
    body: { strategy: clone(draft.value) },
  });
  const signature = simulated.value,
    output = clone(results.value);
  await load(saved.strategy.id);
  if (signature === strategySignature(draft.value)) {
    simulated.value = signature;
    results.value = output;
  }
}
function copy() {
  if (!draft.value) return;
  const value = clone(draft.value);
  delete value.id;
  value.origin = "custom";
  value.readonly = false;
  value.name += " 副本";
  draft.value = value;
  snapshot.value = "";
  simulated.value = "";
  results.value = [];
}
async function create() {
  if (!(await beforeLeave())) return;
  const base = strategies.value.find(
    (s) =>
      s.origin === "system" &&
      s.steps.some(
        (step) =>
          step.module_id ===
          (view.value === "buy" ? "action.buff_lock_pay" : "action.steam_list"),
      ),
  );
  if (!base) {
    ElMessage.warning("请先选择可复制的系统策略");
    return;
  }
  setDraft(base);
  copy();
  draft.value.name = view.value === "buy" ? "新的购入策略" : "新的出售策略";
}
function simulate() {
  return run(async () => {
    if (errors.value.length) throw new Error(errors.value.join("；"));
    const payload = clone(draft.value);
    const data = await api("/strategies/simulate", {
      method: "POST",
      body: { strategy: payload },
    });
    results.value = data.results || [];
    simulated.value = strategySignature(payload);
  });
}
function changeSteps(action) {
  const next = clone(draft.value);
  action(next.steps);
  const validation = structureErrors(
    next,
    catalog.value.modules,
    catalog.value.limits,
  );
  if (validation.length) {
    ElMessage.warning(validation.join("；"));
    return false;
  }
  draft.value = next;
  if (expanded.value >= next.steps.length) expanded.value = -1;
  return true;
}
function move(index, target) {
  if (
    busy.value ||
    !draft.value ||
    index < 0 ||
    target < 0 ||
    target >= draft.value.steps.length
  )
    return;
  if (
    draft.value.steps
      .slice(Math.min(index, target), Math.max(index, target) + 1)
      .some((s) => fixedStep(s, draft.value))
  )
    return;
  if (
    !changeSteps((steps) => {
      const [step] = steps.splice(index, 1);
      steps.splice(target, 0, step);
    })
  )
    return;
  if (expanded.value === index) expanded.value = target;
  else if (index < expanded.value && expanded.value <= target)
    expanded.value -= 1;
  else if (target <= expanded.value && expanded.value < index)
    expanded.value += 1;
}
let dragged = -1;
function add(module) {
  if (
    module.origin === "user" &&
    (module.module_kind !== "declarative" || module.enabled === false)
  )
    return;
  let at = -1;
  const ok = changeSteps((steps) => {
    const step = defaultStep(module);
    // 替换定价核心时同步移除其互斥项，其他冲突交给校验展示。
    if (
      ["pricing.steam_wall_price", "pricing.steam_wall_gap"].includes(module.id)
    ) {
      const index = steps.findIndex((s) =>
        ["pricing.steam_wall_price", "pricing.steam_wall_gap"].includes(
          s.module_id,
        ),
      );
      if (index >= 0) {
        steps.splice(index, 1, step);
        for (let i = steps.length - 1; i >= 0; i--)
          if ((module.conflicts || []).includes(steps[i].module_id))
            steps.splice(i, 1);
        at = steps.indexOf(step);
        return;
      }
    }
    const index = steps.findIndex((s) =>
      [
        "guard.target_balance",
        "action.buff_lock_pay",
        "action.steam_list",
        "action.pause_auto_sell",
      ].includes(s.module_id),
    );
    steps.splice(index < 0 ? steps.length : index, 0, step);
    at = steps.indexOf(step);
  });
  if (ok && at >= 0) expanded.value = at;
}
function removeStep(index) {
  if (
    !changeSteps((steps) => {
      steps.splice(index, 1);
    })
  )
    return;
  if (expanded.value === index) expanded.value = -1;
  else if (expanded.value > index) expanded.value -= 1;
}
function reset() {
  return run(async () => {
    if (
      !(await confirmAction(
        "将当前系统策略参数恢复为默认值并保存。",
        "恢复默认参数",
      ))
    )
      return;
    for (const step of draft.value.steps)
      step.params = defaultStep(moduleFor(step.module_id)).params;
    await save();
  });
}
function activate() {
  return run(async () => {
    if (
      draft.value.origin !== "system" &&
      simulated.value !== strategySignature(draft.value)
    )
      throw new Error("请先模拟当前版本的自定义策略");
    await save();
    await api(`/strategies/${encodeURIComponent(draft.value.id)}/activate`, {
      method: "POST",
      body: { risk_confirmed: true },
    });
    activation.value = false;
    await load(draft.value.id);
    ElMessage.success("策略已启用");
  });
}
function remove() {
  return run(async () => {
    if (
      !(await confirmAction(
        `删除「${draft.value.name}」？正在启用的策略删除后将回退到系统策略。`,
        "删除策略",
      ))
    )
      return;
    if (draft.value.id)
      await api(`/strategies/${encodeURIComponent(draft.value.id)}`, {
        method: "DELETE",
      });
    await load();
  });
}
function chooseImport(kind) {
  importKind.value = kind;
  importInput.value.click();
}
function importFile(event) {
  const file = event.target.files[0];
  event.target.value = "";
  if (!file) return;
  run(async () => {
    if (
      dirty.value &&
      !(await confirmAction("导入后将离开当前未保存的策略草稿，是否继续？"))
    )
      return;
    const value = JSON.parse(await file.text());
    const isModule = importKind.value === "module";
    const data = await api(
      isModule ? "/strategy-modules/import" : "/strategies/import",
      {
        method: "POST",
        body: isModule ? { manifest: value } : { strategy: value },
      },
    );
    if (!isModule) view.value = data.strategy.strategy_type;
    await load(data.strategy?.id);
    ElMessage.success(isModule ? "模块已登记" : "策略已导入，尚未启用");
  });
}
const vLoading = ElLoading.directive;
</script>
<template>
  <div class="stack">
    <el-card>
      <div class="toolbar">
        <el-radio-group
          :model-value="view"
          :disabled="busy"
          @change="selectView"
        >
          <el-radio-button value="buy">购入策略</el-radio-button>
          <el-radio-button value="sell">出售策略</el-radio-button>
          <el-radio-button value="modules">模块管理</el-radio-button>
        </el-radio-group>
        <el-button v-if="view !== 'modules'" :disabled="busy" @click="create">
          新建策略
        </el-button>
        <el-button
          :disabled="busy"
          @click="chooseImport(view === 'modules' ? 'module' : 'strategy')"
        >
          {{ view === "modules" ? "导入用户模块" : "导入策略" }}
        </el-button>
        <input
          ref="importInput"
          type="file"
          accept=".json"
          class="file-input"
          @change="importFile"
        />
      </div>
      <el-alert v-if="error" :title="error" type="error" :closable="false" />
    </el-card>
    <StrategyModuleCatalog
      v-if="view === 'modules'"
      :modules="catalog.modules.filter((module) => !module.hidden)"
      :available-data="catalog.available_data"
    />
    <template v-else>
      <div class="strategy-layout">
        <el-card>
          <div class="toolbar">
            <h2>策略列表</h2>
            <el-tag type="info" effect="plain">
              共 {{ strategies.length }} 个
            </el-tag>
          </div>
          <div class="strategy-choices">
            <button
              v-for="s in strategies"
              :key="s.id"
              class="strategy-choice"
              :class="{ selected: draft?.id === s.id }"
              :disabled="busy"
              @click="selectStrategy(s)"
            >
              <span>{{ s.name }}</span>
              <small>
                {{ s.origin === "system" ? "系统预设" : "自定义" }}
                {{ catalog.active[view] === s.id ? " · 正在启用" : "" }}
              </small>
            </button>
          </div>
          <el-empty
            v-if="!strategies.length"
            description="暂无策略"
            :image-size="60"
          />
        </el-card>
        <el-card v-if="draft" v-loading="busy">
          <div class="toolbar">
            <h2>{{ draft.name }}</h2>
            <el-tag v-if="dirty" type="warning">未保存</el-tag>
            <el-tag v-if="catalog.active[view] === draft.id" type="success">
              当前启用
            </el-tag>
          </div>
          <el-alert
            v-if="draft.origin === 'system'"
            title="系统策略可编辑参数；调整模块结构请先复制为自定义策略。"
            type="info"
            :closable="false"
            class="space-bottom"
          />
          <div class="toolbar">
            <el-button
              type="primary"
              :disabled="busy"
              @click="run(save, '策略已保存')"
            >
              保存
            </el-button>
            <el-button :disabled="busy" @click="copy">复制为自定义</el-button>
            <el-button
              v-if="draft.origin === 'system'"
              :disabled="busy"
              @click="reset"
            >
              恢复默认
            </el-button>
            <el-button :disabled="busy" @click="simulate">样例模拟</el-button>
            <el-button
              type="success"
              plain
              :disabled="
                busy ||
                errors.length > 0 ||
                (draft.origin !== 'system' &&
                  simulated !== strategySignature(draft))
              "
              @click="activation = true"
            >
              启用策略
            </el-button>
            <el-button
              :disabled="busy"
              @click="download(`${draft.name}.json`, draft)"
            >
              导出
            </el-button>
            <el-button
              v-if="draft.origin !== 'system'"
              type="danger"
              plain
              :disabled="busy"
              @click="remove"
            >
              删除
            </el-button>
          </div>
          <el-form
            v-if="draft.origin !== 'system'"
            label-position="top"
            :disabled="busy"
          >
            <el-form-item label="策略名称">
              <el-input v-model="draft.name" />
            </el-form-item>
            <el-form-item label="说明">
              <el-input v-model="draft.description" />
            </el-form-item>
          </el-form>
          <el-alert
            v-if="errors.length"
            :title="errors.join('；')"
            type="warning"
            :closable="false"
            class="space-bottom"
          />
          <div class="toolbar">
            <h3>执行模块 · {{ draft.steps.length }}</h3>
            <el-button
              v-if="draft.origin !== 'system'"
              size="small"
              :disabled="busy"
              @click="picker = true"
            >
              添加模块
            </el-button>
          </div>
          <div
            v-for="(step, index) in draft.steps"
            :key="`${index}-${step.module_id}`"
            class="step-row"
            :class="{
              selected: expanded === index,
              disabled: step.enabled === false,
            }"
            :draggable="!busy && !fixedStep(step, draft) && expanded !== index"
            @dragstart="dragged = index"
            @dragover.prevent
            @drop.prevent="
              move(dragged, index);
              dragged = -1;
            "
            @dragend="dragged = -1"
          >
            <button
              class="step-select"
              :aria-expanded="expanded === index"
              @click="toggleStep(index)"
            >
              <span class="step-number">{{ index + 1 }}</span>
              <span>
                {{ moduleFor(step.module_id).name }}
                <small>
                  {{ fixedStep(step, draft) ? "固定模块" : "可拖动排序" }}
                </small>
              </span>
              <span class="step-toggle" aria-hidden="true">
                {{ expanded === index ? "▾" : "▸" }}
              </span>
            </button>
            <div class="step-actions" v-if="!fixedStep(step, draft)">
              <el-switch
                :model-value="step.enabled !== false"
                size="small"
                :disabled="busy"
                @change="
                  (value) =>
                    changeSteps((steps) => {
                      steps[index].enabled = value;
                    })
                "
              />
              <el-button
                text
                size="small"
                :disabled="busy || index === 0"
                aria-label="上移模块"
                @click="move(index, index - 1)"
              >
                ↑
              </el-button>
              <el-button
                text
                size="small"
                :disabled="busy || index === draft.steps.length - 1"
                aria-label="下移模块"
                @click="move(index, index + 1)"
              >
                ↓
              </el-button>
              <el-button
                text
                type="danger"
                size="small"
                :disabled="busy"
                @click="removeStep(index)"
              >
                移除
              </el-button>
            </div>
            <StrategyParameterPane
              v-if="expanded === index"
              :step="step"
              :module="moduleFor(step.module_id)"
              :busy="busy"
              @update:param="(key, value) => (step.params[key] = value)"
            />
          </div>
        </el-card>
      </div>
      <el-card>
        <div class="toolbar">
          <h2>样例模拟结果</h2>
          <el-tag type="warning">样例步骤</el-tag>
        </div>
        <p class="muted">
          模拟使用样例数据。交易动作始终跳过，未实现计算的步骤标记为
          样例步骤；结果不等于历史回测或实盘验证。修改策略后需重新模拟。
        </p>
        <el-table
          :data="results"
          empty-text="点击“样例模拟”查看当前草稿的执行结果"
        >
          <el-table-column prop="module_name" label="模块" min-width="200" />
          <el-table-column label="结果" width="120">
            <template #default="{ row }">
              <el-tag
                :type="
                  row.status === 'reject' || row.status === 'error'
                    ? 'danger'
                    : row.status === 'sample'
                      ? 'warning'
                      : 'info'
                "
              >
                {{
                  {
                    reject: "已拦截",
                    error: "出错",
                    sample: "样例步骤",
                    pass: "已通过",
                    skipped: "已跳过",
                  }[row.status] || "未知状态"
                }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="reason" label="说明" min-width="280" />
          <el-table-column type="expand">
            <template #default="{ row }">
              <pre class="catalog-data">{{
                JSON.stringify(row.output || {}, null, 2)
              }}</pre>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </template>
    <el-drawer v-model="picker" title="添加策略模块" size="min(520px, 95vw)">
      <el-input
        v-model="query"
        clearable
        placeholder="搜索模块"
        class="space-bottom"
      />
      <div v-for="m in modules" :key="m.id" class="module-option">
        <h3>{{ m.name }}</h3>
        <p class="muted">{{ m.description }}</p>
        <el-button
          size="small"
          :disabled="
            busy ||
            (m.origin === 'user' &&
              (m.module_kind !== 'declarative' || m.enabled === false))
          "
          @click="add(m)"
        >
          添加 / 替换
        </el-button>
      </div>
    </el-drawer>
    <el-dialog
      v-model="activation"
      title="确认策略变更"
      width="var(--dialog-width-wide)"
      :close-on-click-modal="false"
      :before-close="
        (done) => {
          if (!busy) done();
        }
      "
    >
      <el-alert
        title="启用后会影响真实交易决策，请检查变更"
        type="warning"
        :closable="false"
        class="space-bottom"
      />
      <h3>{{ draft?.name }}</h3>
      <ul v-if="diff.length" class="diff-list">
        <li v-for="(change, index) in diff" :key="index">{{ change }}</li>
      </ul>
      <p v-else class="muted">与当前策略没有模块参数差异。</p>
      <el-alert v-if="error" :title="error" type="error" :closable="false" />
      <template #footer>
        <el-button :disabled="busy" @click="activation = false">取消</el-button>
        <el-button type="primary" :loading="busy" @click="activate">
          确认并启用
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>
<style scoped>
.strategy-layout {
  display: grid;
  gap: 20px;
  align-items: start;
}
.strategy-choices {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 12px;
}
.strategy-choice {
  display: flex;
  flex-direction: column;
  gap: 7px;
  text-align: left;
  padding: 14px 14px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: transparent;
  color: inherit;
  cursor: pointer;
}
.strategy-choice:hover:not(:disabled) {
  border-color: var(--el-color-primary-light-5);
}
.strategy-choice:disabled {
  cursor: not-allowed;
}
.strategy-choice small,
.step-select small {
  display: block;
  color: var(--muted);
  font-size: 13px;
}
.selected {
  background: var(--el-color-primary-light-9);
  border-color: var(--el-color-primary-light-5);
}
.step-row {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  margin-bottom: 10px;
  padding: 10px;
}
.step-row.selected {
  border-color: var(--el-color-primary-light-5);
}
.step-select {
  display: flex;
  gap: 10px;
  width: 100%;
  border: 0;
  color: inherit;
  background: transparent;
  text-align: left;
  cursor: pointer;
  padding: 2px;
  align-items: center;
  font-size: 14px;
}
.step-number {
  width: 24px;
  height: 24px;
  border-radius: 6px;
  display: grid;
  place-items: center;
  background: var(--el-fill-color);
  color: var(--muted);
}
.step-toggle {
  margin-left: auto;
  padding-left: 10px;
  color: var(--muted);
  font-size: 12px;
}
.step-actions {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  margin-top: 7px;
  gap: 3px;
}
.step-actions .el-button {
  margin: 0;
}
.step-row.disabled {
  opacity: 0.6;
}
.module-option {
  border-bottom: 1px solid var(--el-border-color-lighter);
  padding: 18px 0;
}
.catalog-data {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  padding: 18px;
  font-size: 13px;
}
.diff-list {
  line-height: 1.8;
  overflow-wrap: anywhere;
  padding-left: 20px;
}
</style>
