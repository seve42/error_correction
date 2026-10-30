/**
 * 任务进度事件 API（导入试卷流程）。
 *
 * 后端把 OCR / 分割各阶段的进度写进内存事件中心，前端通过 EventSource 实时订阅；
 * 订阅失败时用快照接口轮询兜底，保证旧后端或 SSE 不可用时流程也能展示状态。
 */
import { assertJsonSuccess } from './client'

/** 日志级别，与后端事件 JSON 的 level 字段一致。 */
export type TaskLogLevel = 'info' | 'success' | 'warn' | 'error'

/** AI 调用事件的明细；output 为模型原始返回，后端可能按长度截断。 */
export type TaskAiCallDetail = {
  kind: 'ai_call'
  model?: string
  pages?: number | number[] | string
  duration?: number
  tokens?: number | { prompt?: number; completion?: number; total?: number; [key: string]: unknown }
  attempt?: number
  output?: unknown
  output_truncated?: boolean
  [key: string]: unknown
}

/** 进度事件的 detail 字段，普通阶段事件可能没有 detail。 */
export type TaskEventDetail = TaskAiCallDetail | Record<string, unknown>

/** 单条进度事件，seq 单调递增用于断线重连续传。 */
export type TaskProgressEvent = {
  seq: number
  ts: number
  level?: TaskLogLevel
  step?: string
  stage?: string
  message?: string
  progress?: number | null
  detail?: TaskEventDetail | null
  [key: string]: unknown
}

/** GET /api/task/<task_id> 的轮询兜底快照。 */
export type TaskSnapshot = {
  success: true
  task_id: string
  events: TaskProgressEvent[]
  finished: boolean
  ok?: boolean
  message?: string
  [key: string]: unknown
}

const SAFE_SEQ = (after?: number) => Math.max(0, Math.floor(Number(after) || 0))

/** SSE 订阅地址；after 为最后已处理的事件序号，断线重连时从这里续传。 */
export function taskStreamUrl(taskId: string, after = 0) {
  return `/api/task/${encodeURIComponent(String(taskId || ''))}/stream?after=${SAFE_SEQ(after)}`
}

/** 轮询兜底地址。 */
export function taskSnapshotUrl(taskId: string, after = 0) {
  return `/api/task/${encodeURIComponent(String(taskId || ''))}?after=${SAFE_SEQ(after)}`
}

/**
 * 创建任务进度 EventSource。
 * 返回 null 表示当前环境拿不到 EventSource（如 SSR、测试环境或构造异常），调用方应改用轮询。
 */
export function createTaskEventSource(taskId: string, after = 0): EventSource | null {
  if (!taskId) return null
  if (typeof window === 'undefined' || typeof EventSource === 'undefined') return null
  try {
    return new EventSource(taskStreamUrl(taskId, after))
  } catch (_) {
    return null
  }
}

/** 拉取任务事件快照，用于 EventSource 不可用时的轮询兜底。 */
export async function fetchTaskSnapshot(taskId: string, after = 0): Promise<TaskSnapshot> {
  const resp = await fetch(taskSnapshotUrl(taskId, after))
  return assertJsonSuccess<TaskSnapshot>(resp, '获取任务进度失败')
}
