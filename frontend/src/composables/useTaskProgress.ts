/**
 * 任务进度订阅 composable：串联 EventSource 实时流与轮询兜底。
 *
 * 一个 taskId 可能被两个阶段复用（自动模式：OCR 结束后分割继续用同一 task），
 * 因此 connect(after) 允许从最后一条事件之后重连且不清空已有日志。
 */
import { computed, getCurrentScope, onScopeDispose, ref } from 'vue'
import { createTaskEventSource, fetchTaskSnapshot } from '@/api/task'
import type { TaskProgressEvent } from '@/api/task'

/** 事件列表上限，避免几百页任务把内存和 DOM 撑爆；超出后丢弃最早的日志。 */
const MAX_EVENTS = 500
/** SSE 不可用时的轮询间隔；后端未实现任务接口时保持静默重试，不打断主流程。 */
const POLL_INTERVAL_MS = 1500
/**
 * 复用 taskId 跑下一阶段（自动模式的 OCR→分割）时，后端要等 POST 到达才 restart。
 * 这个窗口内轮询拿到的快照仍是上一阶段的 finished=true，必须忽略，否则会提前
 * 判定任务结束并停止轮询，分割阶段的日志就全丢了。窗口取后端占位等待（20s）以内。
 */
const RESTART_GRACE_MS = 10000

/** 优先用 crypto.randomUUID，非安全上下文（http 访问局域网）下退回随机串。 */
function createTaskId() {
  const cryptoApi = typeof crypto !== 'undefined' ? crypto : null
  if (cryptoApi && typeof cryptoApi.randomUUID === 'function') return cryptoApi.randomUUID()
  return `task-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`
}

/** 后端进度是 0..1 小数，这里统一换算成百分比；非法值返回 null 交给 UI 兜底动画。 */
function normalizeProgress(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null
  const num = Number(value)
  if (!Number.isFinite(num)) return null
  const percent = num <= 1 ? num * 100 : num
  return Math.max(0, Math.min(100, percent))
}

function parseEventData(raw: unknown): unknown {
  if (typeof raw !== 'string' || !raw.trim()) return null
  try {
    return JSON.parse(raw)
  } catch (_) {
    return null
  }
}

export function useTaskProgress() {
  const taskId = ref('')
  const events = ref<TaskProgressEvent[]>([])
  const running = ref(false)
  const stage = ref('')
  const progress = ref<number | null>(null)
  const error = ref<string | null>(null)

  const lastSeq = computed(() => {
    const list = events.value
    if (!list.length) return 0
    return Number(list[list.length - 1]?.seq) || 0
  })

  let source: EventSource | null = null
  let pollTimer: ReturnType<typeof setInterval> | null = null
  let polling = false
  let generation = 0
  let restartGraceUntil = 0
  const seenSeqs = new Set<number>()

  const closeSource = () => {
    if (!source) return
    try {
      source.close()
    } catch (_) {
      // 关闭失败不影响后续轮询兜底
    }
    source = null
  }

  const stopPolling = () => {
    if (pollTimer !== null) {
      clearInterval(pollTimer)
      pollTimer = null
    }
    polling = false
  }

  const applyEventMeta = (event: TaskProgressEvent) => {
    if (event.stage) stage.value = String(event.stage)
    const percent = normalizeProgress(event.progress)
    if (percent !== null) progress.value = percent
  }

  const appendEvents = (incoming: unknown) => {
    if (!incoming) return
    const list = Array.isArray(incoming) ? incoming : [incoming]
    let changed = false
    for (const item of list) {
      if (!item || typeof item !== 'object') continue
      const event = item as TaskProgressEvent
      const seq = Number(event.seq)
      if (seq > 0) {
        if (seenSeqs.has(seq)) continue
        seenSeqs.add(seq)
      }
      events.value.push(event)
      changed = true
    }
    if (!changed) return
    // 轮询快照可能一次带回多条，按 seq 排序保证 lastSeq 是真正的最后一条。
    events.value.sort((a, b) => (Number(a.seq) || 0) - (Number(b.seq) || 0))
    if (events.value.length > MAX_EVENTS) {
      events.value.splice(0, events.value.length - MAX_EVENTS)
    }
    const latest = events.value[events.value.length - 1]
    if (latest) applyEventMeta(latest)
  }

  const applyDone = (payload: Record<string, unknown> | null) => {
    stopPolling()
    closeSource()
    running.value = false
    if (payload && payload.ok === false) {
      error.value = String(payload.message || error.value || '任务执行失败')
      return
    }
    if (payload && payload.message) {
      error.value = null
    }
    // 成功结束时后端不一定再发 progress，这里补到 100 让进度条收尾。
    if (progress.value === null || progress.value < 100) progress.value = 100
  }

  const startPolling = (expectedGeneration: number) => {
    stopPolling()
    const tick = async () => {
      if (expectedGeneration !== generation || !taskId.value) {
        stopPolling()
        return
      }
      if (polling) return
      polling = true
      try {
        const snapshot = await fetchTaskSnapshot(taskId.value, lastSeq.value)
        if (expectedGeneration !== generation) return
        appendEvents(snapshot.events)
        if (snapshot.finished) {
          // 复用 taskId 等下一阶段 restart 时，先忽略上一阶段的结束标记。
          const waitingRestart = restartGraceUntil > Date.now()
          if (!waitingRestart) {
            applyDone({ ok: snapshot.ok !== false, message: snapshot.message })
          }
        }
      } catch (_) {
        // 后端尚未提供任务接口时保持静默轮询，进度条由 UI 的 indeterminate 动画兜底。
      } finally {
        polling = false
      }
    }
    pollTimer = setInterval(tick, POLL_INTERVAL_MS)
    void tick()
  }

  const connect = (after?: number, options: { expectRestart?: boolean } = {}) => {
    if (!taskId.value) return
    const from = Math.max(0, Number(after ?? lastSeq.value) || 0)
    restartGraceUntil = options?.expectRestart ? Date.now() + RESTART_GRACE_MS : 0
    if (options?.expectRestart) {
      // 下一阶段还没有新进度，先回到不确定态，避免进度条停在上一阶段的 100%。
      progress.value = null
    }
    running.value = true
    error.value = null
    closeSource()
    stopPolling()
    const expectedGeneration = generation
    const es = createTaskEventSource(taskId.value, from)
    if (!es) {
      // 环境不支持 EventSource（如测试环境）时直接走轮询。
      startPolling(expectedGeneration)
      return
    }
    source = es
    const handleProgress = (event: MessageEvent) => {
      if (expectedGeneration !== generation) return
      appendEvents(parseEventData(event.data))
    }
    es.addEventListener('progress', handleProgress)
    // 兼容后端未使用命名事件、直接发默认 message 的情况。
    es.onmessage = handleProgress
    es.addEventListener('done', (event: MessageEvent) => {
      if (expectedGeneration !== generation) return
      const payload = parseEventData(event.data)
      applyDone(payload && typeof payload === 'object' ? (payload as Record<string, unknown>) : null)
    })
    es.onerror = () => {
      if (expectedGeneration !== generation) return
      // 浏览器会自动重连，但后端未实现 SSE 时会反复报错；统一关闭并改用轮询直到 done。
      closeSource()
      startPolling(expectedGeneration)
    }
  }

  const start = () => {
    close()
    generation += 1
    restartGraceUntil = 0
    seenSeqs.clear()
    taskId.value = createTaskId()
    events.value = []
    stage.value = ''
    progress.value = null
    error.value = null
    running.value = true
    connect(0)
    return taskId.value
  }

  const close = () => {
    stopPolling()
    closeSource()
    running.value = false
  }

  /**
   * 等当前流自然结束（收到 done 或连接关闭），最多等 timeoutMs 后放弃。
   *
   * HTTP 响应通常早于 SSE 最后几帧返回，此时直接 close() 会丢掉尾部事件（常在
   * done/汇总日志里）；但也不能无限等，所以调用方拿结果后自行 close()。
   * 已结束、或一条事件都没收到（旧后端 / SSE 不可用）时立即返回，避免白等拖慢流程。
   */
  const waitForDone = (timeoutMs = 2000, pollMs = 100): Promise<boolean> => {
    if (!running.value) return Promise.resolve(true)
    if (!events.value.length) return Promise.resolve(false)
    const expectedGeneration = generation
    return new Promise<boolean>((resolve) => {
      const startedAt = Date.now()
      const timer = setInterval(() => {
        if (generation !== expectedGeneration) {
          clearInterval(timer)
          resolve(false)
          return
        }
        if (!running.value) {
          clearInterval(timer)
          resolve(true)
          return
        }
        if (Date.now() - startedAt >= timeoutMs) {
          clearInterval(timer)
          resolve(false)
        }
      }, pollMs)
    })
  }

  const reset = () => {
    generation += 1
    restartGraceUntil = 0
    close()
    seenSeqs.clear()
    taskId.value = ''
    events.value = []
    stage.value = ''
    progress.value = null
    error.value = null
  }

  if (getCurrentScope()) {
    onScopeDispose(() => {
      generation += 1
      close()
    })
  }

  return {
    taskId,
    events,
    running,
    stage,
    progress,
    error,
    lastSeq,
    start,
    connect,
    close,
    waitForDone,
    reset,
  }
}
