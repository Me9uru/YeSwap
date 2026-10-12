<script setup>
import "element-plus/es/components/empty/style/css";
import "element-plus/es/components/form/style/css";
import "element-plus/es/components/form-item/style/css";
import "element-plus/es/components/input/style/css";
import "element-plus/es/components/option/style/css";
import "element-plus/es/components/select/style/css";
import "element-plus/es/components/switch/style/css";
import {
  ElEmpty,
  ElForm,
  ElFormItem,
  ElInput,
  ElOption,
  ElSelect,
  ElSwitch,
} from "element-plus";
import NumberInput from "./NumberInput.vue";

defineProps({
  step: { type: Object, required: true },
  module: { type: Object, required: true },
  busy: Boolean,
});
defineEmits(["update:param"]);
</script>
<template>
  <div class="parameter-pane">
    <h3>{{ module.name }}</h3>
    <p class="muted">{{ module.description }}</p>
    <el-form label-position="top" :disabled="busy">
      <el-form-item
        v-for="(meta, key) in module.params_schema"
        :key="key"
        :label="meta.label || key"
      >
        <el-switch
          v-if="meta.type === 'boolean'"
          :model-value="step.params[key]"
          @update:model-value="(value) => $emit('update:param', key, value)"
        />
        <NumberInput
          v-else-if="['integer', 'number'].includes(meta.type)"
          :model-value="step.params[key]"
          @update:model-value="(value) => $emit('update:param', key, value)"
          :min="meta.min"
          :max="meta.max"
          :precision="meta.type === 'integer' ? 0 : undefined"
          :step="meta.type === 'integer' ? 1 : 0.01"
        />
        <el-select
          v-else-if="meta.type === 'select'"
          :model-value="step.params[key]"
          @update:model-value="(value) => $emit('update:param', key, value)"
        >
          <el-option
            v-for="option in meta.options"
            :key="option.value ?? option"
            :label="option.label ?? String(option)"
            :value="option.value ?? option"
          />
        </el-select>
        <el-select
          v-else-if="meta.type === 'array'"
          :model-value="step.params[key]"
          @update:model-value="(value) => $emit('update:param', key, value)"
          multiple
          filterable
          allow-create
          default-first-option
          :reserve-keyword="false"
          placeholder="输入后按回车添加"
        />
        <el-input
          v-else
          :model-value="step.params[key]"
          @update:model-value="(value) => $emit('update:param', key, value)"
        />
      </el-form-item>
    </el-form>
    <el-empty
      v-if="!Object.keys(module.params_schema || {}).length"
      description="该模块没有可编辑参数"
      :image-size="55"
    />
  </div>
</template>
<style scoped>
.parameter-pane {
  border-left: 1px solid var(--el-border-color-lighter);
  padding-left: 22px;
}
@media (max-width: 700px) {
  .parameter-pane {
    border: 0;
    padding: 0;
  }
}
</style>
