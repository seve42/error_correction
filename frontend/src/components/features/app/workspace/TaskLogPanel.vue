<script setup lang="ts">
/**
 * TaskLogPanel.vue
 * 导入试卷流程的任务日志面板：运行中实时滚动展示阶段与进度，完成后折叠保留供回看。
 *
 * 交互约束：
 * - 用户向上滚动后暂停自动滚底，回到底部恢复，避免和正在阅读的日志抢滚动位置。
 * - AI 调用事件默认只展示摘要，原始模型输出按需展开，避免几百页任务把 DOM 撑大。
 */
import { computed, nextTick, ref, watch } from 'vue'
import type { TaskAiCallDetail, TaskProgressEvent } from '@/api/task'

const props = withDefaults(
  defineProps<{
    events?: TaskProgressEvent[]
    running?: boolean
    progress?: number | null
    stage?: string
    defaultOpen?: boolean
  }>(),
  {
    events: () => [],
    running: false,
    progress: null,
    stage: '',
    defaultOpen: false,
  },
)

const open = ref(props.defaultOpen)
const listRef = ref<HTMLElement | null>(null)
/** 自动滚底开关：用户手动上滚时置 false，回到底部或重新展开时恢复。 */
const autoScroll = ref(true)

const eventList = computed(() => props.events || [])

/** 优先用外部传入的当前阶段，其次取最后一条带 stage 的事件。 */
const latestStage = computed(() => {
  if (props.stage) return props.stage
  for (let i = eventList.value.length - 1; i >= 0; i -= 1) {
    const value = eventList.value[i]?.stage
    if (value) return String(value)
  }
  return ''
})

const percentText = computed(() => {
  if (props.progress === null || props.progress === undefined) return ''
  const num = Number(props.progress)
  if (!Number.isFinite(num)) return ''
  return `${Math.round(Math.max(0, Math.min(100, num)))}%`
})

const LEVEL_META: Record<string, { dot: string; label: string }> = {
  info: { dot: 'bg-slate-400 dark:bg-slate-500', label: '信息' },
  success: { dot: 'bg-emerald-500', label: '成功' },
  warn: { dot: 'bg-amber-500', label: '警告' },
  error: { dot: 'bg-rose-500', label: '错误' },
}

function levelOf(event: TaskProgressEvent) {
  return LEVEL_META[String(event?.level || 'info')] || LEVEL_META.info
}

/** 后端 ts 可能是秒（浮点）也可能是毫秒，按量级归一化后格式化。 */
function formatTime(ts: unknown) {
  const num = Number(ts)
  if (!Number.isFinite(num) || num <= 0) return '--:--:--'
  const date = new Date(num < 1e12 ? num * 1000 : num)
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

function isAiCall(event: TaskProgressEvent) {
  const detail = event?.detail as Record<string, unknown> | null | undefined
  return !!detail && typeof detail === 'object' && detail.kind === 'ai_call'
}

function aiDetail(event: TaskProgressEvent) {
  return (event?.detail || {}) as TaskAiCallDetail
}

/** 页范围：后端可能给页码数组、单个页码或页数，统一显示成人类可读文案。 */
function formatPages(pages: unknown) {
  if (pages === null || pages === undefined || pages === '') return ''
  if (Array.isArray(pages)) {
    const list = pages.map((item) => Number(item)).filter((item) => Number.isFinite(item))
    if (!list.length) return pages.join(', ')
    list.sort((a, b) => a - b)
    if (list.length === 1) return `第 ${list[0]} 页`
    return `第 ${list[0]}–${list[list.length - 1]} 页（共 ${list.length} 页）`
  }
  const num = Number(pages)
  return Number.isFinite(num) ? `第 ${num} 页` : String(pages)
}

function formatTokens(tokens: unknown) {
  if (tokens === null || tokens === undefined) return ''
  if (typeof tokens === 'number') return `${tokens} tokens`
  if (typeof tokens === 'object') {
    const record = tokens as Record<string, unknown>
    const total = Number(record.total)
    if (Number.isFinite(total)) return `${total} tokens`
    const prompt = Number(record.prompt || record.prompt_tokens || 0)
    const completion = Number(record.completion || record.completion_tokens || 0)
    if (prompt + completion > 0) return `${prompt + completion} tokens`
    return ''
  }
  return String(tokens)
}

/** 耗时后端可能给秒或毫秒：>=1000 视为毫秒，其余按秒显示。 */
function formatDuration(duration: unknown) {
  const num = Number(duration)
  if (!Number.isFinite(num) || num <= 0) return ''
  const seconds = num >= 1000 ? num / 1000 : num
  return `${seconds >= 10 ? Math.round(seconds) : Number(seconds.toFixed(1))}s`
}

function questionCount(detail: TaskAiCallDetail) {
  const record = detail as Record<string, unknown>
  const candidates = [record.question_count, record.questions_count, record.count]
  for (const value of candidates) {
    const num = Number(value)
    if (Number.isFinite(num)) return `${num} 道`
  }
  if (Array.isArray(record.questions)) return `${record.questions.length} 道`
  return ''
}

/** AI 调用摘要行：模型、页范围、题目数、tokens、耗时、重试次数。 */
function aiMeta(detail: TaskAiCallDetail) {
  const items: Array<{ label: string; value: string }> = []
  if (detail.model) items.push({ label: '模型', value: String(detail.model) })
  const pages = formatPages(detail.pages)
  if (pages) items.push({ label: '页范围', value: pages })
  const count = questionCount(detail)
  if (count) items.push({ label: '题目数', value: count })
  const tokens = formatTokens(detail.tokens)
  if (tokens) items.push({ label: 'tokens', value: tokens })
  const duration = formatDuration(detail.duration)
  if (duration) items.push({ label: '耗时', value: duration })
  const attempt = Number(detail.attempt)
  if (Number.isFinite(attempt) && attempt > 1) items.push({ label: '尝试', value: `第 ${attempt} 次` })
  return items
}

function outputText(detail: TaskAiCallDetail) {
  const output = detail?.output
  if (output === null || output === undefined || output === '') return ''
  if (typeof output === 'string') return output
  try {
    return JSON.stringify(output, null, 2)
  } catch (_) {
    return String(output)
  }
}

const isNearBottom = () => {
  const el = listRef.value
  if (!el) return true
  return el.scrollHeight - el.scrollTop - el.clientHeight < 32
}

const onScroll = () => {
  autoScroll.value = isNearBottom()
}

watch(
  () => [eventList.value.length, props.running, open.value] as const,
  async () => {
    if (!open.value || !props.running || !autoScroll.value) return
    await nextTick()
    const el = listRef.value
    if (el) el.scrollTop = el.scrollHeight
  },
  { flush: 'post' },
)

// 外部从折叠切到展开（如进入运行态）时同步展开；运行结束不强制收起，保留用户选择。
watch(
  () => props.defaultOpen,
  (value) => {
    if (value) {
      open.value = true
      autoScroll.value = true
    }
  },
)
</script>

<template>
  <section
    class="w-full overflow-hidden rounded-xl border border-slate-200 bg-white/70 dark:border-white/[0.08] dark:bg-white/[0.025]"
  >
    <button
      type="button"
      class="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-left transition-colors hover:bg-slate-50 dark:hover:bg-white/[0.04]"
      :aria-expanded="open"
      @click="open = !open"
    >
      <span class="flex items-center gap-1.5 text-xs font-semibold text-slate-700 dark:text-[#f7f8f8]">
        <i class="fa-solid fa-list-ul text-[11px] text-slate-400 dark:text-[#8a8f98]" aria-hidden="true"></i>
        详细日志
      </span>
      <span
        v-if="latestStage"
        class="rounded px-1.5 py-0.5 text-[11px] accent-bg-muted accent-text"
      >当前阶段：{{ latestStage }}</span>
      <span v-if="percentText" class="text-[11px] font-semibold text-slate-500 dark:text-[#8a8f98]">{{ percentText }}</span>
      <span class="text-[11px] text-slate-400 dark:text-[#6b7280]">{{ eventList.length }} 条</span>
      <span class="ml-auto flex items-center gap-2">
        <span v-if="running" class="flex items-center gap-1 text-[11px] text-slate-500 dark:text-[#8a8f98]">
          <span class="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500"></span>
          运行中
        </span>
        <i
          class="fa-solid fa-chevron-down text-[10px] text-slate-400 transition-transform dark:text-[#8a8f98]"
          :class="{ 'rotate-180': open }"
          aria-hidden="true"
        ></i>
      </span>
    </button>

    <div v-show="open" class="border-t border-slate-200 dark:border-white/[0.08]">
      <div
        ref="listRef"
        class="custom-scrollbar max-h-64 overflow-y-auto px-3 py-2"
        @scroll.passive="onScroll"
      >
        <p v-if="!eventList.length" class="py-4 text-center text-xs text-slate-400 dark:text-[#8a8f98]">
          等待任务输出…
        </p>
        <div
          v-for="(event, index) in eventList"
          :key="event.seq ?? `${event.ts}-${index}`"
          class="flex items-start gap-2 py-1 text-xs leading-5"
        >
          <span class="mt-0.5 shrink-0 font-mono text-[10px] tabular-nums text-slate-400 dark:text-[#6b7280]">
            {{ formatTime(event.ts) }}
          </span>
          <span :class="['mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full', levelOf(event).dot]" :title="levelOf(event).label"></span>
          <span
            v-if="event.stage"
            class="mt-0.5 shrink-0 rounded px-1 py-0.5 text-[10px] accent-bg-muted accent-text"
          >{{ event.stage }}</span>
          <div class="min-w-0 flex-1">
            <p class="whitespace-pre-wrap break-words text-slate-700 dark:text-[#c9cdd4]">{{ event.message || '处理中…' }}</p>
            <details v-if="isAiCall(event)" class="mt-1">
              <summary class="cursor-pointer select-none text-[11px] accent-text hover:underline">AI 输出</summary>
              <div class="mt-1 rounded-lg border border-slate-200 bg-slate-50/80 p-2 dark:border-white/[0.08] dark:bg-black/20">
                <div class="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-500 dark:text-[#8a8f98]">
                  <span v-for="item in aiMeta(aiDetail(event))" :key="item.label">{{ item.label }}：{{ item.value }}</span>
                </div>
                <p v-if="aiDetail(event).output_truncated" class="mt-1 text-[11px] text-amber-600 dark:text-amber-400">
                  内容已截断
                </p>
                <pre
                  v-if="outputText(aiDetail(event))"
                  class="custom-scrollbar mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-slate-100/80 p-2 text-[11px] leading-4 text-slate-700 dark:bg-black/30 dark:text-[#c9cdd4]"
                >{{ outputText(aiDetail(event)) }}</pre>
                <p v-else class="mt-1 text-[11px] text-slate-400 dark:text-[#8a8f98]">无原始输出</p>
              </div>
            </details>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
