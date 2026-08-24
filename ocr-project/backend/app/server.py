from __future__ import annotations

import base64
import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .config import OCRConfig
from .ocr_pipeline import OCRPipeline


CONFIG = OCRConfig(
    frame_queue_size=5,
    target_fps=2,
    worker_count=2,
    confidence_threshold=35.0,
    min_laplacian_variance=60.0,
    preprocess_width=1440,
    preprocess_height=900,
    jpeg_quality=85,
    max_duplicate_window=5,
    tesseract_config="--oem 1 --psm 11 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.,:;!?()/%#@+-=<>",
    # Dictionary filtering remains configurable. For live mobile testing we keep it off
    # until the OCR output is stable, then a real English corpus can be layered in.
    valid_word_set=set(),
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    pipeline = OCRPipeline(CONFIG)
    app.state.pipeline = pipeline
    try:
        yield
    finally:
        await pipeline.close()


app = FastAPI(title="StreamOCR Backend", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "streamocr-backend",
        "ocr_ready": True,
    }


@app.get("/api/info")
async def info() -> dict[str, Any]:
    return {
        "service": "StreamOCR Backend",
        "protocols": ["WebSocket", "REST"],
        "websocket_url": "/ws/stream",
        "status": "ready",
    }


@app.post("/api/session/start")
async def start_session() -> dict[str, Any]:
    return {"session_id": uuid.uuid4().hex, "status": "created"}


@app.websocket("/ws/stream")
async def stream(websocket: WebSocket) -> None:
    await websocket.accept()
    pipeline: OCRPipeline = app.state.pipeline
    session_id = "browser-session"
    frame_id = 0

    await websocket.send_json({"type": "ready", "session_id": session_id})

    try:
        while True:
            payload = await websocket.receive_json()
            message_type = str(payload.get("type", "")).lower()

            if message_type == "hello":
                session_id = str(payload.get("session_id") or session_id)
                await websocket.send_json({"type": "ack", "session_id": session_id, "status": "connected"})
                continue

            if message_type != "frame":
                continue

            frame_b64 = str(payload.get("image", ""))
            if not frame_b64:
                continue

            if "," in frame_b64:
                frame_b64 = frame_b64.split(",", 1)[1]

            try:
                image_bytes = base64.b64decode(frame_b64, validate=True)
            except ValueError:
                await websocket.send_json({"type": "error", "detail": "Invalid base64 image payload"})
                continue

            session_key = str(payload.get("session_id") or session_id)
            frame_id += 1
            await pipeline.enqueue(image_bytes, session_key, frame_id, float(payload.get("timestamp") or asyncio.get_running_loop().time()))

            result = await pipeline.process_next()
            if result is None or not result.raw_text:
                await websocket.send_json({"type": "ocr_status", "session_id": session_key, "status": "no_text_detected"})
                continue

            await websocket.send_json(
                {
                    "type": "ocr_result",
                    "session_id": session_key,
                    "text": result.raw_text,
                    "average_confidence": round(result.average_confidence, 2),
                    "tokens": [
                        {"text": token.text, "confidence": round(token.confidence, 2), "bbox": token.bbox}
                        for token in result.tokens
                    ],
                }
            )
    except WebSocketDisconnect:
        await websocket.close()
