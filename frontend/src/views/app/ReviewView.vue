<script setup lang="ts">
/**
 * ReviewView.vue
 * 待复习页面，集中展示需要复习的题目，并支持快速标记与补答案。
 */
import { ref, watch, nextTick, onMounted, computed } from 'vue'
import * as api from '@/api/index'
import { typesetMath as _typesetMath } from '@/utils/index'
import ContentPanel from '@/components/features/app/layout/ContentPanel.vue'
import QuestionDetailModal from '@/components/features/app/question/QuestionDetailModal.vue'
import BaseSelect from '@/components/base/BaseSelect.vue'
import QuestionItem from '@/components/features/app/question/QuestionItem.vue'
import { useToast } from '@/composables/useToast'
import { useImageModal } from '@/composables/useImageModal'
import { useWorkspaceNav } from '@/composables/useWorkspaceNav'
import { useChatSession } from '@/composables/useChatSession'
import { useTheme } from '@/composables/useTheme'
import { useProjects } from '@/composables/useProjects'

const { pushToast } = useToast()
const { openModal } = useImageModal()
const { currentView } = useWorkspaceNav()
const { openChat } = useChatSession()
const { isDark } = useTheme()
const { activeQuestionProjectId } = useProjects()
const theme = computed(() => isDark.value ? 'dark' : 'light')

const answerEditId = ref(null)
const answerEditField = ref('')
const answerEditDraft = ref('')
const answerEditSaving = ref(false)

// ---- 学科筛选 ----
const selectedSubject = ref('')
const subjects = ref([])

// ---- 待复习题目列表 ----
const reviewItems = ref([])
const reviewTotal = ref(0)
const reviewLoading = ref(false)

// ---- 详情弹窗 ----
const detailOpen = ref(false)
const detailQuestion = ref(null)

// ---- AI 分析 ----
// （AI 错题分析入口已下线，api.requestAiAnalysis 仍保留在 api 层备用）

// ---- 数据加载 ----
/**
 * 根据当前错题库加载可筛选的学科列表。
 */
const loadSubjects = async () => {
  if (!activeQuestionProjectId.value) {
    subjects.value = []
    return
  }
  try {
    const data = await api.fetchDashboardStats(undefined, activeQuestionProjectId.value)
    subjects.value = data?.subjects || []
  } catch (_) { }
}

/**
 * 加载待复习题目列表，并在数据更新后重新渲染公式。
 */
const loadReviewItems = async () => {
  if (!activeQuestionProjectId.value) {
    reviewItems.value = []
    reviewTotal.value = 0
    reviewLoading.value = false
    return
  }
  reviewLoading.value = true
  try {
    const params = { review_status: '待复习', page: 1, page_size: 20 }
    if (selectedSubject.value) params.subject = selectedSubject.value
    if (activeQuestionProjectId.value) params.project_id = activeQuestionProjectId.value
    const data = await api.fetchErrorBank(params)
    reviewItems.value = data.items || []
    reviewTotal.value = data.total || 0
    const dueData = await api.fetchDueReviews({
      type: 'question',
      limit: 40,
      project_id: activeQuestionProjectId.value,
    })
    const dueQuestions = dueData.questions || []
    reviewItems.value = selectedSubject.value
      ? dueQuestions.filter(q => q.subject === selectedSubject.value)
      : dueQuestions
    reviewTotal.value = reviewItems.value.length
  } catch (e) {
    pushToast('error', '加载待复习题目失败')
  } finally {
    reviewLoading.value = false
    typesetMath()
  }
}

const loadAll = () => { loadSubjects(); loadReviewItems() }

watch(selectedSubject, () => { loadReviewItems() })
watch(activeQuestionProjectId, () => {
  selectedSubject.value = ''
  loadAll()
})

// ---- 题目操作 ----
/**
 * 等待 DOM 更新后重新排版题目中的数学公式。
 */
const typesetMath = async () => {
  await nextTick()
  await _typesetMath()
}

const openDetail = (q) => { detailQuestion.value = q; detailOpen.value = true }
const closeDetail = () => { detailOpen.value = false; detailQuestion.value = null }

/**
 * 快速修改复习状态，非“待复习”状态会从当前列表移除。
 */
const quickMarkStatus = async (q, status) => {
  try {
    const data = await api.updateReviewStatus(q.id, status)
    q.review_status = data.review_status
    pushToast('success', `已标记为「${status}」`)
    if (status !== '待复习') {
      reviewItems.value = reviewItems.value.filter(x => x.id !== q.id)
      reviewTotal.value = Math.max(0, reviewTotal.value - 1)
    }
  } catch (e) {
    pushToast('error', '更新状态失败')
  }
}

const onDeleted = (id) => {
  reviewItems.value = reviewItems.value.filter(q => q.id !== id)
  reviewTotal.value = Math.max(0, reviewTotal.value - 1)
  closeDetail()
}

const onAnswerSaved = (id, answer, updatedAt) => {
  const q = reviewItems.value.find(x => x.id === id)
  if (q) { q.user_answer = answer; q.updated_at = updatedAt }
}

const startInlineEdit = (q, field) => {
  answerEditId.value = q.id
  answerEditField.value = field
  answerEditDraft.value = q[field] || ''
}
const cancelInlineEdit = () => { answerEditId.value = null }
/**
 * 保存内联编辑的答案或用户笔记。
 */
const saveInlineEdit = async () => {
  if (answerEditSaving.value) return
  const q = reviewItems.value.find(x => x.id === answerEditId.value)
  if (!q) return
  answerEditSaving.value = true
  try {
    if (answerEditField.value === 'answer') {
      await api.saveQuestionAnswer(q.id, answerEditDraft.value)
      q.answer = answerEditDraft.value
    } else {
      await api.saveAnswer(q.id, answerEditDraft.value)
      q.user_answer = answerEditDraft.value
    }
    answerEditId.value = null
    pushToast('success', '已保存')
  } catch (e) {
    pushToast('error', '保存失败')
  } finally {
    answerEditSaving.value = false
  }
}

const onReviewStatusChanged = (id, status, updatedAt) => {
  const q = reviewItems.value.find(x => x.id === id)
  if (q) { q.review_status = status; q.updated_at = updatedAt }
  if (status !== '待复习') {
    reviewItems.value = reviewItems.value.filter(x => x.id !== id)
    reviewTotal.value = Math.max(0, reviewTotal.value - 1)
  }
}

const onCorrectAnswerSaved = (id, answer, updatedAt) => {
  const q = reviewItems.value.find(x => x.id === id)
  if (q) { q.answer = answer; q.updated_at = updatedAt }
}

// ---- 生命周期 ----
onMounted(() => { loadAll() })
</script>

<template>
  <ContentPanel title="待复习">
    <template #toolbar>
      <BaseSelect v-if="subjects.length" v-model="selectedSubject" :options="subjects" placeholder="全部学科"
        width-class="min-w-[140px]" />
      <button @click="currentView = 'workspace'" class="btn-primary group h-10 px-8 shadow-md shadow-blue-500/20">
        <i class="fa-solid fa-plus-circle transition-transform group-hover:rotate-90"></i> 录入新题目
      </button>
    </template>
    <div class="relative h-full overflow-y-auto custom-scrollbar">
      <div class="container relative z-10 mx-auto">

        <!-- 操作栏 -->
        <div class="mb-6 flex flex-wrap items-center justify-between gap-4">
          <h3 class="flex items-center gap-2 text-base font-black text-slate-900 dark:text-white">
            <i class="fa-solid fa-list-check accent-text"></i> 题目列表
            <span v-if="reviewTotal"
              class="ml-2 rounded-full accent-bg-soft px-2 py-1 text-xs font-bold accent-text">{{
              reviewTotal }}</span>
          </h3>
        </div>

        <!-- 加载中 -->
        <div v-if="reviewLoading" class="flex justify-center py-16">
          <div class="h-12 w-12 animate-spin rounded-full border-4 border-blue-500/20 border-t-blue-500"></div>
        </div>

        <!-- 空状态 -->
        <div v-else-if="!reviewItems.length"
          class="flex flex-col items-center justify-center rounded-xl border-2 border-dashed border-slate-200 bg-slate-50/50 py-20 dark:border-white/5 dark:bg-white/5">
          <i class="fa-solid fa-circle-check mb-4 text-4xl text-emerald-400"></i>
          <p class="text-base font-black text-slate-900 dark:text-white">全部搞定了</p>
          <p class="mt-1 text-sm text-slate-500">没有待复习的题目，继续保持</p>
        </div>

        <!-- 题目列表 -->
        <div v-else class="space-y-4">
          <QuestionItem v-for="q in reviewItems" :key="q.id" :question="q" :max-tags="3"
            @click="openDetail(q)">
            <template #extra="{ question }">
              <!-- 录入答案/笔记按钮 -->
              <div class="mt-3 flex flex-wrap items-center gap-2 text-xs" @click.stop>
                <button v-if="!question.answer" @click="startInlineEdit(question, 'answer')"
                  class="inline-flex items-center gap-1 rounded-md border border-dashed border-emerald-300 px-2 py-0.5 font-bold text-emerald-500 hover:bg-emerald-50 dark:border-emerald-500/30 dark:hover:bg-emerald-500/10">
                  <i class="fa-solid fa-plus"></i>录入答案
                </button>
                <button v-if="!question.user_answer" @click="startInlineEdit(question, 'user_answer')"
                  class="inline-flex items-center gap-1 rounded-md border border-dashed border-blue-300 px-2 py-0.5 font-bold text-blue-500 hover:bg-blue-50 dark:border-blue-500/30 dark:hover:bg-blue-500/10">
                  <i class="fa-solid fa-plus"></i>记笔记
                </button>
              </div>
              <!-- 内联编辑区 -->
              <div v-if="answerEditId === question.id"
                class="mt-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-white/10 dark:bg-slate-800"
                @click.stop>
                <div class="mb-2 text-xs font-black uppercase tracking-widest"
                  :class="answerEditField === 'answer' ? 'text-emerald-600 dark:text-emerald-400' : 'text-blue-600 dark:text-blue-400'">
                  {{ answerEditField === 'answer' ? '正确答案' : '我的笔记' }}
                </div>
                <textarea v-model="answerEditDraft" rows="3"
                  :placeholder="answerEditField === 'answer' ? '输入正确答案/解析…' : '记录错因或心得…'"
                  class="w-full resize-none rounded-lg border border-slate-200/80 bg-slate-50 px-3 py-2 font-mono text-xs text-slate-800 placeholder-slate-400 focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-500/20 dark:border-white/10 dark:bg-slate-900 dark:text-slate-200"></textarea>
                <div class="mt-2 flex justify-end gap-2">
                  <button @click="cancelInlineEdit"
                    class="rounded-lg px-3 py-1 text-xs font-bold text-slate-500 hover:text-slate-700 dark:text-slate-400">取消</button>
                  <button @click="saveInlineEdit" :disabled="answerEditSaving"
                    class="rounded-lg px-3 py-1 text-xs font-bold text-white disabled:opacity-50"
                    :class="answerEditField === 'answer' ? 'bg-emerald-500 hover:bg-emerald-600' : 'accent-bg hover:bg-[rgb(var(--accent-hover-rgb))]'">
                    {{ answerEditSaving ? '保存中…' : '保存' }}
                  </button>
                </div>
              </div>
            </template>
            <template #actions>
              <div class="group/tip relative">
                <button @click="quickMarkStatus(q, '复习中')"
                  class="rounded-xl border accent-border accent-bg-soft px-4 py-2 text-xs font-bold accent-text transition-all hover:bg-[rgb(var(--accent-rgb)/0.16)]">
                  <i class="fa-solid fa-spinner mr-1"></i>复习中
                </button>
                <span
                  class="pointer-events-none absolute -top-7 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-md bg-slate-600/90 px-2 py-0.5 text-xs font-semibold text-white opacity-0 shadow-sm transition-opacity group-hover/tip:opacity-100 dark:bg-slate-700/90">
                  标记为复习中
                  <span
                    class="absolute -bottom-1 left-1/2 -translate-x-1/2 border-[3px] border-transparent border-t-slate-600/90 dark:border-t-slate-700/90"></span>
                </span>
              </div>
              <div class="group/tip relative">
                <button @click="quickMarkStatus(q, '已掌握')"
                  class="rounded-xl border border-emerald-500/30 bg-emerald-50/80 px-4 py-2 text-xs font-bold text-emerald-700 transition-all hover:bg-emerald-100 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-400">
                  <i class="fa-solid fa-circle-check mr-1"></i>已掌握
                </button>
                <span
                  class="pointer-events-none absolute -top-7 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-md bg-slate-600/90 px-2 py-0.5 text-xs font-semibold text-white opacity-0 shadow-sm transition-opacity group-hover/tip:opacity-100 dark:bg-slate-700/90">
                  标记为已掌握
                  <span
                    class="absolute -bottom-1 left-1/2 -translate-x-1/2 border-[3px] border-transparent border-t-slate-600/90 dark:border-t-slate-700/90"></span>
                </span>
              </div>
            </template>
          </QuestionItem>
        </div>
      </div>

      <!-- 详情弹窗 -->
      <QuestionDetailModal :open="detailOpen" :question="detailQuestion" @close="closeDetail"
        @open-image="(src) => openModal(src)" @deleted="onDeleted" @answer-saved="onAnswerSaved"
        @correct-answer-saved="onCorrectAnswerSaved"
        @review-status-changed="onReviewStatusChanged" @push-toast="(type, msg) => pushToast(type, msg)"
        @start-chat="(q) => openChat(q)" />
    </div>
  </ContentPanel>
</template>
