"""응답 경로와 분리된 latest-first OCR 실행기."""
from __future__ import annotations

import asyncio
from concurrent.futures import ProcessPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
from typing import Awaitable, Callable

import numpy as np

from ocr_connector import OCRToken, recognize


@dataclass(frozen=True)
class OCRJob:
    seq: int
    image: np.ndarray
    source_w: int
    source_h: int
    captured_at_ms: int


ResultCallback = Callable[[OCRJob, list[OCRToken], float], Awaitable[None]]


class LatestOCRPipeline:
    def __init__(self, on_result: ResultCallback, workers: int = 1) -> None:
        self._on_result = on_result
        self._queue: asyncio.Queue[OCRJob] = asyncio.Queue(maxsize=1)
        self._pool = ProcessPoolExecutor(max_workers=workers)
        self._task: asyncio.Task | None = None
        self.dropped = 0

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    def submit(self, job: OCRJob) -> None:
        if self._queue.full():
            with suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
                self._queue.task_done()
                self.dropped += 1
        self._queue.put_nowait(job)

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        self._pool.shutdown(wait=False, cancel_futures=True)

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            job = await self._queue.get()
            started = loop.time()
            try:
                tokens = await loop.run_in_executor(self._pool, recognize, job.image)
                await self._on_result(job, tokens, (loop.time() - started) * 1000)
            finally:
                self._queue.task_done()
