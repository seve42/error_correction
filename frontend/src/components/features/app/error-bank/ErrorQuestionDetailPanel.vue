<script setup lang="ts">
/**
 * ErrorQuestionDetailPanel.vue
 * 错题库中间题目详情面板：题干、选项、答案解析与作答记录，并支持 AI 生成标准答案解析。
 */
import { computed, ref } from 'vue'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BasePanel from '@/components/base/BasePanel.vue'
import BasePanelTitle from '@/components/base/BasePanelTitle.vue'
import BasePopconfirm from '@/components/base/BasePopconfirm.vue'
import BaseTabs from '@/components/base/BaseTabs.vue'
import BaseTag from '@/components/base/BaseTag.vue'
import BaseToolbarButton from '@/components/base/BaseToolbarButton.vue'
import RichContent from '@/components/features/app/shared/RichContent.vue'
import { buildGeneratedAnswerQuestion, composeAnswerWithReasoning, formatOption } from '@/utils/index'
import * as api from '@/api/index'
import { useSystemStatus } from '@/composables/useSystemStatus'
import { useToast } from '@/composables/useToast'

const props = defineProps({
  question: { type: Object, default: null },
  contentBlocks: { type: Array, default: () => [] },
  optionList: { type: Array, default: () => [] },
  knowledgeTags: { type: Array, default: () => [] },
  activeTab: { type: String, default: 'answer' },
  detailTabs: { type: Array, default: () => [] },
})

const emit = defineEmits([
  'update:activeTab',
  'edit',
  'delete',
  'open-image',
  'open-detail',
  'open-chat',
  'start-practice',
  'back-to-list',
  'answer-saved',
])

const { selectedLlmOption } = useSystemStatus()
const { pushToast } = useToast()
const generatingAnswer = ref(false)
const hasAnswer = computed(() => Boolean(String(props.question?.answer || '').trim()))
// 题目页面原图：跨页题目保留多张，优先 page_images，兼容旧数据的 page_image
const pageImages = computed(() => {
  const list = props.question?.page_images
  if (Array.isArray(list) && list.length) return list
  return props.question?.page_image ? [props.question.page_image] : []
})
const modelReady = computed(() => Boolean(selectedLlmOption.value?.category && selectedLlmOption.value?.model_name))
const generateHint = computed(() => (modelReady.value ? '' : '请先在设置中选择模型'))

const statusTone = (status) => {
  if (status === '已掌握') return 'emerald'
  if (status === '复习中') return 'amber'
  return 'rose'
}

const setActiveTab = (value) => emit('update:activeTab', value)

/** 用当前选中模型生成标准答案与解析，并保存到题目上。 */
const generateAnswer = async () => {
  if (!props.question || generatingAnswer.value) return
  const option = selectedLlmOption.value
  if (!option?.category || !option?.model_name) {
    pushToast('error', '请先在顶部选择一个可用模型')
    return
  }
  generatingAnswer.value = true
  try {
    const payload = buildGeneratedAnswerQuestion(props.question, props.contentBlocks, props.optionList)
    const results = await api.generateAnswers([payload], {
      modelProvider: option.category,
      modelName: option.model_name,
      providerSource: option.source,
      providerId: option.provider_id,
    })
    const result = results[0]
    if (!result) throw new Error('后端未返回生成结果')
    const text = composeAnswerWithReasoning(result.answer, result.reasoning)
    if (!text) throw new Error('生成结果为空')
    const data = await api.saveQuestionAnswer(props.question.id, text)
    props.question.answer = text
    emit('answer-saved', props.question.id, text, data?.updated_at)
    pushToast('success', '已生成标准答案解析')
  } catch (e) {
    pushToast('error', e instanceof Error ? e.message : '生成标准答案解析失败')
  } finally {
    generatingAnswer.value = false
  }
}
</script>

<template>
  <BasePanel v-if="question" body-class="p-5" header-class="flex h-16 items-center px-5 py-3" footer-class="px-5 py-4">
    <template #header>
      <div class="flex w-full items-center justify-between gap-3">
        <BasePanelTitle icon="fa-file-lines">题目详情</BasePanelTitle>
        <div class="flex items-center gap-2">
          <BaseToolbarButton icon="fa-arrow-left-long" title="返回题目列表" @click="emit('back-to-list')">
            <span class="hidden sm:inline">返回列表</span>
          </BaseToolbarButton>
          <BaseToolbarButton icon="fa-pen" title="编辑题目" @click="emit('edit', question, 'question')" />
          <BasePopconfirm title="删除这道题？" description="删除后将从当前错题库永久移除。" confirm-text="删除" danger @confirm="emit('delete', question)">
            <BaseToolbarButton icon="fa-trash-can" variant="danger" title="删除题目" />
          </BasePopconfirm>
        </div>
      </div>
    </template>

        <div class="space-y-5">
          <div class="flex flex-wrap items-center gap-2">
            <BaseTag v-if="question.subject" tone="accent">{{ question.subject }}</BaseTag>
            <BaseTag v-for="tag in knowledgeTags.slice(0, 3)" :key="tag">{{ tag }}</BaseTag>
            <BaseTag :tone="statusTone(question.review_status || '待复习')">{{ question.review_status || '待复习' }}</BaseTag>
          </div>

          <div class="rounded-xl bg-gray-50/85 p-5 dark:bg-white/[0.035]">
            <template v-for="(block, index) in contentBlocks" :key="index">
              <RichContent
                v-if="block.block_type === 'text'"
                class="mb-4 text-[15px] font-medium leading-8 text-gray-800 dark:text-[#d0d6e0]"
                :content="block.content"
              />
              <img
                v-else-if="block.block_type === 'image'"
                :src="block.content"
                class="mb-4 max-h-72 max-w-full cursor-zoom-in rounded-xl object-contain shadow-sm shadow-black/[0.04] dark:shadow-black/20"
                @click="emit('open-image', block.content)"
              />
            </template>
          </div>

          <div v-if="optionList.length" class="grid gap-3 sm:grid-cols-2">
            <div
              v-for="(option, index) in optionList"
              :key="index"
              class="rounded-xl bg-gray-50/80 px-4 py-3 text-sm font-medium text-gray-700 dark:bg-white/[0.035] dark:text-[#d0d6e0]"
            >
              <span class="mr-2 text-gray-400">{{ String.fromCharCode(65 + index) }}.</span>
              <RichContent inline :content="formatOption(option).replace(/^[A-Da-d][.、．]\s*/, '')" />
            </div>
          </div>

          <!-- 题目原图：视觉 OCR 识别时保留的整页图片，跨页题目会有两张 -->
          <div v-if="pageImages.length" class="space-y-2">
            <div class="flex items-center gap-2 text-[11px] font-bold uppercase tracking-widest text-gray-400 dark:text-[#8a8f98]">
              <i class="fa-regular fa-image"></i>
              <span>题目原图<span v-if="pageImages.length > 1">（{{ pageImages.length }} 页）</span></span>
            </div>
            <div class="flex flex-wrap gap-3">
              <img
                v-for="(src, index) in pageImages"
                :key="'page-' + index"
                :src="src"
                class="max-h-56 max-w-full cursor-zoom-in rounded-xl border border-gray-200 object-contain shadow-sm shadow-black/[0.04] dark:border-white/10 dark:shadow-black/20"
                @click="emit('open-image', src)"
              />
            </div>
          </div>

          <div class="rounded-xl bg-white/75 shadow-sm shadow-black/[0.03] dark:bg-white/[0.035] dark:shadow-black/20">
            <BaseTabs :model-value="activeTab" :items="detailTabs" @update:model-value="setActiveTab" />

            <div class="p-5">
              <div v-if="activeTab === 'answer'" class="space-y-3">
                <RichContent v-if="question.answer" class="text-sm leading-7 text-gray-700 dark:text-[#d0d6e0]"
                  :content="question.answer" />
                <BaseEmptyState v-else icon="fa-regular fa-circle-question" title="暂无答案解析" description="可以点击编辑补充标准答案，或用 AI 生成答案解析。" />
              </div>

              <div v-else class="space-y-3">
                <RichContent v-if="question.user_answer" class="text-sm leading-7 text-gray-700 dark:text-[#d0d6e0]"
                  :content="question.user_answer" />
                <BaseEmptyState v-else icon="fa-solid fa-camera" title="还没有作答记录" description="拍照、上传或手动记录你的答案和解题过程。" />
              </div>
            </div>
          </div>
        </div>

    <template #footer>
      <div class="flex flex-wrap items-center gap-3">
        <BaseButton size="sm" variant="primary" @click="emit('open-detail', question)">
          <i class="fa-solid fa-magnifying-glass-chart"></i>
          查看完整解析
        </BaseButton>
        <BasePopconfirm
          v-if="hasAnswer"
          title="重新生成标准答案解析？"
          description="将用 AI 生成的内容覆盖当前保存的答案与解析。"
          confirm-text="重新生成"
          @confirm="generateAnswer"
        >
          <BaseButton size="sm" variant="secondary" :disabled="generatingAnswer || !modelReady" :title="generateHint">
            <i class="fa-solid" :class="generatingAnswer ? 'fa-circle-notch fa-spin' : 'fa-wand-magic-sparkles'"></i>
            {{ generatingAnswer ? '生成中…' : 'AI 重新生成解析' }}
          </BaseButton>
        </BasePopconfirm>
        <BaseButton v-else size="sm" variant="secondary" :disabled="generatingAnswer || !modelReady" :title="generateHint"
          @click="generateAnswer">
          <i class="fa-solid" :class="generatingAnswer ? 'fa-circle-notch fa-spin' : 'fa-wand-magic-sparkles'"></i>
          {{ generatingAnswer ? '生成中…' : 'AI 生成标准答案解析' }}
        </BaseButton>
        <span v-if="!modelReady" class="text-xs text-amber-600 dark:text-amber-400">请先在设置中选择模型</span>
        <BaseButton size="sm" variant="secondary" @click="emit('open-chat', question)">
          <i class="fa-solid fa-comments"></i>
          AI 讲解
        </BaseButton>
        <BaseButton size="sm" variant="secondary" @click="emit('start-practice')">
          <i class="fa-solid fa-calendar-plus"></i>
          加入复习
        </BaseButton>
      </div>
    </template>
  </BasePanel>

  <BasePanel v-else :scroll-body="false" body-class="grid h-full place-items-center p-6">
    <BaseEmptyState icon="fa-solid fa-file-circle-question" title="请选择一道题目" description="从左侧列表选择题目后，这里会展示题干、选项和解析。" />
  </BasePanel>
</template>
