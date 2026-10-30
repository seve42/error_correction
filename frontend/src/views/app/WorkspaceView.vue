<script setup lang="ts">
/**
 * WorkspaceView.vue
 * 录入工作台 — 上传/识别/分割/导出 全流程
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useToast } from '@/composables/useToast'
import { useSystemStatus } from '@/composables/useSystemStatus'
import { useImageModal } from '@/composables/useImageModal'
import { useWorkspaceNav } from '@/composables/useWorkspaceNav'
import { useQuestionList } from '@/composables/useQuestionList'
import { useFileUpload } from '@/composables/useFileUpload'
import { useSplitPipeline } from '@/composables/useSplitPipeline'
import { useProjects } from '@/composables/useProjects'
import ContentPanel from '@/components/features/app/layout/ContentPanel.vue'
import SelectionPanel from '@/components/features/app/workspace/SelectionPanel.vue'
import UploadStage from '@/components/features/app/workspace/UploadStage.vue'
import ReviewStage from '@/components/features/app/workspace/ReviewStage.vue'
import SplitHistory from '@/views/app/SplitHistoryView.vue'
import BaseModal from '@/components/base/BaseModal.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseCheckbox from '@/components/base/BaseCheckbox.vue'
import BaseToolbarButton from '@/components/base/BaseToolbarButton.vue'

defineOptions({ name: 'WorkspaceView' })

const WORKSPACE_STATE_KEY = 'workspace_split_state_v1'

const route = useRoute()
const { pushToast } = useToast()
const { openModal } = useImageModal()
const { currentView } = useWorkspaceNav()
const { questionProjects, noteProjects, activeQuestionProjectId, activeNoteProjectId, loadProjects, setActiveProject } = useProjects()
const {
  statusLoading, statusError,
  providerOptions, hasConfiguredModel, statusPills,
  modelOptionsData, selectedLlmOptionId, selectedLlmOption, doFetchModelOptions
} = useSystemStatus()
const {
  questions, selectedIds,
  toggleQuestion, selectAll, deselectAll, reorderQuestions, typesetMath,
} = useQuestionList()

const props = defineProps({
  theme: { type: String, default: 'dark' },
})

// ── 步骤控制 ────────────────────────────────────────────
const step = ref(1)
const uploadMode = ref('exam')
const workflowMode = ref('manual')
const splitting = ref(false)
const splitCompleted = ref(false)
const showSplitHistory = ref(false)

// 擦除步骤已并入视觉 OCR，试卷模式固定为 上传 → 识别 → 分割 → 导出。
const S = computed(() => ({ UPLOAD: 1, OCR: 2, SPLIT: 3, EXPORT: 4 }))
const workspaceSteps = computed(() => {
  const labels = uploadMode.value === 'note'
    ? ['上传', 'OCR', '整理', '保存']
    : ['上传', '识别', '分割', '导出']
  return labels.map((label, i) => ({
    label,
    done: i + 1 < step.value,
    active: i + 1 === step.value,
  }))
})

// ── 文件上传 ────────────────────────────────────────────
const {
  uploadBusy, uploadReady, pendingFiles, pendingPreviewUrls,
  fileProgress, waitingKeys,
  enqueueUpload, removePendingFile, stopFakeProgress, doReset: _doReset,
} = useFileUpload(pushToast, S, questions, selectedIds, splitCompleted, splitting, uploadMode)

const splitEnabled = computed(() => !splitting.value && !splitCompleted.value && uploadReady.value && !uploadBusy.value && hasConfiguredModel.value)

/**
 * 清理本地保存的工作台分割结果。
 */
const clearPersistedWorkspaceState = () => {
  try { localStorage.removeItem(WORKSPACE_STATE_KEY) } catch (_) { }
}

/**
 * 分割完成后保存结果快照，刷新页面时可以恢复核对页。
 */
const savePersistedWorkspaceState = () => {
  const shouldPersist = splitCompleted.value && questions.value.length > 0
  if (!shouldPersist) {
    clearPersistedWorkspaceState()
    return
  }
  const payload = {
    step: step.value,
    splitCompleted: splitCompleted.value,
    questions: questions.value,
    selectedIds: Array.from(selectedIds),
    currentView: currentView.value,
    uploadMode: uploadMode.value,
    workflowMode: workflowMode.value,
    savedAt: Date.now(),
  }
  try {
    localStorage.setItem(WORKSPACE_STATE_KEY, JSON.stringify(payload))
  } catch (_) { }
}

/**
 * 尝试从 localStorage 恢复上次分割结果和选中状态。
 */
const restorePersistedWorkspaceState = async () => {
  let raw = ''
  try { raw = localStorage.getItem(WORKSPACE_STATE_KEY) || '' } catch (_) { }
  if (!raw) return

  let state = null
  try { state = JSON.parse(raw) } catch (_) { }
  if (!state || !Array.isArray(state.questions) || state.questions.length === 0) return

  questions.value = state.questions
  selectedIds.clear()
  for (const id of (state.selectedIds || [])) selectedIds.add(id)
  splitCompleted.value = Boolean(state.splitCompleted)
  uploadMode.value = state.uploadMode === 'note' ? 'note' : 'exam'
  workflowMode.value = state.workflowMode === 'auto' ? 'auto' : 'manual'
  // 旧快照可能停留在已移除的擦除步骤（step=5），统一收敛到新流程的最大步骤。
  const restoredStep = Number(state.step) || S.value.EXPORT
  step.value = Math.min(Math.max(restoredStep, S.value.UPLOAD), S.value.EXPORT)
  currentView.value = 'workspace_review'
  await nextTick()
  setTimeout(() => { reviewStageRef.value?.triggerTypeset?.() }, 400)
}

/**
 * 重置上传与分割流程，并清理本地恢复快照。
 */
const doReset = async () => {
  await _doReset(modelOptionsData, selectedLlmOptionId, step)
  clearPersistedWorkspaceState()
  step.value = S.value.UPLOAD
}

// ── 分割流水线 ──────────────────────────────────────────
const {
  ocrLoading, ocrPages, ocrDone, notePreview, autoProcessing,
  startProcess, runAutoProcess, doOcr, doSplit, doSaveToDb, saveNoteToProject, setLoadedSplitRecord,
  generateSelectedAnswers,
} = useSplitPipeline(pushToast, currentView, step, S, uploadReady, splitting, splitCompleted, uploadMode, selectedLlmOption, questions, selectedIds, pendingFiles, typesetMath)

const reviewStageRef = ref(null)
const restoringWorkspaceState = ref(true)
const importDialogOpen = ref(false)
const importTargetProjectId = ref(null)
const importSaving = ref(false)
const autoGenerateAnswers = ref(false)
const generatingAnswers = ref(false)
const noteSaveDialogOpen = ref(false)
const noteTargetProjectId = ref(null)
const noteSaving = ref(false)
const updateSelectedLlmOption = (value) => {
  selectedLlmOptionId.value = value || ''
}
const hasReviewContent = computed(() => {
  if (ocrLoading.value) return true
  if (ocrDone.value) return true
  if (splitting.value) return true
  if (splitCompleted.value && questions.value.length > 0) return true
  return false
})

/**
 * 把分割历史中的题目载入当前工作台核对页。
 */
const handleLoadRecord = (qs, record) => {
  questions.value = qs || []; selectedIds.clear()
  setLoadedSplitRecord(record)
  splitCompleted.value = true; step.value = S.value.EXPORT
  currentView.value = 'workspace_review'
  pushToast('success', `已加载「${record?.subject || '历史记录'}」的 ${qs.length} 道题目`)
  nextTick(() => typesetMath())
}

/**
 * 从核对页返回上传页，并重置识别/分割结果。
 */
const handleBack = async () => {
  await doReset()
  ocrPages.value = []; ocrDone.value = false
  notePreview.value = null
  noteSaveDialogOpen.value = false
  currentView.value = 'workspace'
}

/**
 * 打开导入错题库弹窗，要求用户至少选中一道题。
 */
const openImportDialog = () => {
  if (!selectedIds.size) {
    pushToast('error', '请至少选择一道题目！')
    return
  }
  if (!questionProjects.value.length) {
    pushToast('error', '请先创建一个错题库')
    return
  }
  importTargetProjectId.value = activeQuestionProjectId.value || questionProjects.value[0]?.id || null
  autoGenerateAnswers.value = false
  importDialogOpen.value = true
}

const closeImportDialog = () => {
  if (importSaving.value) return
  importDialogOpen.value = false
}

const openNoteSaveDialog = async () => {
  if (!notePreview.value) {
    pushToast('error', '暂无可保存的笔记结果')
    return
  }
  try {
    await loadProjects()
  } catch (error) {
    pushToast('error', error instanceof Error ? error.message : '刷新笔记本列表失败')
    return
  }
  if (!noteProjects.value.length) {
    pushToast('error', '请先创建一个笔记本')
    return
  }
  noteTargetProjectId.value = activeNoteProjectId.value || noteProjects.value[0]?.id || null
  noteSaveDialogOpen.value = true
}

const closeNoteSaveDialog = () => {
  if (noteSaving.value) return
  noteSaveDialogOpen.value = false
}

/**
 * 将选中题目保存到目标错题库，成功后切换当前错题库。
 */
const confirmImportToProject = async () => {
  if (!importTargetProjectId.value || importSaving.value) return
  importSaving.value = true
  try {
    if (autoGenerateAnswers.value) {
      generatingAnswers.value = true
      const generated = await generateSelectedAnswers()
      if (!generated) return
    }
    const saved = await doSaveToDb(importTargetProjectId.value)
    if (saved) {
      setActiveProject(importTargetProjectId.value, 'question')
      importDialogOpen.value = false
    }
  } finally {
    generatingAnswers.value = false
    importSaving.value = false
  }
}

const confirmSaveNoteToProject = async () => {
  if (!noteTargetProjectId.value || noteSaving.value) return
  noteSaving.value = true
  try {
    const savedNote = await saveNoteToProject(noteTargetProjectId.value)
    if (savedNote) {
      setActiveProject(noteTargetProjectId.value, 'note')
      noteSaveDialogOpen.value = false
      currentView.value = 'notes'
    }
  } finally {
    noteSaving.value = false
  }
}

// ── 键盘事件 ────────────────────────────────────────────
const handleStartProcess = () => {
  if (!splitEnabled.value || autoProcessing.value) return
  if (workflowMode.value === 'auto') {
    runAutoProcess()
    return
  }
  startProcess()
}

const onKeydown = (e) => {
  if (e.key === 'a' && (e.ctrlKey || e.metaKey) && questions.value.length) {
    e.preventDefault(); selectAll()
  }
}

// ── 视图切换 ────────────────────────────────────────────
watch(currentView, async (v) => {
  if (v === 'workspace_review') {
    // 首次挂载恢复期间先不做空态回退，避免 refresh 时路由在 review/workspace 间抖动。
    if (restoringWorkspaceState.value) return
    // 兜底：避免导航恢复到 review 路由但没有任何可展示数据，出现空白页。
    if (!hasReviewContent.value) {
      currentView.value = 'workspace'
      step.value = S.value.UPLOAD
      return
    }
    await nextTick()
    setTimeout(() => { reviewStageRef.value?.triggerTypeset?.() }, 650)
  }
}, { immediate: true })

watch(
  [questions, splitCompleted, step, currentView, uploadMode, workflowMode],
  () => savePersistedWorkspaceState(),
  { deep: true },
)

watch(notePreview, (value) => {
  if (uploadMode.value === 'note' && value) {
    openNoteSaveDialog()
  }
})

// ── 生命周期 ────────────────────────────────────────────
import { onMounted } from 'vue'
onMounted(async () => {
  document.addEventListener('keydown', onKeydown)
  // 检查 URL 参数，设置上传模式
  const mode = route.query.mode
  if (mode === 'note') {
    uploadMode.value = 'note'
  }
  try {
    await restorePersistedWorkspaceState()
  } finally {
    restoringWorkspaceState.value = false
  }
  if (currentView.value === 'workspace_review' && !hasReviewContent.value) {
    currentView.value = 'workspace'
    step.value = S.value.UPLOAD
  }
})
onBeforeUnmount(() => {
  stopFakeProgress()
  document.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div class="h-full">
    <Transition name="flip" mode="out-in">
      <!-- 第一页：上传与分析 -->
      <ContentPanel v-if="currentView === 'workspace'" key="upload" title="智能录入与分析" :steps="workspaceSteps"
        :current-step="step - 1" :sidebar-open="showSplitHistory">
        <template #toolbar>
          <BaseToolbarButton
            icon="fa-clock-rotate-left"
            :active="showSplitHistory"
            @click="showSplitHistory = !showSplitHistory"
          >
            分割历史
          </BaseToolbarButton>
        </template>

        <template #sidebar>
          <SplitHistory :theme="theme" :visible="showSplitHistory" @push-toast="pushToast" @open-image="openModal"
            @load-record="(qs, record) => { handleLoadRecord(qs, record); showSplitHistory = false }"
            @go-workspace="currentView = splitCompleted ? 'workspace_review' : 'workspace'" />
        </template>

        <UploadStage :upload-mode="uploadMode" :workflow-mode="workflowMode" :status-loading="statusLoading"
          :status-error="statusError" :status-pills="statusPills" :model-options-data="modelOptionsData"
          :selected-llm-option-id="selectedLlmOptionId" :has-configured-model="hasConfiguredModel"
          :splitting="splitting || autoProcessing" :split-completed="splitCompleted" :pending-files="pendingFiles"
          :file-progress="fileProgress" :waiting-keys="waitingKeys" :upload-busy="uploadBusy"
          :upload-ready="uploadReady" :split-enabled="splitEnabled" @update:upload-mode="(v) => uploadMode = v"
          @update:workflow-mode="(v) => workflowMode = v"
          @update:selected-llm-option-id="updateSelectedLlmOption" @upload="enqueueUpload"
          @remove-file="removePendingFile" @split="handleStartProcess" />
      </ContentPanel>

      <!-- 第二页：解析结果核对 -->
      <ContentPanel v-else-if="currentView === 'workspace_review'" key="review"
        :title="splitting
          ? (uploadMode === 'note' ? '正在整理笔记...' : '正在分割...')
          : uploadMode === 'note' && notePreview
            ? '笔记整理结果'
            : ocrDone && !splitCompleted
              ? '识别预览'
              : '题目数据核对'"
        :steps="workspaceSteps" :current-step="step - 1">
        <template #toolbar>
          <BaseToolbarButton icon="fa-arrow-left-long" @click="handleBack">
            返回
          </BaseToolbarButton>
          <template v-if="ocrDone && !splitCompleted && !splitting">
            <BaseToolbarButton icon="fa-arrows-rotate" @click="doOcr">重新识别</BaseToolbarButton>
            <BaseToolbarButton icon="fa-check" variant="primary" @click="doSplit">{{ uploadMode === 'note' ? '确认并整理' : '确认并分割' }}</BaseToolbarButton>
          </template>
          <template v-else-if="uploadMode === 'note' && notePreview">
            <BaseToolbarButton icon="fa-floppy-disk" variant="primary" @click="openNoteSaveDialog">
              保存到笔记本
            </BaseToolbarButton>
          </template>
        </template>

        <ReviewStage ref="reviewStageRef"
          :ocr-loading="ocrLoading" :ocr-done="ocrDone" :ocr-pages="ocrPages"
          :upload-mode="uploadMode" :splitting="splitting" :split-completed="splitCompleted" :questions="questions" :selected-ids="selectedIds"
          :note-preview="notePreview" :model-name="selectedLlmOption?.model_name || ''"
          :preview-url="pendingPreviewUrls[0]" @toggle-select="toggleQuestion" @select-all="selectAll"
          @deselect-all="deselectAll" @open-image="openModal" @reorder="reorderQuestions" />
      </ContentPanel>
    </Transition>

    <!-- 浮动选择面板 -->
    <SelectionPanel :visible="currentView === 'workspace_review'" :count="selectedIds.size" :show-export="false"
      :show-save="true" @save="openImportDialog" @clear="deselectAll" />

    <BaseModal :open="importDialogOpen" title="导入错题库" icon="fa-database" iconBg="accent-bg-soft"
      iconClass="accent-text" maxWidth="max-w-[30rem]" bodyClass="px-6 pb-3 pt-1" @close="closeImportDialog">
      <div class="space-y-3">
        <p class="text-sm text-slate-500 dark:text-[#8a8f98]">
          将 {{ selectedIds.size }} 道已选题目导入到：
        </p>
        <div class="max-h-64 space-y-2 overflow-y-auto pr-1 custom-scrollbar">
          <button v-for="project in questionProjects" :key="project.id" type="button"
            class="flex w-full items-center gap-3 rounded-lg border px-3 py-2.5 text-left transition-colors"
            :class="String(importTargetProjectId) === String(project.id)
              ? 'border-[rgb(var(--accent-rgb)/0.35)] bg-[rgb(var(--accent-rgb)/0.12)] text-[rgb(var(--accent-strong-rgb))] dark:text-[rgb(var(--accent-hover-rgb))]'
              : 'border-slate-200/70 bg-white/50 text-slate-600 hover:border-slate-300 hover:bg-slate-50 dark:border-white/[0.08] dark:bg-white/[0.03] dark:text-[#aeb6c2] dark:hover:bg-white/[0.05]'"
            @click="importTargetProjectId = project.id">
            <i class="fa-solid fa-database w-4 text-center text-xs"></i>
            <span class="min-w-0 flex-1 truncate text-sm font-medium">{{ project.name }}</span>
            <i v-if="String(importTargetProjectId) === String(project.id)" class="fa-solid fa-check text-xs"></i>
          </button>
        </div>
        <div class="rounded-lg border border-slate-200/70 bg-white/50 px-3 py-2.5 dark:border-white/[0.08] dark:bg-white/[0.03]">
          <BaseCheckbox v-model="autoGenerateAnswers" label="自动生成标准答案解析（AI）"
            description="导入前先用当前模型为选中的题目生成标准答案与解析，取消勾选则直接导入。"
            :disabled="importSaving" />
        </div>
      </div>
      <template #footer>
        <BaseButton variant="secondary" size="sm" :disabled="importSaving" @click="closeImportDialog">
          取消
        </BaseButton>
        <BaseButton variant="primary" size="sm" :disabled="importSaving || !importTargetProjectId"
          @click="confirmImportToProject">
          {{ generatingAnswers ? '生成中…' : importSaving ? '导入中...' : '确认导入' }}
        </BaseButton>
      </template>
    </BaseModal>

    <BaseModal :open="noteSaveDialogOpen" title="保存到笔记本" icon="fa-book-open" iconBg="accent-bg-soft"
      iconClass="accent-text" maxWidth="max-w-[30rem]" bodyClass="px-6 pb-3 pt-1" @close="closeNoteSaveDialog">
      <div class="space-y-3">
        <p class="text-sm text-slate-500 dark:text-[#8a8f98]">
          选择要保存到的笔记本：
        </p>
        <div class="max-h-64 space-y-2 overflow-y-auto pr-1 custom-scrollbar">
          <button v-for="project in noteProjects" :key="project.id" type="button"
            class="flex w-full items-center gap-3 rounded-lg border px-3 py-2.5 text-left transition-colors"
            :class="String(noteTargetProjectId) === String(project.id)
              ? 'border-[rgb(var(--accent-rgb)/0.35)] bg-[rgb(var(--accent-rgb)/0.12)] text-[rgb(var(--accent-strong-rgb))] dark:text-[rgb(var(--accent-hover-rgb))]'
              : 'border-slate-200/70 bg-white/50 text-slate-600 hover:border-slate-300 hover:bg-slate-50 dark:border-white/[0.08] dark:bg-white/[0.03] dark:text-[#aeb6c2] dark:hover:bg-white/[0.05]'"
            @click="noteTargetProjectId = project.id">
            <i class="fa-solid fa-book-open w-4 text-center text-xs"></i>
            <span class="min-w-0 flex-1 truncate text-sm font-medium">{{ project.name }}</span>
            <i v-if="String(noteTargetProjectId) === String(project.id)" class="fa-solid fa-check text-xs"></i>
          </button>
        </div>
      </div>
      <template #footer>
        <BaseButton variant="secondary" size="sm" :disabled="noteSaving" @click="closeNoteSaveDialog">
          取消
        </BaseButton>
        <BaseButton variant="primary" size="sm" :disabled="noteSaving || !noteTargetProjectId"
          @click="confirmSaveNoteToProject">
          {{ noteSaving ? '保存中...' : '确认保存' }}
        </BaseButton>
      </template>
    </BaseModal>
  </div>
</template>
