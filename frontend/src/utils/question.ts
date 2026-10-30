/**
 * Question content helpers.
 */
const cleanInlineMath = (text) => {
  return String(text || '')
    .replace(/\\\((.*?)\\\)/g, '$1')
    .replace(/\\\[(.*?)\\\]/g, '$1')
    .replace(/\${1,2}([^$]+)\${1,2}/g, '$1')
    .replace(/\\sqrt\s*\{([^{}]+)\}/g, '√$1')
    .replace(/\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}/g, '$1/$2')
    .replace(/\\angle/g, '∠')
    .replace(/\\overline\s*\{([^{}]+)\}/g, '$1')
    .replace(/\\bar\s*\{([^{}]+)\}/g, '$1')
    .replace(/\\[a-zA-Z]+/g, '')
    .replace(/[{}]/g, '')
    .replace(/\\+/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

const safeTruncate = (text, maxLen) => {
  if (maxLen <= 0 || text.length <= maxLen) return text
  let end = maxLen
  while (end > Math.max(0, maxLen - 8) && /[\\$({[]/.test(text[end - 1] || '')) end -= 1
  return text.slice(0, end).trimEnd() + '...'
}

export const getQuestionSnippet = (q, maxLen = 0, fallback = '') => {
  if (!q) return fallback
  const blocks = q.content_blocks || q.content_json || []
  const texts = blocks.filter(b => b.block_type === 'text').map(b => b.content || '')
  const raw = cleanInlineMath(texts.join(' ').replace(/<[^>]+>/g, ''))
  if (!raw) return fallback
  return safeTruncate(raw, maxLen)
}

/**
 * 收敛生成标准答案接口所需的单题字段，答题页与工作台共用。
 * blocks/options 允许调用方传入已规范化的数组（如详情面板的 contentBlocks）。
 */
export const buildGeneratedAnswerQuestion = (question, blocks = null, options = null) => {
  if (!question) return null
  return {
    uid: question.uid ?? null,
    id: question.id ?? null,
    question_id: question.question_id ?? question.id ?? null,
    question_type: question.question_type || '',
    content_blocks: blocks ?? question.content_json ?? question.content_blocks ?? [],
    options: options ?? question.options_json ?? question.options ?? [],
  }
}

/** 把标准答案与解析合成可展示、可入库的文本（解析以 Markdown 加粗小标题接在后面）。 */
export const composeAnswerWithReasoning = (answer, reasoning) => {
  const head = String(answer || '').trim()
  const body = String(reasoning || '').trim()
  if (!body) return head
  return head ? `${head}\n\n**解析**\n\n${body}` : `**解析**\n\n${body}`
}

const clamp = (value, min, max) => Math.min(max, Math.max(min, value))

const daysSince = (iso) => {
  if (!iso) return 30
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return 30
  return Math.max(0, Math.floor((Date.now() - date.getTime()) / 86400000))
}

/**
 * 计算错题复习优先级。
 * 分数来自题目的真实状态、更新时间和资料完整度，而不是固定展示值。
 */
export const calculateQuestionPriority = (question) => {
  if (!question) return 0
  if (Number.isFinite(Number(question.review_priority))) {
    return Math.round(clamp(Number(question.review_priority), 0, 100))
  }
  if (question.review_due_at) {
    const due = new Date(question.review_due_at)
    if (!Number.isNaN(due.getTime())) {
      const overdueDays = Math.floor((Date.now() - due.getTime()) / 86400000)
      if (overdueDays >= 0) return Math.round(clamp(82 + overdueDays * 4, 72, 99))
      return Math.round(clamp(58 + overdueDays * 8, 12, 70))
    }
  }

  const status = question.review_status || '待复习'
  const statusScore = {
    待复习: 58,
    复习中: 44,
    已掌握: 18,
  }[status] ?? 50

  const staleDays = daysSince(question.updated_at || question.created_at)
  const staleScore = clamp(staleDays * 2, 0, 26)
  const answerScore = question.user_answer ? 0 : 8
  const analysisScore = question.answer ? 0 : 6
  const correctionScore = question.needs_correction ? 8 : 0
  const tagScore = question.knowledge_tags?.length ? 0 : 4
  const mediaScore = question.has_image || question.has_formula ? 2 : 0

  return Math.round(clamp(
    statusScore + staleScore + answerScore + analysisScore + correctionScore + tagScore + mediaScore,
    5,
    99,
  ))
}
