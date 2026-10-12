<script setup>
import "element-plus/es/components/input-number/style/css";
import {
  computed,
  inject,
  nextTick,
  onMounted,
  ref,
  useAttrs,
  watch,
} from "vue";
import { ElInputNumber, formContextKey } from "element-plus";
defineOptions({ inheritAttrs: false });
const props = defineProps({ disabled: Boolean });
const attrs = useAttrs();
const form = inject(formContextKey, undefined);
const control = ref(null);
const isDisabled = computed(() => props.disabled || !!form?.disabled);
// Element Plus 2.14 的数字输入只在挂载时写 aria-disabled。
// 同步可访问状态，避免异步加载结束后仍被读屏/浏览器识别为禁用。
async function syncDisabled() {
  await nextTick();
  control.value?.$el
    .querySelector("input")
    ?.setAttribute("aria-disabled", String(isDisabled.value));
}
onMounted(syncDisabled);
watch(isDisabled, syncDisabled, { flush: "post" });
</script>
<template>
  <ElInputNumber ref="control" v-bind="attrs" :disabled="isDisabled" />
</template>
