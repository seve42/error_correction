"""视觉 OCR 归一化与题目配图落地的关键测试（不调用外部 API）。"""

from PIL import Image

from core.config import settings
from pipeline import utils, workflow
from pipeline.vision_ocr_client import model_supports_vision, normalize_vision_result


def test_model_supports_vision_only_flash_like_models():
    assert model_supports_vision("deepseek-flash")
    assert model_supports_vision("qwen-vl-max")
    assert not model_supports_vision("deepseek-v4-pro")
    assert not model_supports_vision("")


def test_normalize_merges_pen_grading_and_duplicates():
    raw = {
        "questions": [
            {
                "page_index": 0,
                "qid": "1",
                "section": "一、填空",
                "type": "填空题",
                "text": "计算 473÷52，商是（ ）位数。",
                "knowledge_tags": ["除法", "除法", " ", "余数"],
                "blanks": [
                    {
                        "blank_id": "1-1",
                        "printed_hint": "商是（ ）位数",
                        "student_answer": "一",
                        "pen": "black",
                        "red_verdict": "correct",
                    }
                ],
            },
            # 同页同题号的重复结果应被合并（保留更丰富的一份）
            {"page_index": 0, "qid": "1", "section": "一、填空", "type": "填空题", "text": "重复"},
            {
                "page_index": 1,
                "qid": "9",
                "section": "二、判断",
                "type": "判断题",
                "text": "0 除以任何数都得 0。",
                "blanks": [
                    {"blank_id": "9-1", "student_answer": "×", "pen": "black", "red_verdict": "wrong", "red_annotation": "-1"}
                ],
            },
        ],
        "handwriting": [
            {"page_index": 0, "pen": "red", "role": "tick", "text": "✓", "linked_qid": "1"},
            {"page_index": 1, "pen": "black", "role": "answer", "text": "×", "linked_qid": "9"},
        ],
        "grading": [],
    }

    questions = normalize_vision_result(raw, page_count=2)

    assert len(questions) == 2
    first = questions[0]
    assert first["student_answer"] == "一"
    assert first["grading"]["verdict"] == "correct"
    assert first["grading"]["marks"] == [{"type": "tick", "text": "✓"}]
    # 知识点标签去空、去重、最多 3 个；没有标签的题目保持 None
    assert first["knowledge_tags"] == ["除法", "余数"]

    second = questions[1]
    assert second["question_type"] == "判断题"
    assert second["page_index"] == 1
    assert second["grading"]["verdict"] == "wrong"
    assert second["grading"]["blanks"][0]["annotation"] == "-1"


def test_normalize_maps_question_types():
    raw = {
        "questions": [
            {"qid": "1", "text": "选择题干", "options": ["A. 1", "B. 2"]},
            {"qid": "2", "text": "计算下面各题。", "type": "计算题"},
            {"qid": "3", "text": "填空。", "type": "填空题"},
        ],
        "handwriting": [],
        "grading": [],
    }
    types = [q["question_type"] for q in normalize_vision_result(raw, page_count=1)]
    assert types == ["选择题", "解答题", "填空题"]


def test_normalize_keeps_cross_page_indexes():
    raw = {
        "questions": [
            {"page_index": 0, "page_indexes": [0, 1], "qid": "18", "text": "跨页解答题。"},
            {"page_index": 1, "qid": "19", "text": "下一页题目。"},
            {"page_index": 0, "page_indexes": [5, -1, "x"], "qid": "20", "text": "非法页码。"},
        ],
        "handwriting": [],
        "grading": [],
    }

    questions = normalize_vision_result(raw, page_count=2)

    assert questions[0]["page_index"] == 0
    assert questions[0]["page_indexes"] == [0, 1]
    # 排序按 (page_index, 原始顺序)：跨页题与非法页码题都在第 0 页，下一页题目排在最后
    assert questions[1]["page_indexes"] == [0, 1]
    # 越界/非法页码被 clamp/丢弃后仍包含起始页
    assert questions[2]["page_indexes"] == [1]
    assert "figures" not in questions[0]


def test_materialize_question_images_keeps_pages_and_shares_files(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "question_images_dir", tmp_path / "question_images")
    page_paths = []
    for index in range(2):
        path = tmp_path / f"page_{index}.jpg"
        Image.new("RGB", (50, 30), "white").save(path)
        page_paths.append(str(path))

    questions = [
        {"page_index": 0, "page_indexes": [0, 1], "content_blocks": [{"block_type": "text", "content": "跨页题"}]},
        {"page_index": 0, "page_indexes": [0], "content_blocks": [{"block_type": "text", "content": "同页题"}]},
    ]
    pages = [
        {"page_index": 0, "image_path": page_paths[0]},
        {"page_index": 1, "image_path": page_paths[1]},
    ]

    workflow._materialize_question_images(questions, pages)

    cross, single = questions
    # 跨页题目保留两张页面图；同页题目共享同一份文件
    assert len(cross["page_images"]) == 2
    assert cross["page_image"] == cross["page_images"][0]
    assert single["page_images"] == [cross["page_images"][0]]
    assert all(url.startswith(utils.QUESTION_IMAGE_URL_PREFIX) for url in cross["page_images"])
    # 不再裁剪图形，也不向题干追加 image block
    assert [b["block_type"] for b in cross["content_blocks"]] == ["text"]
    assert len(list((tmp_path / "question_images").rglob("*.jpg"))) == 2


def test_organize_question_images_copies_and_is_idempotent(tmp_path, monkeypatch):
    struct_dir = tmp_path / "struct"
    (struct_dir / "imgs").mkdir(parents=True)
    Image.new("RGB", (10, 10), "white").save(struct_dir / "imgs" / "img_in_image_box_1_2_3_4.jpg")
    monkeypatch.setattr(settings, "struct_dir", struct_dir)
    monkeypatch.setattr(settings, "question_images_dir", tmp_path / "question_images")

    ref = "/images/img_in_image_box_1_2_3_4.jpg"
    question = {
        "content_blocks": [{"block_type": "image", "content": ref}],
        "image_refs": [ref],
        "page_image": ref,
        "page_images": [ref],
    }

    # 第二次调用应幂等：引用已指向 /api/question-image/，不再复制
    for _ in range(2):
        utils.organize_question_images([question])

    new_ref = question["content_blocks"][0]["content"]
    assert new_ref.startswith(utils.QUESTION_IMAGE_URL_PREFIX)
    assert question["image_refs"] == [new_ref]
    assert question["page_images"] == [new_ref]
    assert question["page_image"] == new_ref
    assert question["has_image"] is True
    assert len(list((tmp_path / "question_images").rglob("*.jpg"))) == 1
