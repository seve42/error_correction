export type Id = string | number

export type QuestionContentBlock = {
  block_type: 'text' | 'image' | string
  content?: string
  url?: string
  /** 视觉 OCR 识别出的语义标签，旧数据可能没有。 */
  label?: string
  /** 旧 OCR 数据的坐标；视觉 OCR 通常为 null，前端不能依赖。 */
  bbox?: number[] | null
  /** 笔迹类型：印刷体 / 红笔 / 黑笔。 */
  pen?: 'printed' | 'red' | 'black'
  [key: string]: unknown
}

export type QuestionGradingVerdict = 'correct' | 'wrong' | 'partial' | 'unknown'

export type QuestionGradingBlank = {
  blank_id?: string
  printed_hint?: string
  answer?: string
  verdict?: QuestionGradingVerdict | string
  annotation?: string
  [key: string]: unknown
}

export type QuestionGradingMark = {
  type?: string
  text?: string
  [key: string]: unknown
}

/** 红笔判分结果；旧题没有该字段时前端按无判分展示。 */
export type QuestionGrading = {
  verdict?: QuestionGradingVerdict | string
  annotation?: string
  blanks?: QuestionGradingBlank[]
  marks?: QuestionGradingMark[]
  [key: string]: unknown
}

export type QuestionRedMark = {
  type?: string
  text?: string
  linked_qid?: string
  [key: string]: unknown
}

export type QuestionOption = {
  label?: string
  content?: string
  text?: string
  value?: string
  [key: string]: unknown
}

export type ErrorBankQuestion = {
  id: Id
  subject?: string
  question_type?: string
  content_json?: QuestionContentBlock[]
  content_blocks?: QuestionContentBlock[]
  options_json?: QuestionOption[]
  knowledge_tags?: string[]
  review_status?: string
  user_answer?: string
  // 视觉 OCR 与判分新增的可选字段，旧数据可能缺失。
  page_index?: number
  student_answer?: string
  has_handwriting?: boolean
  grading?: QuestionGrading
  red_marks?: QuestionRedMark[]
  created_at?: string
  updated_at?: string
  [key: string]: unknown
}

export type ChatSession = {
  id: Id
  title?: string
  created_at?: string
  updated_at?: string
  [key: string]: unknown
}

export type ChatMessageRole = 'user' | 'assistant' | 'system'

export type ChatMessage = {
  id?: Id | string
  role: ChatMessageRole
  content: string
  rawContent?: string
  reasoning?: string
  reasoningOpen?: boolean
  created_at?: string
  updated_at?: string
  [key: string]: unknown
}

export type NotePreview = {
  title?: string
  content?: string
  summary?: string
  subject?: string
  knowledge_tags?: string[]
  [key: string]: unknown
}
