"""DeepSeek 视觉 OCR 客户端。

把试卷页面图片直接交给 deepseek-flash 的图像理解能力，一次请求输出
「印刷题干 + 黑/蓝笔手写 + 红笔批改」结构化结果，并用两种格式标记：
- tagged_text：行内标记流（<Q>/<HB>/<HR>）
- questions/handwriting/grading：结构化 JSON

不产生擦除图；页面宽高、预览图由本模块归一化后返回，供路由缓存与前端预览。
"""

import base64
import io
import json
import logging
import os
import pathlib
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from typing import Any, Callable, Dict, List, Optional

from PIL import Image, ImageOps

from core.config import settings
from core.task_progress import EventEmitter, build_ai_call_detail

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.deepseek.com"
MAX_PAGES_PER_REQUEST = 6
PAGE_MAX_SIDE = 2000
PREVIEW_MAX_SIDE = 2400
PREVIEW_QUALITY = 88
SPLIT_MAX_SIDE = 2000
# 超过该像素量或长边时额外附原生分辨率分片，抵消单图约 1300×1300 等效分辨率上限
SPLIT_PIXEL_THRESHOLD = 5_500_000
SPLIT_SIDE_THRESHOLD = 2800
SCHEMA_VERSION = 2
# 单次调用输出上限：一页内容丰富的试卷，完整 JSON（结构化数组 + tagged_text 转写）
# 实测可达 1~2 万 token；原先的 16000 会在长试卷上被截断成半截 JSON，解析失败后
# 只能整批重调一次，白白多花几十秒。deepseek-flash 实测接受 32k，留出余量。
MAX_OUTPUT_TOKENS = 32000

SYSTEM_PROMPT = r"""你是「试卷 OCR 与批改理解引擎」。输入是同一份试卷的连续页面（每页可能附原生分辨率分片）。
你只输出一个 JSON 对象，不要输出解释、Markdown 代码块或多余文字。

一、识别原则
1. 印刷体、黑/蓝笔手写、红笔手写三类严格分开：红笔永远是教师批改痕迹，绝不能当作学生答案；
   黑/蓝笔是学生作答或草稿。
2. 只输出看得清的内容；不确定的字符写「?」，看不清写「看不清」；禁止编造、禁止重复填充。
3. 没有明确的红勾(✓)、红叉(×)、半对、分数、扣分、红笔改写或红笔批注时，
   red_verdict 必须是 "unknown"，禁止默认 correct。
   红笔痕迹通常很细（勾、叉、半勾、划线、圈、分数、扣分、订正、文字批注），
   常与黑/蓝笔重叠，必须逐区域扫描；画面一旦出现红笔，grading 或 handwriting(pen=red)
   中至少要有一条对应记录；确实没有红笔时这两处才为空。
4. 红笔原文照抄（如 "-4"、"=1241"、"订正"、"✓"、"×"），不要翻译、不要推断。
5. 题号以卷面为准；同一大题内题号唯一；不要输出任何坐标，不要编造位置。
6. 先完整枚举红笔批改（本任务最关键的信息），再整理题目与黑/蓝笔作答，最后拼 tagged_text。
7. 数学内容一律用 LaTeX：行内公式 $...$，独立成行公式 $$...$$；分数用 \frac{}{}，
   乘除号用 \times、\div，根号用 \sqrt，上下标用 ^、_，角度用 ^\circ。
   禁止输出 Unicode 数学符号（×、÷、≤、≥、√、≠、≈、° 等）和纯文本公式。
8. 表格用 Markdown 表格或 HTML <table> 表示，禁止用空格/制表符对齐排版。
9. 跨页题目合并成一道题，题号用卷面题号；在 page_indexes 中列出题目内容涉及的全部页
   （通常 1 页；题目延续到下一页时两页都要列出）。

二、输出两种表示
A) tagged_text：按阅读顺序拼接的标记流：
   <Q id="题号" section="大题">题干/选项</Q>
   <HB>黑/蓝笔内容</HB>
   <HR kind="tick|cross|half|score|deduction|circle|correction|annotation" q="题号">红笔原文</HR>
B) 结构化数组：questions / handwriting / grading（见下）。

三、JSON 结构（字段可少不可改名）
{
  "subject": "小学数学",
  "questions": [{
    "page_index": 0,
    "page_indexes": [0],
    "qid": "1",
    "section": "一、填空",
    "type": "填空题",
    "text": "题干（公式用 LaTeX，表格用 Markdown/HTML）",
    "options": ["A. ...", "B. ..."],
    "score": "2",
    "knowledge_tags": ["知识点", "知识点"],
    "blanks": [{
      "blank_id": "1-1",
      "printed_hint": "空附近的印刷文字",
      "student_answer": "50",
      "pen": "black|blue|",
      "red_verdict": "correct|wrong|partial|unknown",
      "red_annotation": ""
    }]
  }],
  "handwriting": [{
    "page_index": 0,
    "pen": "red|black|blue",
    "role": "answer|draft|tick|cross|half|score|deduction|circle|correction|annotation",
    "text": "内容",
    "linked_qid": "1",
    "confidence": 0.8
  }],
  "grading": [{
    "page_index": 0,
    "mark": "tick|cross|half|score|deduction|circle|correction|annotation",
    "verdict": "correct|wrong|partial|unknown",
    "score": "",
    "comment": "",
    "linked_qid": "1"
  }],
  "tagged_text": "..."
}

四、其他要求
- page_index / page_indexes 从 0 开始，对应输入的“第 N 页”减 1。
- 跨页题目合并成一道题，题号用卷面题号；分片之间重复内容只输出一次。
- 试卷标题、页码、页眉页脚不要作为题目。
- page_index 是题目起始页；page_indexes 升序列出题目内容涉及的全部页，跨页时如 [0, 1]。
- 每道题给出 1-3 个知识点标签 knowledge_tags：用二级知识点短语（4-8 个汉字），
  同一知识点全卷统一写法；用户消息里给了已有知识点标签时，只有与本题确实匹配才复用，
  不匹配就新造；不要输出同义重复标签，也不要把题型/科目当知识点。
"""

USER_PROMPT = """下面给出试卷的 {page_count} 页（每页可能含 1 张整页图和若干原生分辨率分片）。
请严格按系统提示输出 JSON：先逐条枚举红笔批改（勾/叉/半对/分数/扣分/订正/批注，一个不漏），
再区分题干、黑/蓝笔手写与红笔批改；逐题给出每个空的作答与红笔判定；
红笔批注照抄原文；整页用于把握题号和大题结构，分片用于精读；重复内容只输出一次。
跨页题目合并为一道题，并在 page_indexes 中列出题目涉及的所有页；数学内容一律用 LaTeX。
已有知识点标签（供复用，仅当与本题确实匹配时使用，不匹配就新造）：{existing_tags}
"""


def _resolve_poppler_path() -> Optional[str]:
    """pdf2image 需要 poppler；start.ps1 会注入 PATH，直接调用时回退到 TASK/tools/poppler。"""
    if shutil.which("pdftoppm"):
        return None
    tools_dir = pathlib.Path(settings.project_root).parent / "tools" / "poppler"
    if not tools_dir.exists():
        return None
    for exe in tools_dir.rglob("pdftoppm.exe"):
        return str(exe.parent)
    return None


def _load_pages(file_path: str) -> List[Image.Image]:
    """把一个输入文件转成 RGB 页面图像列表（PDF 按页渲染）。"""
    suffix = pathlib.Path(file_path).suffix.lower()
    if suffix == ".pdf":
        from pdf2image import convert_from_path

        images = convert_from_path(
            str(file_path),
            fmt="png",
            dpi=200,
            poppler_path=_resolve_poppler_path(),
        )
        return [ImageOps.exif_transpose(img).convert("RGB") for img in images]
    im = Image.open(file_path)
    return [ImageOps.exif_transpose(im).convert("RGB")]


def _encode_jpeg(image: Image.Image, max_side: int, quality: int = 85) -> str:
    w, h = image.size
    if max(w, h) > max_side:
        scale = max_side / max(w, h)
        image = image.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode()


def _split_halves(image: Image.Image) -> List[Image.Image]:
    """按长边切两片（约 6% 重叠），给模型保留原生细节。"""
    w, h = image.size
    if w >= h:
        overlap = int(w * 0.06)
        mid = w // 2
        return [
            image.crop((0, 0, mid + overlap, h)),
            image.crop((mid - overlap, 0, w, h)),
        ]
    overlap = int(h * 0.06)
    mid = h // 2
    return [
        image.crop((0, 0, w, mid + overlap)),
        image.crop((0, mid - overlap, w, h)),
    ]


def _normalize_question_type(raw_type: str, options: List[str]) -> str:
    raw = (raw_type or "").strip()
    if options:
        return "选择题"
    if "判断" in raw:
        return "判断题"
    if "填空" in raw or "口算" in raw:
        return "填空题"
    return "解答题"


def _verdict_summary(verdicts: List[str]) -> str:
    known = [v for v in verdicts if v in ("correct", "wrong", "partial")]
    if not known:
        return "unknown"
    if "wrong" in known and "correct" in known:
        return "partial"
    if "wrong" in known:
        return "wrong"
    if "partial" in known:
        return "partial"
    return "correct"


def _question_richness(question: Dict[str, Any]) -> int:
    score = len(question.get("content_blocks", [{}])[0].get("content", "") if question.get("content_blocks") else "")
    score += 40 * len(question.get("options") or [])
    score += 30 * len((question.get("grading") or {}).get("blanks") or [])
    score += len(question.get("student_answer") or "")
    return score


def _normalize_page_indexes(value: Any, fallback: int, page_count: int) -> List[int]:
    """把题目涉及的页归一化为升序、去重、合法的 0 基页码列表。

    跨页题目会同时保留多张页面图，因此这里允许两个及以上页码。
    """
    indexes: List[int] = []
    if isinstance(value, (list, tuple)):
        for item in value:
            try:
                indexes.append(int(item))
            except (TypeError, ValueError):
                continue
    if not indexes:
        indexes = [fallback]
    limit = max(page_count - 1, 0)
    cleaned = {min(max(index, 0), limit) for index in indexes}
    cleaned.add(min(max(fallback, 0), limit))
    return sorted(cleaned)


def _merge_page_indexes(first: Any, second: Any) -> List[int]:
    """合并同一题多次出现时收集到的页码。"""
    merged = {int(item) for item in list(first or []) + list(second or []) if isinstance(item, int)}
    return sorted(merged)


def _normalize_knowledge_tags(value: Any) -> Optional[List[str]]:
    """把知识点标签规整为去空、去重、最多 3 个；没有有效标签返回 None。"""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return None
    tags: List[str] = []
    for item in value:
        tag = str(item).strip() if item is not None else ""
        if tag and tag not in tags:
            tags.append(tag)
    return tags[:3] or None


def normalize_vision_result(raw: Dict[str, Any], page_count: int) -> List[Dict[str, Any]]:
    """把模型输出归一化为项目现有 questions 结构（附加 student_answer / grading）。"""
    raw_questions = raw.get("questions") or []
    raw_handwriting = raw.get("handwriting") or []
    raw_grading = raw.get("grading") or []

    def _page_index(value: Any) -> int:
        try:
            idx = int(value)
        except (TypeError, ValueError):
            return 0
        return min(max(idx, 0), max(page_count - 1, 0))

    def _text(value: Any) -> str:
        return str(value).strip() if value is not None else ""

    questions: List[Dict[str, Any]] = []
    for order, q in enumerate(raw_questions):
        if not isinstance(q, dict):
            continue
        text = _text(q.get("text"))
        options = [str(o).strip() for o in (q.get("options") or []) if str(o).strip()]
        if not text and not options:
            continue
        qid = _text(q.get("qid")) or str(order + 1)
        page_index = _page_index(q.get("page_index", q.get("page", 0)))
        section = _text(q.get("section")) or None
        question_type = _normalize_question_type(q.get("type"), options)
        # 跨页题目保留涉及的全部页，后续按页保存整页原图
        page_indexes = _normalize_page_indexes(q.get("page_indexes"), page_index, page_count)

        blanks_raw = q.get("blanks") or []
        answers: List[str] = []
        grading_blanks: List[Dict[str, Any]] = []
        verdicts: List[str] = []
        annotations: List[str] = []
        for blank in blanks_raw:
            if not isinstance(blank, dict):
                continue
            answer = _text(blank.get("student_answer"))
            verdict = _text(blank.get("red_verdict")) or "unknown"
            annotation = _text(blank.get("red_annotation"))
            if answer:
                answers.append(answer)
            if verdict not in ("correct", "wrong", "partial", "unknown"):
                verdict = "unknown"
            verdicts.append(verdict)
            if annotation:
                annotations.append(annotation)
            if answer or verdict != "unknown" or annotation:
                grading_blanks.append(
                    {
                        "blank_id": _text(blank.get("blank_id")),
                        "printed_hint": _text(blank.get("printed_hint")),
                        "answer": answer,
                        "verdict": verdict,
                        "annotation": annotation,
                    }
                )

        # 红笔/黑笔标记按 linked_qid 归到题目
        marks: List[Dict[str, Any]] = []
        black_marks: List[str] = []
        for item in raw_handwriting:
            if not isinstance(item, dict):
                continue
            linked = _text(item.get("linked_qid"))
            # 未标注题号的笔迹（如页首总分）不强行归到每道题，避免污染题目级数据
            if not linked or linked != qid:
                continue
            pen = _text(item.get("pen")).lower() or "black"
            if pen == "red":
                marks.append(
                    {
                        "type": _text(item.get("role")) or "annotation",
                        "text": _text(item.get("text")),
                    }
                )
            elif _text(item.get("text")):
                black_marks.append(_text(item.get("text")))
        for item in raw_grading:
            if not isinstance(item, dict):
                continue
            linked = _text(item.get("linked_qid"))
            if not linked or linked != qid:
                continue
            mark = _text(item.get("mark"))
            score = _text(item.get("score"))
            if mark or score:
                marks.append({"type": mark or "annotation", "text": score or _text(item.get("comment"))})
                verdict = _text(item.get("verdict"))
                if verdict in ("correct", "wrong", "partial"):
                    verdicts.append(verdict)

        student_answer = _text(q.get("student_answer"))
        if not student_answer:
            student_answer = "；".join(answers)
        if not student_answer and black_marks:
            student_answer = "；".join(black_marks)[:500]

        grading: Optional[Dict[str, Any]] = None
        if grading_blanks or marks:
            grading = {
                "verdict": _verdict_summary(verdicts),
                "annotation": "；".join(dict.fromkeys(annotations)),
                "blanks": grading_blanks,
                "marks": marks,
            }
            if grading["verdict"] == "unknown" and not grading["annotation"]:
                grading = grading if marks else None

        question = {
            "question_id": qid,
            "section_title": section,
            "question_type": question_type,
            "content_blocks": [{"block_type": "text", "content": text}],
            "options": options or None,
            "option_images": None,
            "has_formula": "$" in text or "\\frac" in text,
            "has_image": False,
            "image_refs": None,
            "knowledge_tags": _normalize_knowledge_tags(q.get("knowledge_tags")),
            "needs_correction": False,
            "ocr_issues": None,
            "student_answer": student_answer or None,
            "has_handwriting": bool(student_answer or black_marks),
            "grading": grading,
            "page_index": page_index,
            "page_indexes": page_indexes,
            "_order": order,
        }
        questions.append(question)

    # 同页同题号去重：保留内容更丰富的一份，位置不变；页码取并集
    deduped: List[Dict[str, Any]] = []
    index_by_key: Dict[tuple, int] = {}
    for question in questions:
        key = (question["page_index"], question["section_title"] or "", question["question_id"])
        if key in index_by_key:
            current = deduped[index_by_key[key]]
            merged_pages = _merge_page_indexes(current.get("page_indexes"), question.get("page_indexes"))
            if _question_richness(question) > _question_richness(current):
                question["page_indexes"] = merged_pages
                deduped[index_by_key[key]] = question
            else:
                current["page_indexes"] = merged_pages
            continue
        index_by_key[key] = len(deduped)
        deduped.append(question)

    deduped.sort(key=lambda q: (q["page_index"], q["_order"]))
    for question in deduped:
        question.pop("_order", None)
    return deduped


def _build_page_blocks(
    questions: List[Dict[str, Any]],
    raw_handwriting: List[Dict[str, Any]],
    raw_grading: List[Dict[str, Any]],
) -> Dict[int, List[Dict[str, Any]]]:
    blocks: Dict[int, List[Dict[str, Any]]] = {}

    def _add(page_index: int, block: Dict[str, Any]) -> None:
        bucket = blocks.setdefault(page_index, [])
        if len(bucket) < 80:
            bucket.append(block)

    for question in questions:
        content = (question.get("content_blocks") or [{}])[0].get("content", "")
        options = question.get("options") or []
        label = f"[{question.get('question_id', '')}] {content}"
        if options:
            label += "\n" + "  ".join(options)
        _add(question["page_index"], {"label": "question", "content": label, "bbox": None, "pen": "printed"})

    for item in raw_handwriting or []:
        if not isinstance(item, dict):
            continue
        try:
            page_index = int(item.get("page_index") or 0)
        except (TypeError, ValueError):
            page_index = 0
        pen = str(item.get("pen") or "black").lower()
        label = "handwriting_red" if pen == "red" else "handwriting_black"
        _add(page_index, {"label": label, "content": str(item.get("text") or ""), "bbox": None, "pen": pen})

    for item in raw_grading or []:
        if not isinstance(item, dict):
            continue
        try:
            page_index = int(item.get("page_index") or 0)
        except (TypeError, ValueError):
            page_index = 0
        parts = [str(item.get("mark") or ""), str(item.get("score") or ""), str(item.get("comment") or "")]
        content = " ".join(p for p in parts if p).strip()
        _add(page_index, {"label": "grading_red", "content": content, "bbox": None, "pen": "red"})

    return blocks


def _resolve_worker_count(max_workers: Optional[int], chunk_count: int) -> int:
    """并发路数：显式参数 > settings.vision_concurrency，且不超过批次数。

    批次数少于配置路数时不需要更多线程，避免空转线程和超发请求占用模型额度。
    """
    configured = max_workers if max_workers is not None else getattr(settings, "vision_concurrency", 1)
    try:
        configured = int(configured)
    except (TypeError, ValueError):
        configured = 1
    return max(1, min(configured, max(chunk_count, 1)))


class _BatchProgress:
    """批次完成计数，保证并发下进度只增不减。

    各批次完成顺序不固定，若直接用批次序号当进度，进度条会来回跳；这里统一
    按“已完成批次数 / 总批次数”发进度。
    """

    def __init__(self, total: int):
        self._total = max(total, 1)
        self._done = 0
        self._lock = threading.Lock()

    def current(self) -> float:
        with self._lock:
            return self._done / self._total

    def complete_next(self) -> float:
        with self._lock:
            self._done = min(self._total, self._done + 1)
            return self._done / self._total


class VisionOutputTruncated(RuntimeError):
    """模型输出触达 max_tokens 上限，返回的 JSON 被截断（不能按普通错误重试）。"""


def _page_range_label(metas: List[Dict[str, Any]]) -> str:
    """把页面元信息转成“第 3 页 / 第 3-5 页”的中文标签（1 基，和界面一致）。"""
    first = metas[0]["page_index"] + 1
    last = metas[-1]["page_index"] + 1
    return f"第 {first} 页" if first == last else f"第 {first}-{last} 页"


def _merge_chunk_data(base: Optional[Dict[str, Any]], incoming: Dict[str, Any]) -> Dict[str, Any]:
    """合并两段识别结果；拆分重试时保持与正常合并（按批顺序）一致。"""
    merged = base or {"subject": "", "questions": [], "handwriting": [], "grading": [], "tagged_text": ""}
    if not merged.get("subject"):
        merged["subject"] = str(incoming.get("subject") or "").strip()
    for key in ("questions", "handwriting", "grading"):
        merged[key].extend(incoming.get(key) or [])
    tagged = str(incoming.get("tagged_text") or "").strip()
    if tagged:
        existing = str(merged.get("tagged_text") or "").strip()
        merged["tagged_text"] = f"{existing}\n\n{tagged}".strip() if existing else tagged
    return merged


def _accumulate_meta(target: Dict[str, Any], meta: Dict[str, Any]) -> None:
    """累加多次调用的元信息（拆批/精简重试后，一次批次可能对应多次模型调用）。"""
    usage = meta.get("usage") or {}
    total = target.setdefault("usage", {})
    for key, value in usage.items():
        if isinstance(value, (int, float)):
            total[key] = (total.get(key) or 0) + value
    target["duration"] = float(target.get("duration") or 0) + float(meta.get("duration") or 0)
    target["attempt"] = int(target.get("attempt") or 0) + int(meta.get("attempt") or 1)
    target["finish_reason"] = meta.get("finish_reason") or target.get("finish_reason") or ""
    target["raw_output"] = f"{target.get('raw_output') or ''}\n{meta.get('raw_output') or ''}".strip()


class VisionOCRClient:
    """deepseek-flash 视觉 OCR 客户端（OpenAI 兼容 chat completions）。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "",
        model: str = "deepseek-flash",
        timeout: int = 240,
    ):
        if not api_key:
            raise ValueError("缺少视觉 OCR Provider 的 API Key")
        self.api_key = api_key
        self.base_url = base_url or DEFAULT_BASE_URL
        self.model = model or "deepseek-flash"
        self.timeout = timeout

    def _client(self):
        from openai import OpenAI
        import httpx

        kwargs = {"api_key": self.api_key, "base_url": self.base_url, "timeout": self.timeout}
        if not settings.trust_env:
            kwargs["http_client"] = httpx.Client(trust_env=False, timeout=self.timeout)
        return OpenAI(**kwargs)

    def _call(
        self,
        messages: List[Dict[str, Any]],
        on_retry: Optional[Callable[[int, Exception], None]] = None,
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """调用一次视觉模型，返回 (解析结果, 调用元信息)。

        元信息带上 usage、耗时、尝试次数和模型原始 content，调用方据此推送
        “AI 输出”事件。重试前的 on_retry 回调让上层能发“重试中”告警；本方法
        不保存任何跨线程可变状态，可被多个 worker 线程同时调用。
        """
        last_error: Optional[Exception] = None
        for attempt in range(1, 3):
            started = time.time()
            try:
                response = self._client().chat.completions.create(
                    model=self.model,
                    messages=messages,
                    response_format={"type": "json_object"},
                    max_tokens=MAX_OUTPUT_TOKENS,
                    temperature=0.2,
                    extra_body={"thinking": {"type": "disabled"}},
                )
                choice = response.choices[0]
                content = choice.message.content or ""
                finish_reason = str(getattr(choice, "finish_reason", "") or "")
                if not content.strip():
                    raise ValueError("模型返回空 content")
                try:
                    data = json.loads(content)
                except json.JSONDecodeError as exc:
                    # 输出被 max_tokens 截断时 JSON 一定不闭合；这种失败重调同样请求只会
                    # 再被截断一次，所以要交给上层拆页/精简，而不是原地重试。
                    if finish_reason == "length" or (len(content) > 20000 and "Unterminated" in str(exc)):
                        raise VisionOutputTruncated(
                            f"输出达到 {MAX_OUTPUT_TOKENS} token 上限被截断（{len(content)} 字符，finish={finish_reason or 'unknown'}）"
                        ) from exc
                    raise
                usage = response.usage.model_dump() if response.usage else {}
                duration = time.time() - started
                logger.info(
                    "视觉 OCR 调用完成: %.1fs, tokens=%s(completion=%s), finish=%s, questions=%s",
                    duration,
                    usage.get("total_tokens"),
                    usage.get("completion_tokens"),
                    finish_reason or "unknown",
                    len(data.get("questions") or []),
                )
                try:
                    from core.usage import record_native_usage

                    record_native_usage("openai", self.model, usage, stage="vision_ocr")
                except Exception:  # noqa: BLE001
                    logger.warning("记录视觉 OCR 用量失败", exc_info=True)
                return data, {
                    "usage": usage,
                    "duration": duration,
                    "attempt": attempt,
                    "finish_reason": finish_reason,
                    "raw_output": content,
                }
            except VisionOutputTruncated:
                # 输出超长是确定性失败：让上层按页拆分或切换精简模式，避免白等一次重试
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning("视觉 OCR 第 %s 次调用失败: %s", attempt, exc)
                if attempt < 2:
                    if on_retry is not None:
                        try:
                            on_retry(attempt, exc)
                        except Exception:  # noqa: BLE001
                            logger.warning("视觉 OCR 重试事件回调失败", exc_info=True)
                    time.sleep(2)
        raise RuntimeError(f"视觉 OCR 调用失败: {type(last_error).__name__}: {last_error}")

    def _build_chunk_messages(
        self,
        chunk_metas: List[Dict[str, Any]],
        chunk_images: List[Image.Image],
        compact: bool = False,
        existing_tags: str = "",
    ) -> List[Dict[str, Any]]:
        """构造一个批次的 messages；图片编码放在 worker 线程内完成。

        PIL 缩放与 base64 编码是 CPU 密集型操作，随批次一起下放到 worker，
        多个批次可以同时编码，同时避免在主线程串行编码拖慢首个请求。
        compact=True 用于输出被截断后的补救：优先保证 JSON 完整闭合，牺牲 tagged_text。
        """
        content: List[Dict[str, Any]] = [
            {
                "type": "text",
                "text": USER_PROMPT.format(
                    page_count=len(chunk_metas), existing_tags=existing_tags or "无"
                )
                + "\n本次页码：" + "、".join(str(p["page_index"]) for p in chunk_metas)
                + (
                    "\n\n【重要】上一次输出超出长度上限被截断：这次必须精简 —— "
                    "tagged_text 输出空字符串，knowledge_tags 仍要保留（每题 1-3 个），"
                    "questions/handwriting/grading 只保留必要字段与红笔批改信息，"
                    "任何内容都不要重复，确保 JSON 完整闭合。"
                    if compact
                    else ""
                ),
            }
        ]
        for meta, image in zip(chunk_metas, chunk_images):
            content.append(
                {
                    "type": "text",
                    "text": f"第 {meta['page_index']} 页整页（原图 {meta['width']}x{meta['height']} 像素）",
                }
            )
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{_encode_jpeg(image, PAGE_MAX_SIDE)}", "detail": "original"},
                }
            )
            if image.width * image.height > SPLIT_PIXEL_THRESHOLD or max(image.width, image.height) > SPLIT_SIDE_THRESHOLD:
                for half_index, half in enumerate(_split_halves(image), 1):
                    content.append(
                        {
                            "type": "text",
                            "text": f"第 {meta['page_index']} 页原生分辨率分片 {half_index}/2（原图 {image.width}x{image.height} 像素）",
                        }
                    )
                    content.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{_encode_jpeg(half, SPLIT_MAX_SIDE, quality=90)}", "detail": "original"},
                        }
                    )
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]

    def _recognize_chunk(
        self,
        chunk_metas: List[Dict[str, Any]],
        chunk_images: List[Image.Image],
        *,
        batch_label: str,
        page_label: str,
        emitter: EventEmitter,
        on_retry: Optional[Callable[[int, Exception], None]] = None,
        existing_tags: str = "",
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """识别一个（子）批次，输出被截断时按页拆分或切精简模式自救。

        模型单次输出有 token 上限，页数多时结构化 JSON 可能写不完。与其原样重调
        （同样会再截断一次），不如把批次对半拆开分别识别，最后按顺序合并；
        单页仍截断时再让模型省略 tagged_text 精简输出。
        """
        try:
            return self._call(
                self._build_chunk_messages(chunk_metas, chunk_images, existing_tags=existing_tags),
                on_retry=on_retry,
            )
        except VisionOutputTruncated as exc:
            if len(chunk_metas) > 1:
                emitter.emit(
                    step="vision_batch",
                    stage="视觉识别",
                    level="warn",
                    message=f"{batch_label}（{page_label}）输出超长被截断：{exc}，拆成两批分别识别",
                )
                middle = len(chunk_metas) // 2
                parts = [
                    (chunk_metas[:middle], chunk_images[:middle]),
                    (chunk_metas[middle:], chunk_images[middle:]),
                ]
                merged: Optional[Dict[str, Any]] = None
                combined: Dict[str, Any] = {}
                for part_metas, part_images in parts:
                    data, meta = self._recognize_chunk(
                        part_metas,
                        part_images,
                        batch_label=batch_label,
                        page_label=_page_range_label(part_metas),
                        emitter=emitter,
                        on_retry=on_retry,
                        existing_tags=existing_tags,
                    )
                    merged = _merge_chunk_data(merged, data)
                    _accumulate_meta(combined, meta)
                return merged or {}, combined

            emitter.emit(
                step="vision_batch",
                stage="视觉识别",
                level="warn",
                message=f"{batch_label}（{page_label}）输出超长被截断：{exc}，改用精简模式重试",
            )
            return self._call(
                self._build_chunk_messages(
                    chunk_metas, chunk_images, compact=True, existing_tags=existing_tags
                ),
                on_retry=on_retry,
            )

    def _run_chunk(
        self,
        chunk_index: int,
        chunk_metas: List[Dict[str, Any]],
        chunk_images: List[Image.Image],
        total_chunks: int,
        emitter: EventEmitter,
        progress: _BatchProgress,
        existing_tags: str = "",
    ) -> Dict[str, Any]:
        """识别一个批次，并把开始/识别中/重试/完成/失败事件推给前端。"""
        # 事件里用 1 基页码，和「第 a-b 页」的中文文案保持一致
        first_page = chunk_metas[0]["page_index"] + 1
        last_page = chunk_metas[-1]["page_index"] + 1
        page_label = _page_range_label(chunk_metas)
        batch_label = f"第 {chunk_index + 1}/{total_chunks} 批"
        emitter.emit(
            step="vision_batch",
            stage="视觉识别",
            message=f"正在识别{batch_label}：{page_label}",
            progress=progress.current(),
        )

        def _on_retry(attempt: int, exc: Exception) -> None:
            emitter.emit(
                step="vision_batch",
                stage="视觉识别",
                level="warn",
                message=f"{batch_label}（{page_label}）第 {attempt} 次调用失败：{type(exc).__name__}: {exc}，2 秒后重试",
                progress=progress.current(),
            )

        # 视觉识别是非流式调用，一批要几十秒，期间前端只有“正在识别”一条日志。
        # 定时推一条“已用 N 秒”，让用户知道任务还在跑而不是卡死。
        started = time.time()
        stop_heartbeat = threading.Event()

        def _heartbeat() -> None:
            while not stop_heartbeat.wait(5.0):
                emitter.emit(
                    step="vision_batch",
                    stage="视觉识别",
                    message=f"{batch_label}（{page_label}）识别中… 已用 {time.time() - started:.0f}s",
                    progress=progress.current(),
                )

        heartbeat = threading.Thread(target=_heartbeat, name="vision-heartbeat", daemon=True)
        heartbeat.start()
        try:
            chunk_result, meta = self._recognize_chunk(
                chunk_metas,
                chunk_images,
                batch_label=batch_label,
                page_label=page_label,
                emitter=emitter,
                on_retry=_on_retry,
                existing_tags=existing_tags,
            )
        except Exception as exc:  # noqa: BLE001
            stop_heartbeat.set()
            emitter.emit(
                step="vision_batch",
                stage="视觉识别",
                level="error",
                message=f"{batch_label}（{page_label}）识别失败：{exc}",
                progress=progress.current(),
            )
            raise
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=1.0)

        usage = meta.get("usage") or {}
        duration = float(meta.get("duration") or 0)
        emitter.emit(
            step="vision_batch",
            stage="视觉识别",
            level="success",
            message=(
                f"{batch_label}识别完成：{page_label}，耗时 {duration:.1f}s，"
                f"tokens {int(usage.get('total_tokens') or 0)}，题目 {len(chunk_result.get('questions') or [])} 道"
            ),
            progress=progress.complete_next(),
            detail=build_ai_call_detail(
                model=self.model,
                pages=list(range(first_page, last_page + 1)),
                duration=duration,
                tokens=usage,
                attempt=int(meta.get("attempt") or 1),
                output=str(meta.get("raw_output") or ""),
            ),
        )
        return chunk_result

    def _run_chunks(
        self,
        chunks: List[tuple],
        workers: int,
        emitter: EventEmitter,
        total_chunks: int,
        existing_tags: str = "",
    ) -> List[Dict[str, Any]]:
        """并发执行所有批次，并按批次顺序返回结果。

        顺序在收集阶段保证：按 futures 列表顺序取 result()，因此下游合并逻辑
        与串行实现完全一致（题目顺序稳定，subject 取第一段非空值）。
        """
        progress = _BatchProgress(total_chunks)
        results: List[Optional[Dict[str, Any]]] = [None] * total_chunks
        if total_chunks == 1:
            _, chunk_metas, chunk_images = chunks[0]
            results[0] = self._run_chunk(
                0, chunk_metas, chunk_images, total_chunks, emitter, progress, existing_tags
            )
            return results  # type: ignore[return-value]
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="vision-ocr") as pool:
            # copy_context 把调用方的 usage 上下文带进 worker，否则并发后
            # record_native_usage 在子线程里拿不到当前计费上下文。
            futures = [
                pool.submit(
                    copy_context().run,
                    self._run_chunk,
                    index,
                    chunk_metas,
                    chunk_images,
                    total_chunks,
                    emitter,
                    progress,
                    existing_tags,
                )
                for index, chunk_metas, chunk_images in chunks
            ]
            for index, future in enumerate(futures):
                results[index] = future.result()
        return results  # type: ignore[return-value]

    def parse_document(
        self,
        file_paths: List[str],
        output_dir: str,
        emitter: Optional[EventEmitter] = None,
        max_workers: Optional[int] = None,
        existing_tags: str = "",
    ) -> Dict[str, Any]:
        """解析一份试卷（可多文件/多页），返回 pages + questions + tagged_text。

        多个批次并发调用视觉模型（默认 settings.vision_concurrency 路），结果仍按
        批次顺序合并，输出与串行实现一致。emitter 传 None 时完全退化为纯函数式
        行为，方便测试和其它调用方复用。existing_tags 为逗号分隔的已有知识点标签，
        供模型优先复用、保持全库标签写法一致。
        """
        emitter = emitter or EventEmitter(None)
        os.makedirs(output_dir, exist_ok=True)
        page_metas: List[Dict[str, Any]] = []
        page_images: List[Image.Image] = []
        page_number = 0

        for file_path in file_paths:
            try:
                images = _load_pages(file_path)
            except Exception as exc:  # noqa: BLE001
                logger.error("页面加载失败 %s: %s", file_path, exc)
                raise RuntimeError(f"页面加载失败（{os.path.basename(file_path)}）：{exc}") from exc
            for image in images:
                preview = image.copy()
                w, h = preview.size
                if max(w, h) > PREVIEW_MAX_SIDE:
                    scale = PREVIEW_MAX_SIDE / max(w, h)
                    preview = preview.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
                preview_name = f"vision_{uuid.uuid4().hex[:8]}_p{page_number + 1:02d}.jpg"
                preview_path = os.path.join(output_dir, preview_name)
                preview.save(preview_path, format="JPEG", quality=PREVIEW_QUALITY)
                page_metas.append(
                    {
                        "page_index": page_number,
                        "width": image.width,
                        "height": image.height,
                        "image_path": preview_path,
                    }
                )
                page_images.append(image)
                page_number += 1

        if not page_images:
            raise RuntimeError("没有可识别的页面")

        total = len(page_images)
        chunks = [
            (
                chunk_index,
                page_metas[start : start + MAX_PAGES_PER_REQUEST],
                page_images[start : start + MAX_PAGES_PER_REQUEST],
            )
            for chunk_index, start in enumerate(range(0, total, MAX_PAGES_PER_REQUEST))
        ]
        total_chunks = len(chunks)
        workers = _resolve_worker_count(max_workers, total_chunks)
        emitter.emit(
            step="vision_load",
            stage="加载页面",
            level="success",
            message=f"已加载 {total} 页图片，分 {total_chunks} 批并行识别（{workers} 路）",
            progress=0.0,
        )

        chunk_results = self._run_chunks(chunks, workers, emitter, total_chunks, existing_tags)

        merged: Dict[str, Any] = {
            "subject": "",
            "questions": [],
            "handwriting": [],
            "grading": [],
            "tagged_text": "",
        }
        for chunk_result in chunk_results:
            merged = _merge_chunk_data(merged, chunk_result)

        emitter.emit(
            step="vision_merge",
            stage="汇总结果",
            level="success",
            message=f"汇总完成：{total} 页，{len(merged['questions'])} 道题目",
            progress=1.0,
        )

        questions = normalize_vision_result(merged, len(page_metas))
        blocks_by_page = _build_page_blocks(questions, merged["handwriting"], merged["grading"])
        pages = [
            {
                "page_index": meta["page_index"],
                "width": meta["width"],
                "height": meta["height"],
                "image_path": meta["image_path"],
                "blocks": blocks_by_page.get(meta["page_index"], []),
            }
            for meta in page_metas
        ]
        return {
            "engine": "vision",
            "schema_version": SCHEMA_VERSION,
            "subject": merged["subject"],
            "pages": pages,
            "questions": questions,
            "tagged_text": merged["tagged_text"][:200_000],
        }


# ── 供路由使用的凭据/能力辅助 ─────────────────────────────────────


VISION_MODEL_HINTS = ("flash", "vision", "vl", "gemini")


def model_supports_vision(model_name: Optional[str]) -> bool:
    name = (model_name or "").strip().lower()
    if not name:
        return False
    return any(hint in name for hint in VISION_MODEL_HINTS)


def resolve_vision_credentials(db, user_id: Optional[int]) -> Optional[Dict[str, str]]:
    """优先个人 openai Provider，其次平台托管；跳过不支持图像理解的模型。"""
    from db import crud

    personal = crud.get_active_provider(db, user_id, "openai") if user_id else None
    system = crud.get_active_system_provider(db, "openai")
    for provider, source in ((personal, "personal"), (system, "system")):
        if not provider or not provider.api_key:
            continue
        model = provider.model_name or "deepseek-flash"
        if not model_supports_vision(model):
            continue
        return {
            "api_key": provider.api_key,
            "base_url": provider.base_url or DEFAULT_BASE_URL,
            "model": model,
            "source": source,
        }
    return None
