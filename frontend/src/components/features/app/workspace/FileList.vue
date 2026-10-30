<script setup lang="ts">
/**
 * FileList.vue
 * 已上传文件列表
 */
defineProps({
  pendingFiles: { type: Array, default: () => [] },
  fileProgress: { type: Object, default: () => ({}) },
  waitingKeys: { type: Object, default: () => new Set() },
  uploadBusy: { type: Boolean, default: false },
  uploadReady: { type: Boolean, default: false },
  splitting: { type: Boolean, default: false },
  splitCompleted: { type: Boolean, default: false },
  // 聚合进度：文件列表是最直观看到"几百张图传到哪了"的位置
  uploadPercent: { type: Number, default: 0 },
  uploadDone: { type: Number, default: 0 },
  uploadTotal: { type: Number, default: 0 },
})
const emit = defineEmits(['remove-file'])
</script>

<template>
  <Transition
    enter-active-class="transition-all duration-500 ease-out"
    enter-from-class="opacity-0 -translate-y-4 max-h-0"
    enter-to-class="opacity-100 translate-y-0 max-h-48"
    leave-active-class="transition-all duration-300 ease-in"
    leave-from-class="opacity-100 translate-y-0 max-h-48"
    leave-to-class="opacity-0 -translate-y-4 max-h-0"
  >
    <div v-if="pendingFiles.length" class="relative z-20 w-full overflow-hidden shrink-0">
      <!-- 总体上传进度，先给整体感知再逐文件查看 -->
      <div v-if="uploadTotal > 0" class="mb-2 flex items-center gap-3">
        <span class="shrink-0 text-[11px] text-gray-500 dark:text-[#8a8f98] transition-colors">
          已上传 <span class="font-semibold text-gray-700 dark:text-[#c9cdd4]">{{ uploadDone }}</span>/{{ uploadTotal }} · {{ uploadPercent }}%
        </span>
        <div class="h-1 min-w-0 flex-1 overflow-hidden rounded-full bg-gray-200/80 dark:bg-white/[0.08]">
          <div
            class="h-full rounded-full accent-bg transition-[width] duration-500 ease-out"
            :style="{ width: uploadPercent + '%' }"
          ></div>
        </div>
      </div>
      <div class="flex max-h-40 flex-wrap items-center gap-2 overflow-y-auto pr-1 custom-scrollbar">
        <div
          v-for="item in pendingFiles"
          :key="item.key"
          class="relative flex items-center gap-2.5 overflow-hidden rounded-lg border border-gray-200 dark:border-white/[0.08] bg-white dark:bg-white/[0.02] px-3 py-2 transition-colors"
        >
          <!-- 进度条背景 -->
          <div
            class="absolute inset-0 -z-10 accent-bg-soft transition-[width] duration-500 ease-out"
            :style="{ width: `${fileProgress[item.key] ?? 0}%` }"
          ></div>

          <i
            class="fa-solid text-sm shrink-0 transition-colors"
            :class="
              item.file.name.toLowerCase().endsWith('.pdf')
                ? 'fa-file-pdf text-rose-500 dark:text-rose-400'
                : 'fa-file-image accent-text'
            "
          ></i>
          <div class="flex flex-col min-w-0 pr-4 text-left">
            <span class="truncate text-xs font-medium text-gray-900 dark:text-[#f7f8f8] transition-colors" :title="item.file.name">
              {{ item.file.name }}
            </span>
            <span class="text-[10px] text-gray-500 dark:text-[#62666d] transition-colors">
              {{ waitingKeys.has(item.key) && uploadBusy ? '等待中' : `${Math.round(fileProgress[item.key] ?? 0)}%` }}
            </span>
          </div>

          <button
            type="button"
            class="absolute right-1.5 top-1.5 flex h-4 w-4 items-center justify-center rounded-md text-gray-400 dark:text-[#62666d] transition-colors hover:text-rose-500 dark:hover:text-rose-400"
            :disabled="splitting || splitCompleted"
            @click.stop="() => emit('remove-file', item.key)"
          >
            <i class="fa-solid fa-xmark text-[8px]"></i>
          </button>
        </div>
      </div>
    </div>
  </Transition>
</template>
