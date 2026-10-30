<script setup lang="ts">
/**
 * ProviderDialog.vue
 * API Provider 配置弹窗
 */
import { ref, computed, watch } from 'vue'
import { useToast } from '@/composables/useToast'
import BaseModal from '@/components/base/BaseModal.vue'
import BaseInput from '@/components/base/BaseInput.vue'
import BaseButton from '@/components/base/BaseButton.vue'

const { pushToast } = useToast()

const props = defineProps({
  open: { type: Boolean, default: false },
  type: { type: String, default: 'openai' }, // 'openai' | 'anthropic'
  editData: { type: Object, default: null }, // null=新增, object=编辑
})

const emit = defineEmits(['close', 'confirm'])

const isEdit = computed(() => !!props.editData)

const typeConfig = computed(() => ({
  openai: {
    title: isEdit.value ? '编辑 OpenAI 兼容供应商' : '添加 OpenAI 兼容供应商',
    iconBg: 'bg-gray-50 dark:bg-white/[0.04] border border-gray-100 dark:border-white/[0.08]',
    iconCls: 'fa-bolt text-slate-600 dark:text-slate-400',
    imgIcon: '/src/assets/provider-openai.svg',
    btnCls: 'bg-slate-900 hover:bg-slate-800 text-white dark:bg-[#f7f8f8] dark:hover:bg-white dark:text-[#1b1b1d]',
    namePlaceholder: '例如：DeepSeek / Qwen / Moonshot',
    urlPlaceholder: '留空使用 OpenAI 官方，或填入 https://api.deepseek.com 等',
    modelPlaceholder: 'gpt-4o-mini',
    defaultName: 'OpenAI Provider',
    secretLabel: 'API Key',
    secretPlaceholder: '输入 API Key',
    urlLabel: 'Base URL',
  },
  anthropic: {
    title: isEdit.value ? '编辑 Anthropic 供应商' : '添加 Anthropic 供应商',
    iconBg: 'bg-gray-50 dark:bg-white/[0.04] border border-gray-100 dark:border-white/[0.08]',
    iconCls: 'fa-brain text-slate-600 dark:text-slate-400',
    imgIcon: '/src/assets/provider-anthropic.svg',
    btnCls: 'bg-slate-900 hover:bg-slate-800 text-white dark:bg-[#f7f8f8] dark:hover:bg-white dark:text-[#1b1b1d]',
    namePlaceholder: '例如：Claude Official',
    urlPlaceholder: '留空使用 Anthropic 官方',
    modelPlaceholder: 'claude-sonnet-4-20250514',
    defaultName: 'Anthropic Provider',
    secretLabel: 'API Key',
    secretPlaceholder: '输入 API Key',
    urlLabel: 'Base URL',
  },
}[props.type]))

const defaultForm = () => ({
  name: '',
  api_key: '',
  base_url: '',
  model_name: '',
  light_model_name: '',
  supports_function_calling: true,
})

const form = ref(defaultForm())

// 模型列表相关
const modelList = ref([])
const fetchingModels = ref(false)
const fetchModelError = ref('')

const canFetchModels = computed(() => {
  // 需要有 API Key（新输入或已设置）
  const hasKey = form.value.api_key || (props.editData?.api_key_set)
  return !!hasKey
})

const fetchModels = async () => {
  if (fetchingModels.value) return
  fetchingModels.value = true
  fetchModelError.value = ''
  modelList.value = []
  try {
    const res = await fetch('/api/models/list', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        type: props.type,
        api_key: form.value.api_key || undefined,
        base_url: form.value.base_url || undefined,
        provider_id: props.editData?.id || undefined,
      }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.error || `HTTP ${res.status}`)
    }
    const data = await res.json()
    const apiModels = data.models || []
    const modelSet = new Set(apiModels)
    // 将当前表单中的模型名也加入列表（兼容废弃模型如 deepseek-chat / deepseek-reasoner）
    const currentModels = (form.value.model_name || '').split(',').map(s => s.trim()).filter(Boolean)
    const currentLightModel = form.value.light_model_name || ''
    for (const m of currentModels) { if (!modelSet.has(m)) { modelSet.add(m); apiModels.push(m) } }
    if (currentLightModel && !modelSet.has(currentLightModel)) { modelSet.add(currentLightModel); apiModels.push(currentLightModel) }
    modelList.value = apiModels
    if (modelList.value.length === 0) {
      pushToast('error', '未获取到可用模型')
    } else {
      pushToast('success', `获取到 ${modelList.value.length} 个可用模型`)
    }
  } catch (e) {
    pushToast('error', e.message || '获取模型列表失败')
  } finally {
    fetchingModels.value = false
  }
}

// 每次打开时重置或回填表单
watch(() => props.open, (v) => {
  if (!v) return
  modelList.value = []
  fetchModelError.value = ''
  openDropdown.value = null
  if (props.editData) {
    form.value = {
      ...defaultForm(),
      name: props.editData.name || '',
      api_key: '',
      base_url: props.editData.base_url || '',
      model_name: props.editData.model_name || '',
      light_model_name: props.editData.light_model_name || '',
      supports_function_calling: props.editData.supports_function_calling ?? true,
    }
  } else {
    form.value = defaultForm()
  }
})

const confirm = () => {
  if (!form.value.name.trim()) {
    form.value.name = typeConfig.value.defaultName
  }
  emit('confirm', { ...form.value })
}

const inputCls = 'w-full rounded-xl border border-slate-200/80 bg-white px-4 py-2.5 text-sm text-slate-800 placeholder-slate-400 transition-colors focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-500/20 dark:border-white/10 dark:bg-slate-800/80 dark:text-slate-200 dark:placeholder-slate-500'

// 自定义下拉
const openDropdown = ref(null) // 'model_name' | 'light_model_name' | null
const toggleDropdown = (field) => {
  openDropdown.value = openDropdown.value === field ? null : field
}
const selectOption = (field, value) => {
  if (field === 'model_name') {
    let current = form.value.model_name ? form.value.model_name.split(',').map(s => s.trim()).filter(Boolean) : []
    if (current.includes(value)) {
      current = current.filter(m => m !== value)
    } else {
      current.push(value)
    }
    form.value.model_name = current.join(', ')
  } else {
    form.value[field] = value
    openDropdown.value = null
  }
}
</script>

<template>
  <BaseModal :open="open" :title="typeConfig.title" :iconBg="typeConfig.iconBg" maxWidth="max-w-lg sm:w-[32rem]"
    :blurBackdrop="false" @close="emit('close')">
    <template #icon>
      <img v-if="typeConfig.imgIcon" :src="typeConfig.imgIcon" class="h-5 w-5 object-contain"
        :class="{ 'dark:invert': typeConfig.imgIcon.includes('provider-openai.svg') }" alt="icon" />
      <i v-else class="fa-solid text-base" :class="typeConfig.iconCls"></i>
    </template>

    <form autocomplete="off" class="space-y-4" @submit.prevent="confirm" @click="openDropdown = null">
      <div>
        <label class="mb-1.5 block text-xs font-bold text-slate-600 dark:text-slate-400">供应商名称</label>
        <BaseInput v-model="form.name" type="text" autocomplete="one-time-code"
          :placeholder="typeConfig.namePlaceholder" inputClass="h-10" autofocus />
      </div>

      <div>
        <label class="mb-1.5 block text-xs font-bold text-slate-600 dark:text-slate-400">{{ typeConfig.secretLabel
          }}</label>
        <BaseInput v-model="form.api_key" type="password" autocomplete="new-password"
          :placeholder="isEdit && editData?.api_key_set ? `已设置 (${editData.api_key_hint})，留空则不修改` : typeConfig.secretPlaceholder"
          inputClass="h-10" />
      </div>

      <div>
        <label class="mb-1.5 block text-xs font-bold text-slate-600 dark:text-slate-400">{{ typeConfig.urlLabel
          }}</label>
        <BaseInput v-model="form.base_url" type="text" autocomplete="one-time-code"
          :placeholder="typeConfig.urlPlaceholder" inputClass="h-10" />
      </div>

      <!-- 获取模型列表按钮 -->
      <div class="flex items-center gap-3">
        <BaseButton variant="secondary" @click="fetchModels" :disabled="!canFetchModels || fetchingModels"
          class="!h-8 !px-3 !text-[11px] !rounded-lg">
          <i class="fa-solid text-[10px]" :class="fetchingModels ? 'fa-circle-notch fa-spin' : 'fa-arrows-rotate'"></i>
          <span class="inline-block w-[4.5rem] text-center">{{ fetchingModels ? '获取中...' : '获取模型列表' }}</span>
        </BaseButton>
        <span v-if="!canFetchModels" class="text-xs text-slate-400 dark:text-slate-500">请先填写 API Key</span>
      </div>

      <div class="grid gap-4" :class="type === 'openai' ? 'sm:grid-cols-2' : ''">
        <div>
          <label class="mb-1.5 flex items-center gap-1.5 text-xs font-bold text-slate-600 dark:text-slate-400">
            {{ '模型(可多选)' }}
            <span class="group relative">
              <i
                class="fa-solid fa-circle-info cursor-help text-slate-400 transition-colors hover:text-blue-500 dark:text-slate-500"></i>
              <span
                class="pointer-events-none absolute bottom-full left-1/2 z-50 mb-2 -translate-x-1/2 whitespace-nowrap rounded-lg border border-slate-200/60 bg-white/90 px-3 py-1.5 text-xs font-normal text-slate-600 opacity-0 shadow-lg transition-opacity group-hover:opacity-100 dark:border-white/10 dark:bg-[#0A0A0F]/90 dark:text-slate-300">
                用于题目分割、纠错等核心任务
              </span>
            </span>
          </label>
          <!-- 有模型列表时用自定义下拉 -->
          <div v-if="modelList.length > 0" class="relative">
            <button type="button" @click.stop="toggleDropdown('model_name')"
              class="flex w-full items-center justify-between rounded-xl border border-slate-200/80 bg-white/70 px-4 py-2.5 text-left text-sm transition-colors dark:border-white/10 dark:bg-slate-800/60"
              :class="form.model_name ? 'text-slate-800 dark:text-slate-200' : 'text-slate-400 dark:text-slate-500'">
              <span class="truncate">{{ form.model_name || '请选择模型' }}</span>
              <i class="fa-solid fa-chevron-down ml-2 text-[10px] text-slate-400 transition-transform"
                :class="openDropdown === 'model_name' ? 'rotate-180' : ''"></i>
            </button>
            <Transition name="dropdown">
              <div v-if="openDropdown === 'model_name'"
                class="absolute z-50 mt-1.5 max-h-48 w-full overflow-y-auto rounded-xl border border-slate-200/60 bg-white py-1 shadow-xl dark:border-white/10 dark:bg-[#0A0A0F]">
                <button v-for="m in modelList" :key="m" type="button" @click.stop="selectOption('model_name', m)"
                  class="flex w-full items-center gap-2 px-4 py-2 text-left text-sm transition-colors hover:bg-gray-50 dark:hover:bg-white/5"
                  :class="(form.model_name ? form.model_name.split(',').map(s => s.trim()) : []).includes(m) ? 'font-bold text-slate-900 dark:text-[#f7f8f8]' : 'text-slate-600 dark:text-slate-400'">
                  <i v-if="(form.model_name ? form.model_name.split(',').map(s => s.trim()) : []).includes(m)"
                    class="fa-solid fa-check text-[10px] text-slate-900 dark:text-[#f7f8f8]"></i>
                  <span
                    :class="!(form.model_name ? form.model_name.split(',').map(s => s.trim()) : []).includes(m) ? 'pl-[18px]' : ''">{{
                    m }}</span>
                </button>
              </div>
            </Transition>
          </div>
          <!-- 无模型列表时用 input -->
          <BaseInput v-else v-model="form.model_name" type="text"
            placeholder="e.g. gpt-4o, deepseek-chat"
            inputClass="h-10" />
        </div>
        <div v-if="type === 'openai'">
          <label class="mb-1.5 flex items-center gap-1.5 text-xs font-bold text-slate-600 dark:text-slate-400">
            辅助模型
            <span class="font-normal text-slate-400">（可选）</span>
            <span class="group relative">
              <i
                class="fa-solid fa-circle-info cursor-help text-slate-400 transition-colors hover:text-blue-500 dark:text-slate-500"></i>
              <span
                class="pointer-events-none absolute bottom-full left-1/2 z-50 mb-2 -translate-x-1/2 whitespace-nowrap rounded-lg border border-slate-200/60 bg-white/90 px-3 py-1.5 text-xs font-normal text-slate-600 opacity-0 shadow-lg transition-opacity group-hover:opacity-100 dark:border-white/10 dark:bg-[#0A0A0F]/90 dark:text-slate-300">
                用于科目识别等简单任务，更快更省 Token
              </span>
            </span>
          </label>
          <div v-if="modelList.length > 0" class="relative">
            <button type="button" @click.stop="toggleDropdown('light_model_name')"
              class="flex w-full items-center justify-between rounded-xl border border-slate-200/80 bg-white/70 px-4 py-2.5 text-left text-sm transition-colors dark:border-white/10 dark:bg-slate-800/60"
              :class="form.light_model_name ? 'text-slate-800 dark:text-slate-200' : 'text-slate-400 dark:text-slate-500'">
              <span class="truncate">{{ form.light_model_name || '不使用' }}</span>
              <i class="fa-solid fa-chevron-down ml-2 text-[10px] text-slate-400 transition-transform"
                :class="openDropdown === 'light_model_name' ? 'rotate-180' : ''"></i>
            </button>
            <Transition name="dropdown">
              <div v-if="openDropdown === 'light_model_name'"
                class="absolute z-50 mt-1.5 max-h-48 w-full overflow-y-auto rounded-xl border border-slate-200/60 bg-white py-1 shadow-xl dark:border-white/10 dark:bg-[#0A0A0F]">
                <button type="button" @click.stop="selectOption('light_model_name', '')"
                  class="flex w-full items-center gap-2 px-4 py-2 text-left text-sm transition-colors hover:bg-gray-50 dark:hover:bg-white/5"
                  :class="!form.light_model_name ? 'font-bold text-slate-900 dark:text-[#f7f8f8]' : 'text-slate-600 dark:text-slate-400'">
                  <i v-if="!form.light_model_name"
                    class="fa-solid fa-check text-[10px] text-slate-900 dark:text-[#f7f8f8]"></i>
                  <span :class="form.light_model_name ? 'pl-[18px]' : ''">不使用</span>
                </button>
                <button v-for="m in modelList" :key="m" type="button" @click.stop="selectOption('light_model_name', m)"
                  class="flex w-full items-center gap-2 px-4 py-2 text-left text-sm transition-colors hover:bg-gray-50 dark:hover:bg-white/5"
                  :class="form.light_model_name === m ? 'font-bold text-slate-900 dark:text-[#f7f8f8]' : 'text-slate-600 dark:text-slate-400'">
                  <i v-if="form.light_model_name === m"
                    class="fa-solid fa-check text-[10px] text-slate-900 dark:text-[#f7f8f8]"></i>
                  <span :class="form.light_model_name !== m ? 'pl-[18px]' : ''">{{ m }}</span>
                </button>
              </div>
            </Transition>
          </div>
          <BaseInput v-else v-model="form.light_model_name" type="text" placeholder="科目识别等轻量任务使用" inputClass="h-10" />
        </div>
      </div>

      <!-- Function Calling 开关（仅 OpenAI） -->
      <div v-if="type === 'openai'" class="border-t border-slate-100 pt-4 dark:border-white/5">
        <label class="flex cursor-pointer items-center justify-between">
          <div>
            <span class="text-sm font-medium text-slate-700 dark:text-slate-300">支持 Function Calling</span>
            <p class="mt-0.5 text-xs text-slate-400 dark:text-slate-500">文心一言、通义千问等不支持时需关闭</p>
          </div>
          <button type="button" @click="form.supports_function_calling = !form.supports_function_calling"
            class="relative inline-flex h-5 w-10 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none"
            :class="form.supports_function_calling ? 'bg-slate-900 dark:bg-[#f7f8f8]' : 'bg-slate-200 dark:bg-white/10'">
            <span
              class="pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow-sm ring-0 transition duration-200 ease-in-out dark:bg-[#0A0A0F]"
              :class="form.supports_function_calling ? 'translate-x-5' : 'translate-x-0'"></span>
          </button>
        </label>
      </div>
    </form>

    <template #footer>
      <BaseButton variant="secondary" @click="emit('close')" class="!h-9 !px-4 !text-[13px] !font-bold !rounded-lg">
        取消
      </BaseButton>
      <BaseButton variant="primary" @click="confirm" class="!h-9 !px-4 !text-[13px] !font-bold !rounded-lg">
        确认保存
      </BaseButton>
    </template>
  </BaseModal>
</template>

<style scoped>
.dialog-fade-enter-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}

.dialog-fade-leave-active {
  transition: opacity 0.15s ease, transform 0.15s ease;
}

.dialog-fade-enter-from,
.dialog-fade-leave-to {
  opacity: 0;
  transform: scale(0.96);
}

.dropdown-enter-active {
  transition: opacity 0.15s ease, transform 0.15s ease;
}

.dropdown-leave-active {
  transition: opacity 0.1s ease, transform 0.1s ease;
}

.dropdown-enter-from,
.dropdown-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
