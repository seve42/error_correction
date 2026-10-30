<script setup lang="ts">
/**
 * ErrorQuestionListPanel.vue
 * 错题库左侧题目列表面板。
 */
import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BasePagination from '@/components/base/BasePagination.vue'
import BasePanel from '@/components/base/BasePanel.vue'
import BasePanelTitle from '@/components/base/BasePanelTitle.vue'
import ErrorBankToolbar from '@/components/features/app/error-bank/ErrorBankToolbar.vue'
import ErrorQuestionListItem from '@/components/features/app/error-bank/ErrorQuestionListItem.vue'
import BatchSelectBar from '@/components/features/app/shared/BatchSelectBar.vue'

defineProps({
  filters: { type: Object, required: true },
  reviewStatusOptions: { type: Array, default: () => [] },
  tagNames: { type: Array, default: () => [] },
  questionTypes: { type: Array, default: () => [] },
  subjects: { type: Array, default: () => [] },
  items: { type: Array, default: () => [] },
  total: { type: Number, default: 0 },
  page: { type: Number, default: 1 },
  pageSize: { type: Number, default: 10 },
  totalPages: { type: Number, default: 0 },
  loading: { type: Boolean, default: false },
  activeQuestionId: { type: [String, Number, null], default: null },
  selectedCount: { type: Number, default: 0 },
  allSelected: { type: Boolean, default: false },
  selecting: { type: Boolean, default: false },
  selectedIds: { type: Object, required: true },
  hasQuestionProject: { type: Boolean, default: false },
})

const emit = defineEmits([
  'toggle-select-all',
  'clear-selection',
  'batch-delete',
  'export-selected',
  'create-question',
  'question-click',
  'toggle-select',
  'mark-status',
  'edit-note',
  'page-change',
])

const isActiveQuestion = (question, activeQuestionId) => String(question?.id) === String(activeQuestionId)
</script>

<template>
  <BasePanel body-class="p-4">
    <template #header>
      <div class="flex w-full items-center justify-between gap-3">
        <BasePanelTitle icon="fa-list-check">题目列表</BasePanelTitle>
      </div>
    </template>

      <ErrorBankToolbar
        :filters="filters"
        :review-status-options="reviewStatusOptions"
        :tag-names="tagNames"
        :question-types="questionTypes"
        :subjects="subjects"
        class="mb-4"
      />

      <BatchSelectBar
        class="mb-3"
        :selected-count="selectedCount"
        :total-count="total"
        :all-selected="allSelected"
        :loading="selecting"
        @toggle-all="emit('toggle-select-all')"
        @clear="emit('clear-selection')"
      >
        <template #actions>
          <BaseButton variant="primary" size="sm" :disabled="!selectedCount" @click="emit('export-selected')">
            <i class="fa-solid fa-file-export"></i> 生成复习卷
          </BaseButton>
          <BaseButton variant="danger" size="sm" :disabled="!selectedCount" @click="emit('batch-delete')">
            <i class="fa-solid fa-trash"></i> 删除选中
          </BaseButton>
        </template>
      </BatchSelectBar>

      <p class="mb-3 text-xs text-gray-500 dark:text-[#8a8f98]">共 {{ total }} 题，当前第 {{ page }} 页</p>

      <div v-if="loading && !items.length" class="space-y-3">
        <div v-for="i in 4" :key="i" class="h-28 animate-pulse rounded-xl bg-gray-100 dark:bg-white/[0.04]"></div>
      </div>

      <BaseEmptyState
        v-else-if="!loading && !items.length"
        icon="fa-solid fa-layer-group"
        :title="hasQuestionProject ? '暂无匹配记录' : '还没有错题库'"
        :description="hasQuestionProject ? '调整筛选条件，或者开始新的录入' : '先在左侧创建一个错题库，再录入题目'"
      >
        <BaseButton variant="primary" size="sm" @click="emit('create-question')">
          <i class="fa-solid fa-plus"></i> 录入新题目
        </BaseButton>
      </BaseEmptyState>

      <div v-else class="space-y-3">
        <ErrorQuestionListItem
          v-for="(question, idx) in items"
          :key="question.id"
          :question="question"
          :index="idx + 1 + (page - 1) * pageSize"
          :active="isActiveQuestion(question, activeQuestionId)"
          :selected="selectedIds.has(question.id)"
          @click="emit('question-click', question)"
          @toggle-select="(id) => emit('toggle-select', id)"
          @mark-status="(q, status) => emit('mark-status', q, status)"
          @edit-note="(q, field) => emit('edit-note', q, field)"
        />
      </div>

    <template v-if="totalPages > 1" #footer>
      <BasePagination :page="page" :total-pages="totalPages" @change="(p) => emit('page-change', p)" />
    </template>
  </BasePanel>
</template>
