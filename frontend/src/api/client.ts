/**
 * API shared client helpers.
 */

/** 一次用量的 token / 费用 / 点数统计，字段均为可选以兼容旧版后端。 */
export type UsageTotals = {
  requests?: number
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
  cached_tokens?: number
  cost_yuan?: number
  points?: number
}

/** 单个模型的用量统计。 */
export type UsageByModel = {
  model_name: string
  requests?: number
  total_tokens?: number
  cost_yuan?: number
  points?: number
}

/** `GET /api/auth/me` 返回的额度快照；后端字段仍在演进，未列出的字段透传。 */
export type QuotaSnapshot = {
  daily_free_quota?: number
  daily_free_used?: number
  remaining?: number
  quota_date?: string
  reset_at?: string
  /** 额度单位，新后端固定为“点”。 */
  unit?: string
  today_usage?: UsageTotals
  today_by_model?: UsageByModel[]
  usage_stats?: {
    breakdown?: unknown[]
    recent_events?: unknown[]
    daily_chart?: {
      days?: unknown[]
      range_days?: number
      actions?: { key: string; label: string }[]
    }
  }
  [key: string]: unknown
}

export type ApiError = Error & {
  status: number | null
  code: string | null
  quota: QuotaSnapshot | null
}

export type ApiRequestBody = Record<string, unknown>

type ApiErrorDetail = {
  status?: number | null
  code?: string | null
  quota?: QuotaSnapshot | null
}

export type ApiResponseLike = ({
  success?: boolean
  error?: string
  message?: string
  code?: string | null
  quota?: QuotaSnapshot | null
} & Record<string, unknown>) | null

type XhrSuccessHandler<TData> = (data: TData) => void
type XhrErrorHandler = (error: ApiError) => void

function createApiError(message: string, { status, code, quota }: ApiErrorDetail = {}): ApiError {
  const error = new Error(message || '请求失败') as ApiError
  error.status = status ?? null
  error.code = code ?? null
  error.quota = quota ?? null
  return error
}

function getErrorMessage(data: ApiResponseLike, fallback?: string, status?: number) {
  return (data && (data.error || data.message)) || fallback || `HTTP ${status}`
}

export function buildModelBody(
  modelProvider: string,
  modelName?: string,
  providerSource?: string,
  providerId?: string | number | null,
  extra: ApiRequestBody = {},
) {
  const body: ApiRequestBody = { model_provider: modelProvider, ...extra }
  if (modelName) body.model_name = modelName
  if (providerSource) body.provider_source = providerSource
  if (providerId) body.provider_id = providerId
  return body
}

export function createNetworkError(message: string, detail: ApiErrorDetail = {}) {
  return createApiError(message, detail)
}

export async function readJsonSafely<TData = ApiResponseLike>(resp: Response): Promise<TData | null> {
  return resp.json().catch(() => null)
}

export async function assertJsonSuccess<TData extends ApiResponseLike = ApiResponseLike>(resp: Response, fallbackMessage?: string): Promise<TData> {
  const data = await readJsonSafely<TData>(resp)
  if (!resp.ok) {
    throw createApiError(getErrorMessage(data, fallbackMessage, resp.status), {
      status: resp.status,
      code: data?.code,
      quota: data?.quota,
    })
  }
  if (data && data.success) return data
  throw createApiError(getErrorMessage(data, fallbackMessage, resp.status), {
    status: resp.status,
    code: data?.code,
    quota: data?.quota,
  })
}

export function handleXhrJsonResult<TData extends ApiResponseLike = ApiResponseLike>(
  xhr: XMLHttpRequest,
  data: TData | null,
  fallbackMessage: string,
  onSuccess?: XhrSuccessHandler<TData>,
  onError?: XhrErrorHandler,
) {
  if (xhr.status >= 200 && xhr.status < 300 && data?.success) {
    onSuccess?.(data)
    return
  }
  onError?.(createApiError(getErrorMessage(data, fallbackMessage, xhr.status), {
    status: xhr.status,
    code: data?.code,
    quota: data?.quota,
  }))
}
