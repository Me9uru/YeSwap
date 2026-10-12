<script setup>
import "element-plus/es/components/alert/style/css";
import "element-plus/es/components/button/style/css";
import "element-plus/es/components/card/style/css";
import "element-plus/es/components/input/style/css";
import "element-plus/es/components/loading/style/css";
import "element-plus/es/components/table/style/css";
import "element-plus/es/components/table-column/style/css";
import {
  ElAlert,
  ElButton,
  ElCard,
  ElInput,
  ElLoading,
  ElTable,
  ElTableColumn,
} from "element-plus";
import { computed, onMounted, ref } from "vue";
import { aggregateByItemName } from "../services/analytics.js";
import { api, amount } from "../services/api.js";
import { useTask } from "../composables/useTask.js";
const { busy, error, run } = useTask();
const data = ref([]),
  search = ref("");
const filtered = computed(() =>
  data.value.filter((row) =>
    row.name.toLowerCase().includes(search.value.toLowerCase()),
  ),
);
function load() {
  return run(async () => {
    const d = await api("/transactions?enrich_current_price=0");
    data.value = aggregateByItemName(
      (d.transactions || []).filter((t) => t.type === "purchase"),
      d.resell_ratio,
    );
  });
}
onMounted(load);
const vLoading = ElLoading.directive;
</script>
<template>
  <el-card>
    <div class="toolbar">
      <h2>饰品收益分析</h2>
      <el-input v-model="search" clearable placeholder="搜索饰品" />
      <el-button :loading="busy" @click="load">刷新分析</el-button>
    </div>
    <el-alert
      title="金额按人民币汇总；税前售出价与折扣估算沿用现有统计口径。"
      type="info"
      :closable="false"
      class="space-bottom"
    />
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-table
      v-loading="busy"
      :data="filtered"
      empty-text="暂无可分析的购入记录"
    >
      <el-table-column prop="name" label="饰品" min-width="230" />
      <el-table-column prop="count" label="购入数量" width="110" sortable />
      <el-table-column
        v-for="col in [
          ['avgPrice', '平均成本'],
          ['avgMp', '平均参考价'],
          ['totalSaleAmount', '售出总额（税前）'],
          ['avgSalePrice', '平均售出价'],
          ['totalCashProfit', '折现盈亏'],
          ['avgDeviation', '成交偏差'],
        ]"
        :key="col[0]"
        :label="col[1]"
        min-width="140"
      >
        <template #default="{ row }">{{ amount(row[col[0]]) }}</template>
      </el-table-column>
      <el-table-column label="平均折扣率" width="130">
        <template #default="{ row }">
          {{ row.avgDiscountRatio?.toFixed(4) ?? "—" }}
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>
