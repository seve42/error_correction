<script setup lang="ts">
/**
 * QuestionList.vue
 * 题目列表容器（选择 + 批量操作）
 */
import { ref, computed, nextTick, watch, onMounted } from 'vue'
import QuestionCard from './QuestionCard.vue'
import BatchSelectBar from '../shared/BatchSelectBar.vue'
import { typesetMath } from '@/utils/index'

const props = defineProps({
  questions: { type: Array, default: () => [] },
  selectedIds: { type: Object, default: () => new Set() },
})

const emit = defineEmits(['toggle-select', 'select-all', 'deselect-all', 'open-image', 'reorder'])

const questionListEl = ref(null)
const questionsBoxEl = ref(null)
let sortable = null

/** 当前列表是否已全部选中，用于全选/取消全选。 */
const allSelected = computed(
  () => props.questions.length > 0 && props.questions.every((q) => props.selectedIds.has(q.uid)),
)

const toggleSelectAll = () => {
  if (allSelected.value) emit('deselect-all')
  else emit('select-all')
}

const triggerTypeset = async () => {
  await nextTick()
  if (questionsBoxEl.value) {
    await typesetMath(questionsBoxEl.value)
  }
}

const initSortable = async () => {
  await nextTick()
  const el = questionListEl.value
  if (!el) return
  const Sortable = window.Sortable
  if (!Sortable) return
  if (sortable) sortable.destroy()
  sortable = Sortable.create(el, {
    animation: 250,
    easing: "cubic-bezier(0.2, 0, 0, 1)",
    handle: '[data-drag-handle="1"]',
    ghostClass: 'opacity-50',
    onEnd: (evt) => {
      const { oldIndex, newIndex } = evt
      if (oldIndex == null || newIndex == null || oldIndex === newIndex) return
      emit('reorder', oldIndex, newIndex)
    },
  })
}

watch(() => props.questions, (val) => {
  if (val && val.length) {
    initSortable()
    triggerTypeset()
  }
}, { flush: 'post', deep: true })

onMounted(() => {
  if (props.questions.length) {
    initSortable()
    triggerTypeset()
  }
})

defineExpose({ initSortable, questionsBoxEl, triggerTypeset })
</script>

<template>
  <div v-if="questions.length" ref="questionsBoxEl" class="relative z-10">
      <!-- 批量选择 -->
      <BatchSelectBar
        class="mt-6"
        :selected-count="selectedIds.size"
        :total-count="questions.length"
        :all-selected="allSelected"
        @toggle-all="toggleSelectAll"
        @clear="emit('deselect-all')"
      />

      <!-- 题目列表 -->
      <div ref="questionListEl" class="relative z-10 mt-3 grid gap-5" id="questionList">
        <QuestionCard
          v-for="q in questions"
          :key="q.uid"
          :question="q"
          :selected="selectedIds.has(q.uid)"
          @toggle="(id) => emit('toggle-select', id)"
          @open-image="(src) => emit('open-image', src)"
        />
      </div>
    </div>
</template>
