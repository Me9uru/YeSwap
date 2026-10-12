<script setup>
import "element-plus/es/components/alert/style/css";
import "element-plus/es/components/button/style/css";
import "element-plus/es/components/card/style/css";
import "element-plus/es/components/input/style/css";
import "element-plus/es/components/link/style/css";
import "element-plus/es/components/loading/style/css";
import "element-plus/es/components/pagination/style/css";
import "element-plus/es/components/table/style/css";
import "element-plus/es/components/table-column/style/css";
import "element-plus/es/components/tag/style/css";
import {
  ElAlert,
  ElButton,
  ElCard,
  ElInput,
  ElLink,
  ElLoading,
  ElPagination,
  ElTable,
  ElTableColumn,
  ElTag,
} from "element-plus";
import { computed, onMounted, ref, watch } from "vue";
import { amount } from "../services/api.js";
import { refreshState, refreshInventory } from "../state/refreshState.js";
const busy = computed(() => refreshState.inventoryBusy);
const error = computed(() => refreshState.inventoryError);
const items = computed(() =>
  refreshState.items.map((item) => ({
    ...item,
    lowest_price:
      refreshState.prices[item.market_hash_name || item.name] ??
      item.lowest_price,
  })),
);
const search = ref(""),
  page = ref(1);
const filtered = computed(() =>
  items.value.filter((i) =>
    (i.name || "").toLowerCase().includes(search.value.toLowerCase()),
  ),
);
watch(search, () => {
  page.value = 1;
});
const load = refreshInventory;
onMounted(() => load());
const vLoading = ElLoading.directive;
</script>
<template>
  <el-card>
    <div class="toolbar">
      <h2>
        库存资产
        <el-tag type="info">{{ items.length }} 件</el-tag>
      </h2>
      <el-input v-model="search" placeholder="搜索饰品名称" clearable />
      <el-button :loading="busy" @click="load(true)">刷新库存</el-button>
    </div>
    <p class="muted">
      已知报价合计：CNY
      {{
        amount(
          items.reduce(
            (total, item) => total + (Number(item.lowest_price) || 0),
            0,
          ),
        )
      }}
      · 税前参考市值，不含未取得报价的资产
    </p>
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      class="space-bottom"
      :closable="false"
    />
    <el-table
      v-loading="busy"
      :data="filtered.slice((page - 1) * 20, page * 20)"
      empty-text="暂无库存，请检查当前账号与登录状态"
    >
      <el-table-column prop="name" label="饰品" min-width="240" />
      <el-table-column label="可上架" width="100">
        <template #default="{ row }">
          <el-tag :type="row.can_sell ? 'success' : 'info'">
            {{ row.can_sell ? "是" : "否" }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="可交易" width="100">
        <template #default="{ row }">
          {{ row.can_trade ? "是" : "否" }}
        </template>
      </el-table-column>
      <el-table-column label="交易冷却" min-width="190">
        <template #default="{ row }">
          {{
            row.cooldown_at_iso
              ? new Date(row.cooldown_at_iso).toLocaleString("zh-CN")
              : row.cooldown_text || "—"
          }}
        </template>
      </el-table-column>
      <el-table-column label="市场价（CNY）" width="145">
        <template #default="{ row }">{{ amount(row.lowest_price) }}</template>
      </el-table-column>
      <el-table-column label="市场" width="130">
        <template #default="{ row }">
          <el-link
            :href="
              'https://steamcommunity.com/market/listings/730/' +
              encodeURIComponent(row.market_hash_name || row.name)
            "
            target="_blank"
            rel="noopener noreferrer"
          >
            Steam
          </el-link>
          ·
          <el-link
            :href="
              'https://buff.163.com/market/csgo?tab=selling&search=' +
              encodeURIComponent(row.market_hash_name || row.name)
            "
            target="_blank"
            rel="noopener noreferrer"
          >
            BUFF
          </el-link>
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
</template>
