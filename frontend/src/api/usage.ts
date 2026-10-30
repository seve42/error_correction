/**
 * 用量统计 API。
 *
 * 管理员可查看全平台汇总；个人今日用量随 `/api/auth/me` 的 `quota.today_usage` 返回，
 * 无需单独请求 `/api/usage/summary`。
 */
import { assertJsonSuccess } from './client'
import type { UsageByModel, UsageTotals } from './client'

export type UsageByAction = UsageByModel & {
  action_type: string
  label: string
}

export type UsageDailyPoint = {
  date: string
  label: string
  requests: number
  total_tokens: number
  cost_yuan: number
  points: number
}

export type UsageRecentEvent = {
  created_at: string
  model_name: string
  action_type: string
  total_tokens: number
  cost_yuan: number
  points: number
  billable: boolean
}

export type UsageByUser = UsageByModel & {
  user_id: number | string
  username: string
}

export type UsageSummary = {
  success: boolean
  days: number
  total: UsageTotals
  by_model: UsageByModel[]
  by_action: UsageByAction[]
  daily: UsageDailyPoint[]
  recent: UsageRecentEvent[]
  /** 仅管理员接口返回。 */
  by_user?: UsageByUser[]
  users_count?: number
}

/** 获取全平台用量汇总（仅管理员可调用）。 */
export async function fetchAdminUsageSummary(days = 14): Promise<UsageSummary> {
  const resp = await fetch(`/api/admin/usage/summary?days=${days}`)
  return assertJsonSuccess<UsageSummary>(resp, '获取全局用量统计失败')
}
