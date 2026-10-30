<script setup lang="ts">
/**
 * BatchSelectBar.vue
 * 列表批量选择条：全选/取消全选、已选数量、清空，以及右侧批量操作插槽。
 * 错题库、导入核对页、分割历史共用同一套选择交互。
 */
import BaseButton from '@/components/base/BaseButton.vue'
import BaseCheckbox from '@/components/base/BaseCheckbox.vue'

withDefaults(defineProps<{
  selectedCount?: number
  totalCount?: number
  allSelected?: boolean
  loading?: boolean
}>(), {
  selectedCount: 0,
  totalCount: 0,
  allSelected: false,
  loading: false,
})

const emit = defineEmits<{
  'toggle-all': []
  clear: []
}>()
</script>

<template>
  <div class="flex flex-wrap items-center gap-3">
    <BaseCheckbox
      :model-value="allSelected"
      :indeterminate="selectedCount > 0 && !allSelected"
      :disabled="loading || !totalCount"
      @update:model-value="emit('toggle-all')"
    >
      {{ allSelected ? '取消全选' : '全选' }}
    </BaseCheckbox>

    <span class="text-xs text-gray-500 dark:text-[#8a8f98]">
      已选 <b class="font-semibold text-slate-700 dark:text-[#d0d6e0]">{{ selectedCount }}</b> / {{ totalCount }} 题
    </span>

    <BaseButton v-if="selectedCount > 0" variant="ghost" size="sm" @click="emit('clear')">
      <i class="fa-solid fa-xmark"></i> 清空
    </BaseButton>

    <div class="ml-auto flex flex-wrap items-center gap-2">
      <slot name="actions" />
    </div>
  </div>
</template>
