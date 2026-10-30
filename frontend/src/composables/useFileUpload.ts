import { ref, reactive, computed, type Ref } from 'vue'
import { fileKey } from '@/utils/index'
import * as api from '@/api/index'

type PendingFile = {
  key: string
  file: File
}

type FileProgressMap = Record<string, number>
type UploadMode = string
type WorkflowSteps = Record<string, number>
type UploadQuestion = Record<string, unknown>

type ToastFn = (type: string, message: string) => void

/** 一批上传的任务描述：keys/orders 与 files 一一对应，resetSession 仅用于会话首批。 */
type UploadBatch = {
  keys: string[]
  orders: number[]
  files: File[]
  resetSession: boolean
}

/** 待上传文件：order 是它在本次选择中的全局序号，后端据此恢复识别顺序。 */
type UploadItem = {
  file: File
  order: number
}

/** 单批文件数上限：请求体积与并发粒度之间的平衡点。 */
const MAX_BATCH_FILES = 10
/** 单批累计字节上限：几百张图必须拆批，否则单个 multipart 请求过大且无法并行。 */
const MAX_BATCH_BYTES = 40 * 1024 * 1024
/** 并发批次数：3 路已能明显提速，继续加大会挤占浏览器连接池并放大后端压力。 */
const MAX_CONCURRENT_BATCHES = 3

export function useFileUpload(
  pushToast: ToastFn,
  S: Ref<WorkflowSteps>,
  questions: Ref<UploadQuestion[]>,
  selectedIds: Set<string | number>,
  splitCompleted: Ref<boolean>,
  splitting: Ref<boolean>,
  uploadMode: Ref<UploadMode>,
) {
  const uploadBusy = ref(false)
  const uploadReady = ref(false)
  const pendingFiles = reactive<PendingFile[]>([])
  const pendingPreviewUrls = computed(() =>
    pendingFiles.filter(pf => pf.file).map(pf => URL.createObjectURL(pf.file))
  )
  const fileProgress = reactive<FileProgressMap>({})
  const waitingKeys = reactive(new Set<string>())
  /** 待上传批次队列；并发上限之外的批次先排队，避免一次性打满后端。 */
  const uploadQueue = reactive<UploadBatch[]>([])
  /** 已收到后端成功响应的文件 key，仅这些文件计入 uploadDone。 */
  const settledKeys = reactive(new Set<string>())
  const activeXhrs = new Set<XMLHttpRequest>()
  let activeBatchCount = 0
  /** 文件在本次选择中的全局序号，递增不复用，删除文件后新增也不会与旧序号撞车。 */
  let nextOrder = 0
  /** reset_session 批次必须独占执行，否则会和并发批次同时改写同一上传会话。 */
  let resetBatchActive = false
  let hadUploadActivity = false

  const setProgress = (key: string, p: number) => { fileProgress[key] = Math.max(0, Math.min(100, Number(p) || 0)) }

  const isPending = (key: string) => pendingFiles.some(pf => pf.key === key)

  const uploadTotal = computed(() => pendingFiles.length)
  const uploadDone = computed(() => pendingFiles.filter(pf => settledKeys.has(pf.key)).length)
  /** 总体进度按全部文件平均，几百张图时单个文件卡住也能看清整体位置。 */
  const uploadPercent = computed(() => {
    if (!pendingFiles.length) return 0
    let sum = 0
    for (const pf of pendingFiles) sum += Math.max(0, Math.min(100, Number(fileProgress[pf.key]) || 0))
    return Math.round(sum / pendingFiles.length)
  })

  /** 同步 busy/ready 两个状态，并在整批上传结束时只提示一次，避免几十批刷屏。 */
  const refreshUploadState = () => {
    const busy = activeBatchCount > 0 || uploadQueue.length > 0
    uploadBusy.value = busy
    uploadReady.value = !busy && uploadDone.value > 0
    if (busy) {
      hadUploadActivity = true
      return
    }
    if (!hadUploadActivity) return
    hadUploadActivity = false
    const done = uploadDone.value
    const total = uploadTotal.value
    if (!done) return
    pushToast('success', done >= total ? `上传完成，共 ${done} 个文件` : `已上传 ${done}/${total} 个文件`)
  }

  const buildBatches = (items: UploadItem[], resetSession: boolean): UploadBatch[] => {
    const batches: UploadBatch[] = []
    let current: UploadBatch | null = null
    let currentBytes = 0
    for (const { file, order } of items) {
      const size = Math.max(0, Number(file.size) || 0)
      const needNew = !current
        || (current.keys.length >= MAX_BATCH_FILES)
        || currentBytes + size > MAX_BATCH_BYTES
      if (needNew) {
        current = { keys: [], orders: [], files: [], resetSession: false }
        currentBytes = 0
        batches.push(current)
      }
      current!.keys.push(fileKey(file))
      current!.orders.push(order)
      current!.files.push(file)
      currentBytes += size
    }
    // 只有首批重置会话；单文件超过 40MB 时上面会自动独占一批，不会死循环。
    if (batches.length && resetSession) batches[0].resetSession = true
    return batches
  }

  const pump = () => {
    if (resetBatchActive) {
      refreshUploadState()
      return
    }
    while (activeBatchCount < MAX_CONCURRENT_BATCHES && uploadQueue.length > 0) {
      const batch = uploadQueue.shift()
      if (!batch) break
      startBatch(batch)
      // 刚启动的是 reset_session 批次，其余批次等它完成后再并发，避免重置被覆盖。
      if (resetBatchActive) break
    }
    refreshUploadState()
  }

  const startBatch = (batch: UploadBatch) => {
    activeBatchCount += 1
    if (batch.resetSession) resetBatchActive = true
    for (const key of batch.keys) waitingKeys.delete(key)

    const liveKeys = batch.keys.filter(isPending)
    if (!liveKeys.length) {
      // 排队期间文件被全部移除，直接跳过这一批。
      if (batch.resetSession) resetBatchActive = false
      activeBatchCount = Math.max(0, activeBatchCount - 1)
      return
    }

    const formData = new FormData()
    if (batch.resetSession) formData.append('reset_session', '1')
    batch.files.forEach((file, index) => {
      const key = batch.keys[index]
      if (!liveKeys.includes(key)) return
      formData.append('files', file)
      formData.append('file_key', key)
      formData.append('file_order', String(batch.orders[index]))
    })

    let xhr: XMLHttpRequest | null = null
    const finishBatch = () => {
      if (xhr) activeXhrs.delete(xhr)
      activeBatchCount = Math.max(0, activeBatchCount - 1)
      if (batch.resetSession) resetBatchActive = false
      for (const key of batch.keys) waitingKeys.delete(key)
      refreshUploadState()
      pump()
    }

    xhr = api.uploadFiles(formData, {
      onProgress: (ratio) => {
        // 传输进度最多到 99%，100% 留给后端确认成功，避免进度条提前"完成"。
        const percent = Math.max(0, Math.min(99, ratio * 100))
        for (const key of batch.keys) {
          if (!isPending(key)) continue
          setProgress(key, Math.max(Number(fileProgress[key]) || 0, percent))
        }
      },
      onSuccess: () => {
        for (const key of batch.keys) {
          if (!isPending(key)) continue
          setProgress(key, 100)
          settledKeys.add(key)
        }
        finishBatch()
      },
      onError: (error) => {
        finishBatch()
        if (pendingFiles.length) {
          pushToast('error', error instanceof Error ? error.message : String(error))
        }
      },
      onAbort: () => {
        finishBatch()
      },
    })
    activeXhrs.add(xhr)
  }

  const enqueueUpload = (files: File[] | FileList | null | undefined) => {
    const list = Array.from(files || [])
    if (!list.length) return
    if (splitCompleted.value || splitting.value) { pushToast('error', '已分割完成，请先重新开始'); return }
    const isFreshStart = pendingFiles.length === 0
    const added: UploadItem[] = []
    for (const file of list) {
      const key = fileKey(file)
      if (pendingFiles.some(x => x.key === key)) continue
      // 序号递增分配：多批并发上传后后端仍能按用户选择顺序识别。
      const order = nextOrder++
      pendingFiles.push({ key, file })
      setProgress(key, 0)
      added.push({ file, order })
    }
    if (!added.length) return

    const batches = buildBatches(added, isFreshStart)
    for (const batch of batches) {
      for (const key of batch.keys) waitingKeys.add(key)
      uploadQueue.push(batch)
    }
    refreshUploadState()
    pump()
  }

  const doCancelFile = async (key: string, _step: unknown) => {
    try {
      const data = await api.cancelFile(key)
      const idx = pendingFiles.findIndex(x => x.key === key)
      if (idx >= 0) pendingFiles.splice(idx, 1)
      delete fileProgress[key]
      waitingKeys.delete(key)
      settledKeys.delete(key)
      for (let i = uploadQueue.length - 1; i >= 0; i--) {
        const batch = uploadQueue[i]
        batch.keys = batch.keys.filter(item => item !== key)
        batch.files = batch.files.filter(file => fileKey(file) !== key)
        if (!batch.keys.length) uploadQueue.splice(i, 1)
      }
      questions.value = []
      selectedIds.clear()
      splitCompleted.value = false
      pushToast('success', data.message || '已撤销')
      if (!pendingFiles.length) {
        // 全部移除时中止在途请求，避免后端继续为已取消的文件建会话。
        for (const xhr of activeXhrs) {
          try { xhr.abort() } catch (_) { /* ignore abort failure */ }
        }
        activeXhrs.clear()
      }
      refreshUploadState()
    } catch (_) { pushToast('error', '撤销失败: 网络错误') }
  }

  const removePendingFile = async (key: string) => {
    if (!key || splitting.value || splitCompleted.value) return
    if (uploadBusy.value || uploadReady.value) { await doCancelFile(key, S.value); return }
    const idx = pendingFiles.findIndex(x => x.key === key)
    if (idx >= 0) pendingFiles.splice(idx, 1)
    delete fileProgress[key]
    waitingKeys.delete(key)
    settledKeys.delete(key)
    for (let i = uploadQueue.length - 1; i >= 0; i--) {
      const batch = uploadQueue[i]
      batch.keys = batch.keys.filter(item => item !== key)
      batch.files = batch.files.filter(file => fileKey(file) !== key)
      if (!batch.keys.length) uploadQueue.splice(i, 1)
    }
    refreshUploadState()
  }

  const doReset = async (
    modelOptionsData: Ref<{ default_option_id?: string | number } | null> | null,
    selectedLlmOptionId: Ref<string | number | null>,
    step: unknown,
  ) => {
    for (const xhr of activeXhrs) {
      try { xhr.abort() } catch (_) { /* ignore abort failure */ }
    }
    activeXhrs.clear()
    activeBatchCount = 0
    nextOrder = 0
    resetBatchActive = false
    hadUploadActivity = false
    try {
      await api.resetUploadSession()
    } catch (_) {
      // Ignore backend cleanup failures during client reset.
    }
    uploadBusy.value = false
    uploadReady.value = false
    splitting.value = false
    splitCompleted.value = false
    pendingFiles.splice(0, pendingFiles.length)
    for (const k of Object.keys(fileProgress)) delete fileProgress[k]
    waitingKeys.clear()
    settledKeys.clear()
    uploadQueue.splice(0, uploadQueue.length)
    questions.value = []
    selectedIds.clear()

    if (modelOptionsData && modelOptionsData.value && modelOptionsData.value.default_option_id) {
      selectedLlmOptionId.value = modelOptionsData.value.default_option_id
    }
    pushToast('success', '已重置')
  }

  return {
    uploadBusy, uploadReady, pendingFiles, pendingPreviewUrls,
    fileProgress, waitingKeys,
    uploadPercent, uploadDone, uploadTotal,
    enqueueUpload, removePendingFile, doCancelFile, doReset,
  }
}
