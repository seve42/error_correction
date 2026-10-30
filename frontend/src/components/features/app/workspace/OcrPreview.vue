<script setup lang="ts">
/**
 * OcrPreview.vue
 * 视觉 OCR 结果预览 — 页面图 + 可选旧数据 bbox 叠加 + 识别文本列表
 *
 * 新的视觉 OCR 不返回可靠 bbox，因此默认只展示页面图和按 label 着色的文本列表；
 * 旧数据若带有 bbox，仍按原逻辑叠加框选，保持向后兼容。
 */
import { ref, computed, onBeforeUnmount, watch } from 'vue'
import SplitLoading from './SplitLoading.vue'

const props = defineProps({
  pages: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  previewUrl: { type: String, default: '' },
  // 当前选择的视觉模型名，用于加载提示；为空时使用通用文案
  modelName: { type: String, default: '' },
})

// 加载标题带上当前模型名，避免绑定具体品牌。
const loadingTitle = computed(() => props.modelName ? `${props.modelName} 正在识别…` : '视觉识别中…')

type ImageSize = { w: number; h: number; ox: number; oy: number }

// 当前页索引；分页与图片尺寸计算都依赖它。
const currentPage = ref(0)

// 每张图片在 object-contain 下的实际渲染区域（含偏移）
const imageSizes = ref<Record<number, ImageSize>>({})
const imgRef = ref<HTMLImageElement | null>(null)

function calcImageSize(img: HTMLImageElement, pageIndex: number) {
  if (!img) return
  const cw = img.clientWidth, ch = img.clientHeight
  const nw = img.naturalWidth, nh = img.naturalHeight
  if (!nw || !nh) return
  const scale = Math.min(cw / nw, ch / nh)
  const rw = nw * scale, rh = nh * scale
  const ox = (cw - rw) / 2, oy = (ch - rh) / 2
  imageSizes.value = { ...imageSizes.value, [pageIndex]: { w: rw, h: rh, ox, oy } }
}

function onImageLoad(pageIndex: number, event: Event) {
  calcImageSize(event.target as HTMLImageElement, pageIndex)
}

// 浏览器缩放/窗口大小变化时重新计算
const resizeObserver = new ResizeObserver(() => {
  if (imgRef.value) calcImageSize(imgRef.value, currentPage.value)
})
onBeforeUnmount(() => resizeObserver.disconnect())

type LabelColor = { border: string; bg: string; tag: string }

// 新视觉 OCR 的语义标签配色；同时兼容旧 PaddleOCR 的标签。
const labelColors: Record<string, LabelColor> = {
  question: { border: 'rgba(59,130,246,0.6)', bg: 'rgba(59,130,246,0.15)', tag: 'rgba(59,130,246,0.85)' },
  handwriting_red: { border: 'rgba(239,68,68,0.6)', bg: 'rgba(239,68,68,0.15)', tag: 'rgba(239,68,68,0.88)' },
  handwriting_black: { border: 'rgba(51,65,85,0.6)', bg: 'rgba(51,65,85,0.15)', tag: 'rgba(51,65,85,0.9)' },
  grading_red: { border: 'rgba(244,63,94,0.6)', bg: 'rgba(244,63,94,0.15)', tag: 'rgba(244,63,94,0.88)' },
  text: { border: 'rgba(59,130,246,0.6)', bg: 'rgba(59,130,246,0.15)', tag: 'rgba(59,130,246,0.85)' },
  title: { border: 'rgba(168,85,247,0.6)', bg: 'rgba(168,85,247,0.15)', tag: 'rgba(168,85,247,0.85)' },
  formula: { border: 'rgba(245,158,11,0.6)', bg: 'rgba(245,158,11,0.15)', tag: 'rgba(245,158,11,0.85)' },
  image: { border: 'rgba(16,185,129,0.6)', bg: 'rgba(16,185,129,0.15)', tag: 'rgba(16,185,129,0.85)' },
  chart: { border: 'rgba(16,185,129,0.6)', bg: 'rgba(16,185,129,0.15)', tag: 'rgba(16,185,129,0.85)' },
  table: { border: 'rgba(236,72,153,0.6)', bg: 'rgba(236,72,153,0.15)', tag: 'rgba(236,72,153,0.85)' },
  aside_text: { border: 'rgba(148,163,184,0.4)', bg: 'rgba(148,163,184,0.08)', tag: 'rgba(148,163,184,0.7)' },
  header: { border: 'rgba(148,163,184,0.4)', bg: 'rgba(148,163,184,0.08)', tag: 'rgba(148,163,184,0.7)' },
  footer: { border: 'rgba(148,163,184,0.4)', bg: 'rgba(148,163,184,0.08)', tag: 'rgba(148,163,184,0.7)' },
}
const defaultColor: LabelColor = { border: 'rgba(148,163,184,0.5)', bg: 'rgba(148,163,184,0.1)', tag: 'rgba(148,163,184,0.75)' }

function getColor(label?: string): LabelColor {
  return labelColors[String(label || '')] || defaultColor
}

// 视觉 OCR 的 bbox 通常为 null，只有旧数据才叠加框选。
function hasBbox(block: any): boolean {
  return Array.isArray(block?.bbox)
    && block.bbox.length === 4
    && block.bbox.every((n: unknown) => Number.isFinite(Number(n)))
}

function boxStyle(block: any, page: any, pageIndex: number) {
  const size = imageSizes.value[pageIndex]
  const pageWidth = Number(page?.page_width || 0)
  const pageHeight = Number(page?.page_height || 0)
  if (!size || !pageWidth || !pageHeight) return null
  const scaleX = size.w / pageWidth
  const scaleY = size.h / pageHeight
  const [x1, y1, x2, y2] = block.bbox
  const color = getColor(block.label)
  return {
    position: 'absolute' as const,
    left: `${size.ox + x1 * scaleX}px`,
    top: `${size.oy + y1 * scaleY}px`,
    width: `${(x2 - x1) * scaleX}px`,
    height: `${(y2 - y1) * scaleY}px`,
    border: `1.5px solid ${color.border}`,
    backgroundColor: color.bg,
    pointerEvents: 'none' as const,
  }
}

function tagStyle(block: any) {
  const color = getColor(block.label)
  return {
    position: 'absolute' as const,
    top: '-1px',
    left: '-1px',
    backgroundColor: color.tag,
    color: '#fff',
    fontSize: '9px',
    lineHeight: '1',
    padding: '2px 4px',
    borderRadius: '0 0 3px 0',
    whiteSpace: 'nowrap' as const,
  }
}

function listTagStyle(block: any) {
  return { backgroundColor: getColor(block.label).tag }
}

// 分页
const currentPageData = computed(() => props.pages[currentPage.value] || null)
const pageBlocks = computed<any[]>(() => currentPageData.value?.blocks || [])

// 重新识别后页数可能变少，避免当前页越界。
watch(() => props.pages.length, (len) => {
  if (!len) currentPage.value = 0
  else if (currentPage.value >= len) currentPage.value = len - 1
})
</script>

<template>
  <div class="flex flex-col h-full">
    <!-- 加载中 -->
    <div v-if="loading" class="flex-1 min-h-0 relative">
      <img v-if="previewUrl" :src="previewUrl" class="absolute inset-0 w-full h-full object-contain opacity-10 blur-sm" alt="" />
      <SplitLoading :title="loadingTitle" subtitle="正在解析页面上的印刷题目、手写作答与红笔批改" />
    </div>

    <!-- 无数据 -->
    <div v-else-if="!pages.length" class="flex-1 flex items-center justify-center">
      <p class="text-sm text-gray-500 dark:text-[#62666d] transition-colors">暂无识别数据</p>
    </div>

    <!-- 预览（单张 + 分页） -->
    <template v-else>
      <div class="flex-1 min-h-0 relative overflow-hidden">
        <img
          v-if="currentPageData?.image_url"
          ref="imgRef"
          :src="currentPageData.image_url"
          class="block w-full h-full object-contain"
          alt=""
          @load="(e) => { onImageLoad(currentPage, e); resizeObserver.disconnect(); resizeObserver.observe(e.target) }"
        />
        <div v-else class="flex h-full items-center justify-center">
          <p class="text-sm text-gray-500 dark:text-[#62666d] transition-colors">暂无页面图</p>
        </div>
        <!-- bbox 叠加层（仅旧数据） -->
        <template v-for="(block, bi) in pageBlocks" :key="'box-' + bi">
          <div v-if="hasBbox(block)" :style="boxStyle(block, currentPageData, currentPage) || { display: 'none' }">
            <span :style="tagStyle(block)">{{ block.label }}</span>
          </div>
        </template>
      </div>

      <!-- 识别文本列表 -->
      <div v-if="pageBlocks.length" class="mt-2 shrink-0 border-t border-gray-200/70 pt-2 dark:border-white/[0.06]">
        <div class="mb-1.5 flex items-center justify-between text-xs text-gray-500 dark:text-[#8a8f98] transition-colors">
          <span class="font-medium">识别结果（{{ pageBlocks.length }} 块）</span>
          <span v-if="pages.length > 1">第 {{ currentPage + 1 }} / {{ pages.length }} 页</span>
        </div>
        <div class="max-h-40 space-y-1 overflow-y-auto pr-1 custom-scrollbar">
          <div v-for="(block, bi) in pageBlocks" :key="'text-' + bi" class="flex items-start gap-2 text-xs leading-relaxed">
            <span class="mt-0.5 shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold text-white" :style="listTagStyle(block)">
              {{ block.label }}
            </span>
            <span class="min-w-0 flex-1 whitespace-pre-wrap break-words text-gray-700 dark:text-[#d0d6e0]">{{
              block.content || '（空内容）' }}</span>
          </div>
        </div>
      </div>

      <!-- 分页（多页时显示） -->
      <div v-if="pages.length > 1" class="flex items-center justify-center gap-2 py-2 shrink-0">
        <button
          v-for="(_, i) in pages"
          :key="i"
          @click="currentPage = i"
          class="h-2 rounded-full transition-all"
          :class="i === currentPage ? 'w-6 accent-bg' : 'w-2 bg-gray-300 dark:bg-white/20 hover:bg-gray-400 dark:hover:bg-white/40'"
        />
      </div>
    </template>
  </div>
</template>
