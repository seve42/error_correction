"""
解题智能体

调用 LLM 对结构化题目进行解答，返回结构化解题结果。
题目按小批发送：整批一次调用时题目多容易超长、输出被截断或解析失败。
"""

import json
import time
import logging
from typing import List, Dict, Any

from agents.error_correction.agent import invoke_structured_output
from .prompts import SOLVE_PROMPT
from .schemas import SolveBatchResult, SolveResult

logger = logging.getLogger(__name__)

# 单次调用的题目数上限，兼顾调用次数与单次输出长度。
DEFAULT_SOLVE_BATCH_SIZE = 4


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


def _invoke_batch(
    batch: List[Dict[str, Any]],
    provider: str,
    model_name: str | None,
    max_retries: int,
) -> SolveBatchResult:
    """单批调用，失败按 2**attempt 退避重试；最终失败抛出最后一个异常。"""
    prompt = _build_prompt(batch)

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            return invoke_structured_output(
                schema=SolveBatchResult,
                prompt=prompt,
                system_prompt=SOLVE_PROMPT,
                provider=provider,
                model_name=model_name,
                temperature=0.0,
                cache_key="solve",
            )
        except Exception as e:
            last_error = e
            logger.warning(
                f"invoke_solve: 第 {attempt}/{max_retries} 次失败 (provider={provider}): {e}"
            )
            if attempt < max_retries:
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


def invoke_solve(
    questions: List[Dict[str, Any]],
    provider: str = "openai",
    max_retries: int = 3,
    *,
    model_name: str | None = None,
    batch_size: int = DEFAULT_SOLVE_BATCH_SIZE,
) -> SolveBatchResult:
    """对一批题目进行解答

    Args:
        questions: 题目列表（Question schema 格式的 dict）
        provider: 模型供应商，"openai" 或 "anthropic"
        max_retries: 单批最大尝试次数
        model_name: 指定模型名称，为 None 时使用 provider 默认模型
        batch_size: 每批题目数

    Returns:
        SolveBatchResult，results 与输入题目顺序一致
    """
    if not questions:
        return SolveBatchResult(results=[])

    batch_size = max(int(batch_size or DEFAULT_SOLVE_BATCH_SIZE), 1)

    results: List[SolveResult] = []
    for start in range(0, len(questions), batch_size):
        batch = [
            _normalize_question(question, start + offset)
            for offset, question in enumerate(questions[start : start + batch_size])
        ]
        batch_result = _invoke_batch(
            batch,
            provider=provider,
            model_name=model_name,
            max_retries=max_retries,
        )
        results.extend(_align_batch(batch, batch_result))
        logger.info(
            f"invoke_solve: 批次 {start // batch_size + 1} 完成 "
            f"{len(batch_result.results)}/{len(batch)} 题 (provider={provider})"
        )

    logger.info(
        f"invoke_solve done: {len(results)} answers "
        f"(provider={provider}, model={model_name or 'default'})"
    )
    return SolveBatchResult(results=results)
