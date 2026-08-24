"""OCR 엔진 연결 지점.

백엔드 담당자는 ``recognize`` 함수의 본문만 Tesseract
``image_to_data`` 호출로 교체하면 된다.
"""
from __future__ import annotations

from typing import TypedDict

import numpy as np


class OCRToken(TypedDict):
    text: str
    confidence: float
    bbox: dict[str, int]


def recognize(image: np.ndarray) -> list[OCRToken]:
    """전처리 흑백 이미지에서 토큰을 반환한다. 현재는 빈 연결 구현이다.

    bbox 좌표는 image 기준 절대 픽셀이다. 별도 프로세스에서 실행되므로
    전역 소켓이나 DB 객체를 참조하면 안 된다.
    """
    _ = image
    return []
