<script setup lang="ts">
/**
 * FileUploader.vue
 * 文件上传拖拽区
 */
import { ref } from 'vue'
import ModelProviderSelect from '@/components/features/app/workspace/ModelProviderSelect.vue'

const props = defineProps({
  pendingFiles: { type: Array, default: () => [] },
  fileProgress: { type: Object, default: () => ({}) },
  waitingKeys: { type: Object, default: () => new Set() },
  uploadBusy: { type: Boolean, default: false },
  uploadReady: { type: Boolean, default: false },
  splitting: { type: Boolean, default: false },
  splitCompleted: { type: Boolean, default: false },
  expand: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  modelOptionsData: { type: Object, default: null },
  selectedLlmOptionId: { type: String, default: '' },
  noModels: { type: Boolean, default: false },
  statusLoading: { type: Boolean, default: false },
  // 聚合上传进度：几百张图分批并发时，用户需要看到整体位置而不是单个文件的细粒度进度
  uploadPercent: { type: Number, default: 0 },
  uploadDone: { type: Number, default: 0 },
  uploadTotal: { type: Number, default: 0 },
})

const emit = defineEmits(['upload', 'remove-file', 'update:selected-llm-option-id'])

const fileInputEl = ref(null)
const uploadHover = ref(false)

const onFileInput = (e) => {
  if (props.disabled) return
  emit('upload', e.target.files)
  e.target.value = ''
}

const onDrop = (e) => {
  e.preventDefault()
  uploadHover.value = false
  if (props.disabled) return
  emit('upload', e.dataTransfer.files)
}

const onClickZone = () => {
  if (props.disabled) return
  fileInputEl.value?.click()
}
</script>

<template>
  <div :class="{ 'flex flex-col flex-1': expand }">
    <div
      class="group relative flex flex-col items-center justify-center rounded-xl border transition-colors"
      :class="[
        uploadHover
          ? 'accent-border bg-gray-100 dark:bg-white/[0.04]'
          : 'border-dashed border-gray-300 dark:border-white/[0.08] hover:border-gray-400 dark:hover:border-white/[0.12] hover:bg-gray-50 dark:hover:bg-white/[0.02]',
        expand ? 'py-14' : 'py-8',
        disabled ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer'
      ]"
      @dragenter.prevent="uploadHover = true"
      @dragover.prevent="uploadHover = true"
      @dragleave.prevent="uploadHover = false"
      @drop="onDrop"
      @click="onClickZone"
      @keydown.enter.prevent="onClickZone"
      @keydown.space.prevent="onClickZone"
      role="button"
      tabindex="0"
    >
      <div class="flex flex-col items-center gap-3">
        <i class="fa-solid fa-cloud-arrow-up text-lg text-gray-400 dark:text-[#62666d] group-hover:text-gray-500 dark:group-hover:text-[#8a8f98] transition-colors"></i>

        <div class="text-center">
          <p v-if="disabled" class="text-sm text-gray-500 dark:text-[#62666d]">
            请先在 <span class="accent-text">系统设置</span> 中配置模型
          </p>
          <p v-else class="text-sm text-gray-500 dark:text-[#8a8f98]">
            拖拽文件到此处或 <span class="accent-text cursor-pointer">浏览文件</span>
          </p>
          <p class="mt-1 text-xs text-gray-400 dark:text-[#62666d]">
            支持PDF/PNG/JPG/BMP/TIF等各种图片格式
          </p>
          <p class="mt-1 text-xs text-gray-400 dark:text-[#62666d]">
            单文件≤50MB&1000页 · 单图片≤10MB · 批量自动分批上传
          </p>
        </div>

        <div class="mt-1 flex items-center gap-3" @click.stop @keydown.stop>
          <span class="text-xs font-medium text-gray-500 dark:text-[#8a8f98]">选择模型</span>
          <ModelProviderSelect
            compact
            :model-value="selectedLlmOptionId"
            :model-options-data="modelOptionsData"
            :disabled="splitting || splitCompleted"
            :no-models="noModels"
            :status-loading="statusLoading"
            @update:model-value="(value) => emit('update:selected-llm-option-id', value)"
          />
        </div>
      </div>

      <input
        ref="fileInputEl"
        type="file"
        class="hidden"
        accept=".pdf,.png,.jpg,.jpeg,.bmp,.tiff,.webp"
        multiple
        @change="onFileInput"
      />
    </div>

    <!-- 总体上传进度：所有文件平均值，几十批并发时仍能看出整体完成度 -->
    <div v-if="uploadTotal > 0" class="mt-3 w-full">
      <div class="flex items-center justify-between text-[11px] text-gray-500 dark:text-[#8a8f98]">
        <span>已上传 {{ uploadDone }}/{{ uploadTotal }} · {{ uploadPercent }}%</span>
        <span>{{ uploadBusy ? '上传中…' : '上传完成' }}</span>
      </div>
      <div class="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-gray-200/80 dark:bg-white/[0.08]">
        <div
          class="h-full rounded-full accent-bg transition-[width] duration-300 ease-out"
          :style="{ width: uploadPercent + '%' }"
        ></div>
      </div>
    </div>
  </div>
</template>
