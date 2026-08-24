from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class OCRConfig:
    """Configuration values for the OCR ingestion pipeline."""

    frame_queue_size: int = 6
    target_fps: int = 3
    worker_count: int = 2
    confidence_threshold: float = 60.0
    min_laplacian_variance: float = 120.0
    preprocess_width: int = 1280
    preprocess_height: int = 720
    jpeg_quality: int = 70
    max_duplicate_window: int = 5
    tesseract_config: str = "--oem 1 --psm 6"
    valid_word_set: set[str] = field(default_factory=set)

    def normalized_word_set(self) -> set[str]:
        return {word.lower() for word in self.valid_word_set}


DEFAULT_CONFIG = OCRConfig()


def build_config(**overrides: object) -> OCRConfig:
    config = OCRConfig()
    for key, value in overrides.items():
        if hasattr(config, key):
            setattr(config, key, value)
    return config
