<script setup lang="ts">
/**
 * ErrorBankView.vue
 * 错题库工作台：左侧题目列表 + 中间题目详情 + 右侧学习分析。
 *
 * 第一版只使用现有后端能力：错题查询、筛选、统计、复习状态和 AI 分析接口。
 */
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue'
import {
  typesetMath as _typesetMath,
} from '@/utils/index'
import { useBatchSelection } from '@/composables/useBatchSelection'
import { useErrorBankActions } from '@/composables/useErrorBankActions'
import { useErrorBankQuery } from '@/composables/useErrorBankQuery'
import { useErrorBankStats } from '@/composables/useErrorBankStats'
import ContentPanel from '@/components/features/app/layout/ContentPanel.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseModal from '@/components/base/BaseModal.vue'
import BaseStat from '@/components/base/BaseStat.vue'
import ErrorQuestionFinderAside from '@/components/features/app/error-bank/ErrorQuestionFinderAside.vue'
import ErrorQuestionDetailPanel from '@/components/features/app/error-bank/ErrorQuestionDetailPanel.vue'
import ErrorQuestionListPanel from '@/components/features/app/error-bank/ErrorQuestionListPanel.vue'
import EditNoteDialog from '@/components/features/app/question/EditNoteDialog.vue'
import QuestionDetailModal from '@/components/features/app/question/QuestionDetailModal.vue'
import { useToast } from '@/composables/useToast'
import { useImageModal } from '@/composables/useImageModal'
import { useWorkspaceNav } from '@/composables/useWorkspaceNav'
import { useChatSession } from '@/composables/useChatSession'
import { useProjects } from '@/composables/useProjects'
import * as api from '@/api/index'
import type { ErrorBankQuestion } from '@/types/domain'

defineProps({
  embedded: { type: Boolean, default: false },
})

const { pushToast } = useToast()
const { openModal } = useImageModal()
const { currentView } = useWorkspaceNav()
const { openChat } = useChatSession()
const { activeQuestionProjectId, questionProjects } = useProjects()
const hasQuestionProject = computed(() => questionProjects.value.length > 0)

const activeTab = ref('answer')
const workbenchView = ref('list')
const statsCollapsed = ref(false)

// 列表常驻复选框：选中状态跨页保留，筛选条件或错题库变化时清空。
const { selectedIds, selecting, toggleSelect, selectIds, clearSelection, selectAllMatching } = useBatchSelection()

const detailTabs = [
  { value: 'answer', label: '答案解析', icon: 'fa-circle-check' },
  { value: 'note', label: '作答记录', icon: 'fa-camera' },
] as const

const typesetMath = async () => {
  await nextTick()
  await _typesetMath()
}

const {
  filters,
  page,
  pageSize,
  items,
  total,
  grandTotal,
  totalPages,
  loading,
  subjects,
  questionTypes,
  tagNames,
  selectedTags,
  activeQuestionId,
  activeQuestion,
  contentBlocks,
  optionList,
  knowledgeTags,
  reviewStatusOptions,
  buildFilterParams,
  doQuery,
  loadFilters,
  refreshTags,
  debouncedQuery,
  resetFilters,
  goPage,
  dispose: disposeQuery,
} = useErrorBankQuery({ activeQuestionProjectId, pushToast, typesetMath })

const { statsCards, loadStats } = useErrorBankStats({ activeQuestionProjectId, grandTotal, total })

const {
  dialogOpen,
  dialogField,
  dialogQuestion,
  dialogSaving,
  detailOpen,
  detailQuestion,
  openDetail,
  openEditDialog,
  onDialogSave,
  quickMarkStatus,
  doDelete,
  doExport,
  startPractice,
} = useErrorBankActions({
  items,
  total,
  grandTotal,
  activeQuestionId,
  selectedIds,
  activeQuestion,
  activeTab,
  pushToast,
  typesetMath,
  loadStats,
})

/** 当前筛选结果是否已全部选中（跨页）。 */
const allSelected = computed(() => total.value > 0 && selectedIds.size >= total.value)

/** 全选所有筛选结果；已全选时取消全选。 */
const toggleSelectAll = async () => {
  if (allSelected.value) {
    clearSelection()
    return
  }
  try {
    await selectAllMatching(async () => {
      const data = await api.fetchErrorBankIds(buildFilterParams())
      return data.ids || []
    })
  } catch (error) {
    pushToast('error', error instanceof Error ? error.message : String(error))
  }
}

const batchDeleteOpen = ref(false)
const batchDeleting = ref(false)

const requestBatchDelete = () => {
  if (selectedIds.size) batchDeleteOpen.value = true
}

/** 批量删除选中题目，成功后刷新列表和统计。 */
const confirmBatchDelete = async () => {
  if (batchDeleting.value || !selectedIds.size) return
  batchDeleting.value = true
  try {
    const data = await api.batchDeleteQuestions(Array.from(selectedIds))
    clearSelection()
    batchDeleteOpen.value = false
    pushToast('success', data.message || `已删除 ${data.deleted || 0} 道题目`)
    await Promise.all([doQuery(), loadStats()])
  } catch (error) {
    pushToast('error', error instanceof Error ? error.message : String(error))
  } finally {
    batchDeleting.value = false
  }
}

/**
 * 选择题目后同步详情和分析区域。
 */
const selectQuestion = async (q: ErrorBankQuestion | null | undefined) => {
  if (!q) return
  activeQuestionId.value = q.id
  activeTab.value = 'answer'
  await nextTick()
  await typesetMath()
}

const handleQuestionClick = async (q: ErrorBankQuestion) => {
  workbenchView.value = 'detail'
  await selectQuestion(q)
}

const handleFinderSelect = async (q: ErrorBankQuestion) => {
  workbenchView.value = 'detail'
  await selectQuestion(q)
}

watch(() => [filters.subject, filters.knowledge_tag, filters.question_type, filters.review_status], () => {
  clearSelection()
  debouncedQuery()
})
watch(() => filters.keyword, () => {
  clearSelection()
  debouncedQuery(500)
})
watch(() => filters.subject, async () => {
  filters.knowledge_tag = ''
  selectedTags.clear()
  try { await refreshTags() } catch (_) { }
})
watch(activeQuestionProjectId, async () => {
  workbenchView.value = 'list'
  clearSelection()
  resetFilters()
  await Promise.all([loadFilters(), loadStats()])
})
watch(activeQuestion, async () => {
  await typesetMath()
}, { flush: 'post' })

onMounted(async () => {
  await Promise.all([loadFilters(), loadStats(), doQuery()])
})

onBeforeUnmount(() => {
  disposeQuery()
})

defineExpose({
  refresh: doQuery,
  clearSelection,
})
</script>

<template>
  <component
    :is="embedded ? 'div' : ContentPanel"
    :title="embedded ? undefined : '错题库'"
    :class="embedded ? 'flex h-full min-h-0 flex-col overflow-hidden p-4' : ''"
  >
    <template v-if="!embedded" #toolbar>
      <BaseButton size="sm" variant="primary" @click="currentView = 'workspace'">
        <i class="fa-solid fa-plus"></i>
        录入题目
      </BaseButton>
    </template>

    <div class="flex h-full min-h-0 flex-col gap-4 overflow-hidden">
      <!-- 顶部统计卡片 -->
      <div class="grid shrink-0 grid-cols-2 gap-3 lg:grid-cols-5">
        <BaseStat
          v-for="card in statsCards"
          :key="card.label"
          :label="card.label"
          :value="card.value"
          :suffix="card.suffix"
          :hint="card.hint"
          :icon="card.icon"
          :tone="card.tone"
        />
      </div>

      <!-- 主体：列表/详情主从切换 + 右侧学习分析 -->
      <div class="grid min-h-0 flex-1 gap-4"
        :class="workbenchView === 'list' ? 'xl:grid-cols-[minmax(0,1fr)_minmax(16rem,0.34fr)]' : ''">
        <div class="flex min-h-0 flex-col gap-3">
          <ErrorQuestionListPanel
            v-if="workbenchView === 'list'"
            class="h-full min-h-0"
            :filters="filters"
            :review-status-options="reviewStatusOptions"
            :tag-names="tagNames"
            :question-types="questionTypes"
            :subjects="subjects"
            :items="items"
            :total="total"
            :page="page"
            :page-size="pageSize"
            :total-pages="totalPages"
            :loading="loading"
            :active-question-id="activeQuestion?.id"
            :selected-count="selectedIds.size"
            :all-selected="allSelected"
            :selecting="selecting"
            :selected-ids="selectedIds"
            :has-question-project="hasQuestionProject"
            @toggle-select-all="toggleSelectAll"
            @clear-selection="clearSelection"
            @batch-delete="requestBatchDelete"
            @export-selected="doExport"
            @create-question="currentView = 'workspace'"
            @question-click="handleQuestionClick"
            @toggle-select="toggleSelect"
            @mark-status="quickMarkStatus"
            @edit-note="openEditDialog"
            @page-change="goPage"
          />

          <ErrorQuestionDetailPanel
            v-else
            v-model:active-tab="activeTab"
            class="h-full min-h-0"
            :question="activeQuestion"
            :content-blocks="contentBlocks"
            :option-list="optionList"
            :knowledge-tags="knowledgeTags"
            :detail-tabs="detailTabs"
            @edit="openEditDialog"
            @delete="doDelete"
            @open-image="openModal"
            @open-detail="openDetail"
            @open-chat="openChat"
            @start-practice="startPractice"
            @back-to-list="workbenchView = 'list'"
            @answer-saved="doQuery"
          />
        </div>

        <!-- 右侧：列表态找题 / 详情态学习分析 -->
        <ErrorQuestionFinderAside
          v-if="workbenchView === 'list'"
          :project-id="activeQuestionProjectId"
          @select-question="handleFinderSelect"
          @error="(e) => pushToast('error', e instanceof Error ? e.message : 'AI 找题失败')"
        />
      </div>

      <EditNoteDialog :open="dialogOpen" :field="dialogField" :question="dialogQuestion" :value="dialogField === 'question'
        ? (dialogQuestion?.content_json?.filter(b => b.block_type === 'text').map(b => b.content).join('\n') || '')
        : (dialogQuestion?.[dialogField] || '')"
        :value-answer="dialogField === 'question' ? (dialogQuestion?.answer || '') : ''" :saving="dialogSaving"
        @close="dialogOpen = false" @save="onDialogSave" />

      <QuestionDetailModal :open="detailOpen" :question="detailQuestion" @close="detailOpen = false"
        @open-image="openModal" @deleted="doQuery" @push-toast="pushToast" @start-chat="openChat"
        @answer-saved="doQuery" @review-status-changed="doQuery" />

      <BaseModal :open="batchDeleteOpen" title="批量删除题目" icon="fa-trash" icon-class="text-rose-500"
        icon-bg="bg-rose-50 dark:bg-rose-500/10" max-width="max-w-[28rem]" @close="batchDeleteOpen = false">
        <p class="text-sm leading-relaxed text-slate-600 dark:text-[#8a8f98]">
          确定删除选中的 {{ selectedIds.size }} 道题目？删除后无法恢复。
        </p>
        <template #footer>
          <BaseButton variant="secondary" size="sm" :disabled="batchDeleting" @click="batchDeleteOpen = false">
            取消
          </BaseButton>
          <BaseButton variant="danger" size="sm" :loading="batchDeleting" @click="confirmBatchDelete">
            确认删除
          </BaseButton>
        </template>
      </BaseModal>
    </div>
  </component>
</template>
