<script setup>
import "element-plus/es/components/alert/style/css";
import "element-plus/es/components/button/style/css";
import "element-plus/es/components/card/style/css";
import "element-plus/es/components/dialog/style/css";
import "element-plus/es/components/form/style/css";
import "element-plus/es/components/form-item/style/css";
import "element-plus/es/components/input/style/css";
import "element-plus/es/components/loading/style/css";
import "element-plus/es/components/message/style/css";
import "element-plus/es/components/option/style/css";
import "element-plus/es/components/pagination/style/css";
import "element-plus/es/components/select/style/css";
import "element-plus/es/components/table/style/css";
import "element-plus/es/components/table-column/style/css";
import "element-plus/es/components/tag/style/css";
import NumberInput from "../widgets/NumberInput.vue";
import { computed, onMounted, ref, watch } from "vue";
import {
  ElAlert,
  ElButton,
  ElCard,
  ElDialog,
  ElForm,
  ElFormItem,
  ElInput,
  ElLoading,
  ElMessage,
  ElOption,
  ElPagination,
  ElSelect,
  ElTable,
  ElTableColumn,
  ElTag,
} from "element-plus";
import {
  api,
  amount,
  clone,
  dateTime,
  transactionIdentity,
} from "../services/api.js";
import { useTask, confirmAction } from "../composables/useTask.js";
import { refreshState, refreshPrices } from "../state/refreshState.js";
const props = defineProps({ mode: String });
const { busy, error, run } = useTask();
const rows = ref([]),
  search = ref(""),
  page = ref(1),
  selection = ref([]),
  dialog = ref(""),
  form = ref({}),
  targets = ref([]);
async function beforeLeave() {
  return (
    !busy.value &&
    (!dialog.value ||
      (await confirmAction("交易记录尚未保存，离开会丢弃输入。", "离开编辑")))
  );
}
defineExpose({ beforeLeave });
const filtered = computed(() =>
  rows.value.filter((row) => {
    const match =
      props.mode === "sales"
        ? row.type === "sale"
        : row.type === "purchase" &&
          (props.mode !== "purchases" || !(Number(row.sale_price) > 0));
    return (
      match &&
      (row.name || "").toLowerCase().includes(search.value.toLowerCase())
    );
  }),
);
watch(search, () => {
  page.value = 1;
});
async function load() {
  rows.value =
    (await api("/transactions?enrich_current_price=0")).transactions || [];
  selection.value = [];
  page.value = Math.min(
    page.value,
    Math.max(1, Math.ceil(filtered.value.length / 20)),
  );
}
onMounted(() => run(load));
function status(row) {
  return Number(row.sale_price) > 0
    ? "已售出"
    : row.pending_receipt
      ? "待收货"
      : row.listing
        ? "在售"
        : "持有";
}
function open(kind, row) {
  dialog.value = kind;
  targets.value = clone(row ? [row] : selection.value);
  form.value =
    kind === "add"
      ? {
          name: "",
          steam_link: "",
          price: undefined,
          quantity: 1,
          goods_id: undefined,
        }
      : kind === "sell"
        ? { sale_proceeds: undefined }
        : {
            ...clone(row),
            state: row.pending_receipt
              ? "pending"
              : row.listing
                ? "listing"
                : "holding",
          };
}
function save() {
  return run(async () => {
    const value = clone(form.value);
    if (dialog.value === "sell") {
      if (!(value.sale_proceeds > 0)) throw new Error("实际到账金额必须大于 0");
      if (
        !(await confirmAction(
          `为 ${targets.value.length} 条记录填写每件实际到账 CNY ${amount(value.sale_proceeds)}？此操作只更新本地记录。`,
          "记录售出",
        ))
      )
        return;
      try {
        for (const row of targets.value)
          await api("/transaction", {
            method: "PUT",
            body: {
              ...transactionIdentity(row),
              sale_proceeds: value.sale_proceeds,
            },
          });
      } catch (e) {
        dialog.value = "";
        await load();
        throw e;
      }
    } else {
      if (!value.name?.trim() && !value.steam_link?.trim())
        throw new Error("请填写饰品名称或 Steam 市场链接");
      if (!(value.price > 0)) throw new Error("金额必须大于 0");
      const body =
        dialog.value === "add"
          ? value
          : {
              ...transactionIdentity(targets.value[0]),
              name: value.name,
              price: value.price,
              goods_id: value.goods_id || undefined,
              ...(value.type === "purchase"
                ? {
                    market_price: value.market_price || 0,
                    assetid: value.assetid || null,
                    pending_receipt: value.state === "pending",
                    listing: value.state === "listing",
                  }
                : {}),
            };
      await api(dialog.value === "add" ? "/purchase" : "/transaction", {
        method: dialog.value === "add" ? "POST" : "PUT",
        body,
      });
    }
    dialog.value = "";
    await load();
    ElMessage.success("记录已保存");
  });
}
function remove(list) {
  return run(async () => {
    const selected = clone(list);
    if (
      !(await confirmAction(
        `删除 ${selected.length} 条本地交易记录？记录会同时从持仓与历史中移除。`,
        "删除交易记录",
      ))
    )
      return;
    try {
      for (const row of selected.sort((a, b) => b.idx - a.idx))
        await api(
          `/transaction?${new URLSearchParams(transactionIdentity(row))}`,
          { method: "DELETE" },
        );
    } finally {
      await load();
    }
  });
}
function delist(row) {
  return run(async () => {
    const target = clone(row);
    if (
      !(await confirmAction(
        `从 Steam 市场下架「${target.name}」？下架后资产 ID 可能变化。`,
        "下架饰品",
      ))
    )
      return;
    const result = await api(
      `/purchase/${target.idx}/delist${target.db_id ? `?db_id=${target.db_id}` : ""}`,
      { method: "POST" },
    );
    ElMessage.success(result.warning || result.message || "下架请求已处理");
    await load();
  });
}
const vLoading = ElLoading.directive;
</script>
<template>
  <el-card>
    <div class="toolbar">
      <h2>交易明细</h2>
      <el-input v-model="search" placeholder="搜索饰品名称" clearable />
      <el-button :loading="busy" @click="run(load)">刷新记录</el-button>
      <el-button
        v-if="mode !== 'sales'"
        type="primary"
        :disabled="busy"
        @click="open('add')"
      >
        补录购入
      </el-button>
      <el-button
        v-if="mode === 'purchases'"
        :loading="busy"
        @click="run(refreshPrices, '市场价已刷新')"
      >
        刷新市场价
      </el-button>
    </div>
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
      class="space-bottom"
    />
    <div v-if="selection.length" class="toolbar">
      <el-tag>已选 {{ selection.length }} 条</el-tag>
      <el-button
        v-if="mode === 'purchases'"
        :disabled="busy"
        @click="open('sell')"
      >
        批量记录售出
      </el-button>
      <el-button
        type="danger"
        plain
        :disabled="busy"
        @click="remove(selection)"
      >
        删除所选
      </el-button>
    </div>
    <el-table
      v-loading="busy"
      :data="filtered.slice((page - 1) * 20, page * 20)"
      @selection-change="selection = $event"
      empty-text="暂无交易记录"
    >
      <el-table-column type="selection" width="45" />
      <el-table-column
        prop="name"
        label="饰品"
        min-width="230"
        show-overflow-tooltip
      />
      <el-table-column label="时间" min-width="170">
        <template #default="{ row }">{{ dateTime(row.at) }}</template>
      </el-table-column>
      <el-table-column label="金额（CNY）" width="135">
        <template #default="{ row }">{{ amount(row.price) }}</template>
      </el-table-column>
      <el-table-column v-if="mode !== 'sales'" label="购入时市场价" width="135">
        <template #default="{ row }">{{ amount(row.market_price) }}</template>
      </el-table-column>
      <el-table-column
        v-if="mode === 'purchases'"
        label="现市场价（CNY）"
        width="155"
      >
        <template #default="{ row }">
          {{
            amount(refreshState.prices[row.name] ?? row.current_market_price)
          }}
        </template>
      </el-table-column>
      <el-table-column v-if="mode !== 'sales'" label="状态" width="95">
        <template #default="{ row }">
          <el-tag
            :type="
              row.sale_price
                ? 'success'
                : row.pending_receipt
                  ? 'warning'
                  : 'info'
            "
          >
            {{ status(row) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column
        v-if="mode === 'history'"
        label="售出价（税前 CNY）"
        width="165"
      >
        <template #default="{ row }">{{ amount(row.sale_price) }}</template>
      </el-table-column>
      <el-table-column
        prop="assetid"
        label="资产 ID"
        min-width="130"
        show-overflow-tooltip
      />
      <el-table-column label="操作" min-width="205" fixed="right">
        <template #default="{ row }">
          <el-button
            link
            type="primary"
            :disabled="busy"
            @click="open('edit', row)"
          >
            编辑
          </el-button>
          <el-button
            v-if="mode === 'purchases'"
            link
            type="primary"
            :disabled="busy"
            @click="open('sell', row)"
          >
            记录售出
          </el-button>
          <el-button
            v-if="row.type === 'purchase' && row.listing"
            link
            type="warning"
            :disabled="busy"
            @click="delist(row)"
          >
            下架
          </el-button>
          <el-button link type="danger" :disabled="busy" @click="remove([row])">
            删除
          </el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-pagination
      v-model:current-page="page"
      :total="filtered.length"
      :page-size="20"
      layout="total, prev, pager, next"
    />
  </el-card>
  <el-dialog
    :model-value="!!dialog"
    :title="
      dialog === 'add'
        ? '补录购入'
        : dialog === 'sell'
          ? '记录实际售出到账'
          : '编辑交易记录'
    "
    width="var(--dialog-width)"
    :close-on-click-modal="false"
    :before-close="
      (done) => {
        if (!busy) {
          dialog = '';
          done();
        }
      }
    "
  >
    <el-form label-position="top" :disabled="busy">
      <template v-if="dialog === 'sell'">
        <el-alert
          title="填写每件实际到账金额（已扣手续费，CNY）"
          description="此操作仅记录已完成的售出，不会在 Steam 创建卖单。"
          type="info"
          :closable="false"
          class="space-bottom"
        />
        <el-form-item label="实际到账（人民币 / 件）">
          <NumberInput
            v-model="form.sale_proceeds"
            :min="0.01"
            :precision="2"
          />
        </el-form-item>
      </template>
      <template v-else>
        <el-form-item label="饰品名称">
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item v-if="dialog === 'add'" label="或 Steam 市场链接">
          <el-input v-model="form.steam_link" />
        </el-form-item>
        <div class="form-grid">
          <el-form-item label="金额（人民币 / 件）">
            <NumberInput v-model="form.price" :min="0.01" :precision="2" />
          </el-form-item>
          <el-form-item v-if="dialog === 'add'" label="数量">
            <NumberInput v-model="form.quantity" :min="1" :precision="0" />
          </el-form-item>
          <el-form-item label="BUFF goods_id">
            <NumberInput v-model="form.goods_id" :min="1" :precision="0" />
          </el-form-item>
        </div>
        <template v-if="dialog === 'edit' && form.type === 'purchase'">
          <el-form-item label="购入时市场价（CNY）">
            <NumberInput v-model="form.market_price" :min="0" :precision="2" />
          </el-form-item>
          <el-form-item label="资产 ID">
            <el-input v-model="form.assetid" />
          </el-form-item>
          <el-form-item label="持仓状态">
            <el-select v-model="form.state">
              <el-option label="持有" value="holding" />
              <el-option label="在售" value="listing" />
              <el-option label="待收货" value="pending" />
            </el-select>
          </el-form-item>
        </template>
      </template>
    </el-form>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <template #footer>
      <el-button :disabled="busy" @click="dialog = ''">取消</el-button>
      <el-button type="primary" :loading="busy" @click="save">
        保存记录
      </el-button>
    </template>
  </el-dialog>
</template>
