import { computed, reactive, ref, type Ref } from 'vue'
import * as api from '@/api/index'
import type { ErrorBankQuestion, Id, QuestionContentBlock, QuestionOption } from '@/types/domain'

type UseErrorBankQueryOptions = {
  activeQuestionProjectId: Ref<Id | null>
  pushToast: (type: string, message: string) => void
  typesetMath: () => Promise<void>
}

type ErrorBankFilters = {
  subject: string[]
  knowledge_tag: string[]
  question_type: string[]
  keyword: string
  review_status: string
}

type ErrorBankFilterParams = {
  project_id: Id
  subject?: string[]
  knowledge_tag?: string[]
  question_type?: string[]
  keyword?: string
  review_status?: string
}

type ErrorBankQueryParams = ErrorBankFilterParams & {
  page: number
  page_size: number
}

export function useErrorBankQuery({ activeQuestionProjectId, pushToast, typesetMath }: UseErrorBankQueryOptions) {
  const filters = reactive({
    subject: [],
    knowledge_tag: [],
    question_type: [],
    keyword: '',
    review_status: '',
  } as ErrorBankFilters)
  const page = ref(1)
  const pageSize = ref(10)
  const items = ref<ErrorBankQuestion[]>([])
  const total = ref(0)
  const grandTotal = ref(0)
  const totalPages = ref(0)
  const loading = ref(false)
  const subjects = ref<string[]>([])
  const questionTypes = ref<string[]>([])
  const tagNames = ref<string[]>([])
  const selectedTags = reactive(new Set<string>())
  const activeQuestionId = ref<Id | null>(null)
  let debounceTimer: ReturnType<typeof setTimeout> | null = null

  const activeQuestion = computed(() =>
    items.value.find(q => String(q.id) === String(activeQuestionId.value)) || items.value[0] || null
  )
  const contentBlocks = computed<QuestionContentBlock[]>(() => activeQuestion.value?.content_json || [])
  const optionList = computed<QuestionOption[]>(() => activeQuestion.value?.options_json || [])
  const knowledgeTags = computed(() => activeQuestion.value?.knowledge_tags || [])

  const reviewStatusOptions = ['待复习', '复习中', '已掌握']

  const buildFilterParams = (): ErrorBankFilterParams => {
    const params: ErrorBankFilterParams = { project_id: activeQuestionProjectId.value }
    if (filters.subject?.length) params.subject = filters.subject
    if (filters.knowledge_tag?.length) params.knowledge_tag = filters.knowledge_tag
    if (filters.question_type?.length) params.question_type = filters.question_type
    if (filters.keyword) params.keyword = filters.keyword
    if (filters.review_status) params.review_status = filters.review_status
    return params
  }

  const doQuery = async () => {
    if (!activeQuestionProjectId.value) {
      items.value = []
      total.value = 0
      grandTotal.value = 0
      totalPages.value = 0
      loading.value = false
      activeQuestionId.value = null
      return
    }
    loading.value = true
    try {
      const params: ErrorBankQueryParams = { page: page.value, page_size: pageSize.value, ...buildFilterParams() }

      const data = await api.fetchErrorBank(params)
      items.value = data.items || []
      total.value = data.total || 0
      grandTotal.value = data.grand_total ?? data.total ?? 0
      totalPages.value = data.total_pages || 0

      if (!items.value.some(q => String(q.id) === String(activeQuestionId.value))) {
        activeQuestionId.value = items.value[0]?.id || null
      }

      // 删除题目后当前页可能超出范围，回退到最后一页再查一次
      if (page.value > 1 && (totalPages.value === 0 || page.value > totalPages.value)) {
        page.value = Math.max(1, totalPages.value)
        await doQuery()
      }
    } catch (error) {
      pushToast('error', error instanceof Error ? error.message : String(error))
    } finally {
      loading.value = false
      await typesetMath()
    }
  }

  const refreshTags = async () => {
    if (!activeQuestionProjectId.value) {
      tagNames.value = []
      return
    }
    const raw = await api.fetchTagNames(filters.subject?.length === 1 ? filters.subject[0] : undefined, activeQuestionProjectId.value)
    tagNames.value = [...new Set(raw)]
  }

  const loadFilters = async () => {
    if (!activeQuestionProjectId.value) {
      subjects.value = []
      questionTypes.value = []
      tagNames.value = []
      return
    }
    try {
      const [subjectRows, typeRows] = await Promise.all([
        api.fetchSubjects(activeQuestionProjectId.value),
        api.fetchQuestionTypes(activeQuestionProjectId.value),
      ])
      subjects.value = subjectRows
      questionTypes.value = typeRows
      await refreshTags()
    } catch (_) {
      pushToast('error', '加载筛选项失败')
    }
  }

  const debouncedQuery = (delay = 300) => {
    clearTimeout(debounceTimer)
    debounceTimer = setTimeout(async () => {
      page.value = 1
      await doQuery()
    }, delay)
  }

  const resetFilters = () => {
    filters.subject = []
    filters.knowledge_tag = []
    filters.question_type = []
    filters.keyword = ''
    filters.review_status = ''
    selectedTags.clear()
    page.value = 1
    doQuery()
  }

  const goPage = (nextPage) => {
    if (nextPage < 1 || nextPage > totalPages.value || nextPage === page.value) return
    page.value = nextPage
    doQuery()
  }

  const dispose = () => {
    if (debounceTimer) clearTimeout(debounceTimer)
  }

  return {
    filters,
    page,
    pageSize,
    items,
    total,
    grandTotal,
    totalPages,
    loading,
    subjects,
    questionTypes,
    tagNames,
    selectedTags,
    activeQuestionId,
    activeQuestion,
    contentBlocks,
    optionList,
    knowledgeTags,
    reviewStatusOptions,
    buildFilterParams,
    doQuery,
    loadFilters,
    refreshTags,
    debouncedQuery,
    resetFilters,
    goPage,
    dispose,
  }
}
