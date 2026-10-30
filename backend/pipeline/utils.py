"""
通用工具函数
包含确定性的工具逻辑，不需要LLM参与
"""

import os
import re
import shutil
import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Dict, Any
from dotenv import load_dotenv
from rich.console import Console

from core.config import settings

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
console = Console()

# 题目配图（图形裁剪与整页原图）的对外访问前缀，由 routes/upload.py 提供
QUESTION_IMAGE_URL_PREFIX = "/api/question-image/"


def _safe_join_under(root: str, relative: str) -> str | None:
    """把相对路径拼到 root 下；越界（目录遍历）时返回 None。"""
    root_abs = os.path.abspath(str(root))
    target = os.path.abspath(os.path.join(root_abs, relative))
    if target != root_abs and not target.startswith(root_abs + os.sep):
        return None
    return target


def resolve_question_image_path(ref: str) -> str | None:
    """把题目图片引用解析为磁盘文件路径，解析不到返回 None。

    支持：
    - /api/question-image/<rel>：question_images_dir 下的持久配图；
    - /images/<name>、imgs/<name>：PaddleOCR 分支的 struct_dir/imgs 配图；
    - 绝对文件路径（历史 split 结果中的真实图片路径）。
    """
    ref = (ref or "").strip()
    if not ref:
        return None
    if ref.startswith(QUESTION_IMAGE_URL_PREFIX):
        path = _safe_join_under(
            settings.question_images_dir, ref[len(QUESTION_IMAGE_URL_PREFIX):]
        )
        return path if path and os.path.isfile(path) else None
    if ref.startswith("/images/"):
        candidate = os.path.join(
            str(settings.struct_dir), "imgs", ref[len("/images/"):]
        )
    elif ref.startswith("imgs/"):
        candidate = os.path.join(
            str(settings.struct_dir), ref[len("imgs/"):]
        )
    elif os.path.isabs(ref):
        candidate = ref
    else:
        return None
    return candidate if os.path.isfile(candidate) else None


def organize_question_images(questions: List[Dict[str, Any]]) -> None:
    """入库前把题目图片复制到持久目录，并把引用重写为 /api/question-image/...。

    幂等：已指向 /api/question-image/ 的引用不做处理；解析不到真实文件时保留原引用。
    重写范围：image content_block 的 content、image_refs、page_images/page_image。
    """
    root = str(settings.question_images_dir)
    for question in questions or []:
        local_dir = None
        copied: Dict[str, str] = {}

        def _persist(ref):
            nonlocal local_dir
            url = (ref or "").strip()
            if not url or url.startswith(QUESTION_IMAGE_URL_PREFIX):
                return url
            if url in copied:
                return copied[url]
            source = resolve_question_image_path(url)
            if not source:
                return url
            if local_dir is None:
                local_dir = uuid.uuid4().hex
                os.makedirs(os.path.join(root, local_dir), exist_ok=True)
            suffix = os.path.splitext(source)[1].lower() or ".jpg"
            name = f"{uuid.uuid4().hex[:12]}{suffix}"
            shutil.copyfile(source, os.path.join(root, local_dir, name))
            new_url = f"{QUESTION_IMAGE_URL_PREFIX}{local_dir}/{name}"
            copied[url] = new_url
            return new_url

        blocks = question.get("content_blocks") or []
        has_image_block = False
        for block in blocks:
            if block.get("block_type") == "image":
                has_image_block = True
                block["content"] = _persist(block.get("content"))
        image_refs = question.get("image_refs")
        if isinstance(image_refs, list):
            rewritten: List[str] = []
            for ref in image_refs:
                new_ref = _persist(ref)
                if new_ref and new_ref not in rewritten:
                    rewritten.append(new_ref)
            question["image_refs"] = rewritten or None
        page_images = question.get("page_images")
        if isinstance(page_images, list):
            persisted_pages = [p for p in (_persist(ref) for ref in page_images) if p]
            question["page_images"] = persisted_pages or None
            page_images = question["page_images"]
        if page_images:
            question["page_image"] = page_images[0]
        elif question.get("page_image"):
            question["page_image"] = _persist(question.get("page_image"))
        if has_image_block:
            question["has_image"] = True


def prepare_input(file_path: str) -> List[str]:
    """
    步骤1: 准备输入文件

    验证文件格式，将文件复制到 PAGES_DIR 统一管理。
    PDF 和图片均直接保留原始格式，由 PaddleOCR API 统一处理。

    Args:
        file_path: 输入文件路径 (PDF或图片文件)

    Returns:
        List[str]: 标准化后的文件路径列表（单个文件返回单元素列表）

    Raises:
        FileNotFoundError: 文件不存在
        ValueError: 不支持的文件格式
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"文件不存在: {file_path}")

    pages_dir = settings.pages_dir
    os.makedirs(pages_dir, exist_ok=True)

    file_ext = Path(file_path).suffix.lower()
    file_stem = Path(file_path).stem

    supported_exts = {f".{ext}" for ext in settings.allowed_extensions}
    if file_ext not in supported_exts:
        raise ValueError(
            f"不支持的文件格式: {file_ext}\n"
            f"支持的格式: {', '.join(sorted(supported_exts))}"
        )

    console.print(f"[cyan]正在处理文件: {file_path}[/cyan]")

    output_path = os.path.join(pages_dir, f"{file_stem}{file_ext}")
    shutil.copy2(file_path, output_path)
    console.print(f"[green]  [OK] 文件已复制: {output_path}[/green]")

    return [output_path]


def export_wrongbook(
    questions: List[Dict[str, Any]],
    selected_uids: List[str],
    output_path: str = None,
    batch_info: Dict[str, Any] = None
) -> str:
    """
    步骤5: 导出错题本

    将用户选择的题目导出为Markdown格式的错题本，并可选入库到数据库

    Args:
        questions: 题目列表
        selected_uids: 用户选择的题目 uid 列表
        output_path: 输出Markdown文件路径（可选）
        batch_info: 批次信息（用于入库），包含 original_filename, subject 等

    Returns:
        str: 导出的Markdown文件路径
    """
    if output_path is None:
        os.makedirs(settings.results_dir, exist_ok=True)
        output_path = os.path.join(settings.results_dir, "wrongbook.md")

    # 用 uid 过滤选中的题目，保持原始顺序
    uid_set = set(selected_uids)
    selected_questions = [
        q
        for q in questions
        if q.get('uid') in uid_set or q.get('question_id') in uid_set
    ]

    # 构建Markdown内容
    md_content = "# 错题本\n\n"
    md_content += f"> 共收录 {len(selected_questions)} 道题目\n\n"
    md_content += "---\n\n"

    output_dir = os.path.dirname(os.path.abspath(output_path))
    rel_struct_dir = os.path.relpath(settings.struct_dir, output_dir)
    rel_question_images_dir = os.path.relpath(settings.question_images_dir, output_dir)
    _img_src_re = re.compile(
        r'src="((?:/images/|imgs/|/api/question-image/)[^"]+)"'
    )

    def _resolve_image_path(p: str) -> str:
        """将 Agent 输出的图片路径统一转为 Markdown 可用的相对路径。

        Agent 有时输出 '/images/xxx.jpg'（Flask 路由），有时输出 'imgs/xxx.jpg'
        （LLM 对路径的自由变形），统一映射到 struct_dir/imgs/ 下的相对路径；
        视觉分支的题目配图（/api/question-image/...）映射到 question_images 目录。
        """
        p = p.strip()
        if p.startswith(QUESTION_IMAGE_URL_PREFIX):
            return f"{rel_question_images_dir}/{p[len(QUESTION_IMAGE_URL_PREFIX):]}"
        if p.startswith("/images/"):
            return f"{rel_struct_dir}/imgs/{p[len('/images/'):]}"
        if p.startswith("imgs/"):
            return f"{rel_struct_dir}/imgs/{p[len('imgs/'):]}"
        return p

    def _fix_html_image_src(html: str) -> str:
        """修正 HTML 内 <img src> 中的图片路径，与独立 image block 保持一致。"""
        return _img_src_re.sub(
            lambda m: f'src="{_resolve_image_path(m.group(1))}"', html
        )

    _INIT = object()  # 初始哨兵，确保第一个有 section_title 的题一定触发标题输出
    current_section = _INIT
    unsorted_started = False
    serial = 0  # 全局序号

    for q in selected_questions:
        section = q.get('section_title')

        if section:
            # 有 section_title：正常输出大题分组标题
            if section != current_section:
                current_section = section
                md_content += f"## {section}\n\n"
        else:
            # section=None：首次遇到时输出"（未分类）"节标题
            if not unsorted_started:
                unsorted_started = True
                md_content += "## （未分类）\n\n"

        serial += 1
        md_content += f"### {serial}. 题目 {q.get('question_id', '')} ({q.get('question_type', '未知')})\n\n"

        # 获取图片引用列表，用于填充空的 image block
        image_refs = q.get('image_refs') or []
        rendered_images = set()  # 记录已渲染的图片路径（原始路径，供 image_refs 兜底比对）

        # 预扫描：收集已内嵌在 HTML 文本块（如 table）中的图片路径，避免重复渲染
        html_embedded = set()
        for block in q.get('content_blocks', []):
            if block.get('block_type') == 'text':
                for m in _img_src_re.finditer(block.get('content', '')):
                    html_embedded.add(m.group(1).strip())

        # 添加内容块（只有 text 和 image，公式以 LaTeX 标记嵌入 text 中）
        for block in q.get('content_blocks', []):
            if block['block_type'] == 'text':
                content = block['content'].replace('\n', '  \n')
                # 修正 HTML 内嵌图片路径（table 中的 <img src>）
                content = _fix_html_image_src(content)
                md_content += f"{content}\n\n"
            elif block['block_type'] == 'image':
                image_path = block.get('content', '').strip()
                if not image_path or image_path in html_embedded:
                    # 已在 HTML 表格中渲染过，跳过独立 image block 避免重复
                    continue
                rendered_images.add(image_path)
                resolved = _resolve_image_path(image_path)
                if resolved:
                    md_content += f"![图片]({resolved})\n\n"

        # 添加选项
        if q.get('options'):
            for option in q['options']:
                md_content += f"{option}  \n"
            md_content += "\n"

        # 兜底：渲染 image_refs 中未被 content_blocks 覆盖、也未在 HTML 中出现的图片
        remaining_images = [p for p in image_refs if p not in rendered_images and p not in html_embedded]
        if remaining_images:
            for image_path in remaining_images:
                md_content += f"![图片]({_resolve_image_path(image_path)})\n\n"

        answer_prefix = "####" if section else "###"
        my_answer = (q.get("student_answer") or "").strip()
        md_content += f"{answer_prefix} 我的答案\n\n"
        if my_answer:
            md_content += f"{my_answer}\n\n"
        else:
            md_content += "_（请在此处填写你的答案）_\n\n"

        grading = q.get("grading") or {}
        if grading:
            verdict_text = {
                "correct": "判对",
                "wrong": "判错",
                "partial": "部分对",
                "unknown": "待确认",
            }.get(grading.get("verdict"), "")
            grading_line = f"> 红笔批改：{verdict_text or '有标记'}"
            annotation = (grading.get("annotation") or "").strip()
            if annotation:
                grading_line += f"；批注：{annotation}"
            mark_texts = [
                str(m.get("text") or m.get("type") or "").strip()
                for m in (grading.get("marks") or [])
                if (m.get("text") or m.get("type"))
            ]
            if mark_texts:
                grading_line += "；标记：" + "、".join(dict.fromkeys(mark_texts))
            md_content += grading_line + "\n\n"

        md_content += f"{answer_prefix} 正确答案\n\n"
        md_content += "_（请在此处填写正确答案）_\n\n"

        md_content += f"{answer_prefix} 解析\n\n"
        md_content += "_（请在此处填写解题思路和知识点）_\n\n"

        md_content += "---\n\n"

    # 保存Markdown
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(md_content)

    console.print(f"[green][OK] 错题本已导出: {output_path}[/green]")

    return output_path


def simplify_ocr_results(ocr_results: list) -> List[Dict[str, Any]]:
    """简化 OCR 结果，只保留 split 所需字段

    将 PaddleOCR API 原始返回结果精简为 block_label、block_content、block_order 三个字段。
    供 workflow 和 retry_ocr 共享使用。

    Args:
        ocr_results: PaddleOCR API 原始返回结果列表

    Returns:
        简化后的 OCR 数据列表，每项包含 page_index 和 blocks
    """
    simplified = []
    page_index = 0
    for result in ocr_results:
        if "layoutParsingResults" not in result:
            continue
        for page in result["layoutParsingResults"]:
            if "prunedResult" not in page:
                continue
            parsing_res = page["prunedResult"].get("parsing_res_list", [])
            slim_blocks = []
            # block_label 归一化映射：OCR API 可能返回新标签，统一转为已知类型
            _label_normalize = {
                "display_formula": "formula",
                "number": "text",
            }
            for b in parsing_res:
                content = b.get("block_content", "")
                label = _label_normalize.get(b.get("block_label", ""), b.get("block_label", ""))
                if label in ("image", "chart") and not content:
                    bbox = b.get("block_bbox")
                    if bbox:
                        prefix = "img_in_chart_box" if label == "chart" else "img_in_image_box"
                        content = f"/images/{prefix}_{int(bbox[0])}_{int(bbox[1])}_{int(bbox[2])}_{int(bbox[3])}.jpg"
                # 统一 OCR 原始输出中的图片路径：imgs/ → /images/
                if 'src="imgs/' in content:
                    content = content.replace('src="imgs/', 'src="/images/')
                slim_blocks.append({
                    "block_label": label,
                    "block_content": content,
                    "block_order": b.get("block_order"),
                })
            simplified.append({
                "page_index": page_index,
                "blocks": slim_blocks,
            })
            page_index += 1
    return simplified


def run_async(coro):
    """在同步上下文中运行异步协程（兼容已有事件循环）

    如果当前线程已有 running 事件循环（如在 Flask/Jupyter 中），
    则在新线程中创建事件循环运行；否则直接 asyncio.run。

    Args:
        coro: 异步协程对象

    Returns:
        协程返回值
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    else:
        return asyncio.run(coro)
