import { assertJsonSuccess, buildModelBody, createNetworkError, handleXhrJsonResult } from './client'
import type { ApiRequestBody, ApiResponseLike } from './client'
import type { QuestionGrading } from '@/types/domain'

type Id = string | number
type UploadFilesResponse = Exclude<ApiResponseLike, null>

/** 视觉 OCR 引擎：前端固定传 vision，实际使用哪个视觉模型由后端配置决定。 */
export type OcrEngine = 'vision'

/** 视觉 OCR 的单个内容块；bbox 通常为 null，前端不依赖坐标。 */
export type OcrBlock = {
  label: string
  content: string
  bbox: number[] | null
  pen?: 'printed' | 'red' | 'black'
  [key: string]: unknown
}

/** 视觉 OCR 的单页结果。 */
export type OcrPage = {
  page_index: number
  image_url: string | null
  page_width: number
  page_height: number
  blocks: OcrBlock[]
  [key: string]: unknown
}

export type OcrResponse = {
  success: true
  message?: string
  engine?: string
  pages?: OcrPage[]
}

/** 分割接口返回的题目，新字段均为可选，旧数据可能不包含。 */
export type SplitQuestion = {
  uid?: Id
  page_index?: number
  student_answer?: string
  has_handwriting?: boolean
  grading?: QuestionGrading
  red_marks?: Array<{ type?: string; text?: string; linked_qid?: string }>
  [key: string]: unknown
}

type SplitResponse = {
  success: true
  run_id?: Id | null
  questions?: SplitQuestion[]
  warnings?: string[]
  message?: string
}
type ExportQuestionsResponse = { success: true; output_path?: string; download_url?: string; message?: string }
type SaveToDbResponse = { success: true; message?: string }

type UploadFilesOptions = {
  onProgress?: (progress: number) => void
  onSuccess?: (data: UploadFilesResponse) => void
  onError?: (error: unknown) => void
  onAbort?: () => void
}

export async function cancelFile(fileKey: string) {
  const resp = await fetch('/api/cancel_file', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file_key: fileKey }),
  })
  return assertJsonSuccess(resp, '撤销失败')
}

export async function resetUploadSession() {
  const resp = await fetch('/api/upload/reset', {
    method: 'POST',
  })
  return assertJsonSuccess(resp, '重置上传会话失败')
}

export function uploadFiles(formData: FormData, { onProgress, onSuccess, onError, onAbort }: UploadFilesOptions = {}) {
  const xhr = new XMLHttpRequest()
  xhr.open('POST', '/api/upload', true)

  if (onProgress) {
    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total)
    })
  }

  xhr.addEventListener('load', () => {
    let data: UploadFilesResponse | null = null
    try { data = JSON.parse(xhr.responseText) as UploadFilesResponse } catch (_) { }
    handleXhrJsonResult(xhr, data, '文件处理失败', onSuccess, onError)
  })

  xhr.addEventListener('error', () => onError?.(createNetworkError('上传失败: 网络错误', {})))
  xhr.addEventListener('abort', () => onAbort?.())

  xhr.send(formData)
  return xhr
}

/**
 * 调用视觉 OCR 接口，识别上传页面上的印刷题、手写作答与红笔批改。
 * 后端不再返回可靠 bbox，预览以页面图和识别文本列表为主。
 */
export async function runOcr(engine: OcrEngine = 'vision', taskId?: string) {
  const body: ApiRequestBody = { engine }
  // task_id 与 SSE 订阅共用；后端未实现时忽略该字段即可，不影响旧流程。
  if (taskId) body.task_id = taskId
  const resp = await fetch('/api/ocr', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return assertJsonSuccess<OcrResponse>(resp, '视觉识别失败')
}

/**
 * 调用题目分割接口；固定携带 ocr_engine，与后端的视觉 OCR 结果对齐。
 */
export async function splitQuestions(
  modelProvider: string,
  modelName?: string,
  providerSource?: string,
  providerId?: Id | null,
  ocrEngine: OcrEngine = 'vision',
  taskId?: string,
) {
  const extra: ApiRequestBody = { ocr_engine: ocrEngine }
  if (taskId) extra.task_id = taskId
  const resp = await fetch('/api/split', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(buildModelBody(modelProvider, modelName, providerSource, providerId, extra)),
  })
  return assertJsonSuccess<SplitResponse>(resp, '题目分割失败')
}

export async function exportQuestions(selectedIds: Id[], runId?: Id | null) {
  const body: ApiRequestBody = { selected_ids: selectedIds }
  if (runId) body.run_id = runId
  const resp = await fetch('/api/export', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return assertJsonSuccess<ExportQuestionsResponse>(resp, '导出失败')
}

export async function saveToDb(
  selectedIds: Id[],
  answers: Array<Record<string, unknown>> = [],
  runId?: Id | null,
  projectId?: Id | null,
  splitRecordId?: Id | null,
) {
  const body: ApiRequestBody = { selected_ids: selectedIds, answers }
  if (runId) body.run_id = runId
  if (splitRecordId) body.split_record_id = splitRecordId
  if (projectId) body.project_id = projectId
  const resp = await fetch('/api/save-to-db', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return assertJsonSuccess<SaveToDbResponse>(resp, '导入错题库失败')
}

