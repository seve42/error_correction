<script setup lang="ts">
/**
 * QuestionItem.vue
 * 题目条目（分割结果列表项）
 */
import { getQuestionSnippet } from '@/utils/index'
import BaseCard from '@/components/base/BaseCard.vue'
import RichContent from '@/components/features/app/shared/RichContent.vue'

const props = defineProps({
  question: { type: Object, required: true },
  selected: { type: Boolean, default: false },
  selectable: { type: Boolean, default: false },
  showStatus: { type: Boolean, default: false },
  maxTags: { type: Number, default: 0 },
})

const emit = defineEmits(['click', 'toggle-select'])

const summary = () => getQuestionSnippet(props.question)

const tags = () => {
  const all = props.question.knowledge_tags || []
  return props.maxTags ? all.slice(0, props.maxTags) : all
}

const statusClass = (status) => {
  if (status === '已掌握') return 'bg-emerald-500/10 text-emerald-600 dark:bg-emerald-500/20 dark:text-emerald-400 border border-emerald-200/50 dark:border-emerald-500/30'
  if (status === '复习中') return 'bg-amber-500/10 text-amber-600 dark:bg-amber-500/20 dark:text-amber-400 border border-amber-200/50 dark:border-amber-500/30'
  return 'bg-rose-500/10 text-rose-600 dark:bg-rose-500/20 dark:text-rose-400 border border-rose-200/50 dark:border-rose-500/30'
}

const statusColor = (status) => {
  if (status === '已掌握') return 'text-emerald-500 dark:text-emerald-400'
  if (status === '复习中') return 'text-amber-500 dark:text-amber-400'
  return 'text-rose-500 dark:text-rose-400'
}

const statusIcon = (status) => {
  if (status === '已掌握') return 'fa-circle-check'
  if (status === '复习中') return 'fa-spinner'
  return 'fa-clock'
}
</script>

<template>
  <BaseCard
    @click="selectable ? emit('toggle-select', question.id) : emit('click', question)"
    class="group cursor-pointer"
    :class="{ 'accent-border accent-bg-muted': selected }"
  >
    <div class="flex items-start gap-4">
      <!-- 选择复选框 -->
      <div v-if="selectable" class="flex shrink-0 items-center pt-1" @click.stop="emit('toggle-select', question.id)">
        <div class="flex h-5 w-5 items-center justify-center rounded-lg border-2 transition-all"
          :class="selected ? 'accent-border accent-bg text-white' : 'border-gray-200 dark:border-white/[0.15] bg-white dark:bg-transparent'">
          <i v-if="selected" class="fa-solid fa-check text-xs"></i>
        </div>
      </div>

      <div class="min-w-0 flex-1">
        <!-- 标签行 -->
        <div class="mb-2 flex flex-wrap items-center gap-2">
          <!-- 复习状态图标 -->
          <i v-if="showStatus" class="fa-solid text-sm" :class="[statusIcon(question.review_status), statusColor(question.review_status)]"></i>
          <span class="rounded-full bg-gray-100 dark:bg-white/[0.04] px-2 py-0.5 text-xs font-medium text-gray-500 dark:text-[#8a8f98] transition-colors">{{ question.question_type }}</span>
          <span v-if="question.subject" class="rounded-full accent-bg-soft px-2 py-0.5 text-xs font-medium accent-text transition-colors">{{ question.subject }}</span>
          <span v-for="tag in tags()" :key="tag" class="rounded-full border border-gray-200 dark:border-white/[0.06] px-2 py-0.5 text-xs font-medium text-gray-500 dark:text-[#8a8f98] transition-colors">{{ tag }}</span>
        </div>

        <!-- 摘要 -->
        <p class="line-clamp-2 text-sm font-medium leading-relaxed text-gray-700 dark:text-[#d0d6e0] group-hover:text-gray-900 dark:group-hover:text-[#f7f8f8] transition-colors">{{ summary() }}</p>

        <!-- 题目图片 -->
        <div v-if="question.content_json?.some(b => b.block_type === 'image' && b.content)" class="mt-3 flex flex-wrap gap-2">
          <img
            v-for="(b, i) in question.content_json.filter(b => b.block_type === 'image' && b.content)"
            :key="i"
            :src="b.content"
            class="max-h-32 rounded-lg border border-gray-200 dark:border-white/[0.06] object-contain transition-colors"
            @click.stop
          />
        </div>

        <!-- 选择题选项 -->
        <div v-if="question.options_json?.length" class="mt-3 grid grid-cols-2 gap-1.5">
          <div
            v-for="(opt, idx) in question.options_json"
            :key="idx"
            class="flex items-start gap-2 rounded-md border border-gray-200 dark:border-white/[0.06] bg-gray-50 dark:bg-white/[0.02] px-3 py-1.5 text-xs font-medium text-gray-600 dark:text-[#8a8f98] transition-colors"
          >
            <span class="shrink-0 text-gray-400 dark:text-[#62666d] transition-colors">{{ String.fromCharCode(65 + idx) }}.</span>
            <RichContent class="line-clamp-1 min-w-0" :content="String(opt).replace(/^[A-Da-d][.、．]\s*/, '')" />
          </div>
        </div>

        <!-- 扩展插槽（内联编辑等） -->
        <slot name="extra" :question="question" />
      </div>

      <!-- 右侧操作区插槽 -->
      <div v-if="$slots.actions" class="flex shrink-0 gap-2" @click.stop>
        <slot name="actions" :question="question" />
      </div>
    </div>
  </BaseCard>
</template>
