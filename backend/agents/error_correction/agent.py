"""
错题本智能体工厂
- 内层：create_agent (langchain) + ToolStrategy — 结构化输出题目分割结果
"""

import json
import logging
import re
import threading
import time
from langchain_core.messages import SystemMessage, HumanMessage
from pydantic import BaseModel, Field

from core.config import settings
from core.llm import init_model as _init_model
from .prompts import SPLIT_PROMPT, SPLIT_PROMPT_LITE, CORRECTION_PROMPT
from .schemas import QuestionSplitResult, CorrectionResult

logger = logging.getLogger(__name__)

# Agent 实例缓存（避免每次调用重复初始化模型和编译图）
_agent_cache = {}
_agent_cache_lock = threading.Lock()
_structured_mode_cache = {}
_structured_mode_lock = threading.Lock()

STRUCTURED_PROVIDER = "provider"
STRUCTURED_TOOL = "tool"
STRUCTURED_JSON_MODE = "json_mode"
STRUCTURED_PROMPT_JSON = "prompt_json"


class _StructuredProbeResult(BaseModel):
    ok: bool = Field(description="Whether the probe succeeded")


def detect_subject_via_llm(ocr_text_sample: str, db_subjects: list, provider: str = "openai") -> str:
    """通过轻量 LLM 识别试卷科目

    Args:
        ocr_text_sample: 前几页 OCR 文本拼接
        db_subjects: 数据库已有科目列表
        provider: 模型供应商

    Returns:
        识别出的科目名称，失败时返回空字符串
    """
    model = _init_model(temperature=0.0, provider=provider, use_light=True)

    existing = '、'.join(db_subjects) if db_subjects else '无'
    prompt = (
        f'已有科目列表：{existing}\n\n'
        f'以下是一份试卷的 OCR 文字节选：\n{ocr_text_sample[:1500]}\n\n'
        '判断试卷科目，输出格式【学段+科目】。'
        '学段：小学/初中/高中/大学；未标注学段不默认，直接用"未知学段"。'
        '科目可能包含大学课程（如高等数学、线性代数、概率论、大学物理、数据结构等）。'
        '只输出结果，不要引号/解释/换行；无法判断输出"未知"。'
    )

    response = model.invoke([HumanMessage(content=prompt)])
    result = response.content.strip()

    if not result or '未知' in result:
        return ""

    return result


def create_inner_split_agent(provider: str = "openai", model_name: str | None = None):
    """创建内层题目分割智能体

    使用 create_agent + ToolStrategy，保证结构化输出。
    无外部工具，专注于将 OCR 数据分割为 QuestionSplitResult。

    由 split_batch 工具内部调用。

    Args:
        provider: 模型供应商，"openai" 或 "anthropic"
        model_name: 指定模型名称，为 None 时使用 provider 默认模型

    Returns:
        create_agent 返回的 CompiledStateGraph
    """
    from langchain.agents import create_agent
    from langchain.agents.structured_output import ToolStrategy

    model = _init_model(temperature=0.1, provider=provider, model_name=model_name)

    return create_agent(
        model=model,
        tools=[],
        system_prompt=SPLIT_PROMPT,
        response_format=ToolStrategy(
            schema=QuestionSplitResult,
            handle_errors=True,
        ),
    )


def create_correction_agent(provider: str = "openai", model_name: str | None = None):
    """创建内层 OCR 纠错智能体

    使用 create_agent + ToolStrategy，保证结构化输出。
    无外部工具，专注于修复 OCR 错误并输出 CorrectionResult。

    由 correct_batch 工具内部调用。

    Args:
        provider: 模型供应商，"openai" 或 "anthropic"
        model_name: 指定模型名称，为 None 时使用 provider 默认模型

    Returns:
        create_agent 返回的 CompiledStateGraph
    """
    from langchain.agents import create_agent
    from langchain.agents.structured_output import ToolStrategy

    model = _init_model(temperature=0.0, provider=provider, model_name=model_name)

    return create_agent(
        model=model,
        tools=[],
        system_prompt=CORRECTION_PROMPT,
        response_format=ToolStrategy(
            schema=CorrectionResult,
            handle_errors=True,
        ),
    )


def _structured_cache_key(provider: str, model_name: str | None, cfg) -> tuple:
    effective_model = cfg.resolve_model_name(model_name)
    return (
        (provider or "").strip().lower(),
        cfg.base_url or "",
        effective_model or "",
        bool(getattr(cfg, "supports_function_calling", True)),
    )


def _candidate_structured_modes(cfg) -> list[str]:
    modes = [STRUCTURED_PROVIDER]
    if getattr(cfg, "supports_function_calling", True):
        modes.append(STRUCTURED_TOOL)
    modes.extend([STRUCTURED_JSON_MODE, STRUCTURED_PROMPT_JSON])
    return modes


def _schema_json_instruction(schema) -> str:
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    return (
        "\n\nReturn only valid JSON. Do not wrap the JSON in Markdown fences. "
        "The root value must match this JSON Schema exactly:\n"
        f"{schema_json}"
    )


def _messages_for_structured_mode(
    *,
    system_prompt: str,
    prompt: str,
    schema,
    mode: str,
) -> list:
    extra = _schema_json_instruction(schema) if mode in {
        STRUCTURED_JSON_MODE,
        STRUCTURED_PROMPT_JSON,
    } else ""
    return [
        SystemMessage(content=f"{system_prompt}{extra}"),
        HumanMessage(content=prompt),
    ]


def _invoke_with_structured_output(model, schema, messages: list, method: str):
    kwargs = {"method": method}
    if method == "json_schema":
        kwargs["strict"] = True
    try:
        return model.with_structured_output(schema, **kwargs).invoke(messages)
    except TypeError:
        kwargs.pop("strict", None)
        return model.with_structured_output(schema, **kwargs).invoke(messages)


def _content_from_message(response) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content)


def _decode_json_from_text(text: str):
    value = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", value, re.DOTALL | re.IGNORECASE)
    if fence:
        value = fence.group(1).strip()

    decoder = json.JSONDecoder()
    try:
        parsed, _ = decoder.raw_decode(value)
        return parsed
    except json.JSONDecodeError:
        pass

    for start, char in enumerate(value):
        if char not in "[{":
            continue
        try:
            parsed, _ = decoder.raw_decode(value[start:])
            return parsed
        except json.JSONDecodeError:
            continue
    raise ValueError("No valid JSON object found in model response")


def _validate_structured_payload(schema, data):
    if isinstance(data, list):
        fields = getattr(schema, "model_fields", {})
        if len(fields) == 1:
            only_field = next(iter(fields))
            data = {only_field: data}
    return schema.model_validate(data)


def _invoke_prompt_json(
    *,
    model,
    schema,
    system_prompt: str,
    prompt: str,
):
    messages = _messages_for_structured_mode(
        system_prompt=system_prompt,
        prompt=prompt,
        schema=schema,
        mode=STRUCTURED_PROMPT_JSON,
    )
    response = model.invoke(messages)
    payload = _decode_json_from_text(_content_from_message(response))
    return _validate_structured_payload(schema, payload)


def _probe_structured_mode(
    *,
    provider: str,
    model_name: str | None,
    mode: str,
) -> None:
    if mode == STRUCTURED_PROMPT_JSON:
        return

    model = _init_model(temperature=0.0, provider=provider, model_name=model_name)
    messages = [
        SystemMessage(content="Return a structured response with ok=true."),
        HumanMessage(content="Return ok=true."),
    ]
    if mode == STRUCTURED_PROVIDER:
        result = _invoke_with_structured_output(
            model,
            _StructuredProbeResult,
            messages,
            "json_schema",
        )
    elif mode == STRUCTURED_TOOL:
        result = _invoke_with_structured_output(
            model,
            _StructuredProbeResult,
            messages,
            "function_calling",
        )
    elif mode == STRUCTURED_JSON_MODE:
        result = _invoke_with_structured_output(
            model,
            _StructuredProbeResult,
            _messages_for_structured_mode(
                system_prompt="Return a structured response.",
                prompt="Return ok=true.",
                schema=_StructuredProbeResult,
                mode=STRUCTURED_JSON_MODE,
            ),
            "json_mode",
        )
    else:
        raise ValueError(f"Unknown structured output mode: {mode}")

    ok = result.get("ok") if isinstance(result, dict) else getattr(result, "ok", False)
    if not ok:
        raise RuntimeError(f"Structured probe returned invalid result for {mode}")


def _resolve_structured_mode(
    *,
    provider: str,
    model_name: str | None,
    cfg,
    candidate_modes: list[str],
) -> str:
    cache_key = _structured_cache_key(provider, model_name, cfg)
    with _structured_mode_lock:
        cached = _structured_mode_cache.get(cache_key)
    if cached in candidate_modes:
        return cached

    for mode in candidate_modes:
        try:
            _probe_structured_mode(
                provider=provider,
                model_name=model_name,
                mode=mode,
            )
            with _structured_mode_lock:
                _structured_mode_cache[cache_key] = mode
            logger.info(
                "Structured output mode selected: provider=%s model=%s mode=%s",
                provider,
                cfg.resolve_model_name(model_name),
                mode,
            )
            return mode
        except Exception as exc:
            logger.info(
                "Structured output probe failed: provider=%s model=%s mode=%s error=%s",
                provider,
                cfg.resolve_model_name(model_name),
                mode,
                exc,
            )

    with _structured_mode_lock:
        _structured_mode_cache[cache_key] = STRUCTURED_PROMPT_JSON
    return STRUCTURED_PROMPT_JSON


def _invoke_tool_strategy(
    *,
    provider: str,
    model_name: str | None,
    prompt: str,
    cache_key: str,
    agent_factory,
):
    full_key = f"{cache_key}_{provider}_{model_name or 'default'}"
    with _agent_cache_lock:
        if full_key not in _agent_cache:
            logger.info(f"创建 Agent 实例 (key={full_key})")
            _agent_cache[full_key] = agent_factory(provider=provider, model_name=model_name)
        agent = _agent_cache[full_key]
    response = agent.invoke(
        {"messages": [{"role": "user", "content": prompt}]},
        config={"recursion_limit": 100},
    )
    structured = response.get("structured_response")
    if not structured:
        raise RuntimeError("ToolStrategy did not return structured_response")
    return structured


def _invoke_structured_by_mode(
    *,
    mode: str,
    prompt: str,
    provider: str,
    model_name: str | None,
    temperature: float,
    schema,
    system_prompt_fallback: str,
    cache_key: str,
    agent_factory,
):
    if mode == STRUCTURED_TOOL:
        model = _init_model(temperature=temperature, provider=provider, model_name=model_name)
        return _invoke_with_structured_output(
            model,
            schema,
            _messages_for_structured_mode(
                system_prompt=system_prompt_fallback,
                prompt=prompt,
                schema=schema,
                mode=mode,
            ),
            "function_calling",
        )

    model = _init_model(temperature=temperature, provider=provider, model_name=model_name)
    if mode == STRUCTURED_PROVIDER:
        return _invoke_with_structured_output(
            model,
            schema,
            _messages_for_structured_mode(
                system_prompt=system_prompt_fallback,
                prompt=prompt,
                schema=schema,
                mode=mode,
            ),
            "json_schema",
        )
    if mode == STRUCTURED_JSON_MODE:
        return _invoke_with_structured_output(
            model,
            schema,
            _messages_for_structured_mode(
                system_prompt=system_prompt_fallback,
                prompt=prompt,
                schema=schema,
                mode=mode,
            ),
            "json_mode",
        )
    if mode == STRUCTURED_PROMPT_JSON:
        return _invoke_prompt_json(
            model=model,
            schema=schema,
            system_prompt=system_prompt_fallback,
            prompt=prompt,
        )
    raise ValueError(f"Unknown structured output mode: {mode}")


def _invoke_structured(
    *,
    prompt: str,
    provider: str,
    model_name: str | None,
    temperature: float,
    schema,
    system_prompt_fallback: str,
    cache_key: str,
    agent_factory,
):
    """通用结构化输出调用，按模型能力自动选择策略。"""
    cfg = settings.get_provider(provider)
    candidate_modes = _candidate_structured_modes(cfg)
    selected_mode = _resolve_structured_mode(
        provider=provider,
        model_name=model_name,
        cfg=cfg,
        candidate_modes=candidate_modes,
    )
    start_index = candidate_modes.index(selected_mode)
    last_error = None
    for mode in candidate_modes[start_index:]:
        mode_start = time.time()
        try:
            logger.info(
                "Structured output invoke start: provider=%s model=%s mode=%s",
                provider,
                cfg.resolve_model_name(model_name),
                mode,
            )
            result = _invoke_structured_by_mode(
                mode=mode,
                prompt=prompt,
                provider=provider,
                model_name=model_name,
                temperature=temperature,
                schema=schema,
                system_prompt_fallback=system_prompt_fallback,
                cache_key=cache_key,
                agent_factory=agent_factory,
            )
            with _structured_mode_lock:
                _structured_mode_cache[_structured_cache_key(provider, model_name, cfg)] = mode
            logger.info(
                "Structured output invoke done: provider=%s model=%s mode=%s elapsed=%.2fs",
                provider,
                cfg.resolve_model_name(model_name),
                mode,
                time.time() - mode_start,
            )
            return result
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Structured output mode failed: provider=%s model=%s mode=%s elapsed=%.2fs error=%s",
                provider,
                cfg.resolve_model_name(model_name),
                mode,
                time.time() - mode_start,
                exc,
            )
            continue

    raise last_error or RuntimeError("Structured output invocation failed")


def invoke_split(prompt: str, provider: str = "openai", model_name: str | None = None):
    """统一调用分割，屏蔽 ToolStrategy vs with_structured_output 差异"""
    return _invoke_structured(
        prompt=prompt, provider=provider, model_name=model_name,
        temperature=0.1, schema=QuestionSplitResult,
        system_prompt_fallback=SPLIT_PROMPT_LITE,
        cache_key="split", agent_factory=create_inner_split_agent,
    )


def invoke_correction(prompt: str, provider: str = "openai", model_name: str | None = None):
    """统一调用纠错，屏蔽 ToolStrategy vs with_structured_output 差异"""
    return _invoke_structured(
        prompt=prompt, provider=provider, model_name=model_name,
        temperature=0.0, schema=CorrectionResult,
        system_prompt_fallback=CORRECTION_PROMPT,
        cache_key="correction", agent_factory=create_correction_agent,
    )


def invoke_structured_output(
    *,
    schema,
    prompt: str,
    system_prompt: str,
    provider: str = "openai",
    model_name: str | None = None,
    temperature: float = 0.0,
    cache_key: str = "generic",
):
    """通用结构化输出调用，供其他 Agent 复用同一套多模式回退。"""
    return _invoke_structured(
        prompt=prompt,
        provider=provider,
        model_name=model_name,
        temperature=temperature,
        schema=schema,
        system_prompt_fallback=system_prompt,
        cache_key=cache_key,
        agent_factory=None,
    )
