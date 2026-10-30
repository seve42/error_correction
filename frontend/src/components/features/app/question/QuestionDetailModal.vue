<script setup lang="ts">
/**
 * QuestionDetailModal.vue
 * 题目详情弹窗（查看题目内容、答案、生成解析与对话）
 */
import { ref, computed, watch } from 'vue'
import * as api from '@/api/index'
import { useSystemStatus } from '@/composables/useSystemStatus'
import RichContent from '@/components/features/app/shared/RichContent.vue'
import BasePopconfirm from '@/components/base/BasePopconfirm.vue'
import { buildGeneratedAnswerQuestion, composeAnswerWithReasoning } from '@/utils/index'

const props = defineProps({
  open: { type: Boolean, default: false },
  question: { type: Object, default: null },
})

const extraImages = computed(() => {
  const refs = props.question?.image_refs || []
  if (!refs.length) return []
  const blocks = props.question?.content_json || []
  const embedded = new Set(
    blocks.filter(b => b.block_type === 'image' && b.content).map(b => b.content)
  )
  const htmlContent = blocks.filter(b => b.block_type === 'text').map(b => b.content || '').join(' ')
  return refs.filter(r => {
    if (embedded.has(r)) return false
    const filename = r.split('/').pop()
    if (filename && htmlContent.includes(filename)) return false
    return true
  })
})

const emit = defineEmits(['close', 'open-image', 'deleted', 'answer-saved', 'correct-answer-saved', 'review-status-changed', 'push-toast', 'start-chat'])

// 题目页面原图：跨页题目保留多张，优先 page_images，兼容旧数据的 page_image
const pageImages = computed(() => {
  const list = props.question?.page_images
  if (Array.isArray(list) && list.length) return list
  return props.question?.page_image ? [props.question.page_image] : []
})
const { selectedLlmOption } = useSystemStatus()

const userAnswer = ref('')
const isSaving = ref(false)
const answerText = ref('')
const isAnswerSaving = ref(false)
const generatingAnswer = ref(false)
const hasAnswer = computed(() => Boolean(String(props.question?.answer || '').trim()))
const modelReady = computed(() => Boolean(selectedLlmOption.value?.category && selectedLlmOption.value?.model_name))
const generateHint = computed(() => (modelReady.value ? '' : '请先在设置中选择模型'))

watch(() => props.open, (newVal) => {
  if (newVal && props.question) {
    userAnswer.value = props.question.user_answer || ''
    answerText.value = props.question.answer || ''
  }
})

const saveAnswer = async () => {
  if (!props.question) return
  isSaving.value = true
  try {
    const data = await api.saveAnswer(props.question.id, userAnswer.value)
    emit('answer-saved', props.question.id, data.user_answer, data.updated_at)
    emit('push-toast', 'success', '答题记录已保存')
  } catch (e) {
    emit('push-toast', 'error', '保存失败')
  } finally {
    isSaving.value = false
  }
}

const saveCorrectAnswer = async () => {
  if (!props.question) return
  isAnswerSaving.value = true
  try {
    await api.saveQuestionAnswer(props.question.id, answerText.value)
    props.question.answer = answerText.value
    emit('push-toast', 'success', '正确答案已保存')
  } catch (e) {
    emit('push-toast', 'error', '保存失败')
  } finally {
    isAnswerSaving.value = false
  }
}

/** 用当前选中模型生成标准答案与解析，并保存到题目上。 */
const generateStandardAnswer = async () => {
  if (!props.question || generatingAnswer.value) return
  const option = selectedLlmOption.value
  if (!option?.category || !option?.model_name) {
    emit('push-toast', 'error', '请先在顶部选择一个可用模型')
    return
  }
  generatingAnswer.value = true
  try {
    const payload = buildGeneratedAnswerQuestion(props.question)
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
    answerText.value = text
    props.question.answer = text
    emit('correct-answer-saved', props.question.id, text, data?.updated_at)
    emit('push-toast', 'success', '已生成标准答案解析')
  } catch (e) {
    emit('push-toast', 'error', e instanceof Error ? e.message : '生成标准答案解析失败')
  } finally {
    generatingAnswer.value = false
  }
}

const doDelete = async () => {
  if (!window.confirm('确定要从题库中永久删除这道题吗？')) return
  try {
    await api.deleteQuestion(props.question.id)
    emit('deleted', props.question.id)
    emit('push-toast', 'success', '题目已移除')
  } catch (e) {
    emit('push-toast', 'error', '删除失败')
  }
}

const changeReviewStatus = async (status) => {
  if (!props.question || props.question.review_status === status) return
  try {
    const data = await api.updateReviewStatus(props.question.id, status)
    props.question.review_status = data.review_status
    emit('review-status-changed', props.question.id, data.review_status, data.updated_at)
    emit('push-toast', 'success', `已标记为「${status}」`)
  } catch (e) {
    emit('push-toast', 'error', '更新状态失败')
  }
}

const reviewStatusOptions = [
  { value: '待复习', icon: 'fa-clock', color: 'rose' },
  { value: '复习中', icon: 'fa-spinner', color: 'amber' },
  { value: '已掌握', icon: 'fa-circle-check', color: 'emerald' },
]
</script>

<template>
  <Teleport to="body">
    <Transition name="dialog-overlay" appear>
      <div v-if="open" class="dialog-backdrop fixed inset-0 z-[100] bg-black/40" @click="emit('close')"></div>
    </Transition>

    <Transition name="dialog-content" appear>
      <div v-if="open" class="fixed inset-0 z-[101] flex items-center justify-center p-4 sm:p-6"
        @click.self="emit('close')">
        <!-- 弹窗主体 -->
        <div
          class="relative flex h-full max-h-[90vh] w-full max-w-5xl flex-col overflow-hidden rounded-xl border border-slate-200/60 bg-white shadow-2xl transition-all dark:border-[#2f3336] dark:bg-[#1b1b1d]">

          <!-- 头部控制栏 -->
          <div
            class="flex shrink-0 items-center justify-between border-b border-slate-100 bg-slate-50/50 px-8 py-5 dark:border-white/5 dark:bg-white/5">
            <div class="flex items-center gap-4">
              <div class="flex h-10 w-10 items-center justify-center rounded-xl accent-bg text-white shadow-lg">
                <i class="fa-solid fa-file-lines text-lg"></i>
              </div>
              <div>
                <h3 class="text-lg font-black tracking-tight text-slate-900 dark:text-white">题目详情分析</h3>
                <p class="text-[10px] font-black uppercase tracking-widest text-slate-400">题目编号 #{{ question?.id }}</p>
              </div>
            </div>

            <div class="flex items-center gap-3">
              <!-- 复习状态切换 -->
              <div class="flex items-center gap-1 rounded-xl bg-slate-100 p-1 dark:bg-white/5">
                <button v-for="opt in reviewStatusOptions" :key="opt.value" @click="changeReviewStatus(opt.value)"
                  class="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[10px] font-black uppercase tracking-wider transition-all"
                  :class="(question?.review_status || '待复习') === opt.value
                    ? opt.color === 'emerald' ? 'bg-emerald-500 text-white shadow-sm' : opt.color === 'amber' ? 'bg-amber-500 text-white shadow-sm' : 'bg-rose-500 text-white shadow-sm'
                    : 'text-slate-400 hover:text-slate-600 dark:hover:text-slate-300'">
                  <i class="fa-solid" :class="opt.icon"></i> {{ opt.value }}
                </button>
              </div>
              <div class="mx-1 h-6 w-px bg-slate-200 dark:bg-white/10"></div>
              <button @click="doDelete"
                class="h-10 w-10 rounded-xl text-slate-400 hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-500/10 transition-all">
                <i class="fa-regular fa-trash-can"></i>
              </button>
              <div class="mx-2 h-6 w-px bg-slate-200 dark:bg-white/10"></div>
              <button @click="emit('close')"
                class="h-10 w-10 rounded-xl bg-slate-100 text-slate-500 hover:bg-slate-200 dark:bg-white/5 dark:text-slate-400 transition-all">
                <i class="fa-solid fa-xmark"></i>
              </button>
            </div>
          </div>

          <!-- 内容主体 -->
          <div class="flex min-h-0 flex-1 flex-col overflow-hidden md:flex-row">
            <!-- 左侧：题干内容 (滚动区) -->
            <div
              class="flex-1 overflow-y-auto border-r border-slate-100 p-8 dark:border-white/5 bg-slate-50/60 dark:bg-transparent">
              <!-- 核心题干 -->
              <div class="question-view">
                <template v-for="(b, i) in question?.content_json" :key="i">
                  <RichContent v-if="b.block_type === 'text'"
                    class="mb-6 text-[15px] font-medium leading-relaxed text-slate-800 dark:text-slate-200"
                    :content="b.content" />
                  <img v-else-if="b.block_type === 'image'" :src="b.content"
                    class="mb-6 max-w-full cursor-zoom-in rounded-xl border border-slate-200 shadow-sm dark:border-white/10"
                    @click="emit('open-image', b.content)" />
                </template>

                <!-- 兜底渲染 image_refs 中未嵌入 content_json 的图片 -->
                <div v-if="extraImages.length" class="mb-6 flex flex-wrap gap-3">
                  <img v-for="(src, i) in extraImages" :key="'extra-' + i" :src="src"
                    class="max-h-[240px] cursor-zoom-in rounded-xl border border-slate-200 object-contain shadow-sm dark:border-white/10"
                    @click="emit('open-image', src)" />
                </div>

                <!-- 选项 -->
                <div v-if="question?.options_json" class="mt-8 grid gap-3">
                  <div v-for="(opt, idx) in question.options_json" :key="idx"
                    class="rounded-xl border border-slate-100 bg-slate-50/50 p-4 text-[15px] font-medium text-slate-700 dark:border-white/5 dark:bg-white/5 dark:text-slate-300">
                    <RichContent :content="String(opt ?? '')" />
                  </div>
                </div>

                <!-- 题目原图：视觉 OCR 识别时保留的整页图片，跨页题目会有两张 -->
                <div v-if="pageImages.length" class="mt-8">
                  <div class="mb-2 flex items-center gap-2 text-[11px] font-bold uppercase tracking-widest text-slate-400">
                    <i class="fa-regular fa-image"></i>
                    <span>题目原图<span v-if="pageImages.length > 1">（{{ pageImages.length }} 页）</span></span>
                  </div>
                  <div class="flex flex-wrap gap-3">
                    <img v-for="(src, index) in pageImages" :key="'page-' + index" :src="src"
                      class="max-h-40 max-w-full cursor-zoom-in rounded-xl border border-slate-200 object-contain transition-transform hover:scale-[1.01] dark:border-white/10"
                      @click="emit('open-image', src)" />
                  </div>
                </div>
              </div>
            </div>

            <!-- 右侧：笔记与答案解析 -->
            <div class="flex w-full flex-col overflow-hidden bg-white md:w-[400px] dark:bg-[#1b1b1d]">
              <div class="flex shrink-0 border-b border-slate-100 py-4 dark:border-white/5">
                <span class="px-6 text-xs font-black uppercase tracking-widest accent-text">我的笔记</span>
              </div>

              <div class="min-h-0 flex-1 overflow-y-auto p-6 pb-8 space-y-6">
                <!-- 正确答案 -->
                <div>
                  <label
                    class="mb-3 block text-[10px] font-black uppercase tracking-widest text-emerald-500 dark:text-emerald-400">
                    <i class="fa-solid fa-circle-check mr-1"></i>正确答案/解析
                  </label>
                  <textarea v-model="answerText" rows="4" placeholder="录入正确答案或标准解析…（支持 Markdown / LaTeX）"
                    class="w-full rounded-lg border border-slate-200 bg-white p-4 text-sm font-medium outline-none transition-all focus:border-emerald-500 focus:ring-4 focus:ring-emerald-500/5 dark:border-white/5 dark:bg-white/5 dark:text-white"></textarea>
                  <div v-if="answerText.trim()"
                    class="mt-3 rounded-lg border border-emerald-100 bg-emerald-50/40 p-3 dark:border-emerald-500/20 dark:bg-emerald-500/[0.05]">
                    <div class="mb-2 text-[10px] font-black uppercase tracking-widest text-emerald-500/80 dark:text-emerald-400/80">
                      解析预览
                    </div>
                    <RichContent class="text-sm leading-relaxed text-slate-700 dark:text-slate-300"
                      :content="answerText" />
                  </div>
                </div>
                <button @click="saveCorrectAnswer" :disabled="isAnswerSaving" class="btn-success w-full h-12">
                  <i class="fa-solid" :class="isAnswerSaving ? 'fa-circle-notch fa-spin' : 'fa-save'"></i>
                  {{ isAnswerSaving ? '同步中...' : '保存正确答案' }}
                </button>

                <div v-if="hasAnswer" class="flex justify-center">
                  <BasePopconfirm title="重新生成标准答案解析？"
                    description="将用 AI 生成的内容覆盖当前保存的答案与解析。" confirm-text="重新生成"
                    @confirm="generateStandardAnswer">
                    <button :disabled="generatingAnswer || !modelReady" :title="generateHint"
                      class="group inline-flex items-center justify-center gap-2 rounded-lg border accent-border accent-bg-muted px-8 py-3 text-sm font-black accent-text transition-all hover:bg-[rgb(var(--accent-rgb)/0.12)] disabled:cursor-not-allowed disabled:opacity-60">
                      <i class="fa-solid"
                        :class="generatingAnswer ? 'fa-circle-notch fa-spin' : 'fa-wand-magic-sparkles'"></i>
                      {{ generatingAnswer ? '生成中…' : 'AI 重新生成解析' }}
                    </button>
                  </BasePopconfirm>
                </div>
                <div v-else class="flex justify-center">
                  <button :disabled="generatingAnswer || !modelReady" :title="generateHint" @click="generateStandardAnswer"
                    class="group inline-flex items-center justify-center gap-2 rounded-lg border accent-border accent-bg-muted px-8 py-3 text-sm font-black accent-text transition-all hover:bg-[rgb(var(--accent-rgb)/0.12)] disabled:cursor-not-allowed disabled:opacity-60">
                    <i class="fa-solid"
                      :class="generatingAnswer ? 'fa-circle-notch fa-spin' : 'fa-wand-magic-sparkles'"></i>
                    {{ generatingAnswer ? '生成中…' : 'AI 生成标准答案解析' }}
                  </button>
                </div>
                <p v-if="!modelReady" class="text-center text-xs text-amber-600 dark:text-amber-400">
                  请先在设置中选择模型
                </p>

                <div class="my-2 border-t border-slate-100 dark:border-white/5"></div>

                <!-- 用户笔记 -->
                <div>
                  <label class="mb-3 block text-[10px] font-black uppercase tracking-widest text-slate-400">
                    <i class="fa-solid fa-pen-to-square mr-1"></i>我的错因/心得笔记
                  </label>
                  <textarea v-model="userAnswer" rows="4" placeholder="记录下当时的解题思路，或者标记这里错在哪里了..."
                    class="w-full rounded-lg border border-slate-200 bg-white p-4 text-sm font-medium outline-none transition-all focus:border-[rgb(var(--accent-rgb)/0.4)] focus:ring-4 focus:ring-[rgb(var(--accent-rgb)/0.05)] dark:border-white/5 dark:bg-white/5 dark:text-white"></textarea>
                  <div v-if="userAnswer.trim()"
                    class="mt-3 rounded-lg border border-slate-100 bg-slate-50/60 p-3 dark:border-white/5 dark:bg-white/[0.03]">
                    <div class="mb-2 text-[10px] font-black uppercase tracking-widest text-slate-400">笔记预览</div>
                    <RichContent class="text-sm leading-relaxed text-slate-700 dark:text-slate-300"
                      :content="userAnswer" />
                  </div>
                </div>
                <button @click="saveAnswer" :disabled="isSaving" class="btn-primary w-full h-12">
                  <i class="fa-solid" :class="isSaving ? 'fa-circle-notch fa-spin' : 'fa-save'"></i>
                  {{ isSaving ? '同步中...' : '保存学习笔记' }}
                </button>
                <button @click="emit('start-chat', question); emit('close')"
                  class="group flex w-full items-center justify-center gap-2 rounded-lg border accent-border accent-bg-muted py-4 text-sm font-black accent-text transition-all hover:bg-[rgb(var(--accent-rgb)/0.12)]">
                  <i class="fa-solid fa-comments"></i> AI 辅导对话
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.dialog-backdrop {
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}

.dialog-overlay-enter-active,
.dialog-overlay-leave-active {
  transition: opacity 0.22s ease, backdrop-filter 0.22s ease, -webkit-backdrop-filter 0.22s ease;
}

.dialog-overlay-enter-from,
.dialog-overlay-leave-to {
  opacity: 0;
}

.dialog-content-enter-active,
.dialog-content-leave-active {
  transition: opacity 0.22s ease, transform 0.22s cubic-bezier(0.16, 1, 0.3, 1);
}

.dialog-content-enter-from,
.dialog-content-leave-to {
  opacity: 0;
  transform: scale(0.96) translateY(8px);
}
</style>
