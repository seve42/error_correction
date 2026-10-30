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


def test_normalize_keeps_cleaned_figures():
    raw = {
        "questions": [
            {
                "page_index": 0,
                "qid": "1",
                "text": "根据统计图回答问题。",
                "figures": [
                    {"bbox": [120, 340, 780, 620], "role": "chart", "caption": "统计图"},
                    {"bbox": [5, 5, 5, 5], "role": "figure"},  # 宽高为 0，非法丢弃
                    {"bbox": [100, 100, 200, 200], "role": "unknown"},  # role 归一为 figure
                    {"bbox": [120, 340, 780, 620], "role": "chart"},  # 重复 bbox 去重
                    {"bbox": [-20, 0, 1200, 1100], "role": "table"},  # 越界坐标 clamp
                ],
            }
        ],
        "handwriting": [],
        "grading": [],
    }

    (question,) = normalize_vision_result(raw, page_count=1)
    figures = question["figures"]
    assert [f["bbox"] for f in figures] == [
        [120, 340, 780, 620],
        [100, 100, 200, 200],
        [0, 0, 1000, 1000],
    ]
    assert [f["role"] for f in figures] == ["chart", "figure", "table"]
    assert figures[0]["caption"] == "统计图"
    # 真正裁图在 workflow 落地，归一化阶段仍标记为无图
    assert question["has_image"] is False
    assert question["image_refs"] is None


def test_materialize_question_images_crops_and_keeps_full_page(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "question_images_dir", tmp_path / "question_images")
    page_path = tmp_path / "page_0.jpg"
    Image.new("RGB", (200, 100), "white").save(page_path)

    questions = [
        {
            "page_index": 0,
            "content_blocks": [{"block_type": "text", "content": "如图，求阴影部分面积。"}],
            "figures": [
                {"bbox": [0, 0, 500, 1000], "role": "figure", "caption": ""},
                {"bbox": [0, 0, 1, 1], "role": "figure", "caption": ""},  # 裁剪不足 8px，丢弃
            ],
        }
    ]
    pages = [{"page_index": 0, "image_path": str(page_path), "width": 200, "height": 100}]

    workflow._materialize_question_images(questions, pages)

    question = questions[0]
    image_blocks = [b for b in question["content_blocks"] if b["block_type"] == "image"]
    assert len(image_blocks) == 1
    assert question["image_refs"] == [image_blocks[0]["content"]]
    assert question["has_image"] is True
    assert question["page_image"].startswith(utils.QUESTION_IMAGE_URL_PREFIX)

    rel = image_blocks[0]["content"][len(utils.QUESTION_IMAGE_URL_PREFIX):]
    crop = settings.question_images_dir / rel
    with Image.open(crop) as image:
        assert image.size == (100, 100)
    assert crop.name.startswith("figure_1_")


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
    }

    # 第二次调用应幂等：引用已指向 /api/question-image/，不再复制
    for _ in range(2):
        utils.organize_question_images([question])

    new_ref = question["content_blocks"][0]["content"]
    assert new_ref.startswith(utils.QUESTION_IMAGE_URL_PREFIX)
    assert question["image_refs"] == [new_ref]
    assert question["page_image"] == new_ref
    assert question["has_image"] is True
    assert len(list((tmp_path / "question_images").rglob("*.jpg"))) == 1
