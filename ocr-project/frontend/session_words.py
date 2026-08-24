"""PoC용 메모리 세션 단어 저장소. 서버 재시작 시 비워진다."""
from __future__ import annotations

import csv
import io
import threading
import time
import uuid


class SessionWords:
    def __init__(self) -> None:
        self._items: dict[str, dict] = {}
        self._seen: set[str] = set()
        self._lock = threading.Lock()

    def add_tokens(self, tokens: list[dict], seq: int) -> list[dict]:
        added = []
        with self._lock:
            for token in tokens:
                text = str(token.get("text", "")).strip()
                key = text.casefold()
                if not text or key in self._seen:
                    continue
                item = {
                    "id": uuid.uuid4().hex,
                    "text": text,
                    "confidence": float(token.get("confidence", 0)),
                    "bbox": token.get("bbox", {}),
                    "seq": seq,
                    "created_at_ms": int(time.time() * 1000),
                }
                self._items[item["id"]] = item
                self._seen.add(key)
                added.append(dict(item))
        return added

    def list(self) -> list[dict]:
        with self._lock:
            return [dict(v) for v in self._items.values()]

    def delete(self, item_id: str) -> bool:
        with self._lock:
            item = self._items.pop(item_id, None)
            if item:
                self._seen.discard(item["text"].casefold())
            return item is not None

    def csv_bytes(self) -> bytes:
        out = io.StringIO(newline="")
        writer = csv.writer(out)
        writer.writerow(["id", "text", "confidence", "seq", "created_at_ms"])
        for item in self.list():
            writer.writerow([item[k] for k in ("id", "text", "confidence", "seq", "created_at_ms")])
        return ("\ufeff" + out.getvalue()).encode("utf-8")
