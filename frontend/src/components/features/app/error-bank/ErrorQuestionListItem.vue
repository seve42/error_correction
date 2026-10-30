<script setup lang="ts">
/**
 * ErrorQuestionListItem.vue
 * 错题库左侧列表中的单道题目卡片。
 */
import BaseCheckbox from '@/components/base/BaseCheckbox.vue'
import BaseTag from '@/components/base/BaseTag.vue'
import { calculateQuestionPriority, getQuestionSnippet } from '@/utils/index'

const props = defineProps({
  question: { type: Object, required: true },
  index: { type: Number, required: true },
  active: { type: Boolean, default: false },
  selected: { type: Boolean, default: false },
})

const emit = defineEmits(['click', 'toggle-select', 'mark-status', 'edit-note'])

const statusTone = (status) => {
  if (status === '已掌握') return 'emerald'
  if (status === '复习中') return 'amber'
  return 'rose'
}

const questionPriority = (question) => calculateQuestionPriority(question)

const formatDate = (iso) => {
  if (!iso) return '暂无日期'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '暂无日期'
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

const questionText = (question, maxLen = 120) => getQuestionSnippet(question, maxLen, '暂无题干内容')
</script>

<template>
  <article
    class="group relative grid cursor-pointer grid-cols-[auto,1fr,auto] overflow-hidden rounded-xl bg-gray-50/80 transition-all hover:bg-white hover:shadow-sm hover:shadow-black/[0.04] dark:bg-white/[0.03] dark:hover:bg-white/[0.055] dark:hover:shadow-black/20"
    :class="active ? 'bg-gray-100 dark:bg-white/[0.075]' : ''"
    @click="emit('click', question)"
  >
    <div class="flex shrink-0 items-stretch border-r border-gray-200 dark:border-white/[0.06]">
      <div class="flex w-9 items-center justify-center" @click.stop>
        <BaseCheckbox :model-value="selected" @update:model-value="emit('toggle-select', question.id)" />
      </div>
      <div class="flex w-14 flex-col items-center justify-center border-l border-gray-200 dark:border-white/[0.06]">
        <span
          class="flex h-8 w-8 items-center justify-center rounded-full text-sm font-black text-white"
          :class="active ? 'accent-bg' : 'bg-orange-500/80'"
        >
          {{ index }}
        </span>
        <span class="mt-2 text-xs font-bold accent-text">{{ questionPriority(question) }}%</span>
        <span class="text-[10px] text-gray-400 dark:text-[#62666d]">优先级</span>
      </div>
    </div>

    <div class="min-w-0 p-4">
      <div class="mb-2 flex flex-wrap items-center gap-1.5">
        <BaseTag v-if="question.subject" tone="accent">{{ question.subject }}</BaseTag>
        <BaseTag v-for="tag in (question.knowledge_tags || []).slice(0, 2)" :key="tag">{{ tag }}</BaseTag>
        <BaseTag :tone="statusTone(question.review_status || '待复习')">{{ question.review_status || '待复习' }}</BaseTag>
      </div>
      <p class="line-clamp-2 text-sm font-medium leading-relaxed text-gray-700 dark:text-[#d0d6e0]">
        {{ questionText(question, 110) }}
      </p>
      <div class="mt-3 flex items-center gap-3 text-xs text-gray-400 dark:text-[#62666d]">
        <span><i class="fa-regular fa-calendar mr-1"></i>{{ formatDate(question.updated_at || question.created_at) }}</span>
        <span>题型：{{ question.question_type || '未知' }}</span>
      </div>
    </div>

    <div class="flex w-10 flex-col items-center justify-center gap-3 pr-3 text-gray-400 dark:text-[#62666d]">
      <button
        class="transition-colors hover:accent-text"
        title="切换掌握状态"
        @click.stop="emit('mark-status', question, question.review_status === '已掌握' ? '待复习' : '已掌握')"
      >
        <i class="fa-regular" :class="question.review_status === '已掌握' ? 'fa-star text-amber-400' : 'fa-star'"></i>
      </button>
      <button class="transition-colors hover:accent-text" title="记录答案" @click.stop="emit('edit-note', question, 'user_answer')">
        <i class="fa-solid fa-camera"></i>
      </button>
    </div>
  </article>
</template>
