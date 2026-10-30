import { ref, computed, watch } from 'vue'
import * as api from '@/api/index'

/**
 * useSystemStatus.js
 * 管理系统依赖状态、可用模型列表和当前选中的大模型配置。
 */

// 安全的 localStorage 访问：浏览器禁用存储时不影响页面运行。
const safeLocalStorage = {
  /**
   * 读取本地存储，失败时返回兜底值。
   */
  getItem(key, fallback = '') {
    try {
      return localStorage.getItem(key) || fallback
    } catch {
      return fallback
    }
  },
  /**
   * 写入本地存储，失败时只记录警告。
   */
  setItem(key, value) {
    try {
      localStorage.setItem(key, value)
    } catch (e) {
      console.warn(`[useSystemStatus] localStorage.setItem failed for "${key}":`, e)
    }
  },
}

// 模块级单例状态：多个组件共享同一份系统状态与模型选择。
const statusLoading = ref(true)
const systemStatus = ref(null)
const statusError = ref('')
const selectedModel = ref('')

const modelOptionsLoading = ref(false)
const modelOptionsData = ref(null)
const selectedLlmOptionId = ref(safeLocalStorage.getItem('selected_llm_option_id', ''))

const splitModelNames = (value) => String(value || '')
  .split(',')
  .map((item) => item.trim())
  .filter(Boolean)

const normalizeModelOptions = (data) => {
  const normalizedOptions = []
  let normalizedDefaultId = data?.default_option_id || ''

  for (const option of (data?.options || [])) {
    const models = splitModelNames(option.model_name)
    if (models.length <= 1) {
      normalizedOptions.push({
        ...option,
        model_name: models[0] || option.model_name || '',
        label: models[0] || option.model_name || option.label || '',
      })
      continue
    }

    models.forEach((model, index) => {
      const optionId = `${option.source}:${option.category}:${option.provider_id}:${model}`
      if (option.option_id === data.default_option_id && index === 0) {
        normalizedDefaultId = optionId
      }
      normalizedOptions.push({
        ...option,
        option_id: optionId,
        model_name: model,
        label: model,
        is_default: Boolean(option.is_default && index === 0),
      })
    })
  }

  return {
    ...data,
    options: normalizedOptions,
    default_option_id: normalizedDefaultId,
  }
}

const providerOptions = computed(() => {
  const s = systemStatus.value
  return s && s.available_models ? s.available_models : []
})

const hasConfiguredModel = computed(() => providerOptions.value.some(p => p.configured))

const selectedProvider = computed(() => {
  for (const p of providerOptions.value) {
    if (p.models && p.models.includes(selectedModel.value)) return p.value
  }
  const configured = providerOptions.value.find(p => p.configured)
  return configured ? configured.value : (providerOptions.value[0]?.value ?? 'openai')
})

// 旧版模型选择依赖 selectedModel，这里继续保持兼容同步。
watch(systemStatus, (newVal) => {
  if (newVal && newVal.available_models) {
    const configured = newVal.available_models.find(m => m.configured)
    if (configured) selectedModel.value = configured.default_model
  }
})

const statusPills = computed(() => {
  if (statusLoading.value) return [
    { key: 'vision', loading: true, label: '视觉识别' },
    { key: 'ensexam', loading: true, label: 'EnsExam' },
    { key: 'langsmith', loading: true, label: 'LangSmith' },
  ]
  const s = systemStatus.value
  if (!s) return []
  const pills = []
  // 视觉识别状态以是否已配置模型为准，不绑定具体模型品牌。
  pills.push({ key: 'vision', ok: hasConfiguredModel.value, label: hasConfiguredModel.value ? '视觉识别' : '视觉识别未配置' })
  if (s.ensexam_configured) {
    pills.push({ key: 'ensexam', ok: true, label: 'EnsExam' })
  }
  pills.push(s.langsmith_enabled
    ? { key: 'langsmith', ok: true, label: 'LangSmith追踪' }
    : { key: 'langsmith', ok: false, label: 'LangSmith', isPlaceholder: true }
  )
  return pills
})

/**
 * 获取后端系统状态，例如 OCR、EnsExam、LangSmith 是否可用。
 */
const doFetchStatus = async () => {
  statusLoading.value = true
  statusError.value = ''
  try {
    systemStatus.value = await api.fetchStatus()
  } catch (e) {
    statusError.value = e instanceof Error ? e.message : String(e)
  } finally {
    statusLoading.value = false
  }
}

/**
 * 获取可用模型选项，并修正本地保存的无效选中项。
 */
const doFetchModelOptions = async () => {
  modelOptionsLoading.value = true
  try {
    const data = normalizeModelOptions(await api.fetchModelOptions())
    modelOptionsData.value = data

    // 如果没有选中项，或者选中项不在当前可用列表中，则使用默认选项
    const options = data.options || []
    const serverSelectedId = data.selected_option_id || ''
    const isServerSelectionValid = serverSelectedId && options.some(o => o.option_id === serverSelectedId && o.available)
    const isValidSelection = selectedLlmOptionId.value && options.some(o => o.option_id === selectedLlmOptionId.value && o.available)
    if (isServerSelectionValid && serverSelectedId !== selectedLlmOptionId.value) {
      selectedLlmOptionId.value = serverSelectedId
    } else if (!isValidSelection) {
      selectedLlmOptionId.value = data.default_option_id || (options[0]?.option_id || '')
      if (selectedLlmOptionId.value) {
        try {
          safeLocalStorage.setItem('selected_llm_option_id', selectedLlmOptionId.value)
        } catch (e) {
          console.warn('[useSystemStatus] 保存选中模型失败:', e)
        }
      }
    }

    // 确保 selectedModel 也被初始化同步
    if (selectedLlmOption.value) {
      selectedModel.value = selectedLlmOption.value.model_name
    }
  } catch (e) {
    console.error('获取模型选项失败', e)
  } finally {
    modelOptionsLoading.value = false
  }
}

// 用户切换模型时持久化选项，同时同步旧的 selectedModel 字段。
watch(selectedLlmOptionId, async (newId) => {
  if (newId) {
    try {
      safeLocalStorage.setItem('selected_llm_option_id', newId)
    } catch (e) {
      console.warn('[useSystemStatus] 保存选中模型失败:', e)
    }

    // 同步更新旧的 selectedModel 状态，以兼容设置页等旧逻辑
    if (selectedLlmOption.value) {
      selectedModel.value = selectedLlmOption.value.model_name
    }

    try {
      await api.updateModelSelection(newId)
    } catch (e) {
      console.warn('[useSystemStatus] 保存服务端模型选择失败:', e)
    }
  }
})

// 当前真正选中的模型配置对象，供分割、笔记整理等流程读取。
const selectedLlmOption = computed(() => {
  if (!modelOptionsData.value || !modelOptionsData.value.options) return null
  return modelOptionsData.value.options.find(o => o.option_id === selectedLlmOptionId.value) || null
})

/**
 * useSystemStatus — 系统状态单例 composable
 * 任何组件调用都返回同一份响应式状态
 */
export function useSystemStatus() {
  return {
    statusLoading, systemStatus, statusError, selectedModel,
    providerOptions, hasConfiguredModel, selectedProvider, statusPills,
    modelOptionsLoading, modelOptionsData, selectedLlmOptionId, selectedLlmOption,
    doFetchStatus, doFetchModelOptions,
  }
}
