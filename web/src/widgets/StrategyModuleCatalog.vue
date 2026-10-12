<script setup>
import "element-plus/es/components/alert/style/css";
import "element-plus/es/components/card/style/css";
import "element-plus/es/components/collapse/style/css";
import "element-plus/es/components/collapse-item/style/css";
import "element-plus/es/components/input/style/css";
import "element-plus/es/components/table/style/css";
import "element-plus/es/components/table-column/style/css";
import "element-plus/es/components/tag/style/css";
import {
  ElAlert,
  ElCard,
  ElCollapse,
  ElCollapseItem,
  ElInput,
  ElTable,
  ElTableColumn,
  ElTag,
} from "element-plus";
import { computed, ref } from "vue";

const props = defineProps({
  modules: { type: Array, required: true },
  availableData: { type: Object, default: () => ({}) },
});
const query = ref("");
const filtered = computed(() =>
  props.modules.filter((module) =>
    `${module.name} ${module.description}`
      .toLowerCase()
      .includes(query.value.toLowerCase()),
  ),
);
</script>
<template>
  <el-card>
    <el-input
      v-model="query"
      placeholder="搜索模块"
      clearable
      class="space-bottom"
    />
    <el-alert
      title="声明式模块可加入策略；外部代码模块只登记展示，不会执行。"
      type="info"
      :closable="false"
      class="space-bottom"
    />
    <el-table :data="filtered">
      <el-table-column prop="name" label="模块" min-width="190" />
      <el-table-column prop="description" label="说明" min-width="260" />
      <el-table-column prop="id" label="标识" min-width="230" />
      <el-table-column label="类型" width="130">
        <template #default="{ row }">
          <el-tag>{{
            row.module_kind === "declarative"
              ? "声明式"
              : row.module_kind === "builtin" || !row.module_kind
                ? "内置"
                : "外部模块"
          }}</el-tag>
        </template>
      </el-table-column>
    </el-table>
    <el-collapse>
      <el-collapse-item title="声明式模块可用数据与操作符">
        <pre class="catalog-data">{{
          JSON.stringify(availableData || {}, null, 2)
        }}</pre>
      </el-collapse-item>
    </el-collapse>
  </el-card>
</template>
<style scoped>
.catalog-data {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  padding: 18px;
  font-size: 13px;
}
</style>
