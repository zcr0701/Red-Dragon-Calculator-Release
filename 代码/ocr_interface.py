from typing import Any, Iterable, List, Optional, Tuple

import cv2
import mss
import numpy as np
from paddleocr import PaddleOCR


Box = Tuple[int, int, int, int]


def create_ocr(lang: str = "ch", **kwargs: Any) -> PaddleOCR:
    options = {
        "lang": lang,
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_textline_orientation": False
    }
    options.update(kwargs)
    return PaddleOCR(**options)


def capture_region(box: Box) -> Optional[np.ndarray]:
    x1, y1, x2, y2 = [int(v) for v in box]

    if x2 <= x1 or y2 <= y1:
        return None

    monitor = {
        "left": x1,
        "top": y1,
        "width": x2 - x1,
        "height": y2 - y1
    }

    with mss.mss() as sct:
        img = np.array(sct.grab(monitor))

    return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)


def extract_texts_from_paddle_result(result: Iterable[Any]) -> List[str]:
    texts: List[str] = []

    for item in result:
        data = getattr(item, "json", None)

        if callable(data):
            data = data()

        if not isinstance(data, dict):
            continue

        res = data.get("res", {})

        if isinstance(res, dict):
            rec_texts = res.get("rec_texts", [])

            if isinstance(rec_texts, list):
                texts.extend(str(text) for text in rec_texts)

    return texts


def normalize_ocr_text(text: str) -> str:
    return "\n".join(
        line.strip()
        for line in text.splitlines()
        if line.strip()
    )


class OCRInterface:
    def __init__(self, lang: str = "ch", **ocr_kwargs: Any):
        self.ocr = create_ocr(lang=lang, **ocr_kwargs)

    def recognize_box(self, box: Box) -> str:
        img = capture_region(box)

        if img is None:
            return ""

        result = self.ocr.predict(img)
        texts = extract_texts_from_paddle_result(result)

        return normalize_ocr_text("\n".join(texts))
