from __future__ import annotations

from .config import APPROVED_PROCESSING_MODEL, TranscriptCaptureConfig


class TranscriptProcessor:
    def __init__(self, config: TranscriptCaptureConfig):
        self.config = config
        self.validate_model_policy(config.processing_model)

    @staticmethod
    def validate_model_policy(model: str) -> str:
        if model != APPROVED_PROCESSING_MODEL:
            raise ValueError(f"transcript processing model must be exactly {APPROVED_PROCESSING_MODEL}")
        return model

    def is_enabled(self) -> bool:
        return bool(self.config.external_synthesis_enabled and self.config.paid_provider_allowed)
