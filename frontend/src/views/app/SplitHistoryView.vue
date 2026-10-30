<script setup lang="ts">
/**
 * SplitHistoryView.vue
 * 分割历史页面，展示历史分割记录，并支持把历史题目重新载入工作台。
 */
import { ref, computed, watch, nextTick } from 'vue'
import * as api from '@/api/index'
import BatchSelectBar from '@/components/features/app/shared/BatchSelectBar.vue'
import { useBatchSelection } from '@/composables/useBatchSelection'
import { getQuestionSnippet, typesetMath as _typesetMath } from '@/utils/index'

const props = defineProps({
  theme: { type: String, default: 'light' },
  visible: { type: Boolean, default: false },
})

const emit = defineEmits(['push-toast', 'open-image', 'load-record', 'go-workspace'])

// ---- 列表数据 ----
const records = ref([])
const loading = ref(false)
const detailLoading = ref(false)

// ---- 详情面板 ----
const activeRecord = ref(null)
const activeQuestions = ref([])
const { selectedIds, toggleSelect, selectIds, clearSelection } = useBatchSelection()

/** 当前历史记录题目是否已全部选中。 */
const allQuestionsSelected = computed(
  () => activeQuestions.value.length > 0 && activeQuestions.value.every((q) => selectedIds.has(q.uid)),
)

const toggleSelectAllQuestions = () => {
  if (allQuestionsSelected.value) clearSelection()
  else selectIds(activeQuestions.value.map((q) => q.uid))
}

/**
 * 加载最近的分割历史记录。
 */
const loadRecords = async () => {
  loading.value = true
  try {
    records.value = await api.fetchSplitRecords(20)
  } catch (e) {
    emit('push-toast', 'error', '加载分割历史失败: ' + (e instanceof Error ? e.message : String(e)))
  } finally {
    loading.value = false
  }
}

/**
 * 展开历史记录详情，加载题目并重新渲染公式。
 */
const openDetail = async (record) => {
  if (activeRecord.value?.id === record.id) {
    closeDetail()
    return
  }
  detailLoading.value = true
  activeRecord.value = record
  activeQuestions.value = []
  clearSelection()
  try {
    const detail = await api.fetchSplitRecordDetail(record.id)
    // 兼容旧历史记录（无 uid）：按索引补齐
    const qs = detail.questions || []
    qs.forEach((q, i) => { if (q.uid == null) q.uid = `hist_${i}` })
    activeQuestions.value = qs
  } catch (e) {
    emit('push-toast', 'error', '加载记录详情失败')
    activeRecord.value = null
  } finally {
    detailLoading.value = false
  }
  await nextTick()
  const el = document.querySelector('.split-history-questions')
  if (el) await _typesetMath(el)
}

const closeDetail = () => {
  activeRecord.value = null
  activeQuestions.value = []
  clearSelection()
}

/**
 * 将选中的历史题目载入工作台；未选择时默认载入全部题目。
 */
const loadToWorkspace = () => {
  if (!activeQuestions.value.length) return
  const qs = selectedIds.size > 0
    ? activeQuestions.value.filter(q => selectedIds.has(q.uid))
    : activeQuestions.value
  emit('load-record', qs, activeRecord.value)
}

// ---- 格式化 ----
/**
 * 兼容后端无时区时间，统一转换成可比较的 Date 对象。
 */
const parseApiDate = (iso) => {
  if (!iso) return ''
  const value = String(iso)
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value) ? value : `${value}Z`
  const d = new Date(normalized)
  return Number.isNaN(d.getTime()) ? null : d
}

/**
 * 格式化列表里的相对时间展示。
 */
const formatDate = (iso) => {
  if (!iso) return ''
  const d = parseApiDate(iso)
  if (!d) return ''
  const now = new Date()
  const diffMs = now - d
  const diffMin = Math.floor(diffMs / 60000)
  if (diffMin < 1) return '刚刚'
  if (diffMin < 60) return `${diffMin} 分钟前`
  const diffHr = Math.floor(diffMin / 60)
  if (diffHr < 24) return `${diffHr} 小时前`
  const diffDay = Math.floor(diffHr / 24)
  if (diffDay < 7) return `${diffDay} 天前`
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  const hr = String(d.getHours()).padStart(2, '0')
  const min = String(d.getMinutes()).padStart(2, '0')
  return y === now.getFullYear() ? `${m}-${day} ${hr}:${min}` : `${y}-${m}-${day}`
}

const formatFullDate = (iso) => {
  if (!iso) return ''
  const d = parseApiDate(iso)
  if (!d) return ''
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
}

const providerLabel = (p) => {
  const map = { deepseek: 'DeepSeek', ernie: '文心一言', openai: 'OpenAI' }
  return map[p] || p || '未知'
}

const providerColor = (p) => {
  if (p === 'deepseek') return 'bg-blue-100 text-blue-700 dark:bg-blue-500/20 dark:text-blue-300'
  if (p === 'ernie') return 'bg-amber-100 text-amber-700 dark:bg-amber-500/20 dark:text-amber-300'
  return 'bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300'
}

const questionTypeIcon = (t) => {
  if (t === '选择题') return 'fa-list-check'
  if (t === '填空题') return 'fa-pen-to-square'
  if (t === '判断题') return 'fa-circle-check'
  if (t === '解答题' || t === '计算题') return 'fa-calculator'
  return 'fa-question'
}

const questionTypeBadge = (t) => {
  if (t === '选择题') return 'bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-300'
  if (t === '填空题') return 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300'
  if (t === '判断题') return 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300'
  return 'bg-slate-100 text-slate-600 dark:bg-slate-500/15 dark:text-slate-300'
}

/**
 * 历史记录卡片宽度有限，题目内容按纯文本摘要展示，避免富文本撑破卡片。
 */
const summaryText = (q) => getQuestionSnippet(q, 220, '（无文本内容）')

// ---- 初始化 ----
watch(() => props.visible, (v) => {
  if (v) loadRecords()
}, { immediate: true })

defineExpose({ refresh: loadRecords })
</script>

<template>
  <div class="flex h-full flex-col overflow-hidden">
    <div class="relative flex h-full flex-col">
      <!-- 页头 -->
      <div class="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-white/[0.05] shrink-0 transition-colors">
        <span class="text-xs font-medium text-gray-900 dark:text-[#f7f8f8] transition-colors">分割历史</span>
        <button
          @click="loadRecords"
          :disabled="loading"
          class="text-xs text-gray-500 dark:text-[#8a8f98] hover:text-gray-700 dark:hover:text-[#d0d6e0] transition-colors disabled:opacity-50"
        >
          <i class="fa-solid fa-arrows-rotate text-[10px]" :class="{ 'animate-spin': loading }"></i>
        </button>
      </div>

      <!-- 主体 -->
      <div class="flex-1 overflow-y-auto custom-scrollbar">

        <!-- 加载状态 -->
        <div v-if="loading && !records.length" class="flex items-center justify-center py-10">
          <div class="h-5 w-5 animate-spin rounded-full border-2 border-gray-200 dark:border-white/10 border-t-[rgb(var(--accent-rgb))] transition-colors"></div>
        </div>

        <!-- 空状态 -->
        <div v-else-if="!loading && !records.length" class="px-4 py-10 text-center transition-colors">
          <i class="fa-solid fa-clock-rotate-left text-lg text-gray-400 dark:text-[#62666d] mb-2 transition-colors"></i>
          <p class="text-xs text-gray-400 dark:text-[#62666d] transition-colors">暂无记录</p>
        </div>

        <!-- 记录列表 -->
        <div v-else>
          <div
            v-for="(r, idx) in records"
            :key="r.id"
            class="group cursor-pointer border-gray-200 px-4 py-3 transition-colors hover:bg-gray-50 dark:border-white/[0.05] dark:hover:bg-white/[0.03]"
            :class="activeRecord?.id === r.id ? 'bg-gray-100 dark:bg-white/[0.04]' : 'border-b'"
            @click="openDetail(r)"
          >
            <!-- 一行：科目 + 时间 + 题数 -->
            <div class="flex items-center justify-between mb-1">
              <span class="text-sm font-medium text-gray-900 dark:text-[#f7f8f8] truncate transition-colors">{{ r.subject || '未识别' }}</span>
              <span class="text-xs tabular-nums accent-text">{{ r.question_count }} 题</span>
            </div>
            <!-- 二行：文件数 + 时间 -->
            <div class="flex items-center gap-2 text-xs text-gray-500 no-underline dark:text-[#62666d] transition-colors [&_*]:no-underline">
              <span>{{ (r.file_names || []).length }} 个文件</span>
              <span>·</span>
              <span :title="formatFullDate(r.created_at)">{{ formatDate(r.created_at) }}</span>
            </div>

            <!-- 展开详情面板 -->
            <Transition name="split-record-expand">
              <div v-if="activeRecord?.id === r.id" class="split-record-detail">
                <div class="split-record-detail-inner">
                <!-- 加载中 -->
                <div v-if="detailLoading" class="flex items-center justify-center py-10">
                  <div class="h-7 w-7 animate-spin rounded-full border-[3px] border-slate-200 border-t-[rgb(var(--accent-rgb))] dark:border-slate-700"></div>
                </div>

                <!-- 题目列表 -->
                <div v-else class="split-history-questions">
                  <!-- 工具栏 -->
                  <div class="border-b border-slate-200/40 dark:border-white/5">
                    <div class="py-2">
                      <BatchSelectBar
                        :selected-count="selectedIds.size"
                        :total-count="activeQuestions.length"
                        :all-selected="allQuestionsSelected"
                        @toggle-all="toggleSelectAllQuestions"
                        @clear="clearSelection"
                      >
                        <template #actions>
                          <button
                            @click.stop="loadToWorkspace"
                            class="inline-flex min-w-0 items-center justify-center gap-2 rounded-lg accent-bg px-3 py-2 text-xs font-bold text-white shadow-sm transition-all hover:shadow-md"
                          >
                            <i class="fa-solid fa-arrow-right-to-bracket"></i>
                            <span class="truncate">{{ selectedIds.size > 0 ? `加载选中 ${selectedIds.size}` : '全部加载' }}</span>
                          </button>
                        </template>
                      </BatchSelectBar>
                    </div>
                  </div>

                  <!-- 题目网格 -->
                  <div class="grid grid-cols-1 gap-2.5">
                    <div
                      v-for="q in activeQuestions"
                      :key="q.uid"
                      @click.stop="toggleSelect(q.uid)"
                      class="cursor-pointer rounded-lg border p-3 transition-all duration-200"
                      :class="selectedIds.has(q.uid)
                        ? 'accent-border accent-bg-muted ring-1 ring-[rgb(var(--accent-rgb)/0.3)]'
                        : 'border-slate-200/50 bg-white/50 hover:border-[rgb(var(--accent-rgb)/0.35)] hover:bg-white/80 dark:border-white/[0.05] dark:bg-white/[0.02] dark:hover:border-[rgb(var(--accent-rgb)/0.25)]'"
                    >
                      <div class="mb-2 flex items-start gap-2">
                        <!-- 选中指示 -->
                        <div
                          class="flex h-5 w-5 shrink-0 items-center justify-center rounded-md border text-[10px] transition-colors"
                          :class="selectedIds.has(q.uid)
                            ? 'border-[rgb(var(--accent-rgb))] accent-bg text-white'
                            : 'border-slate-300 dark:border-white/15'"
                        >
                          <i v-if="selectedIds.has(q.uid)" class="fa-solid fa-check"></i>
                        </div>
                        <span class="text-xs font-black text-slate-700 dark:text-slate-200">
                          {{ q.question_id || '?' }}
                        </span>
                        <span v-if="q.question_type" class="rounded-md px-1.5 py-0.5 text-[10px] font-bold" :class="questionTypeBadge(q.question_type)">
                          <i class="fa-solid mr-0.5 text-[8px]" :class="questionTypeIcon(q.question_type)"></i>
                          {{ q.question_type }}
                        </span>
                        <span v-if="q.has_formula" class="rounded-md accent-bg-soft px-1.5 py-0.5 text-[10px] font-bold accent-text">
                          <i class="fa-solid fa-square-root-variable text-[8px]"></i>
                        </span>
                        <span v-if="q.has_image" class="rounded-md bg-cyan-100/80 px-1.5 py-0.5 text-[10px] font-bold text-cyan-600 dark:bg-cyan-500/15 dark:text-cyan-300">
                          <i class="fa-solid fa-image text-[8px]"></i>
                        </span>
                      </div>
                      <!-- 题目内容 -->
                      <div class="line-clamp-3 text-xs leading-relaxed text-slate-600 dark:text-slate-400">
                        {{ summaryText(q) }}
                      </div>
                      <!-- 知识点标签 -->
                      <div v-if="q.knowledge_tags?.length" class="mt-2 flex flex-wrap gap-1">
                        <span
                          v-for="tag in q.knowledge_tags.slice(0, 3)"
                          :key="tag"
                          class="rounded-md bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold text-slate-500 dark:bg-white/[0.05] dark:text-slate-400"
                        >{{ tag }}</span>
                        <span v-if="q.knowledge_tags.length > 3" class="text-[10px] text-slate-400 dark:text-slate-500">+{{ q.knowledge_tags.length - 3 }}</span>
                      </div>
                    </div>
                  </div>

                  <!-- 空题目提示 -->
                  <div v-if="!activeQuestions.length && !detailLoading" class="py-8 text-center text-sm text-slate-400 dark:text-slate-500">
                    此记录无题目数据
                  </div>
                </div>
                </div>
              </div>
            </Transition>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.split-record-detail {
  display: grid;
  grid-template-rows: 1fr;
  opacity: 1;
  overflow: hidden;
  transform: translateY(0);
  transition:
    grid-template-rows 320ms cubic-bezier(0.22, 1, 0.36, 1),
    opacity 220ms ease,
    transform 320ms cubic-bezier(0.22, 1, 0.36, 1);
}

.split-record-detail-inner {
  min-height: 0;
  overflow: hidden;
}

.split-record-expand-enter-from,
.split-record-expand-leave-to {
  grid-template-rows: 0fr;
  opacity: 0;
  transform: translateY(-4px);
}

.split-record-expand-enter-to,
.split-record-expand-leave-from {
  grid-template-rows: 1fr;
  opacity: 1;
  transform: translateY(0);
}

.split-record-expand-leave-active {
  transition-duration: 260ms;
  transition-timing-function: cubic-bezier(0.4, 0, 0.2, 1);
}
</style>
