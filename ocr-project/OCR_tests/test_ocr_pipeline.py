import asyncio
from unittest.mock import AsyncMock

import pytest

from backend.app.config import OCRConfig
from backend.app.ocr_pipeline import FrameEnvelope, LatestFirstQueue, OCRPipeline, PostProcessor


def test_latest_first_queue_keeps_newest_frame() -> None:
    async def runner() -> None:
        queue = LatestFirstQueue(maxsize=2)
        await queue.put(FrameEnvelope("s1", 1, 0.1, b"first"))
        await queue.put(FrameEnvelope("s1", 2, 0.2, b"second"))
        await queue.put(FrameEnvelope("s1", 3, 0.3, b"third"))

        assert queue.get_nowait().frame_id == 3
        assert queue.get_nowait().frame_id == 2
        assert queue.empty() is True

    asyncio.run(runner())


def test_post_processor_filters_noise_and_duplicates() -> None:
    config = OCRConfig(
        confidence_threshold=60.0,
        max_duplicate_window=3,
        valid_word_set={"python", "react", "fastapi"},
    )
    processor = PostProcessor(config)
    payload = [
        {"text": "Python", "confidence": 96.0, "bbox": {"x": 1, "y": 2, "w": 3, "h": 4}},
        {"text": "zzzz", "confidence": 90.0, "bbox": {"x": 1, "y": 2, "w": 3, "h": 4}},
        {"text": "Python", "confidence": 92.0, "bbox": {"x": 1, "y": 2, "w": 3, "h": 4}},
        {"text": "fastapi", "confidence": 65.0, "bbox": {"x": 1, "y": 2, "w": 3, "h": 4}},
    ]

    result = processor.process(payload)
    assert [token.text for token in result] == ["Python", "fastapi"]


def test_pipeline_process_next_accepts_valid_tokens() -> None:
    async def runner() -> None:
        config = OCRConfig(valid_word_set={"python", "streamocr"}, confidence_threshold=60.0)
        pipeline = OCRPipeline(config)
        pipeline.preprocessor.preprocess = lambda payload: b"processed"
        pipeline.worker_pool.submit = AsyncMock(
            return_value=[
                {"text": "Python", "confidence": 96.0, "bbox": {"x": 1, "y": 2, "w": 3, "h": 4}},
                {"text": "StreamOCR", "confidence": 88.0, "bbox": {"x": 1, "y": 2, "w": 3, "h": 4}},
                {"text": "noise", "confidence": 20.0, "bbox": {"x": 1, "y": 2, "w": 3, "h": 4}},
            ]
        )

        await pipeline.enqueue(b"raw-frame", "session-1", 1)
        result = await pipeline.process_next()

        assert result is not None
        assert result.raw_text in {"Python StreamOCR", "StreamOCR Python"}
        assert result.average_confidence > 0

        pipeline.worker_pool.close()

    asyncio.run(runner())
