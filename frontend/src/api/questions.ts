import { assertJsonSuccess } from './client'
import type { ErrorBankQuestion, Id } from '@/types/domain'

type ErrorBankQueryParams = Record<string, string | number | boolean | (string | number)[] | null | undefined>

type FindQuestionsOptions = {
  projectId?: Id | null
  limit?: number
}

type DueReviewsParams = {
  type?: string
  target_type?: string
  limit?: number
  project_id?: Id | null
}

type AiAnalysisOptions = {
  providerSource?: string
  providerId?: Id | null
  modelProvider?: string
  modelName?: string
}

/** 生成答案解析时可选的任务日志参数：同一 task_id 会续接 OCR/分割的进度事件。 */
type GenerateAnswersOptions = AiAnalysisOptions & { taskId?: string | null }

/** 生成标准答案解析时提交的单题字段，顺序与响应一一对应。 */
export type GeneratedAnswerQuestion = {
  uid?: string | null
  id?: Id | null
  question_id?: string | number | null
  question_type?: string
  content_blocks?: unknown[]
  options?: unknown[]
}

export type GeneratedAnswer = {
  uid?: string | null
  id?: Id | null
  answer?: string
  reasoning?: string
  confidence?: number
}

type GenerateAnswersResponse = {
  success: true
  results?: GeneratedAnswer[]
}

type ExportFromDbResponse = {
  success: true
  output_path?: string
  download_url?: string
  message?: string
}

type AiAnalysisResponse = {
  success: true
  analysis?: string | null
  message?: string
}

function toQueryString(params: ErrorBankQueryParams) {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (Array.isArray(v)) {
      if (v.length) qs.set(k, v.join(','))
    } else if (v !== null && v !== undefined && v !== '') qs.set(k, String(v))
  }
  return qs.toString()
}

export async function fetchErrorBank(params: ErrorBankQueryParams = {}) {
  const resp = await fetch(`/api/error-bank?${toQueryString(params)}`)
  return assertJsonSuccess<{ success: true; items: ErrorBankQuestion[]; total?: number; grand_total?: number; total_pages?: number }>(
    resp,
    '查询错题库失败',
  )
}

/** 按当前筛选条件获取全部题目 ID，供错题库「全选所有筛选结果」（跨页）使用。 */
export async function fetchErrorBankIds(params: ErrorBankQueryParams = {}) {
  const resp = await fetch(`/api/error-bank/ids?${toQueryString(params)}`)
  return assertJsonSuccess<{ success: true; ids: Id[]; total?: number }>(resp, '获取题目列表失败')
}

export async function findQuestionsByDescription(query: string, { projectId, limit = 8 }: FindQuestionsOptions = {}) {
  const qs = new URLSearchParams()
  qs.set('q', query)
  qs.set('limit', String(limit))
  if (projectId) qs.set('project_id', String(projectId))
  const resp = await fetch(`/api/error-bank/find?${qs}`)
  return assertJsonSuccess<{ success: true; questions?: ErrorBankQuestion[]; items?: ErrorBankQuestion[] }>(resp, 'AI 找题失败')
}

export async function fetchSubjects(projectId?: Id | null) {
  const qs = new URLSearchParams()
  if (projectId) qs.set('project_id', String(projectId))
  const resp = await fetch(`/api/subjects?${qs}`)
  const data = await assertJsonSuccess<{ success: true; subjects: string[] }>(resp, '获取科目列表失败')
  return data.subjects
}

export async function fetchQuestionTypes(projectId?: Id | null) {
  const qs = new URLSearchParams()
  if (projectId) qs.set('project_id', String(projectId))
  const resp = await fetch(`/api/question-types?${qs}`)
  const data = await assertJsonSuccess<{ success: true; question_types: string[] }>(resp, '获取题型列表失败')
  return data.question_types
}

export async function fetchTagNames(subject?: string | null, projectId?: Id | null) {
  const qs = new URLSearchParams()
  if (subject) qs.set('subject', String(subject))
  if (projectId) qs.set('project_id', String(projectId))
  const resp = await fetch(`/api/stats?${qs}`)
  const data = await assertJsonSuccess<{ success: true; stats?: Array<{ tag_name?: string }> }>(resp, '获取标签列表失败')
  return (data.stats || []).map(s => s.tag_name)
}

export async function saveAnswer(questionId: Id, userAnswer: string) {
  const resp = await fetch(`/api/question/${questionId}/answer`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_answer: userAnswer }),
  })
  return assertJsonSuccess(resp, '保存答案失败')
}

export async function exportFromDb(selectedIds: Id[]) {
  const resp = await fetch('/api/export-from-db', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ selected_ids: selectedIds }),
  })
  return assertJsonSuccess<ExportFromDbResponse>(resp, '导出失败')
}

export async function deleteQuestion(questionId: Id) {
  const resp = await fetch(`/api/question/${questionId}`, { method: 'DELETE' })
  return assertJsonSuccess(resp, '删除失败')
}

/** 批量删除题目（一次请求删除多道题）。 */
export async function batchDeleteQuestions(questionIds: Id[]) {
  const resp = await fetch('/api/questions/batch-delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids: questionIds }),
  })
  return assertJsonSuccess<{ success: true; deleted: number; message?: string }>(resp, '批量删除失败')
}

export async function updateReviewStatus(questionId: Id, reviewStatus: string) {
  const resp = await fetch(`/api/question/${questionId}/review-status`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ review_status: reviewStatus }),
  })
  return assertJsonSuccess(resp, '更新复习状态失败')
}

export async function recordQuestionReview(questionId: Id, rating = 'good') {
  const resp = await fetch(`/api/question/${questionId}/review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rating }),
  })
  return assertJsonSuccess(resp, 'record review failed')
}

export async function fetchDueReviews(params: DueReviewsParams = {}) {
  const qs = new URLSearchParams()
  if (params.type) qs.set('type', String(params.type))
  if (params.target_type) qs.set('target_type', String(params.target_type))
  if (params.limit) qs.set('limit', String(params.limit))
  if (params.project_id) qs.set('project_id', String(params.project_id))
  const resp = await fetch(`/api/review/due?${qs}`)
  return assertJsonSuccess<{ success: true; items?: ErrorBankQuestion[]; total?: number }>(resp, 'load due reviews failed')
}

export async function fetchDashboardStats(subject?: string | null, projectId?: Id | null) {
  const qs = new URLSearchParams()
  if (subject) qs.set('subject', String(subject))
  if (projectId) qs.set('project_id', String(projectId))
  const resp = await fetch(`/api/dashboard-stats?${qs}`)
  return assertJsonSuccess<Record<string, unknown> & { success: true }>(resp, '获取统计数据失败')
}

export async function requestAiAnalysis(questionIds: Id[], { providerSource, providerId, modelProvider, modelName }: AiAnalysisOptions = {}) {
  const body: Record<string, string | number | Id[] | null> = { question_ids: questionIds }
  if (modelProvider) body.model_provider = modelProvider
  if (modelName) body.model_name = modelName
  if (providerSource) body.provider_source = providerSource
  if (providerId) body.provider_id = providerId

  const resp = await fetch('/api/ai-analysis', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return assertJsonSuccess<AiAnalysisResponse>(resp, 'AI 分析请求失败')
}

export async function updateQuestion(questionId: Id, payload: Record<string, unknown>) {
  const resp = await fetch(`/api/question/${questionId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return assertJsonSuccess(resp, '保存失败')
}

export async function saveQuestionAnswer(questionId: Id, answer: string) {
  const resp = await fetch(`/api/question/${questionId}/answer`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ answer }),
  })
  return assertJsonSuccess(resp, '保存答案失败')
}

/**
 * 调用后端批量生成标准答案与解析。
 * 返回顺序与请求的 questions 一致，失败时抛出带 status 的错误。
 */
export async function generateAnswers(
  questions: GeneratedAnswerQuestion[],
  { providerSource, providerId, modelProvider, modelName, taskId }: GenerateAnswersOptions = {},
): Promise<GeneratedAnswer[]> {
  const body: Record<string, unknown> = { questions }
  if (modelProvider) body.model_provider = modelProvider
  if (modelName) body.model_name = modelName
  if (providerSource) body.provider_source = providerSource
  if (providerId) body.provider_id = providerId
  if (taskId) body.task_id = taskId

  const resp = await fetch('/api/generate-answers', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await assertJsonSuccess<GenerateAnswersResponse>(resp, '生成标准答案失败')
  return data.results || []
}

