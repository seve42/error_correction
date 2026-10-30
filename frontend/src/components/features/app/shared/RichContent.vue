<script setup lang="ts">
/**
 * RichContent.vue
 * 题目、答案与解析的富文本容器：HTML 走 DOMPurify 白名单，纯文本按 Markdown + LaTeX 渲染，
 * 渲染完成后触发 MathJax 排版。
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { isHtml, renderMarkdown, sanitizeHtml, typesetMath } from '@/utils/index'

const props = defineProps({
  content: { type: String, default: '' },
  /** 内容确定是 HTML 时跳过 Markdown 解析，直接净化渲染。 */
  forceHtml: { type: Boolean, default: false },
  /** 行内展示（选项、填空标注等），段落不再独占一行。 */
  inline: { type: Boolean, default: false },
})

const root = ref(null)

const html = computed(() => {
  const text = props.content || ''
  if (!text) return ''
  return props.forceHtml || isHtml(text) ? sanitizeHtml(text) : renderMarkdown(text)
})

/** 只有含数学定界符时才调用 MathJax，避免列表页为纯文本发起大量排版。 */
const requestTypeset = async () => {
  if (!props.content || !/[$\\]/.test(props.content)) return
  await nextTick()
  if (root.value) await typesetMath(root.value)
}

onMounted(requestTypeset)
watch(() => props.content, requestTypeset)
</script>

<template>
  <component :is="inline ? 'span' : 'div'" ref="root" class="rich-content"
    :class="inline ? 'rich-content--inline' : ''" v-html="html"></component>
</template>

<style scoped>
.rich-content {
  @apply break-words;
  color: inherit;
}

.rich-content :deep(p) {
  @apply my-1 leading-relaxed;
}

.rich-content :deep(p:first-child) {
  @apply mt-0;
}

.rich-content :deep(p:last-child) {
  @apply mb-0;
}

.rich-content :deep(table) {
  @apply my-3 w-full rounded-xl border border-slate-200 bg-white/50 text-sm dark:border-white/10 dark:bg-white/[0.02];
  display: block;
  overflow-x: auto;
}

.rich-content :deep(th) {
  @apply whitespace-nowrap bg-slate-100 px-3 py-2 text-left font-bold dark:bg-white/5;
}

.rich-content :deep(td) {
  @apply border-t border-slate-100 px-3 py-2 dark:border-white/10;
}

.rich-content :deep(ul) {
  @apply my-2 list-disc pl-5;
}

.rich-content :deep(ol) {
  @apply my-2 list-decimal pl-5;
}

.rich-content :deep(li) {
  @apply my-0.5;
}

.rich-content :deep(a) {
  @apply text-blue-600 hover:underline dark:text-blue-400;
}

.rich-content :deep(img) {
  @apply my-3 inline-block max-w-full rounded-xl border border-slate-200 object-contain dark:border-white/10;
  max-height: 480px;
}

.rich-content :deep(pre) {
  @apply my-3 overflow-x-auto rounded-xl bg-slate-900/95 p-4 text-xs leading-relaxed text-slate-100 dark:bg-black/40;
}

.rich-content :deep(code) {
  @apply rounded bg-slate-100 px-1 py-0.5 font-mono text-[0.9em] dark:bg-white/10;
}

.rich-content :deep(pre code) {
  @apply bg-transparent p-0 dark:bg-transparent;
}

.rich-content :deep(blockquote) {
  @apply my-3 border-l-2 border-slate-300 pl-3 text-gray-500 dark:border-white/20 dark:text-gray-400;
}

.rich-content :deep(h1),
.rich-content :deep(h2),
.rich-content :deep(h3),
.rich-content :deep(h4) {
  @apply my-3 font-bold leading-snug;
}

/* 长公式允许横向滚动，避免撑破卡片或弹窗。 */
.rich-content :deep(mjx-container) {
  max-width: 100%;
}

.rich-content :deep(mjx-container[display='true']) {
  overflow-x: auto;
  overflow-y: hidden;
  padding: 2px 0;
}

.rich-content--inline :deep(p) {
  display: inline;
  margin: 0;
}

.rich-content--inline :deep(mjx-container[display='true']) {
  display: inline-block;
  max-width: 100%;
}
</style>
