<script setup lang="ts">
/**
 * ReviewStage.vue
 * 工作台第二页：识别结果核对（OCR 预览 / 分割中 / 题目列表）
 */
import { nextTick, ref, watch } from 'vue'
import BaseTag from '@/components/base/BaseTag.vue'
import OcrPreview from '@/components/features/app/workspace/OcrPreview.vue'
import SplitLoading from '@/components/features/app/workspace/SplitLoading.vue'
import QuestionList from '@/components/features/app/question/QuestionList.vue'
import { renderMarkdown, typesetMath } from '@/utils/index'

const props = defineProps({
  uploadMode: String,
  ocrLoading: Boolean,
  ocrDone: Boolean,
  ocrPages: Array,
  splitting: Boolean,
  splitCompleted: Boolean,
  questions: Array,
  selectedIds: Set,
  previewUrl: String,
  notePreview: Object,
  modelName: String,
})

const emit = defineEmits([
  'toggle-select', 'select-all', 'deselect-all', 'open-image', 'reorder',
])

const questionListRef = ref(null)
const notePreviewRef = ref(null)

const typesetNotePreview = async () => {
  if (props.uploadMode !== 'note' || !props.notePreview || !notePreviewRef.value) return
  await nextTick()
  window.setTimeout(() => {
    if (notePreviewRef.value) typesetMath(notePreviewRef.value)
  }, 100)
}

watch(() => [props.notePreview?.title, props.notePreview?.content_markdown], typesetNotePreview, { immediate: true })

defineExpose({
  triggerTypeset: () => questionListRef.value?.triggerTypeset?.(),
})
</script>

<template>
  <!-- OCR 加载中 / 识别结果预览 -->
  <OcrPreview
    v-if="ocrLoading || (ocrDone && !splitCompleted && !splitting)"
    :pages="ocrPages"
    :loading="ocrLoading"
    :preview-url="previewUrl"
    :model-name="modelName"
  />

  <!-- 分割进行中 -->
  <div v-else-if="splitting" class="flex-1 min-h-0 relative">
    <img v-if="previewUrl" :src="previewUrl" class="absolute inset-0 w-full h-full object-contain opacity-10 blur-sm" alt="" />
    <SplitLoading />
  </div>

  <!-- 笔记整理结果 -->
  <div
    v-else-if="uploadMode === 'note' && splitCompleted && notePreview"
    class="flex-1 overflow-y-auto custom-scrollbar py-2"
  >
    <section class="mx-auto max-w-4xl rounded-xl border border-gray-200 bg-white/80 p-5 dark:border-white/[0.07] dark:bg-white/[0.035]">
      <div class="mb-4 flex flex-wrap items-center gap-2">
        <BaseTag v-if="notePreview.subject" tone="accent">{{ notePreview.subject }}</BaseTag>
        <BaseTag v-for="tag in notePreview.knowledge_tags || []" :key="tag">{{ tag }}</BaseTag>
      </div>
      <h2 class="mb-5 text-xl font-bold text-gray-900 dark:text-[#f7f8f8]">
        {{ notePreview.title || '未命名笔记' }}
      </h2>
      <article
        ref="notePreviewRef"
        class="prose prose-slate max-w-none dark:prose-invert prose-headings:text-slate-900 dark:prose-headings:text-white prose-p:leading-relaxed prose-a:text-[rgb(var(--accent-rgb))] prose-pre:bg-slate-50 dark:prose-pre:bg-slate-900 prose-pre:border prose-pre:border-slate-200/60 dark:prose-pre:border-white/10"
        v-html="renderMarkdown(notePreview.content_markdown || '')"
      ></article>
    </section>
  </div>

  <!-- 题目列表 -->
  <div v-if="splitCompleted && questions.length" class="flex-1 overflow-y-auto pr-2 custom-scrollbar py-2 pb-24">
    <QuestionList
      ref="questionListRef"
      :questions="questions"
      :selected-ids="selectedIds"
      @toggle-select="(id) => emit('toggle-select', id)"
      @select-all="emit('select-all')"
      @deselect-all="emit('deselect-all')"
      @open-image="(src) => emit('open-image', src)"
      @reorder="(qs) => emit('reorder', qs)"
    />
  </div>
</template>
