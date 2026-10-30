<script setup lang="ts">
/**
 * UploadStage.vue
 * 工作台第一页：上传与分析（模式切换 + 流程步骤 + 文件上传）
 */
import { computed } from 'vue'
import BaseSegmented from '@/components/base/BaseSegmented.vue'
import BaseCheckbox from '@/components/base/BaseCheckbox.vue'
import StatusBar from '@/components/features/app/workspace/StatusBar.vue'
import FileUploader from '@/components/features/app/workspace/FileUploader.vue'
import FileList from '@/components/features/app/workspace/FileList.vue'
import ActionBar from '@/components/features/app/workspace/ActionBar.vue'

const props = defineProps({
  uploadMode: String,
  workflowMode: { type: String, default: 'manual' },
  // 生成标准答案解析：导入流程开始前决定，作为分割后的独立一步执行
  autoGenerateAnswers: { type: Boolean, default: true },
  // StatusBar
  statusLoading: Boolean,
  statusError: [String, null],
  statusPills: Array,
  modelOptionsData: Object,
  selectedLlmOptionId: String,
  hasConfiguredModel: Boolean,
  splitting: Boolean,
  splitCompleted: Boolean,
  // FileUploader / FileList
  pendingFiles: Array,
  fileProgress: Object,
  waitingKeys: Object,
  uploadBusy: Boolean,
  uploadReady: Boolean,
  // 聚合上传进度：几百张图分批并发时，用户需要看到"传到第几个/总进度"
  uploadPercent: Number,
  uploadDone: Number,
  uploadTotal: Number,
  // ActionBar
  splitEnabled: Boolean,
})

const emit = defineEmits([
  'update:upload-mode',
  'update:workflow-mode',
  'update:auto-generate-answers',
  'update:selected-llm-option-id',
  'upload',
  'remove-file',
  'split',
])

// 上传模式选项：试卷走分割纠错流程，笔记走整理保存流程。
const uploadModeOptions = [
  { value: 'exam', label: '试卷分割', icon: 'fa-file-lines' },
  { value: 'note', label: '笔记整理', icon: 'fa-book-open' },
]

const workflowModeOptions = [
  { value: 'manual', label: '流程模式', icon: 'fa-list-check' },
  { value: 'auto', label: '自动模式', icon: 'fa-bolt' },
]

// 流程步骤卡片：试卷模式把「生成解析」单独列一步，与左侧步骤条一致。
const flowSteps = computed(() => {
  if (props.uploadMode === 'note') {
    return [
      { icon: 'fa-cloud-arrow-up', label: '上传笔记' },
      { icon: 'fa-eye', label: 'AI 识别' },
      { icon: 'fa-wand-magic-sparkles', label: '智能整理' },
      { icon: 'fa-bookmark', label: '保存笔记' },
    ]
  }
  return [
    { icon: 'fa-cloud-arrow-up', label: '上传文件' },
    { icon: 'fa-eye', label: 'AI 识别' },
    { icon: 'fa-scissors', label: '分割纠错' },
    { icon: 'fa-lightbulb', label: '生成解析' },
    { icon: 'fa-file-export', label: '导出归档' },
  ]
})
</script>

<template>
  <!-- 工具栏：模式切换 + 引擎状态 -->
  <div class="flex flex-wrap items-center gap-3">
    <!-- 模式切换 -->
    <BaseSegmented
      :model-value="uploadMode"
      :options="uploadModeOptions"
      @update:model-value="(value) => emit('update:upload-mode', value)"
    />

    <div class="h-4 w-px bg-gray-300 dark:bg-white/[0.08] transition-colors"></div>

    <BaseSegmented
      :model-value="workflowMode"
      :options="workflowModeOptions"
      @update:model-value="(value) => emit('update:workflow-mode', value)"
    />

    <!-- 生成解析：仅试卷模式，默认开启，导入流程开始时即确定 -->
    <BaseCheckbox v-if="uploadMode !== 'note'" class="ml-1"
      :model-value="autoGenerateAnswers"
      label="生成标准答案解析"
      description="分割完成后自动为全部题目生成标准答案与解析"
      @update:model-value="(value) => emit('update:auto-generate-answers', value)" />

    <!-- 引擎状态 -->
    <div class="ml-auto">
      <StatusBar :status-loading="statusLoading" :status-error="statusError" :status-pills="statusPills" />
    </div>
  </div>

  <!-- 上传区 -->
  <div class="flex flex-1 min-h-0 flex-col items-center justify-center gap-6 overflow-y-auto custom-scrollbar py-8">
    <!-- 引导信息 -->
    <div class="w-full max-w-2xl text-center transition-colors">
      <h2 class="mb-3 text-3xl font-black leading-tight text-gray-900 dark:text-[#f7f8f8] md:text-4xl">
        <template v-if="uploadMode === 'note'">
          智能笔记整理<span class="accent-text">工作台</span>
        </template>
        <template v-else>
          智能录入与分析<span class="accent-text">工作台</span>
        </template>
      </h2>
      <p class="text-sm leading-relaxed text-gray-500 dark:text-[#62666d] md:whitespace-nowrap">
        {{ uploadMode === 'note'
          ? '支持拍照或扫描件，AI 将自动识别内容并整理为结构化笔记'
          : '支持 PDF 和图片格式，上传后自动识别手写与红笔批改，并完成题目分割和知识点标注'
        }}
      </p>
    </div>

    <!-- 流程步骤卡片 -->
    <div class="grid w-full max-w-2xl gap-4 transition-colors"
      :class="uploadMode === 'note' ? 'grid-cols-4' : 'grid-cols-5'">
      <div v-for="item in flowSteps" :key="item.label"
        class="flex flex-col items-center gap-3 rounded-lg brand-btn p-4 text-center">
        <div class="flex h-12 w-12 items-center justify-center rounded-lg bg-gray-100 dark:bg-white/[0.04]">
          <i class="fa-solid text-xl accent-text" :class="item.icon"></i>
        </div>
        <span class="text-sm text-gray-500 dark:text-[#8a8f98]">{{ item.label }}</span>
      </div>
    </div>

    <!-- 拖拽上传 -->
    <FileUploader :pending-files="pendingFiles" :file-progress="fileProgress" :waiting-keys="waitingKeys"
      :upload-busy="uploadBusy" :upload-ready="uploadReady" :upload-percent="uploadPercent"
      :upload-done="uploadDone" :upload-total="uploadTotal"
      :splitting="splitting" :split-completed="splitCompleted"
      :expand="false" :disabled="!hasConfiguredModel"
      :model-options-data="modelOptionsData" :selected-llm-option-id="selectedLlmOptionId"
      :status-loading="statusLoading" :no-models="!hasConfiguredModel"
      @update:selected-llm-option-id="(v) => emit('update:selected-llm-option-id', v)"
      @upload="(f) => emit('upload', f)"
      @remove-file="(f) => emit('remove-file', f)" class="w-full max-w-2xl" />

    <!-- 已上传文件 -->
    <FileList class="w-full max-w-2xl" :pending-files="pendingFiles" :file-progress="fileProgress"
      :waiting-keys="waitingKeys" :upload-busy="uploadBusy" :upload-ready="uploadReady" :splitting="splitting"
      :upload-percent="uploadPercent" :upload-done="uploadDone" :upload-total="uploadTotal"
      :split-completed="splitCompleted" @remove-file="(f) => emit('remove-file', f)" />

    <!-- 操作按钮 -->
    <ActionBar class="mt-4" :split-enabled="splitEnabled" :export-enabled="false"
      :splitting="splitting" :split-completed="splitCompleted" :upload-mode="uploadMode"
      :workflow-mode="workflowMode"
      @split="emit('split')" />
  </div>
</template>

<style scoped></style>
