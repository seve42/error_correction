"""
解题智能体

调用 LLM 对结构化题目进行解答，返回结构化解题结果。
题目按小批发送：整批一次调用时题目多容易超长、输出被截断或解析失败。
多批之间用线程池并发，配合进度事件让前端看到"第几批在跑、跑到哪一步"。
"""

import json
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from typing import Any, Callable, Dict, List, Optional

from agents.error_correction.agent import invoke_structured_output
from core import task_progress
from core.config import settings
from .prompts import SOLVE_PROMPT
from .schemas import SolveBatchResult, SolveResult

logger = logging.getLogger(__name__)

# 单次调用的题目数上限，兼顾调用次数与单次输出长度。
DEFAULT_SOLVE_BATCH_SIZE = 4

# 进度事件里的步骤标识与中文阶段名，前端日志面板直接展示 stage。
ANSWER_STEP = "answer"
ANSWER_STAGE = "生成答案解析"


def _normalize_question(question: Dict[str, Any], index: int) -> Dict[str, Any]:
    """补全 question_id，保证模型返回结果能与输入一一对应。"""
    item = dict(question)
    raw_id = item.get("question_id")
    item["question_id"] = (
        str(raw_id).strip() if raw_id not in (None, "") else f"q_{index}"
    )
    return item


def _build_prompt(batch: List[Dict[str, Any]]) -> str:
    return f"""请解答以下 {len(batch)} 道题目，返回每道题的答案和解题过程。

题目数据：
{json.dumps(batch, ensure_ascii=False, indent=2)}"""


def _result_text(result: SolveBatchResult) -> str:
    """把结构化结果序列化成日志里的「AI 输出」，用户展开即可看到本批真正产出了什么。"""
    try:
        return json.dumps(result.model_dump(), ensure_ascii=False)
    except Exception:  # 模型给出异常字段时不要让日志影响主流程
        return json.dumps(
            {"results": [item.model_dump() for item in result.results]},
            ensure_ascii=False,
            default=str,
        )


class _BatchProgress:
    """已完成批次数 → 进度比例。

    并发批次完成顺序不确定，按"已完成数/总数"发进度，保证进度条单调递增。
    """

    def __init__(self, total: int):
        self._total = max(int(total or 1), 1)
        self._done = 0
        self._lock = threading.Lock()

    def current(self) -> float:
        with self._lock:
            return round(self._done / self._total, 4)

    def complete_next(self) -> float:
        with self._lock:
            self._done += 1
            return round(self._done / self._total, 4)


def _emit(emitter, **kwargs) -> None:
    """emitter 可能为空（CLI/测试调用），这里统一兜住空值。"""
    if emitter is not None:
        emitter.emit(**kwargs)


def _invoke_batch(
    batch: List[Dict[str, Any]],
    provider: str,
    model_name: Optional[str],
    max_retries: int,
    on_retry: Optional[Callable[[int, Exception], None]] = None,
) -> tuple[SolveBatchResult, int]:
    """单批调用，失败按 2**attempt 退避重试；返回结果与实际用掉的尝试次数。"""
    prompt = _build_prompt(batch)

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            result = invoke_structured_output(
                schema=SolveBatchResult,
                prompt=prompt,
                system_prompt=SOLVE_PROMPT,
                provider=provider,
                model_name=model_name,
                temperature=0.0,
                cache_key="solve",
            )
            return result, attempt
        except Exception as e:
            last_error = e
            logger.warning(
                f"invoke_solve: 第 {attempt}/{max_retries} 次失败 (provider={provider}): {e}"
            )
            if attempt < max_retries:
                if on_retry is not None:
                    on_retry(attempt, e)
                wait = 2 ** attempt
                logger.info(f"invoke_solve: 等待 {wait}s 后重试...")
                time.sleep(wait)

    raise last_error


def _align_batch(
    batch: List[Dict[str, Any]], batch_result: SolveBatchResult
) -> List[SolveResult]:
    """把模型结果按 question_id 对回输入顺序；漏答时补占位结果，避免前端错位。"""
    by_id: Dict[str, SolveResult] = {}
    for item in batch_result.results:
        by_id.setdefault((item.question_id or "").strip(), item)

    aligned: List[SolveResult] = []
    for offset, question in enumerate(batch):
        question_id = question["question_id"]
        hit = by_id.get(question_id)
        if hit is None and len(batch_result.results) == len(batch):
            # 模型改写了题号（如多了"题"字）时按顺序兜底
            hit = batch_result.results[offset]
        if hit is None:
            hit = SolveResult(
                question_id=question_id,
                answer="",
                reasoning="模型未返回该题的解答，请重试。",
                confidence=0.0,
            )
        elif hit.question_id != question_id:
            hit = hit.model_copy(update={"question_id": question_id})
        aligned.append(hit)

    return aligned


def _resolve_worker_count(max_workers: Optional[int], batch_count: int) -> int:
    """并发路数：显式参数优先，其次读配置，且不超过实际批次数。"""
    configured = (
        max_workers
        if max_workers is not None
        else getattr(settings, "answer_concurrency", 1)
    )
    try:
        configured = int(configured)
    except (TypeError, ValueError):
        configured = 1
    return max(1, min(configured, max(batch_count, 1)))


def _solve_one(
    batch_index: int,
    total_batches: int,
    start: int,
    batch: List[Dict[str, Any]],
    provider: str,
    model_name: Optional[str],
    max_retries: int,
    emitter,
    progress: _BatchProgress,
) -> tuple[int, List[SolveResult]]:
    """执行一批：发开始/重试/完成事件，返回按输入顺序对齐的结果。"""
    first_no = start + 1
    last_no = start + len(batch)
    label = f"第 {batch_index + 1}/{total_batches} 批（第 {first_no}-{last_no} 题）"
    _emit(
        emitter,
        step=ANSWER_STEP,
        stage=ANSWER_STAGE,
        level="info",
        message=f"正在生成{label}的答案解析",
        progress=progress.current(),
    )

    def _on_retry(attempt: int, exc: Exception) -> None:
        _emit(
            emitter,
            step=ANSWER_STEP,
            stage=ANSWER_STAGE,
            level="warn",
            message=f"{label}第 {attempt} 次调用失败：{exc}，准备重试",
            progress=progress.current(),
        )

    started = time.time()
    try:
        batch_result, attempt = _invoke_batch(
            batch,
            provider=provider,
            model_name=model_name,
            max_retries=max_retries,
            on_retry=_on_retry,
        )
    except Exception as exc:
        _emit(
            emitter,
            step=ANSWER_STEP,
            stage=ANSWER_STAGE,
            level="error",
            message=f"{label}生成失败：{exc}",
            progress=progress.current(),
        )
        raise

    duration = time.time() - started
    _emit(
        emitter,
        step=ANSWER_STEP,
        stage=ANSWER_STAGE,
        level="success",
        message=f"{label}完成：耗时 {duration:.1f}s，{len(batch)} 道题",
        progress=progress.complete_next(),
        detail=task_progress.build_ai_call_detail(
            model=model_name or provider,
            pages=[],
            duration=duration,
            attempt=attempt,
            output=_result_text(batch_result),
        ),
    )
    logger.info(
        f"invoke_solve: 批次 {batch_index + 1}/{total_batches} 完成 "
        f"{len(batch_result.results)}/{len(batch)} 题 (provider={provider})"
    )
    return batch_index, _align_batch(batch, batch_result)


def invoke_solve(
    questions: List[Dict[str, Any]],
    provider: str = "openai",
    max_retries: int = 3,
    *,
    model_name: Optional[str] = None,
    batch_size: int = DEFAULT_SOLVE_BATCH_SIZE,
    emitter=None,
    max_workers: Optional[int] = None,
) -> SolveBatchResult:
    """对一批题目进行解答

    Args:
        questions: 题目列表（Question schema 格式的 dict）
        provider: 模型供应商，"openai" 或 "anthropic"
        max_retries: 单批最大尝试次数
        model_name: 指定模型名称，为 None 时使用 provider 默认模型
        batch_size: 每批题目数
        emitter: 进度事件出口（EventEmitter），为 None 时不发事件
        max_workers: 并发批次数，为 None 时读 settings.answer_concurrency

    Returns:
        SolveBatchResult，results 与输入题目顺序一致
    """
    if not questions:
        return SolveBatchResult(results=[])

    batch_size = max(int(batch_size or DEFAULT_SOLVE_BATCH_SIZE), 1)

    batches: List[tuple[int, List[Dict[str, Any]]]] = []
    for start in range(0, len(questions), batch_size):
        batch = [
            _normalize_question(question, start + offset)
            for offset, question in enumerate(questions[start : start + batch_size])
        ]
        batches.append((start, batch))

    total_batches = len(batches)
    workers = _resolve_worker_count(max_workers, total_batches)
    progress = _BatchProgress(total_batches)

    def _task_args(index: int, start: int, batch: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "batch_index": index,
            "total_batches": total_batches,
            "start": start,
            "batch": batch,
            "provider": provider,
            "model_name": model_name,
            "max_retries": max_retries,
            "emitter": emitter,
            "progress": progress,
        }

    ordered: List[tuple[int, List[SolveResult]]] = []
    if total_batches == 1 or workers <= 1:
        # 单批时没必要起线程池，保持原有的串行日志与调用栈。
        for index, (start, batch) in enumerate(batches):
            ordered.append(_solve_one(**_task_args(index, start, batch)))
    else:
        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="answer-solve"
        ) as pool:
            futures = [
                # copy_context 在提交线程执行，把请求上下文（Provider 激活、额度统计）
                # 带进工作线程；否则并发批次会丢 Provider 配置或漏记 token 用量。
                pool.submit(
                    copy_context().run, _solve_one, **_task_args(index, start, batch)
                )
                for index, (start, batch) in enumerate(batches)
            ]
            # 按提交顺序取结果，合并后仍是题目原始顺序；任一批失败则整体失败。
            ordered = [future.result() for future in futures]

    ordered.sort(key=lambda item: item[0])
    results: List[SolveResult] = []
    for _, batch_results in ordered:
        results.extend(batch_results)

    logger.info(
        f"invoke_solve done: {len(results)} answers "
        f"(provider={provider}, model={model_name or 'default'}, "
        f"batches={total_batches}, workers={workers})"
    )
    return SolveBatchResult(results=results)
