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
import time
import uuid
from typing import Any, Dict, List, Optional

from PIL import Image, ImageOps

from core.config import settings

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
5. 题号以卷面为准；同一大题内题号唯一；除 figures 的 bbox 外不要输出坐标，不要编造位置。
6. 先完整枚举红笔批改（本任务最关键的信息），再整理题目与黑/蓝笔作答，最后拼 tagged_text。
7. 数学内容一律用 LaTeX：行内公式 $...$，独立成行公式 $$...$$；分数用 \frac{}{}，
   乘除号用 \times、\div，根号用 \sqrt，上下标用 ^、_，角度用 ^\circ。
   禁止输出 Unicode 数学符号（×、÷、≤、≥、√、≠、≈、° 等）和纯文本公式。
8. 表格用 Markdown 表格或 HTML <table> 表示，禁止用空格/制表符对齐排版。
9. 题目自带图形/几何图/统计图/电路图/表格时，在 figures 中给出裁剪范围，
   供后续从页面图片中剪出配图；没有图形的题目不要输出 figures。

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
    "qid": "1",
    "section": "一、填空",
    "type": "填空题",
    "text": "题干（公式用 LaTeX，表格用 Markdown/HTML）",
    "options": ["A. ...", "B. ..."],
    "score": "2",
    "figures": [{
      "bbox": [120, 340, 780, 620],
      "role": "figure|table|chart|diagram",
      "caption": "图注/表名（没有则留空）"
    }],
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
- page_index 从 0 开始，对应输入的“第 N 页”减 1。
- 跨页题目合并成一道题，题号用卷面题号；分片之间重复内容只输出一次。
- 试卷标题、页码、页眉页脚不要作为题目。
- figures.bbox 是相对整页的 0-1000 归一化坐标 [x1, y1, x2, y2]，(x1,y1) 为左上、(x2,y2) 为右下；
  必须完整包住图形本身（含坐标轴、标注、单位、图注），不要框进题干正文或整页；
  一题多图按从左到右、从上到下顺序输出，同一张图不要重复；边界看不清时宁可略大也不要切掉内容。
"""

USER_PROMPT = """下面给出试卷的 {page_count} 页（每页可能含 1 张整页图和若干原生分辨率分片）。
请严格按系统提示输出 JSON：先逐条枚举红笔批改（勾/叉/半对/分数/扣分/订正/批注，一个不漏），
再区分题干、黑/蓝笔手写与红笔批改；逐题给出每个空的作答与红笔判定；
红笔批注照抄原文；整页用于把握题号和大题结构，分片用于精读；重复内容只输出一次。
题目自带图形/表格时，用 figures 给出相对整页 0-1000 的 bbox 裁剪框；数学内容一律用 LaTeX。
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


FIGURE_ROLES = {"figure", "table", "chart", "diagram"}


def _clean_figure_bbox(value: Any) -> Optional[List[int]]:
    """把模型给出的 bbox 归一化为 0-1000 整数坐标，非法时返回 None。"""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    coords = []
    for item in value:
        try:
            coords.append(float(item))
        except (TypeError, ValueError):
            return None
    try:
        x1, y1, x2, y2 = [min(max(int(round(c)), 0), 1000) for c in coords]
    except (ValueError, OverflowError):  # NaN / inf
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    return [x1, y1, x2, y2]


def clean_figures(value: Any) -> List[Dict[str, Any]]:
    """清洗图形裁剪信息：保留 bbox/role/caption，丢弃非法 bbox 并按 bbox 去重。"""
    figures: List[Dict[str, Any]] = []
    seen = set()
    if not isinstance(value, list):
        return figures
    for item in value:
        if not isinstance(item, dict):
            continue
        bbox = _clean_figure_bbox(item.get("bbox"))
        if bbox is None:
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in FIGURE_ROLES:
            role = "figure"
        caption = str(item.get("caption") or "").strip()
        key = tuple(bbox)
        if key in seen:
            continue
        seen.add(key)
        figures.append({"bbox": bbox, "role": role, "caption": caption})
    return figures


def _merge_figures(first: Any, second: Any) -> List[Dict[str, Any]]:
    """合并同一题多次出现时收集到的 figures，按 bbox 去重。"""
    merged: List[Dict[str, Any]] = []
    seen = set()
    for figure in list(first or []) + list(second or []):
        key = tuple(figure.get("bbox") or [])
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(figure)
    return merged


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
        # 图形裁剪信息留给 workflow 裁图；此处只做清洗，非法 bbox 直接丢弃
        figures = clean_figures(q.get("figures"))

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
            "knowledge_tags": None,
            "needs_correction": False,
            "ocr_issues": None,
            "student_answer": student_answer or None,
            "has_handwriting": bool(student_answer or black_marks),
            "grading": grading,
            "figures": figures,
            "page_index": page_index,
            "_order": order,
        }
        questions.append(question)

    # 同页同题号去重：保留内容更丰富的一份，位置不变；figures 取并集
    deduped: List[Dict[str, Any]] = []
    index_by_key: Dict[tuple, int] = {}
    for question in questions:
        key = (question["page_index"], question["section_title"] or "", question["question_id"])
        if key in index_by_key:
            current = deduped[index_by_key[key]]
            merged_figures = _merge_figures(current.get("figures"), question.get("figures"))
            if _question_richness(question) > _question_richness(current):
                question["figures"] = merged_figures
                deduped[index_by_key[key]] = question
            else:
                current["figures"] = merged_figures
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

    def _call(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        last_error: Optional[Exception] = None
        for attempt in range(1, 3):
            started = time.time()
            try:
                response = self._client().chat.completions.create(
                    model=self.model,
                    messages=messages,
                    response_format={"type": "json_object"},
                    max_tokens=16000,
                    temperature=0.2,
                    extra_body={"thinking": {"type": "disabled"}},
                )
                content = response.choices[0].message.content or ""
                if not content.strip():
                    raise ValueError("模型返回空 content")
                data = json.loads(content)
                usage = response.usage.model_dump() if response.usage else {}
                logger.info(
                    "视觉 OCR 调用完成: %.1fs, tokens=%s, questions=%s",
                    time.time() - started,
                    usage.get("total_tokens"),
                    len(data.get("questions") or []),
                )
                try:
                    from core.usage import record_native_usage

                    record_native_usage("openai", self.model, usage, stage="vision_ocr")
                except Exception:  # noqa: BLE001
                    logger.warning("记录视觉 OCR 用量失败", exc_info=True)
                return data
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning("视觉 OCR 第 %s 次调用失败: %s", attempt, exc)
                if attempt < 2:
                    time.sleep(2)
        raise RuntimeError(f"视觉 OCR 调用失败: {type(last_error).__name__}: {last_error}")

    def parse_document(self, file_paths: List[str], output_dir: str) -> Dict[str, Any]:
        """解析一份试卷（可多文件/多页），返回 pages + questions + tagged_text。"""
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

        merged: Dict[str, Any] = {
            "subject": "",
            "questions": [],
            "handwriting": [],
            "grading": [],
            "tagged_text": "",
        }
        total = len(page_images)
        for start in range(0, total, MAX_PAGES_PER_REQUEST):
            chunk = page_metas[start : start + MAX_PAGES_PER_REQUEST]
            chunk_images = page_images[start : start + MAX_PAGES_PER_REQUEST]
            content: List[Dict[str, Any]] = [
                {
                    "type": "text",
                    "text": USER_PROMPT.format(page_count=len(chunk))
                    + "\n本次页码：" + "、".join(str(p["page_index"]) for p in chunk),
                }
            ]
            for meta, image in zip(chunk, chunk_images):
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

            chunk_result = self._call(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": content},
                ]
            )
            if not merged["subject"]:
                merged["subject"] = str(chunk_result.get("subject") or "").strip()
            for key in ("questions", "handwriting", "grading"):
                merged[key].extend(chunk_result.get(key) or [])
            tagged = str(chunk_result.get("tagged_text") or "").strip()
            if tagged:
                merged["tagged_text"] = (merged["tagged_text"] + "\n\n" + tagged).strip() if merged["tagged_text"] else tagged

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
