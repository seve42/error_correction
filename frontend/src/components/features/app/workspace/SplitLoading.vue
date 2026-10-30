<script setup lang="ts">
/**
 * SplitLoading.vue
 * 分割/识别加载动画：在原有扫描动画上叠加真实任务进度与日志面板。
 *
 * 后端未推送事件时 progress 为 null，进度条保持原来的 indeterminate 动画，
 * 保证旧后端或 SSE 不可用时页面不报错、不卡死。
 */
import { computed } from 'vue'
import BaseLogo from '@/components/base/BaseLogo.vue'
import TaskLogPanel from './TaskLogPanel.vue'
import type { TaskProgressEvent } from '@/api/task'

const props = withDefaults(
  defineProps<{
    title?: string
    subtitle?: string
    progress?: number | null
    stage?: string
    events?: TaskProgressEvent[]
    running?: boolean
  }>(),
  {
    title: 'AI 正在深度解析',
    subtitle: '正在识别题目结构、提取知识点并构建图谱',
    progress: null,
    stage: '',
    events: () => [],
    running: true,
  },
)

const hasRealProgress = computed(() => {
  if (props.progress === null || props.progress === undefined) return false
  return Number.isFinite(Number(props.progress))
})
const realPercent = computed(() => Math.max(0, Math.min(100, Number(props.progress) || 0)))
</script>

<template>
  <Transition name="fade">
    <div class="loading-overlay">
      <div class="loading-content">
        <!-- 核心扫描动画区 -->
        <div class="scanner-container">
          <!-- 背景光晕 -->
          <div class="scanner-glow"></div>

          <!-- 旋转轨道 -->
          <div class="orbit orbit-1"></div>
          <div class="orbit orbit-2"></div>
          <div class="orbit orbit-3"></div>

          <!-- 核心图标 -->
          <div class="core-icon">
            <BaseLogo size="lg" />
            <!-- 核心脉冲 -->
            <div class="core-pulse"></div>
          </div>

          <!-- 扫描线 -->
          <div class="scan-line"></div>
        </div>

        <!-- 文本引导 -->
        <div class="text-container">
          <h3 class="status-title text-slate-900 dark:text-white transition-colors">{{ title }}</h3>
          <p class="status-subtitle text-slate-500 dark:text-slate-400 transition-colors">{{ subtitle }}</p>
          <p v-if="stage" class="stage-text text-slate-500 dark:text-slate-400 transition-colors">当前阶段：{{ stage }}</p>

          <!-- 真实进度优先，拿不到进度时保留原模拟动画 -->
          <div class="progress-container">
            <div
              v-if="hasRealProgress"
              class="progress-bar-determinate"
              :style="{ width: `${realPercent}%` }"
            ></div>
            <div v-else class="progress-bar">
              <div class="progress-glow"></div>
            </div>
          </div>
        </div>

        <!-- 实时日志：任务运行中默认展开，可折叠；日志区自身滚动，整块高度自适应 -->
        <div v-if="running || events.length" class="log-container">
          <TaskLogPanel
            :events="events"
            :running="running"
            :progress="progress ?? null"
            :stage="stage"
            default-open
          />
        </div>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.loading-overlay {
  position: absolute;
  inset: 0;
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
}

.loading-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2rem;
  /* 日志面板展开后整体可能超出视口，改为整块滚动、日志区内部再滚动 */
  width: min(760px, 92vw);
  max-height: 100%;
  overflow-y: auto;
  padding: 1.5rem 0;
}

/* --- 核心动画容器 --- */
.scanner-container {
  position: relative;
  width: 160px;
  height: 160px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.scanner-glow {
  position: absolute;
  width: 120%;
  height: 120%;
  background: radial-gradient(circle, rgb(var(--accent-rgb) / 0.15) 0%, transparent 70%);
  filter: blur(20px);
  animation: breathe 4s ease-in-out infinite;
}

/* 旋转轨道 */
.orbit {
  position: absolute;
  border-radius: 50%;
  border: 1.5px solid rgb(var(--accent-rgb) / 0.1);
  border-top-color: rgb(var(--accent-rgb) / 0.5);
}

.orbit-1 {
  width: 100%;
  height: 100%;
  animation: rotate 8s linear infinite;
}

.orbit-2 {
  width: 75%;
  height: 75%;
  animation: rotate 5s linear infinite reverse;
  border-top-color: rgb(var(--accent-hover-rgb) / 0.5);
}

.orbit-3 {
  width: 50%;
  height: 50%;
  animation: rotate 3s linear infinite;
  border-top-color: rgb(var(--accent-rgb) / 0.5);
}

/* 核心图标 */
.core-icon {
  position: relative;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: center;
}

.core-pulse {
  position: absolute;
  inset: -4px;
  border-radius: 0.75rem;
  background: rgb(var(--accent-rgb) / 0.2);
  animation: pulse 2s ease-out infinite;
}

/* 扫描线 */
.scan-line {
  position: absolute;
  left: 0;
  right: 0;
  height: 2px;
  background: linear-gradient(90deg, transparent, rgb(var(--accent-rgb) / 0.8), transparent);
  box-shadow: 0 0 15px rgb(var(--accent-rgb) / 0.5);
  z-index: 5;
  animation: scan 3s ease-in-out infinite;
}

/* --- 文本容器 --- */
.text-container {
  text-align: center;
  max-width: 300px;
}

.status-title {
  font-size: 1.125rem;
  font-weight: 900;
  letter-spacing: 0.05em;
  margin-bottom: 0.5rem;
}

.status-subtitle {
  font-size: 0.75rem;
  font-weight: 600;
  line-height: 1.5;
}

/* --- 进度条 --- */
.progress-container {
  margin-top: 1.5rem;
  width: 100%;
  height: 4px;
  background: rgb(var(--accent-rgb) / 0.05);
  border-radius: 999px;
  overflow: hidden;
}

.stage-text {
  margin-top: 0.75rem;
  font-size: 0.7rem;
  font-weight: 600;
  letter-spacing: 0.02em;
}

/* 真实进度：宽度随事件 progress 变化，不再跑模拟动画 */
.progress-bar-determinate {
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, rgb(var(--accent-rgb)), rgb(var(--accent-hover-rgb)));
  transition: width 0.3s ease;
}

.log-container {
  width: 100%;
}

.progress-bar {
  width: 40%;
  height: 100%;
  background: linear-gradient(90deg, rgb(var(--accent-rgb)), rgb(var(--accent-hover-rgb)));
  border-radius: 999px;
  position: relative;
  animation: progress-move 2.5s ease-in-out infinite;
}

.progress-glow {
  position: absolute;
  top: 0;
  right: 0;
  height: 100%;
  width: 20px;
  background: white;
  filter: blur(4px);
  opacity: 0.5;
}

/* --- 关键帧 --- */
@keyframes rotate {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

@keyframes breathe {
  0%, 100% { opacity: 0.5; transform: scale(1); }
  50% { opacity: 1; transform: scale(1.1); }
}

@keyframes pulse {
  0% { transform: scale(0.95); opacity: 0.8; }
  100% { transform: scale(1.4); opacity: 0; }
}

@keyframes scan {
  0%, 100% { top: 0; opacity: 0; }
  20%, 80% { opacity: 1; }
  50% { top: 100%; }
}

@keyframes progress-move {
  0% { transform: translateX(-100%); }
  50% { transform: translateX(150%); }
  100% { transform: translateX(-100%); }
}

/* --- Transition --- */
.fade-enter-active, .fade-leave-active {
  transition: opacity 0.4s ease;
}
.fade-enter-from, .fade-leave-to {
  opacity: 0;
}
</style>
