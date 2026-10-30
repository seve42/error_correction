import { ref } from 'vue'
import * as api from '@/api/index'
import { useAuth } from '@/composables/useAuth'
import { useProjects } from '@/composables/useProjects'
import { useTaskProgress } from '@/composables/useTaskProgress'
import { buildGeneratedAnswerQuestion, composeAnswerWithReasoning } from '@/utils/index'
import type { NotePreview } from '@/types/domain'

const QUOTA_EXCEEDED_CODE = 'DAILY_FREE_QUOTA_EXCEEDED'

/**
 * useSplitPipeline.js
 * 串联“视觉 OCR、AI 分割、笔记整理、导出、入库”的工作台主流程。
 */
export function useSplitPipeline(pushToast, currentView, step, S, uploadReady, splitting, splitCompleted, uploadMode, selectedLlmOption, questions, selectedIds, pendingFiles, typesetMath, autoGenerateAnswers) {
  const { setQuotaSnapshot, refreshCurrentUser } = useAuth()
  const { activeQuestionProjectId, loadProjects } = useProjects()
  const ocrLoading = ref(false)
  const ocrPages = ref([])
  const ocrDone = ref(false)
  const notePreview = ref<NotePreview | null>(null)
  const currentRunId = ref(null)
  const currentSplitRecordId = ref(null)
  const autoProcessing = ref(false)
  // 生成标准答案解析是导入流程的独立一步，用自己的 loading 与分割阶段区分。
  const answering = ref(false)
  // 识别与分割共用同一份任务日志；自动模式下 split 复用 OCR 的 taskId 续接事件。
  const progress = useTaskProgress()

  const setLoadedSplitRecord = (record) => {
    currentRunId.value = record?.run_id || null
    currentSplitRecordId.value = record?.id || null
  }

  /**
   * 从接口错误里同步额度快照，并判断是否命中免费额度耗尽。
   */
  const syncQuotaFromError = (error) => {
    if (error?.quota) setQuotaSnapshot(error.quota)
    return error?.code === QUOTA_EXCEEDED_CODE
  }

  /**
   * 主流程成功后刷新用户额度，让页面展示和后端额度保持一致。
   */
  const refreshQuotaSnapshot = async () => {
    try {
      await refreshCurrentUser()
    } catch (_) { }
  }

  /**
   * 试卷模式直接从视觉 OCR 开始，笔记模式同样先识别再整理。
   */
  const startProcess = () => {
    doOcr()
  }

  /**
   * 调用视觉 OCR 接口识别上传图片或 PDF，并同步扣减后的额度。
   */
  const doOcr = async () => {
    if (ocrLoading.value) return false
    ocrLoading.value = true
    ocrDone.value = false
    ocrPages.value = []
    notePreview.value = null
    currentView.value = 'workspace_review'
    step.value = S.value.OCR
    // 先建任务再发请求：后端允许先订阅后执行，事件不会因为请求晚到而丢失。
    const taskId = progress.start()
    try {
      const data = await api.runOcr('vision', taskId)
      ocrPages.value = data.pages || []
      ocrDone.value = true
      await refreshQuotaSnapshot()
      pushToast('success', `视觉识别完成，共 ${data.pages?.length || 0} 页`)
      return true
    } catch (e) {
      if (syncQuotaFromError(e)) {
        pushToast('error', e.message || '今日免费体验次数已用完')
      } else {
        pushToast('error', e.message || '识别失败')
      }
      currentView.value = 'workspace'
      step.value = S.value.UPLOAD
      return false
    } finally {
      ocrLoading.value = false
      // HTTP 响应早于 SSE 尾部几十毫秒：先等 done/最后几条事件落地（最多 2s）再断流，
      // 否则日志会缺最后一条汇总事件；拿不到流时立即返回，不拖慢流程。
      await progress.waitForDone()
      // 自动模式下紧接着要分割，这里只关流不清日志，doSplit 会用 lastSeq 重连。
      progress.close()
    }
  }

  /**
   * 为全部已分割题目生成标准答案与解析（导入流程独立一步）。
   * keepTask 为 true 时续接分割阶段的同一 taskId，日志与进度条接着往下滚动；
   * 生成失败不阻断后续导入，用户仍可先核对题目再手动补生成。
   */
  const doGenerateAnswers = async (options: { keepTask?: boolean } = {}) => {
    if (!autoGenerateAnswers?.value) return true
    const targets = questions.value.slice()
    if (!targets.length) return true

    const option = selectedLlmOption?.value
    if (!option?.category || !option?.model_name) {
      pushToast('error', '请先在顶部选择一个可用模型')
      return false
    }

    answering.value = true
    step.value = S.value.ANSWER
    if (options?.keepTask && progress.taskId.value) {
      // 自动模式/流程模式都是同一份日志：按上一阶段的 seq 续连，新阶段事件接着追加。
      progress.connect(progress.lastSeq.value, { expectRestart: true })
    } else {
      progress.start()
    }
    try {
      const payloads = targets.map(q => buildGeneratedAnswerQuestion(q))
      const results = await api.generateAnswers(payloads, {
        modelProvider: option.category,
        modelName: option.model_name,
        providerSource: option.source,
        providerId: option.provider_id,
        taskId: progress.taskId.value,
      })
      // 后端保证结果顺序与请求一致，按索引回填
      let filled = 0
      targets.forEach((q, index) => {
        const result = results[index]
        if (!result) return
        const text = composeAnswerWithReasoning(result.answer, result.reasoning)
        if (!text) return
        q.answer = text
        filled += 1
      })
      if (!filled) {
        pushToast('error', 'AI 未返回有效答案，请稍后重试')
        return false
      }
      if (filled < targets.length) {
        pushToast('success', `已生成 ${filled} 道题的标准答案解析，${targets.length - filled} 道题未生成`)
      } else {
        pushToast('success', `已生成 ${filled} 道题的标准答案解析`)
      }
      if (typeof typesetMath === 'function') await typesetMath()
      return true
    } catch (e) {
      if (syncQuotaFromError(e)) {
        pushToast('error', e.message || '今日免费体验次数已用完')
      } else {
        pushToast('error', '生成标准答案解析失败: ' + (e instanceof Error ? e.message : String(e)))
      }
      return false
    } finally {
      answering.value = false
      // 等解答阶段最后几条事件落地再断流，核对页回看时日志才完整。
      await progress.waitForDone()
      progress.close()
    }
  }

  /**
   * 根据上传模式分流：错题模式执行 AI 分割，笔记模式执行笔记整理。
   * keepTask 为 true 时续用 doOcr 已建的任务（自动模式），否则新建一份只包含分割阶段的日志。
   */
  const doSplit = async (options: { keepTask?: boolean } = {}) => {
    if (!uploadReady.value || splitting.value || splitCompleted.value) return false

    if (uploadMode.value === 'note') {
      return Boolean(await doNoteOrganize())
    }

    splitting.value = true
    currentRunId.value = null
    currentSplitRecordId.value = null
    ocrDone.value = false
    step.value = S.value.SPLIT
    if (options?.keepTask && progress.taskId.value) {
      // 自动模式：OCR 已结束并关流，按最后一条 seq 重连，日志继续追加而不是清空。
      // expectRestart 让轮询兜底忽略 POST 到达前的「已完成」快照，等后端 restart 后继续收事件。
      progress.connect(progress.lastSeq.value, { expectRestart: true })
    } else {
      progress.start()
    }
    pushToast('info', '正在调用AI分割题目，请稍候...', 1800)
    try {
      const data = await api.splitQuestions(
        selectedLlmOption.value?.category || 'openai',
        selectedLlmOption.value?.model_name || '',
        selectedLlmOption.value?.source || '',
        selectedLlmOption.value?.provider_id || '',
        'vision',
        progress.taskId.value,
      )
      currentRunId.value = data.run_id || null
      currentSplitRecordId.value = null
      questions.value = data.questions || []
      selectedIds.clear()
      if (data.warnings && data.warnings.length) {
        for (const w of data.warnings) pushToast('warning', w, 6000)
      }
      if (questions.value.length > 0) {
        splitCompleted.value = true
        if (!data.warnings || !data.warnings.length) {
          pushToast('success', `成功分割 ${questions.value.length} 道题目`)
        }
        // 先收完分割阶段的 done 再续连：否则新流会重放旧的 done，浏览器提前关流，解答阶段日志全丢。
        await progress.waitForDone()
        // 分割完成后按上传页的选项继续生成标准答案解析（独立一步，日志续接同一 taskId）。
        await doGenerateAnswers({ keepTask: true })
        step.value = S.value.EXPORT
        await typesetMath()
        await refreshQuotaSnapshot()
        setTimeout(() => {
          if (currentView.value === 'workspace') {
            currentView.value = 'workspace_review'
          }
        }, 800)
        return true
      }
      return false
    } catch (e) {
      if (syncQuotaFromError(e)) {
        pushToast('error', e.message || '今日免费体验次数已用完')
      } else {
        pushToast('error', '分割失败: ' + (e instanceof Error ? e.message : String(e)))
      }
      return false
    } finally {
      splitting.value = false
      // 等分割阶段的 done/尾部事件到达再断流，ReviewStage 回看时日志才完整。
      await progress.waitForDone()
      // 成功后保留事件供 ReviewStage 回看，只停止运行态与订阅。
      progress.close()
    }
  }

  /**
   * 自动模式：上传完成后按当前配置一路执行到可导出/可保存阶段。
   */
  const runAutoProcess = async () => {
    if (autoProcessing.value || !uploadReady.value || splitCompleted.value) return
    autoProcessing.value = true
    try {
      if (uploadMode.value === 'note') {
        await doNoteOrganize()
        return
      }

      const ocrOk = await doOcr()
      if (!ocrOk) return

      // 两阶段复用同一 taskId：分割事件接着 OCR 的 seq 继续推送，日志不丢。
      await doSplit({ keepTask: true })
    } finally {
      autoProcessing.value = false
    }
  }

  /**
   * 将上传文件提交给笔记整理接口，生成笔记后切换到笔记视图。
   */
  const doNoteOrganize = async () => {
    splitting.value = true
    currentRunId.value = null
    currentSplitRecordId.value = null
    notePreview.value = null
    step.value = S.value.SPLIT
    pushToast('info', '正在调用AI整理笔记，请稍候...', 3000)
    try {
      const formData = new FormData()
      for (const pf of pendingFiles) {
        if (pf.file) {
          // 由于部分浏览器/上传库会锁定最初选中的 File 对象导致 ERR_UPLOAD_FILE_CHANGED 错误
          // 在提交给笔记整理前，我们将其重新拷贝为一个新的 File 对象
          const newFile = new File([pf.file], pf.file.name, { type: pf.file.type, lastModified: pf.file.lastModified })
          formData.append('files', newFile)
        }
      }
      formData.append('model_provider', selectedLlmOption.value?.category || 'openai')
      if (selectedLlmOption.value?.model_name) {
        formData.append('model_name', selectedLlmOption.value.model_name)
      }
      if (selectedLlmOption.value?.source) {
        formData.append('provider_source', selectedLlmOption.value.source)
      }
      if (selectedLlmOption.value?.provider_id) {
        formData.append('provider_id', selectedLlmOption.value.provider_id)
      }
      const data = await new Promise<{ note_preview?: NotePreview | null }>((resolve, reject) => {
        api.createNote(formData, {
          onSuccess: (data) => resolve(data),
          onError: (error) => reject(error),
        })
      })

      notePreview.value = data.note_preview || null
      splitCompleted.value = true
      step.value = S.value.EXPORT
      await refreshQuotaSnapshot()
      pushToast('success', '笔记整理完成，请选择笔记本保存')
      return notePreview.value
    } catch (e) {
      if (syncQuotaFromError(e)) {
        pushToast('error', e.message || '今日免费体验次数已用完')
      } else {
        pushToast('error', '笔记整理失败: ' + (e instanceof Error ? e.message : String(e)))
      }
      return null
    } finally {
      splitting.value = false
    }
  }

  /**
   * 将整理结果保存到指定笔记本。
   */
  const saveNoteToProject = async (projectId) => {
    if (!notePreview.value) {
      pushToast('error', '暂无可保存的整理结果')
      return null
    }
    if (!projectId) {
      pushToast('error', '请先选择一个笔记本')
      return null
    }
    try {
      const note = await api.saveOrganizedNote({
        ...notePreview.value,
        project_id: projectId,
      })
      notePreview.value = null
      await loadProjects()
      pushToast('success', '笔记已保存到笔记本')
      return note
    } catch (e) {
      pushToast('error', '保存失败: ' + (e instanceof Error ? e.message : String(e)))
      return null
    }
  }

  /**
   * 导出选中的错题，并触发浏览器下载生成的 markdown 文件。
   */
  const doExport = async () => {
    if (!selectedIds.size) { pushToast('error', '请至少选择一道题目！'); return }
    try {
      const data = await api.exportQuestions(Array.from(selectedIds), currentRunId.value)
      step.value = S.value.EXPORT + 1
      pushToast('success', `错题本导出成功！已保存到: ${data.output_path}`)
      let filename = 'wrongbook.md'
      if (data.output_path) {
        const parts = String(data.output_path).split(/[/\\]/)
        const last = parts[parts.length - 1]
        if (last) filename = last
      }
      let downloadHref = data.download_url || `/download/${encodeURIComponent(filename)}`
      downloadHref += downloadHref.includes('?') ? `&t=${Date.now()}` : `?t=${Date.now()}`
      const a = document.createElement('a')
      a.href = downloadHref
      a.download = filename
      a.style.display = 'none'
      document.body.appendChild(a)
      a.click()
      a.remove()
    } catch (e) {
      pushToast('error', '导出失败: ' + (e instanceof Error ? e.message : String(e)))
    }
  }

  /**
   * 把选中的错题保存到当前错题库，并刷新错题库页面。
   */
  const doSaveToDb = async (targetProjectId, errorBankRef) => {
    if (!selectedIds.size) { pushToast('error', '请至少选择一道题目！'); return false }
    try {
      const projectId = targetProjectId || activeQuestionProjectId.value
      if (!projectId) { pushToast('error', '请先创建并选择一个错题库'); return false }
      const answers = questions.value
        .filter(q => selectedIds.has(q.uid) && (q.answer || q.user_answer))
        .map(q => ({ uid: q.uid, answer: q.answer || '', user_answer: q.user_answer || '' }))
      const data = await api.saveToDb(Array.from(selectedIds), answers, currentRunId.value, projectId, currentSplitRecordId.value)
      await loadProjects()
      pushToast('success', data.message || '已导入错题库')
      errorBankRef?.value?.refresh()
      return true
    } catch (e) {
      pushToast('error', '导入失败: ' + (e instanceof Error ? e.message : String(e)))
      return false
    }
  }

  return {
    ocrLoading, ocrPages, ocrDone, notePreview, autoProcessing, answering,
    progress,
    startProcess, runAutoProcess, doOcr, doSplit, doGenerateAnswers, doExport, doSaveToDb, saveNoteToProject, setLoadedSplitRecord,
  }
}
