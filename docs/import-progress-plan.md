# 导入流程实时日志、AI 输出展示与并发改造规划

> 状态：待评审。本文件只做规划，不含代码实现。
> 范围：试卷导入（exam）与笔记导入（note）的「上传 → 擦除 → OCR → 分割/整理」处理链路。
> 不包含：AI 对话流式改造、题库/复习/统计、导出逻辑重写。

---

## 1. 现状与问题

### 1.1 当前链路

```
前端 WorkspaceView
  ├─ useFileUpload          一次 XHR 上传全部文件（/api/upload）
  ├─ useSplitPipeline       串行 await 每一步
  │    ├─ doErase            POST /api/erase   （同步阻塞，返回预览图）
  │    ├─ doOcr              POST /api/ocr     （同步阻塞，返回 bbox 预览）
  │    ├─ doSplit            POST /api/split   （同步阻塞，跑 LangGraph）
  │    └─ doNoteOrganize     POST /api/notes/  （同步阻塞，OCR + LLM）
  └─ ReviewStage            loading 时只显示 SplitLoading 动画 + 固定文案
```

关键代码位置：

| 位置 | 现状 |
| --- | --- |
| `backend/routes/upload.py:470` | `/api/erase`，串行逐页调用擦除模型（`:530-537`） |
| `backend/routes/upload.py:591` | `/api/ocr`，PDF 串行、图片一次性并发；只返回最终结果 |
| `backend/routes/upload.py:786` | `/api/split`，同步 `workflow_graph.invoke(...)`（`:915-916`） |
| `backend/pipeline/workflow.py:118` | PDF OCR 串行重试；图片 OCR 走异步客户端 |
| `backend/pipeline/workflow.py:750` | 重叠批次 `batch_size=2, overlap=1` |
| `backend/pipeline/workflow.py:765` | 批次并行硬编码 `max_workers = min(len(batches), 3)` |
| `backend/pipeline/workflow.py:935` | 纠错把全部待纠错题目放进一次 `correct_batch.invoke` |
| `backend/pipeline/paddleocr_client.py:413` | `parse_images_async` 对所有图片 `asyncio.gather`，无并发上限（`:436`） |
| `backend/pipeline/paddleocr_client.py:293` | 轮询进度只 `console.print`，没有对外事件 |
| `backend/agents/note/agent.py:202` | 笔记长文本分批串行调用 LLM |
| `frontend/src/composables/useSplitPipeline.ts:68` | 每一步阻塞等待整段任务结束，无中间状态 |
| `frontend/src/components/features/app/workspace/SplitLoading.vue` | 只有固定文案 + 假进度条 |
| `frontend/src/components/features/app/workspace/ReviewStage.vue:53` | 按「擦除中/OCR 中/分割中」三选一显示 loading |
| `frontend/src/composables/useFileUpload.ts:66` | 所有文件塞进同一个 FormData、一次 XHR（`:82`） |
| `frontend/src/composables/useFileUpload.ts:29` | `pendingPreviewUrls` 计算属性每次求值都 `createObjectURL`，不可回收 |
| `backend/routes/notes.py:250` | 笔记模式同样是同步 OCR + 同步 LLM |

### 1.2 问题归纳

- **P1：没有步骤级事件通道。** 前端只能拿到最终响应，无法知道当前在哪一页、哪一批、已完成多少、ETA 多久。
- **P2：AI 调用不可见。** split_batch / correct_batch / 科目识别 / 笔记整理都没有把 prompt、原始输出、耗时、重试暴露给前端；出问题只能翻后端日志。
- **P3：多图/多页没有进度。** 几百张图片时前端只有一个无限转圈，用户无法判断卡住还是正常。
- **P4：并发度低且不可配置。** OCR 无上限并发（可能触发供应商限流），分割批次固定 3，擦除完全串行，纠错单次大调用，笔记分批串行。
- **P5：上传形态不适合大批量。** 单请求受 `MAX_CONTENT_LENGTH=50MB` 限制，几百张图片必然超限；前端还会为每张图创建 ObjectURL。
- **P6：缺少取消、断线恢复、运行记录。** 刷新页面或网络抖动后无法恢复；后台仍在跑，前端已丢失状态。

---

## 2. 目标与验收标准

### 2.1 功能目标

1. **每一步都有详细日志**：上传、擦除、OCR、分割、纠错、笔记整理各自输出结构化事件与可读日志，带时间戳、级别、阶段、计数、耗时。
2. **AI 调用可见**：每次 LLM 调用展示用途、模型、输入（prompt）、输出（结构化结果/原始文本/思考内容，若供应商返回）、耗时、token 用量、重试次数；支持展开、复制、下载完整内容。
3. **多图进度**：N 张图片/N 页 PDF 时显示「已完成/总数」、逐文件/逐页状态、各阶段进度、总体进度、已用时长、预计剩余时间。
4. **可取消、可恢复**：支持取消当前步骤；刷新页面后可通过 run_id 重新接上 SSE 并恢复状态。
5. **并发提速**：OCR、分割、纠错、笔记、擦除按配置并发，并带全局限流、429 退避与重试；前端上传分片并发。
6. **向后兼容**：现有 `/api/erase`、`/api/ocr`、`/api/split`、`/api/notes/` 接口行为不变，旧测试继续通过；新能力走新接口，可分阶段上线。

### 2.2 验收标准（可测试）

- 处理开始后 1 秒内前端出现第一阶段事件；每个阶段有 start/progress/finish 事件，`completed/total` 单调递增且最终相等（失败项单独计数）。
- 100 张图片导入：上传完成后即可看到总页数（PDF 可按 API 返回页数逐步修正），逐页状态可见；日志面板不卡顿（滚动 60fps，事件批量渲染）。
- 每次 AI 调用在 UI「AI 调用」列表中出现一条记录，包含输入/输出；输出失败时显示错误与重试次数。
- 刷新页面后 3 秒内恢复运行状态与日志尾部；SSE 不可用时自动降级轮询。
- 取消后 5 秒内停止提交新任务（已在执行的 LLM 调用允许跑完），状态标记 `cancelled`。
- 现有后端测试与前端测试全部通过；`npm run build` 通过。
- 并发压测：60 页试卷在默认配置下，OCR+分割阶段墙钟时间相对现状下降 ≥ 40%（受供应商限流影响，以实测为准），日志中能看到并发度与实际排队情况。

---

## 3. 总体设计

### 3.1 架构

```
浏览器
  ├─ POST /api/import/runs            创建一次导入运行（exam/note）
  ├─ POST /api/import/runs/{id}/steps/{step}   触发某一步（erase/ocr/split/note/auto）
  ├─ GET  /api/import/runs/{id}        运行快照（刷新/重连恢复）
  ├─ GET  /api/import/runs/{id}/events SSE 实时事件流（Last-Event-ID 续传）
  ├─ POST /api/import/runs/{id}/cancel 取消
  └─ GET  /api/import/runs/{id}/log    下载完整日志

Flask（threaded，单进程）
  routes/import_runs.py  ──► core/import_runs.py（ImportRun + 事件存储）
        │                         ▲
        │                         │ emit()
        ▼                         │
  pipeline/import_service.py ─────┘
        ├─ pipeline/workflow.py        （分割/纠错/OCR 现有链路，加进度回调）
        ├─ pipeline/paddleocr_client.py（OCR 限流 + 逐图进度）
        ├─ text_eraser_model            （擦除，线程池 + 进度）
        └─ agents/*                     （LLM 调用，经 core/llm_callbacks.py 捕获）
```

### 3.2 关键设计决定

| 决定 | 选择 | 理由 |
| --- | --- | --- |
| 推送方式 | **SSE**（`text/event-stream`） | 项目已有 `/api/chat/{id}/stream` SSE 先例；单向、实现简单、可断线重连；不引入 WebSocket 依赖 |
| 执行方式 | 请求线程 → **内存 Run + 后台线程** | 保持路由轻量；任务耗时分钟级，不能占用 HTTP 请求直到结束 |
| 事件模型 | 统一 `ImportEvent`（seq/type/stage/level/message/data） | 日志、进度、AI、状态共用一条流；前端一个 reducer 处理 |
| 持久化 | `runtime_data/import_runs/<run_id>/` 下 JSONL + 快照 + AI 原文 | 不先动数据库；重启后至少可下载日志；后续可迁移 DB |
| LangGraph | 不改图结构，只加 **进度回调 + 可配置并发** | 主流程已验证，避免大改；`thread_id` 已按 run 隔离 |
| 兼容 | 旧接口保留；新前端用新接口 | 降低回归风险，可分 PR 上线 |

---

## 4. 后端设计

### 4.1 运行对象 `ImportRun`（`backend/core/import_runs.py`）

```python
@dataclass
class ImportStep:
    name: str                 # prepare | erase | ocr | split | correct | note
    status: str               # pending | running | succeeded | failed | cancelled
    completed: int = 0
    total: int = 0
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    result_ref: str | None = None   # 结果快照键，避免事件里反复传大对象

@dataclass
class ImportRun:
    id: str                   # uuid4，对外暴露
    user_id: int | None
    mode: str                 # exam | note
    auto: bool
    status: str               # idle | running | succeeded | failed | cancelled
    current_step: str | None
    steps: dict[str, ImportStep]
    events: deque             # maxlen=IMPORT_LOG_MAX_EVENTS，环形
    seq: int
    created_at/updated_at
    context: dict             # 文件路径、模型选择、erase 开关、结果引用等
    cancel_event: threading.Event
```

Store 需提供：

- `create(user_id, mode, ...)`、`get_owned(run_id, user_id)`、`start_step(run_id, step)`、`finish_step(...)`、`cancel(run_id)`。
- 每用户同时只允许 1 个运行；全局同时运行上限 `IMPORT_JOB_MAX_CONCURRENT`，超出排队（事件 `queued`）。
- 运行结束后保留内存 `IMPORT_RUN_TTL_HOURS`（默认 24h），定时/惰性清理；文件保留 `IMPORT_LOG_RETENTION_DAYS`（默认 7 天）。
- 线程安全：`threading.Lock` 保护 seq/events/steps；SSE 生成器通过 `Condition` 等待新事件。
- **不依赖 Flask `request`/`session`**：创建时快照 `user_id`、文件路径、模型选择；后台线程内需要 provider 上下文时重新绑定（见 4.10）。

### 4.2 事件模型

所有事件统一结构：

```json
{
  "seq": 42,
  "ts": "2026-10-30T19:12:33.120+09:00",
  "type": "progress",
  "stage": "ocr",
  "level": "info",
  "message": "OCR 完成 12/120 页",
  "data": {
    "completed": 12,
    "total": 120,
    "percent": 10.0,
    "overall_percent": 14.5,
    "eta_seconds": 210,
    "active": 4
  }
}
```

事件类型：

| type | 关键 data | 用途 |
| --- | --- | --- |
| `run_created` / `run_finished` | `status`, `summary` | 生命周期 |
| `step_started` / `step_progress` / `step_finished` | `step`, `completed`, `total`, `percent`, `elapsed_ms` | 步骤进度 |
| `log` | `logger`, `level`, `message` | 现有 Python 日志桥接 |
| `page_status` | `page_key`, `file`, `page`, `status`, `ms`, `error` | 逐文件/逐页状态 |
| `batch_status` | `batch_index`, `status`, `questions`, `attempt` | 分割/纠错批次 |
| `ai_call_started` | `call_id`, `purpose`, `provider`, `model`, `prompt_chars`, `prompt_preview`, `scope` | AI 调用开始 |
| `ai_call_finished` | `call_id`, `ok`, `elapsed_ms`, `prompt_tokens`, `completion_tokens`, `output_chars`, `output_preview`, `artifact` | AI 调用结束 |
| `ai_call_failed` | `call_id`, `attempt`, `error` | AI 失败与重试 |
| `ai_token`（二期） | `call_id`, `delta` | 可选 token 流 |
| `warning` / `error` | `message`, `hint`, `code` | 用户可读告警 |
| `result_ready` | `result_ref`, `summary` | 某步结果可获取 |
| `heartbeat`（注释帧） | — | SSE 保活 |

约束：

- 单事件 JSON ≤ 16KB（默认），prompt/output 只放 preview（默认 2000 字符），全文放 artifact 文件并通过 `GET /api/import/runs/{id}/artifacts/{call_id}` 获取/下载。
- 高频 token 事件不进入主环形缓冲（二期单独通道），避免冲掉进度事件。
- 事件必须可序列化；禁止放 API Key、Authorization、带签名 URL。

### 4.3 API 契约（`backend/routes/import_runs.py`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/import/runs` | 创建运行。exam/note 均先用分片上传接口保存文件，再以 JSON 引用 `file_keys`；兼容 note 直接 multipart（小批量） |
| GET | `/api/import/runs/{id}` | 快照：状态、各步进度、计数、结果引用、`last_seq` |
| POST | `/api/import/runs/{id}/steps/{step}` | 触发一步：`erase` `ocr` `split` `note` `auto` |
| GET | `/api/import/runs/{id}/events` | SSE；支持 `Last-Event-ID` 或 `?after=` |
| POST | `/api/import/runs/{id}/cancel` | 请求取消当前步骤/运行 |
| GET | `/api/import/runs/{id}/log?format=jsonl\|txt` | 下载完整日志 |
| GET | `/api/import/runs/{id}/artifacts/{call_id}` | 下载 AI 输入/输出全文 |
| GET | `/api/import/runs/active` | 当前用户未结束的运行（刷新恢复） |

创建示例：

```http
POST /api/import/runs
Content-Type: application/json

{
  "mode": "exam",
  "auto": false,
  "erase_enabled": true,
  "model_provider": "openai",
  "model_name": "gpt-4o-mini",
  "provider_source": "user",
  "provider_id": 3
}
```

```json
{
  "success": true,
  "run": {
    "id": "0f4c...",
    "status": "idle",
    "mode": "exam",
    "steps": {"erase": "pending", "ocr": "pending", "split": "pending", "correct": "pending"}
  },
  "stream_url": "/api/import/runs/0f4c.../events",
  "snapshot_url": "/api/import/runs/0f4c..."
}
```

触发步骤：`POST /api/import/runs/{id}/steps/auto` → `202 {"success": true, "step": "auto", "status": "running"}`；同一 run 同时只能有一个步骤在跑，重复触发返回 `409 RUN_BUSY`。

SSE 细节：

- 响应头：`Content-Type: text/event-stream`、`Cache-Control: no-cache`、`X-Accel-Buffering: no`、`Connection: keep-alive`。
- 帧格式：`id: <seq>\nevent: <type>\ndata: <json>\n\n`，启动先发 `retry: 2000`。
- 每 15 秒无事件发 `: heartbeat\n\n` 注释帧。
- 重连：前端带 `Last-Event-ID`；服务端从 `after+1` 回放；若 `after` 小于环形缓冲最早 seq，先发 `run_snapshot` 事件（含最新状态），再继续。
- 慢客户端：不做每订阅者队列，直接按 `after` 读环形缓冲，天然背压；`heartbeat` 保证代理不超时。
- 鉴权：复用全局登录中间件；handler 内校验 `run.user_id == session['user_id']`（管理员按现有 `is_admin` 规则）。
- 运行结束后事件流发送 `run_finished` 后关闭；前端收到即停止重连。

兼容与部署：SSE 依赖单进程内存状态。开发环境 `threaded=True` 已满足；生产若用 gunicorn/waitress，必须 **单 worker + 多线程**（如 `gunicorn -w 1 --threads 8`）。多 worker 需二期改为 DB/Redis 事件总线，规划中不做。

### 4.4 服务层抽取（`backend/pipeline/import_service.py`）

按后端规则「路由只做参数校验与响应整理」，把现有路由内编排迁出：

- `start_exam_erase(run) -> dict`：复用 `_expand_erase_sources`、额度校验、会话写回、预览 payload。
- `start_exam_ocr(run) -> dict`：复用 `prepare_input`、OCR client、缓存写回、bbox 预览组装。
- `start_exam_split(run) -> dict`：复用 `run_store.create_split_run`、`workflow_graph.invoke`、保存分割记录、额度扣减。
- `start_note_pipeline(run) -> dict`：复用笔记 OCR + `invoke_note_organize` + note 保存逻辑。
- `run_auto(run)`：顺序执行上述步骤，任一步失败即停止并写 `error` 事件。

旧路由 `/api/erase`、`/api/ocr`、`/api/split`、`/api/notes/` 改为调用同一服务函数并同步返回结果（保持现有响应结构），行为与额度逻辑不变。这样：

- 旧测试的 monkeypatch 目标需要小幅调整（如 `routes.upload.workflow_graph` → 服务模块），但接口契约不变。
- 新 job 只是「后台线程 + 事件发射」的外壳，业务逻辑只有一份。

### 4.5 进度埋点

| 阶段 | total 来源 | 进度粒度 |
| --- | --- | --- |
| prepare | 上传文件数 | 每文件 |
| erase | PDF 页数 + 图片张数 | 每页 |
| ocr | 文件数优先；PDF 返回页数后修正为页 | 每文件/每页；PDF 轮询的 `extractedPages/totalPages` 也透传 |
| split | 批次数（页数 - overlap 推算） | 每批次（started/finished/retry） |
| correct | 分块数 | 每块 |
| note | OCR 页数、LLM 分批数 | 每批 |

总体进度按阶段权重计算（可配置）：

```
exam: prepare 5% + erase 20% + ocr 35% + split 35% + correct 5%
note: prepare 5% + ocr 40% + note 55%
```

总数未知时（如 PDF 尚未拿到页数）发 indeterminate 状态，前端显示不确定进度条；拿到总数后切换为确定进度。ETA 用最近 30 秒吞吐量滑动平均，样本不足时不显示。

### 4.6 AI 调用捕获（`backend/core/llm_callbacks.py`）

目标：**不改每个业务 Agent 的调用签名**，在 `core/llm.py:init_model()` 创建模型时自动挂载回调；用上下文变量标注当前调用用途。

```python
# 伪代码
_current_ai_scope: ContextVar[dict | None]   # {purpose, batch_index, page_range, stage}

class ImportLLMCallback(BaseCallbackHandler):
    def on_chat_model_start(...): emit ai_call_started（messages → prompt 文本，脱敏、截断）
    def on_llm_end(response):     emit ai_call_finished（generations/tool_calls/reasoning、usage）
    def on_llm_error(err):        emit ai_call_failed
    def on_llm_new_token(token):  # 仅流式模型、二期启用
        emit ai_token（节流）
```

挂载方式：

- `init_model()` 在返回前检查 `get_current_import_run()`；有活动 run 则 `model.callbacks = [handler]`。
- 业务侧只需在调用 LLM 的边界包一层 `with ai_scope(purpose="split_batch", batch_index=i, pages=(6, 7)):`：
  - `pipeline/workflow.py` 的 `_invoke_split`、`correct_questions_node`、科目识别；
  - `agents/error_correction/tools/question_tools.py` 的 `split_batch` / `correct_batch`；
  - `agents/note/agent.py` 的每批整理。
- 结构化输出的探针调用（`_probe_structured_mode`）也捕获，但 `purpose="probe"`，前端默认折叠/过滤。
- 输出内容：优先取 `structured_response` / tool call 的 JSON；再退化为 message content；若有 `reasoning_content` 或 `additional_kwargs` 中的思考字段一并展示（供应商有则显示）。
- 全文落盘：`import_runs/<run_id>/ai/<call_id>_prompt.txt`、`_output.json`；事件只带 preview 与 artifact 地址。
- 重试：`_retry_invoke` 每次 attempt 发 `ai_call_failed`/`ai_call_started`，带 `attempt`；最终失败发 `error` 与可读 hint（401/429/超时分别给建议）。
- token 流式：一期先做「调用级」完整输入/输出（非流式也能拿到完整响应）；二期对支持流式的模型（笔记整理、可选分割）打开 `streaming=True` 并节流推送 token，避免大改 Agent 内部图。
- 脱敏：回调序列化 messages 时保留正文，但不记录 headers、API Key、完整 base_url 查询参数；错误信息做同样处理。

### 4.7 日志桥接

- `ImportRun` 持有一个 `queue`/状态；`_ImportLogHandler(logging.Handler)` 在 `run` 创建时临时挂到 `pipeline`、`agents`、`routes.upload`、`routes.notes` 的 logger（或全局 root，按 `contextvar` 判断是否属于当前 run）。
- 记录 logger 名、level、message；按 logger 名前缀映射到 stage（`pipeline.workflow` → split、`pipeline.paddleocr_client` → ocr、`text_eraser_model` → erase、`agents.*` → 对应阶段）。
- 现有 `rich.console.print` 内容不强制重复进事件；以 `logger` 为准，必要时在关键点补 `logger.info`。
- 前端提供「简要 / 详细 / 调试」三档：`log`（用户可读进度摘要）、`detail`（含诊断）、`debug`（含每次 LLM 边界/重试）。后端事件带 `level`，前端按档位过滤。

### 4.8 持久化与清理

```
runtime_data/
└── import_runs/
    └── <run_id>/
        ├── run.json              # 创建参数、步骤状态、结果引用
        ├── events.jsonl          # 全量事件（append-only）
        ├── snapshot.json         # 最新快照（步骤完成后更新）
        ├── ai/
        │   ├── <call_id>_prompt.txt
        │   └── <call_id>_output.json
        └── log.txt               # 人类可读日志（可选，由 events 生成）
```

- 事件追加写用 `threading.Lock` + `flush`；写入失败只记 warning，不影响主流程。
- 启动时清理过期目录；运行结束按 TTL 清内存、按天数清文件。
- `result` 大对象（questions、ocr_pages）仍放原有目录（run_store/result_dir），快照只存引用，避免重复大 JSON。
- 可选二期：复用 `WorkflowRun` 表新增 `run_type="import"` 持久化运行记录，支持重启后仍可查询历史（需要迁移与测试，不在一期）。

### 4.9 并发设计

| 阶段 | 现状 | 目标设计 | 关键风险 |
| --- | --- | --- | --- |
| 上传 | 单请求全量 | 前端分片（如每片 20 个文件 / 40MB），3 路并发；后端沿用多文件接口 | 会话写冲突：job 运行时禁止新上传/重置，或等待 |
| 擦除 | 串行逐页 | `ThreadPoolExecutor(IMPORT_ERASE_WORKERS)`；按线程创建 `InferenceEngine`（thread-local） | torch 模型线程安全/内存；CPU 上并发收益有限，**先 benchmark 再定默认值** |
| OCR | 所有图片无上限 `gather` | `asyncio.Semaphore(IMPORT_OCR_CONCURRENCY)`；按文件排队；单图失败重试 2 次后标记失败并继续 | 供应商 QPS/429；大 PDF 单任务无法拆分 |
| 分割 | `max_workers=min(n,3)` | 可配 `IMPORT_LLM_CONCURRENCY`（默认 3-4）；统一队列，记录排队/开始/完成/重试 | 供应商限流；批次过多时上下文/成本 |
| 纠错 | 全部题目一次调用 | 按页/每 6 题分块，`IMPORT_CORRECTION_CONCURRENCY` 路并行；单块失败只影响本块 | 分块后跨题关联可能变弱（当前 prompt 已按题纠错，影响小） |
| 笔记整理 | 分批串行 | 分批并行（默认 2），按原顺序合并 | 合并顺序与去重 |
| 全局 | 无 | 每用户 1 个 run；全局并发上限；LLM 全局信号量 + 供应商级信号量 | 多用户时互相挤占 |

实现要点：

- `paddleocr_client.parse_images_async(..., concurrency=None, on_progress=None)`：worker 协程从 `asyncio.Queue` 取图片，提交/轮询/下载，完成后回调；保留结果顺序；`gather(return_exceptions=True)` 后区分成功与失败。
- `parse_pdf` 的轮询回调把 `extractProgress` 转成 `step_progress`（页级）。
- `workflow.py`：
  - 批次并发改为 `ThreadPoolExecutor(max_workers=settings.import_llm_concurrency)`；`copy_context().run` 保留。
  - 批大小/重叠从 settings 读取（默认仍 2/1，避免精度回退）。
  - 纠错改为：`flagged` 按页分组 → 每块构造只含相关页的 `ocr_context` → 并行调用 `correct_batch` → 合并。
  - 每个批次边界检查 `cancel_event`，取消后不再提交新任务，已提交的等待结束。
- 重试：统一指数退避 + jitter（例如 1s/3s/7s），识别 429 的 `Retry-After`；400/401 不盲目重试，直接给出配置提示。
- 并发上限要在日志/事件中体现（`active/max`、排队时长），便于用户理解「为什么没有更快」。

### 4.10 线程与 provider 上下文

- 后台线程不能使用 Flask `session`：创建 run 时快照 `user_id` 与文件路径。
- `core/config.py` 的 provider 注册表是 **ContextVar**（`_request_provider_context`），请求线程设置的值不会自动进入后台线程。后台任务启动时显式执行：
  ```python
  with SessionLocal() as db:
      _apply_provider_context(db, user_id)   # 现有函数，抽取到 core 层复用
  try:
      ...run...
  finally:
      settings.clear_request_provider_context()
  ```
- 线程池内复用 `copy_context().run`（现有分割逻辑已有此模式），保证 `ai_scope`、run 上下文传递；OCR 的 asyncio 回调使用显式传参，不依赖 contextvar。
- LangGraph `workflow_graph` 全局共享 + `MemorySaver`：每个 run 使用独立 `thread_id`（沿用现有 `run_id`）。**实施前需并发验证**：两个用户同时跑 split 是否串状态；若 `MemorySaver` 并发不安全，改为每个 run 单独 `build_workflow()`（构建成本低），或加进程内互斥。此项列入 P1 验证清单。

### 4.11 配置项（`backend/core/config.py` + `backend/.env.example`）

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `APP_IMPORT_OCR_CONCURRENCY` | 4 | OCR 图片并发 |
| `APP_IMPORT_LLM_CONCURRENCY` | 3 | 分割/纠错等 LLM 任务并发 |
| `APP_IMPORT_ERASE_WORKERS` | 1 | 擦除线程数；benchmark 后决定是否默认 2 |
| `APP_IMPORT_CORRECTION_CONCURRENCY` | 2 | 纠错并行块数 |
| `APP_IMPORT_CORRECTION_BATCH_SIZE` | 6 | 每块题目数 |
| `APP_IMPORT_NOTE_BATCH_CONCURRENCY` | 2 | 笔记分批并行 |
| `APP_IMPORT_SPLIT_BATCH_SIZE` / `..._OVERLAP` | 2 / 1 | 分割批次参数 |
| `APP_IMPORT_JOB_MAX_CONCURRENT` | 2 | 全局同时运行任务 |
| `APP_IMPORT_RUN_TTL_HOURS` | 24 | 内存保留 |
| `APP_IMPORT_LOG_RETENTION_DAYS` | 7 | 文件保留 |
| `APP_IMPORT_LOG_MAX_EVENTS` | 5000 | 环形缓冲 |
| `APP_IMPORT_EVENT_PREVIEW_CHARS` | 2000 | 事件内文本上限 |
| `APP_IMPORT_SSE_HEARTBEAT_SECONDS` | 15 | 心跳 |

统一放在 `Settings`（`env_prefix="APP_"`，见 `backend/core/config.py:187-192`），因此环境变量实际名为 `APP_IMPORT_*`；上传分片阈值是纯前端策略，可作为前端常量（默认每片 20 个文件 / 40MB），不必进后端配置。

### 4.12 安全

- 运行归属校验：所有 run 接口校验 `user_id`；`admin` 按现有 `is_admin` 语义决定是否可看全部。
- SSE/日志下载同样走登录中间件，不额外豁免。
- 日志与 AI artifact 中禁止出现 API Key/token；回调与错误信息统一 `redact_secrets()`。
- 文件路径使用现有 `_safe_join` 思路；artifact 名只允许 `call_id` 白名单格式（uuid），禁止路径穿越。
- prompt 包含用户试卷内容：仅本人可访问；导出/下载走鉴权接口。
- 取消/删除时清理临时文件，避免运行目录无限增长。

### 4.13 与现有额度/会话逻辑的兼容

- 额度校验与扣减时机保持现状：擦除按页、split 按 OCR 页数；后台任务在成功完成后扣减，失败/取消不扣。
- 运行期间锁定该用户的上传会话：`/api/upload`、`/api/upload/reset`、`/api/cancel_file` 检测到活跃 run 时返回 `409 IMPORT_RUN_ACTIVE`（或前端禁用，后端兜底）。
- `_clear_upload_runtime_state` 不得在运行中被调用；job 结束不自动清文件（用户「返回/重置」时再清）。
- 旧接口与新接口不要同时跑同一用户，避免状态竞争。

---

## 5. 前端设计

### 5.1 交互结构

处理中（review 页替换现有 `SplitLoading`）：

```
┌──────────────────────────────────────────────────────────────┐
│ 处理中 · 第 3/5 步 分割题目          已用 02:41  预计剩余 01:20  [取消] │
│ ████████████████████░░░░░░░░  总体 62%                        │
├──────────────────────────────────────────────────────────────┤
│ 步骤：上传 ✓ → 擦除 ✓ → OCR ✓ → 分割 ● → 纠错 ○             │
│ 分割：批次 14/38  并发 3/3  失败 0  最长等待 12s              │
├───────────────────────────────┬──────────────────────────────┤
│ 实时日志 [全部|步骤|AI|警告|错误] [搜索] [跟随] [复制] [下载]  │
│ 19:12:01 INFO  ocr    OCR 12/120 页完成 (1.8s)                │
│ 19:12:02 INFO  split  批次 14 完成：4 道题 (8.4s)             │
│ 19:12:03 AI    split  gpt-4o-mini 调用完成 8.4s  [展开输入/输出] │
│ ...（虚拟滚动，自动到底部；上滑暂停跟随）                      │
├───────────────────────────────┴──────────────────────────────┤
│ 页签：AI 调用 (14) │ 文件进度 (120) │ 结果预览                 │
│ AI 卡片：用途/模型/耗时/token/重试；展开显示 输入 | 输出        │
│ 页状态：✅ 完成 ● 处理中 ⏳ 排队 ❌ 失败（可查看错误）          │
└──────────────────────────────────────────────────────────────┘
```

完成后：保留结果页原有展示（擦除对比、OCR 预览、题目列表）；工具栏新增「查看处理日志」，打开抽屉回看本次运行。

### 5.2 数据层

**`frontend/src/api/importRuns.ts`**

```ts
createImportRun(payload)                 // JSON 或 FormData
runImportStep(runId, step)               // erase|ocr|split|note|auto
fetchImportRun(runId)                    // 快照
cancelImportRun(runId)
openImportEventStream(runId, { after, onEvent, onError, signal })
fetchImportArtifact(runId, callId)
downloadImportLog(runId, format)
```

`openImportEventStream` 用 `fetch` + `ReadableStream`（项目已有 ChatView 流式解析先例），不用 `EventSource`，便于：带 cookie、AbortController 取消、自定义 header、复用错误处理。解析器需处理：

- 跨 chunk 的半帧；
- `event:` / `data:` / `id:` 多行；
- 注释帧（`: heartbeat`）忽略；
- 断线后指数退避重连（1s→2s→5s，上限 10s），带 `Last-Event-ID`；
- 连续失败 3 次后降级为每 2s 轮询快照，并提示「实时连接不可用」。

**`frontend/src/composables/useImportRun.ts`**

```ts
state: {
  run, stages, overall, elapsed, eta,
  logs: RawLog[],            // 环形上限 3000
  aiCalls: Map<callId, AiCall>,
  pages: Map<pageKey, PageState>,
  connection: 'idle'|'live'|'reconnecting'|'polling'|'closed',
  filters: { level, stage, keyword, follow }
}
actions: startRun, startStep, cancel, reconnect, loadSnapshot, clear
```

- 事件 reducer 纯函数，便于单测。
- 事件先写 buffer，`requestAnimationFrame` / 50ms 批量 flush 到响应式状态，避免每次事件触发整页渲染。
- `pages` / `aiCalls` 使用 `shallowReactive` 或普通 Map + `version` 计数触发更新；日志数组用 `shallowRef`。
- 运行 id 与 `last_seq` 写入 `workspace_split_state_v1`（扩展字段），刷新后优先重连。

### 5.3 组件拆分

| 文件 | 职责 | 复用的 base 组件 |
| --- | --- | --- |
| `workspace/ProcessingPanel.vue` | 处理中总容器：总体进度、阶段时间线、页签、取消 | `BaseProgress`、`BaseTabs`、`BaseButton`、`BaseTag` |
| `workspace/StageProgressList.vue` | 各步骤状态/计数/耗时 | `BaseTimeline`、`BaseProgress` |
| `workspace/ProcessingLogConsole.vue` | 日志过滤、搜索、跟随、复制/下载、虚拟滚动 | `BaseVirtualList`、`BaseToolbarButton`、`BaseSearchInput`、`BaseCopyButton` |
| `workspace/AiCallCard.vue` | 单次 AI 调用：折叠头 + 输入/输出 tab + 元信息 | `BaseAccordion`、`BaseTabs`、`BaseCodeBlock`、`BaseCopyButton` |
| `workspace/PageStatusGrid.vue` | 逐页/逐文件状态网格（懒加载缩略图） | `BaseTag`、`BaseTooltip`、`BaseImage` |
| `workspace/ImportLogDrawer.vue` | 完成后回看日志（抽屉） | `BaseDrawer` |

### 5.4 现有文件改造

- `WorkspaceView.vue`：接入 `useImportRun`；运行中把 `currentView` 的 review 分支交给 `ProcessingPanel`；`workspace_split_state_v1` 增加 `importRunId/lastSeq`；工具栏加「查看处理日志」。
- `ReviewStage.vue`：`eraseLoading/ocrLoading/splitting` 三分支合并为「有活跃 run → ProcessingPanel」，完成后仍按原逻辑展示 `ErasePreview` / `OcrPreview` / 题目列表。
- `useSplitPipeline.ts`：`doErase/doOcr/doSplit/doNoteOrganize` 改为「创建/复用 run + 触发 step + 等结果」，结果从快照/`step_finished` 事件取；保留旧串行函数作为 fallback（开关）。
- `useFileUpload.ts`：
  - 上传分片并发（默认 3），每片独立 XHR 与进度，失败片重试 1 次；
  - 笔记模式不再把所有文件塞进一次 `/api/notes/` 请求：先走同一套分片上传拿 `file_keys`，再创建 note run，避免大请求超限；
  - `pendingPreviewUrls` 改为显式创建/回收的 `previewUrls`（仅前 6 张），修复 ObjectURL 泄漏；
  - 展示「共 N 个 / X MB」与总上传进度；单文件失败可单独重试/移除。
- `UploadStage.vue` / `ActionBar.vue`：显示上传/处理真实阶段，不再用「正在自动处理…」笼统文案。
- `api/upload.ts`：增加分片上传辅助（或放 `importRuns.ts`）。
- `SplitLoading.vue`：保留作为处理面板内的轻量动画，不再承担唯一信息展示。

### 5.5 几百张图片的专项

1. **上传**：分片请求（文件数/字节双阈值），并发 3；后端返回每片结果，前端按 `file_key` 合并；超过建议上限（如 500 张或 1.5GB）先提示。
2. **预览**：仅对前 N 张创建缩略图 URL；其余用图标/文件名占位；`URL.revokeObjectURL` 在移除/重置时释放。
3. **页网格**：用 `BaseVirtualList` 或 `content-visibility: auto`；图片 `loading="lazy"`；不一次性渲染 500 个 DOM。
4. **日志**：环形缓冲 + 批量 flush；只渲染可视窗口 + 最近 200 条；「加载更早」从快照/日志文件补。
5. **结果**：题目列表已有分页/懒渲染逻辑则复用；若一次几百题，核对页避免全量 MathJax 排版（分批 `requestIdleCallback`）。
6. **内存**：事件 data 不保留原始大对象；前端缓存只保留预览字符串。

### 5.6 主题与可访问性

- 全部使用现有 Tailwind + CSS 变量（`--accent-rgb` 等），亮/暗主题同时验证。
- 日志区等宽字体、可选中复制；级别用颜色 + 文本双编码，避免只靠颜色。
- 阶段切换用 `aria-live="polite"` 播报；取消/重连按钮有明确 focus 状态。
- 遵循 `prefers-reduced-motion`，进度动画可关闭。
- 文案中文，错误提示给出下一步动作（检查 API Key / 降低并发 / 重试）。

---

## 6. 分阶段实施计划

| 阶段 | 内容 | 主要交付物 | 验证 | 估算 |
| --- | --- | --- | --- | --- |
| P0 | 事件模型 + 运行对象骨架 + 单测 | `core/import_runs.py`、`test_import_runs.py` | 事件 seq/环形缓冲/取消/TTL 单测 | 1d |
| P1 | 服务层抽取 + 新路由 + SSE + 旧接口兼容 | `pipeline/import_service.py`、`routes/import_runs.py`、快照/取消/日志下载 | 路由集成测试（Flask test client 读 SSE）、旧测试通过 | 2-3d |
| P2 | 全链路进度埋点 + AI 回调 + 日志桥接 | workflow/OCR/erase/notes 埋点、`core/llm_callbacks.py`、artifact | 事件顺序/计数/失败重试测试；AI 输入输出快照 | 2-3d |
| P3 | 并发改造 + 配置 + 压测 | 并发信号量、纠错分块、笔记并行、擦除 worker、`.env.example` | 并发上限测试、60 页压测对比 | 2d |
| P4 | 前端进度/日志/AI 卡片/页状态 | `useImportRun`、`ProcessingPanel` 等组件；改造 Workspace/Review/Upload | Vitest + 浏览器手工验证 | 3d |
| P5 | 大数量上传/虚拟化/断线恢复 + 文档收尾 | 分片上传、ObjectURL 修复、日志抽屉、README/规则同步 | 100+ 图手工验证、构建通过 | 2d |

建议拆两个 PR：

1. **后端地基**（P0-P2）：不接前端也能用（curl/测试验证），风险可控。
2. **端到端体验**（P3-P5）：并发 + 前端 + 压测 + 文档。

---

## 7. 测试计划

### 7.1 后端（`backend/tests/`）

| 新增/修改 | 覆盖点 |
| --- | --- |
| `test_import_runs.py` | run 生命周期、seq 单调、环形缓冲、TTL、归属校验、取消状态机、并发上限 |
| `test_import_runs_routes.py` | 创建/触发/快照/404/409/鉴权；SSE 帧格式、`Last-Event-ID` 回放、心跳、结束后关闭；日志/artifact 下载 |
| `test_import_service.py` | mock OCR/erase/LLM，断言阶段事件顺序、计数、错误传播、额度扣减时机 |
| `test_llm_callbacks.py` | 回调捕获 prompt/输出/token、重试 attempt、脱敏、artifact 写入 |
| `test_concurrency_limits.py` | OCR 并发 ≤ N、批次并发 ≤ N、纠错分块并行、取消后不再提交 |
| `test_workflow_helpers.py`（扩展） | 批次构建、纠错分块、进度回调参数不影响现有结果 |
| `test_web_routes.py`（调整） | 旧接口仍返回原结构；新服务抽取后 monkeypatch 目标更新；额度测试全绿 |

原则：不触网（mock PaddleOCR/LLM），SSE 用 test client 迭代读取并设超时；并发测试用 `threading.Barrier`/计数器断言峰值，避免时间敏感断言。

### 7.2 前端（`frontend/src/__tests__/`）

- `importRuns.test.ts`：SSE 分帧解析（跨 chunk、多行 data、注释、id）、重连退避、轮询降级、Abort。
- `useImportRun.test.ts`：reducer 状态流转、整体进度计算、日志上限、AI 调用合并、取消/重连、快照恢复。
- `upload-chunks.test.ts`：分片阈值、并发上限、失败重试、`file_key` 合并、ObjectURL 回收。
- 组件冒烟：`ProcessingLogConsole` 过滤/搜索/空状态；`AiCallCard` 展开输入输出；`PageStatusGrid` 状态渲染。

### 7.3 手工/性能

- 用 20/60/120 张图片（或 30 页 PDF）各跑一次，记录各阶段耗时、并发度、失败重试。
- 校验：刷新恢复、取消、SSE 断网重连、AI 输入/输出完整、日志下载、暗/亮主题。
- 对比改造前后墙钟时间，写入 PR 描述（规则要求部署/影响说明）。

---

## 8. 风险与对策

| 风险 | 影响 | 对策 |
| --- | --- | --- |
| 擦除模型并发不安全/内存高 | 崩溃、OOM | 默认 1 worker；thread-local engine；先 benchmark；图片大时限制并发/缩略 |
| LLM 供应商限流 429 | 大面积失败/更慢 | 全局+供应商信号量、指数退避+jitter、尊重 `Retry-After`、失败批次单独重试 |
| Flask 线程被 SSE 耗尽 | 请求阻塞 | 单进程 + 心跳 + 连接数上限；生产 gunicorn `-w 1 --threads N`；轮询降级 |
| 多 worker 部署 | SSE/快照跨进程丢失 | 文档明确单 worker；二期 DB/Redis 事件总线 |
| LangGraph MemorySaver 并发串状态 | 题目/用户串数据 | P1 并发验证；必要时每 run 独立 graph 或加互斥 |
| provider ContextVar 未传入后台线程 | 用错 Key/模型 | 后台线程显式绑定 provider 上下文并 finally 清理；集成测试覆盖 |
| 事件量过大（几百页） | 前端卡顿/SSE 拥塞 | 环形缓冲、预览截断、批量 flush、虚拟列表、事件节流 |
| 大 payload 写事件 | 内存/带宽 | preview + artifact；单事件 ≤16KB；questions 只传引用 |
| 取消无法中断已发出的 LLM 请求 | 取消延迟 | 明确语义「不再提交新任务，当前调用跑完」；UI 显示取消中 |
| 额度重复扣减 | 用户投诉 | 复用现有扣减函数；每个 run/step 幂等状态；失败/取消不扣；测试覆盖 |
| 旧测试因抽取失败 | 回归风险 | P1 只搬移不改行为；先跑旧测试再改；monkeypatch 目标同步更新 |

---

## 9. 需要确认的决策

1. **部署形态**：是否单进程多线程运行（推荐，当前 `start.ps1` 场景）？若必须多 worker，P1 就要引入 DB/Redis 事件存储，工作量 +2~3d。
2. **AI 输入展示范围**：默认展示完整 prompt（含试卷 OCR 文本）并提供「简要/详细」开关，是否可接受？（推荐：是，内容仅本人可见）
3. **日志保留**：内存 24h、文件 7 天，是否需要更久或在「分割历史」里回看？
4. **并发默认值**：OCR 4 / LLM 3 / 擦除 1 / 纠错 2 的保守默认是否合适？是否有供应商 QPS 上限需要写死更低？
5. **范围确认**：笔记导入（note）是否纳入本次改造（推荐纳入，复用同一套 run/SSE）？

---

## 10. 附：文件变更清单

### 后端新增

- `backend/core/import_runs.py`：运行对象、事件、环形缓冲、TTL、清理
- `backend/core/llm_callbacks.py`：LangChain 回调、AI artifact、脱敏
- `backend/pipeline/import_service.py`：从 upload/notes 路由抽取的编排服务
- `backend/routes/import_runs.py`：run/step/SSE/snapshot/cancel/log API
- `backend/tests/test_import_runs.py`、`test_import_runs_routes.py`、`test_import_service.py`、`test_llm_callbacks.py`、`test_concurrency_limits.py`

### 后端修改

- `backend/routes/upload.py`：保留接口，改为调用 service；加活跃 run 互斥
- `backend/routes/notes.py`：抽出 note 服务；新 run 入口
- `backend/routes/__init__.py`：注册新 Blueprint
- `backend/pipeline/workflow.py`：进度回调、批次并发可配、纠错分块、取消检查
- `backend/pipeline/paddleocr_client.py`：限流、逐图进度、部分失败、PDF 轮询进度
- `backend/pipeline/utils.py`：`run_async` 可选上下文传递、`prepare_input` 进度/命名
- `backend/agents/error_correction/tools/question_tools.py`、`backend/agents/note/agent.py`、`backend/agents/error_correction/agent.py`：`ai_scope` 与重试事件
- `backend/core/llm.py`：自动挂载回调、可选流式
- `backend/core/config.py`、`backend/.env.example`：并发/SSE/日志配置
- `backend/tests/test_web_routes.py`、`test_workflow_helpers.py`、`test_ocr_api.py`：适配与扩展

### 前端新增

- `frontend/src/api/importRuns.ts`
- `frontend/src/composables/useImportRun.ts`
- `frontend/src/components/features/app/workspace/ProcessingPanel.vue`
- `.../StageProgressList.vue`、`ProcessingLogConsole.vue`、`AiCallCard.vue`、`PageStatusGrid.vue`、`ImportLogDrawer.vue`
- `frontend/src/__tests__/importRuns.test.ts`、`useImportRun.test.ts`、`upload-chunks.test.ts`

### 前端修改

- `frontend/src/views/app/WorkspaceView.vue`、`.../workspace/ReviewStage.vue`、`UploadStage.vue`、`ActionBar.vue`、`SplitLoading.vue`（复用）
- `frontend/src/composables/useSplitPipeline.ts`、`useFileUpload.ts`
- `frontend/src/api/upload.ts`（如分片逻辑放这里）
- `frontend/src/__tests__/api.test.ts`（如上传封装变化）

### 文档

- `README.md`：功能列表补充「实时处理日志 / AI 输出 / 大批量导入」
- `backend/.env.example`：新增配置说明
- `DEPLOY.md`：单 worker + 多线程 + SSE 代理注意事项
- `rules/development/后端规则.md`：补充「长任务运行对象、SSE、事件与日志约定」
- `rules/development/前端规则.md`：补充「SSE 消费、日志面板、事件节流约定」

---

## 11. 一期不做（明确边界）

- 不引入 WebSocket / 消息队列 / Redis。
- 不改 LangGraph 图拓扑、不重写分割/纠错 prompt。
- 不把处理日志写入数据库（二期可迁移 `WorkflowRun`）。
- 不做多进程/多机任务调度。
- 不改 AI 对话、题库、复习、统计、导出展示逻辑（仅复用其现有接口）。
