<script setup lang="ts">
/**
 * ActionBar.vue
 * 操作栏（导出、保存、取消等按钮）
 */

const props = defineProps({
  splitEnabled: { type: Boolean, default: false },
  exportEnabled: { type: Boolean, default: false },
  splitting: { type: Boolean, default: false },
  splitCompleted: { type: Boolean, default: false },
  uploadMode: { type: String, default: 'exam' },
  workflowMode: { type: String, default: 'manual' },
})

const emit = defineEmits(['split', 'export', 'save-to-db'])
</script>

<template>
  <div class="flex items-center justify-center gap-4">
    <!-- 主操作区 -->
    <div class="flex items-center gap-3">
    <!-- 主操作按钮 -->
    <button
      type="button"
      class="group relative inline-flex h-14 items-center justify-center sm:w-auto w-full disabled:cursor-not-allowed disabled:opacity-50"
      :disabled="!splitEnabled || splitting"
      @click="emit('split')"
    >
      <!-- 按钮本体 -->
      <span class="relative overflow-hidden inline-flex h-full w-full items-center justify-center gap-2 rounded-lg px-8 text-sm font-medium text-white brand-btn transition-colors">
        <template v-if="workflowMode === 'auto'">
          <template v-if="splitting">
            <i class="fa-solid fa-spinner fa-spin text-gray-600 dark:text-white/80"></i>
            <span class="text-gray-900 dark:text-white">{{ uploadMode === 'note' ? '正在自动整理笔记...' : '正在自动处理...' }}</span>
          </template>
          <template v-else-if="splitCompleted">
            <i class="fa-solid fa-check-double text-emerald-500 dark:text-emerald-400"></i>
            <span class="text-gray-900 dark:text-white">{{ uploadMode === 'note' ? '整理已完成' : '解析已完成' }}</span>
          </template>
          <template v-else>
            <i class="fa-solid fa-bolt text-gray-600 dark:text-white/80"></i>
            <span class="text-gray-900 dark:text-white">{{ uploadMode === 'note' ? '开始自动整理笔记' : '开始自动处理到导出阶段' }}</span>
          </template>
        </template>
        <template v-else-if="splitting">
          <i class="fa-solid fa-spinner fa-spin" :class="uploadMode === 'note' ? 'text-emerald-500 dark:text-emerald-400' : 'text-gray-600 dark:text-white/80'"></i>
          <span class="text-gray-900 dark:text-white">{{ uploadMode === 'note' ? '正在整理笔记...' : '正在智能分割...' }}</span>
        </template>
        <template v-else-if="splitCompleted">
          <i class="fa-solid fa-check-double text-emerald-500 dark:text-emerald-400"></i>
          <span class="text-gray-900 dark:text-white">{{ uploadMode === 'note' ? '整理已完成' : '解析已完成' }}</span>
        </template>
        <template v-else>
          <i class="fa-solid text-gray-600 dark:text-white/80" :class="uploadMode === 'note' ? 'fa-book-open' : 'fa-eye'"></i>
          <span class="text-gray-900 dark:text-white">{{ uploadMode === 'note' ? '启动 AI 笔记整理' : '开始识别' }}</span>
        </template>
      </span>
    </button>

    <!-- 导出按钮 -->
    <Transition name="action-btn">
      <button
        v-if="exportEnabled"
        type="button"
        class="group relative inline-flex h-14 items-center justify-center gap-3 rounded-lg brand-btn px-8 text-sm font-medium tracking-widest text-gray-900 dark:text-[#f7f8f8] hover:border-gray-400 dark:hover:border-white/[0.12] hover:bg-gray-100 dark:hover:bg-white/[0.05] disabled:cursor-not-allowed disabled:opacity-30 transition-colors"
        @click="emit('export')"
      >
        <i class="fa-solid fa-file-export transition-transform group-hover:-translate-x-0.5 text-gray-600 dark:text-white/80"></i>
        导出错题本
      </button>
    </Transition>

    <!-- 导入错题库按钮 -->
    <Transition name="action-btn">
      <button
        v-if="exportEnabled"
        type="button"
        class="group relative inline-flex h-14 items-center justify-center gap-3 rounded-lg brand-btn px-8 text-sm font-medium tracking-widest text-gray-900 dark:text-[#f7f8f8] hover:border-gray-400 dark:hover:border-white/[0.12] hover:bg-gray-100 dark:hover:bg-white/[0.05] disabled:cursor-not-allowed disabled:opacity-30 transition-colors"
        @click="emit('save-to-db')"
      >
        <i class="fa-solid fa-database transition-transform group-hover:-translate-y-0.5 text-gray-600 dark:text-white/80"></i>
        导入错题库
      </button>
    </Transition>

    </div>
  </div>
</template>

<style scoped>
.action-btn-enter-active {
  transition: opacity 0.3s ease, transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
}
.action-btn-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}
.action-btn-enter-from {
  opacity: 0;
  transform: translateY(8px) scale(0.95);
}
.action-btn-leave-to {
  opacity: 0;
  transform: translateY(8px) scale(0.95);
}
</style>
