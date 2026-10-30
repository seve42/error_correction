<script setup lang="ts">
/**
 * ErrorBankView.vue
 * 閿欓搴撳伐浣滃彴锛氬乏渚ч鐩垪琛?+ 涓棿棰樼洰璇︽儏 + 鍙充晶瀛︿範鍒嗘瀽銆?
 *
 * 绗竴鐗堝彧浣跨敤鐜版湁鍚庣鑳藉姏锛氶敊棰樻煡璇€佺瓫閫夈€佺粺璁°€佸涔犵姸鎬佸拰 AI 鍒嗘瀽鍗犱綅鎺ュ彛銆?
 */
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue'
import {
  typesetMath as _typesetMath,
} from '@/utils/index'
import { useSelectableList } from '@/composables/useSelectableList'
import { useErrorBankActions } from '@/composables/useErrorBankActions'
import { useErrorBankQuery } from '@/composables/useErrorBankQuery'
import { useErrorBankStats } from '@/composables/useErrorBankStats'
import ContentPanel from '@/components/features/app/layout/ContentPanel.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseStat from '@/components/base/BaseStat.vue'
import ErrorQuestionFinderAside from '@/components/features/app/error-bank/ErrorQuestionFinderAside.vue'
import ErrorQuestionDetailPanel from '@/components/features/app/error-bank/ErrorQuestionDetailPanel.vue'
import ErrorQuestionListPanel from '@/components/features/app/error-bank/ErrorQuestionListPanel.vue'
import EditNoteDialog from '@/components/features/app/question/EditNoteDialog.vue'
import QuestionDetailModal from '@/components/features/app/question/QuestionDetailModal.vue'
import SelectionPanel from '@/components/features/app/workspace/SelectionPanel.vue'
import { useToast } from '@/composables/useToast'
import { useImageModal } from '@/composables/useImageModal'
import { useWorkspaceNav } from '@/composables/useWorkspaceNav'
import { useChatSession } from '@/composables/useChatSession'
import { useProjects } from '@/composables/useProjects'
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

const { selectMode, selectedIds, toggleSelectMode, toggleSelect, clearSelection } = useSelectableList()

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

/**
 * 閫夋嫨棰樼洰鍚庡悓姝ヨ鎯呭拰鍒嗘瀽鍖哄煙銆?
 */
const selectQuestion = async (q: ErrorBankQuestion | null | undefined) => {
  if (!q) return
  activeQuestionId.value = q.id
  activeTab.value = 'answer'
  await nextTick()
  await typesetMath()
}

const handleQuestionClick = async (q: ErrorBankQuestion) => {
  if (selectMode.value) toggleSelect(q.id)
  else {
    workbenchView.value = 'detail'
    await selectQuestion(q)
  }
}

const handleFinderSelect = async (q: ErrorBankQuestion) => {
  workbenchView.value = 'detail'
  await selectQuestion(q)
}

watch(() => [filters.subject, filters.knowledge_tag, filters.question_type, filters.review_status], () => debouncedQuery())
watch(() => filters.keyword, () => debouncedQuery(500))
watch(() => filters.subject, async () => {
  filters.knowledge_tag = ''
  selectedTags.clear()
  try { await refreshTags() } catch (_) { }
})
watch(activeQuestionProjectId, async () => {
  workbenchView.value = 'list'
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
  toggleSelectMode,
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
        褰曞叆棰樼洰
      </BaseButton>
    </template>

    <div class="flex h-full min-h-0 flex-col gap-4 overflow-hidden">
      <!-- 椤堕儴缁熻鍗＄墖 -->
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

      <!-- 涓讳綋锛氬垪琛?璇︽儏涓讳粠鍒囨崲 + 鍙充晶瀛︿範鍒嗘瀽 -->
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
            :select-mode="selectMode"
            :selected-ids="selectedIds"
            :has-question-project="hasQuestionProject"
            @toggle-select-mode="toggleSelectMode"
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

        <!-- 鍙充晶锛氬垪琛ㄦ€佹壘棰?/ 璇︽儏鎬佸涔犲垎鏋?-->
        <ErrorQuestionFinderAside
          v-if="workbenchView === 'list'"
          :project-id="activeQuestionProjectId"
          @select-question="handleFinderSelect"
          @error="(e) => pushToast('error', e instanceof Error ? e.message : 'AI 鎵鹃澶辫触')"
        />
      </div>

      <SelectionPanel :visible="selectMode" :count="selectedIds.size" @export="doExport" @clear="clearSelection" />

      <EditNoteDialog :open="dialogOpen" :field="dialogField" :question="dialogQuestion" :value="dialogField === 'question'
        ? (dialogQuestion?.content_json?.filter(b => b.block_type === 'text').map(b => b.content).join('\n') || '')
        : (dialogQuestion?.[dialogField] || '')"
        :value-answer="dialogField === 'question' ? (dialogQuestion?.answer || '') : ''" :saving="dialogSaving"
        @close="dialogOpen = false" @save="onDialogSave" />

      <QuestionDetailModal :open="detailOpen" :question="detailQuestion" @close="detailOpen = false"
        @open-image="openModal" @deleted="doQuery" @push-toast="pushToast" @start-chat="openChat"
        @answer-saved="doQuery" @review-status-changed="doQuery" />
    </div>
  </component>
</template>
