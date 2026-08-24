from __future__ import annotations

import asyncio
import math
import re
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

try:
    import numpy as np
except ModuleNotFoundError:  # pragma: no cover - dependency guards
    np = None

try:
    import cv2
except ModuleNotFoundError:  # pragma: no cover - dependency guards
    cv2 = None

try:
    import pytesseract
except ModuleNotFoundError:  # pragma: no cover - dependency guards
    pytesseract = None

from .config import OCRConfig, DEFAULT_CONFIG


@dataclass(slots=True)
class FrameEnvelope:
    session_id: str
    frame_id: int
    timestamp: float
    payload: bytes
    source: str = "browser"


@dataclass(slots=True)
class TokenResult:
    text: str
    confidence: float
    bbox: dict[str, float]


@dataclass(slots=True)
class OCRResult:
    tokens: list[TokenResult] = field(default_factory=list)
    raw_text: str = ""
    average_confidence: float = 0.0


class LatestFirstQueue:
    """Bounded queue that keeps the newest entries first for OCR processing."""

    def __init__(self, maxsize: int = 6) -> None:
        self.maxsize = maxsize
        self._queue: deque[FrameEnvelope] = deque(maxlen=maxsize)

    def put_nowait(self, item: FrameEnvelope) -> None:
        if len(self._queue) >= self.maxsize:
            self._queue.popleft()
        self._queue.append(item)

    async def put(self, item: FrameEnvelope) -> None:
        self.put_nowait(item)

    def get_nowait(self) -> Optional[FrameEnvelope]:
        if not self._queue:
            return None
        return self._queue.pop()

    def empty(self) -> bool:
        return len(self._queue) == 0


class OpenCVPreprocessor:
    """Normalize camera frames before OCR extraction."""

    def __init__(self, config: OCRConfig = DEFAULT_CONFIG) -> None:
        self.config = config

    def ensure_runtime(self) -> None:
        if cv2 is None or np is None:
            raise RuntimeError("OpenCV and NumPy are required for preprocessing.")

    def preprocess(self, frame: bytes) -> bytes:
        self.ensure_runtime()
        arr = np.frombuffer(frame, dtype=np.uint8)
        image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("Unable to decode frame payload as image data.")

        if image.shape[0] > self.config.preprocess_height or image.shape[1] > self.config.preprocess_width:
            scale = min(
                self.config.preprocess_width / image.shape[1],
                self.config.preprocess_height / image.shape[0],
            )
            new_width = max(1, int(image.shape[1] * scale))
            new_height = max(1, int(image.shape[0] * scale))
            image = cv2.resize(image, (new_width, new_height), interpolation=cv2.INTER_AREA)

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)

        if gray.std() < 10:
            raise ValueError("Frame has insufficient contrast for OCR analysis.")

        threshold = cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31,
            10,
        )

        laplacian = cv2.Laplacian(gray, cv2.CV_64F).var()
        if laplacian < self.config.min_laplacian_variance:
            raise ValueError(
                f"Frame blurred: Laplacian variance {laplacian:.2f} below threshold {self.config.min_laplacian_variance}."
            )

        rotated = self._deskew(threshold)
        encoded = cv2.imencode(".png", rotated)[1]
        return encoded.tobytes()

    def _deskew(self, image: np.ndarray) -> np.ndarray:
        coords = np.column_stack(np.where(image > 0))
        if coords.size == 0:
            return image
        angle = self._estimate_rotation(coords)
        if abs(angle) < 1e-3:
            return image
        (h, w) = image.shape[:2]
        center = (w // 2, h // 2)
        matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
        rotated = cv2.warpAffine(image, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
        return rotated

    def _estimate_rotation(self, coords: np.ndarray) -> float:
        dx = coords[:, 1] - coords[:, 1].mean()
        dy = coords[:, 0] - coords[:, 0].mean()
        angle = math.degrees(math.atan2(dy.sum(), dx.sum()))
        return float(angle)


class TesseractWorkerPool:
    """Worker pool for OCR execution in a separate process context."""

    def __init__(self, config: OCRConfig = DEFAULT_CONFIG) -> None:
        self.config = config
        self._max_workers = max(1, config.worker_count)
        self._executor = ProcessPoolExecutor(max_workers=self._max_workers)

    def submit(self, frame_bytes: bytes) -> asyncio.Future[Any]:
        loop = asyncio.get_running_loop()
        return loop.run_in_executor(self._executor, self._run_worker, frame_bytes, self.config.tesseract_config)

    @staticmethod
    def _run_worker(frame_bytes: bytes, tesseract_config: str) -> list[dict[str, Any]]:
        if pytesseract is None:
            raise RuntimeError("pytesseract is not installed. Install backend/requirements.txt first.")
        if cv2 is None:
            raise RuntimeError("OpenCV is not installed. Install backend/requirements.txt first.")

        image = cv2.imdecode(np.frombuffer(frame_bytes, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise ValueError("Unable to decode preprocessed frame before OCR.")

        result = pytesseract.image_to_data(
            image,
            config=tesseract_config,
            output_type=pytesseract.Output.DICT,
        )

        tokens: list[dict[str, Any]] = []
        for index, value in enumerate(result["text"]):
            cleaned = value.strip()
            if not cleaned:
                continue
            confidence = float(result["conf"][index]) if result["conf"][index] != "-1" else 0.0
            tokens.append(
                {
                    "text": cleaned,
                    "confidence": confidence,
                    "bbox": {
                        "x": float(result["left"][index]),
                        "y": float(result["top"][index]),
                        "w": float(result["width"][index]),
                        "h": float(result["height"][index]),
                    },
                }
            )
        return tokens

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)


class PostProcessor:
    """Applies confidence and dictionary filtering, then removes near-identical OCR tokens."""

    def __init__(self, config: OCRConfig = DEFAULT_CONFIG) -> None:
        self.config = config
        self._word_set = config.normalized_word_set()
        self._recent_tokens: deque[str] = deque(maxlen=max(1, config.max_duplicate_window))

    def process(self, token_payload: Iterable[dict[str, Any]]) -> list[TokenResult]:
        accepted: list[TokenResult] = []
        for token in token_payload:
            text = str(token.get("text", "")).strip()
            if not text:
                continue
            if float(token.get("confidence", 0.0)) < self.config.confidence_threshold:
                continue
            normalized = self._normalize_word(text)
            if not normalized:
                continue
            if self._word_set and normalized not in self._word_set:
                continue
            if normalized in self._recent_tokens:
                continue
            self._recent_tokens.append(normalized)
            accepted.append(
                TokenResult(
                    text=text,
                    confidence=float(token.get("confidence", 0.0)),
                    bbox=dict(token.get("bbox", {})),
                )
            )
        return accepted

    def _normalize_word(self, value: str) -> str:
        return re.sub(r"[^A-Za-z]", "", value).lower()


class OCRPipeline:
    """Queue-driven OCR processing flow matching the StreamOCR backend contract."""

    def __init__(self, config: OCRConfig = DEFAULT_CONFIG) -> None:
        self.config = config
        self.queue: LatestFirstQueue = LatestFirstQueue(maxsize=config.frame_queue_size)
        self.preprocessor = OpenCVPreprocessor(config)
        self.worker_pool = TesseractWorkerPool(config)
        self.postprocessor = PostProcessor(config)

    async def enqueue(self, frame: bytes, session_id: str, frame_id: int, timestamp: Optional[float] = None) -> None:
        await self.queue.put(FrameEnvelope(session_id=session_id, frame_id=frame_id, timestamp=float(timestamp or asyncio.get_running_loop().time()), payload=frame))

    async def process_next(self) -> Optional[OCRResult]:
        if self.queue.empty():
            return None

        frame_envelope = self.queue.get_nowait()
        if frame_envelope is None:
            return None

        try:
            processed = self.preprocessor.preprocess(frame_envelope.payload)
            token_payload = await self.worker_pool.submit(processed)
            accepted = self.postprocessor.process(token_payload)
            if not accepted:
                return OCRResult()
            raw_text = " ".join(token.text for token in accepted)
            average_confidence = sum(token.confidence for token in accepted) / len(accepted)
            return OCRResult(tokens=accepted, raw_text=raw_text, average_confidence=average_confidence)
        except Exception:
            return OCRResult()

    async def process_until_empty(self) -> list[OCRResult]:
        results: list[OCRResult] = []
        while True:
            item = await asyncio.to_thread(self.queue.get_nowait)
            if item is None:
                break
            processed = self.preprocessor.preprocess(item.payload)
            token_payload = await self.worker_pool.submit(processed)
            accepted = self.postprocessor.process(token_payload)
            if accepted:
                raw_text = " ".join(token.text for token in accepted)
                results.append(
                    OCRResult(
                        tokens=accepted,
                        raw_text=raw_text,
                        average_confidence=sum(token.confidence for token in accepted) / len(accepted),
                    )
                )
        return results

    async def close(self) -> None:
        self.worker_pool.close()
