"""Qt worker for fetching a provider's live model IDs without freezing the UI."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QApplication

from llm import LlmSettings, list_models


class ModelCatalogWorker(QThread):
    loaded = Signal(list)
    failed = Signal(str)

    def __init__(self, settings: LlmSettings, key: str) -> None:
        # The application owns the worker so a settings window can close while
        # a bounded network request is still in flight.
        super().__init__(QApplication.instance())
        self.settings = settings
        self.key = key

    def run(self) -> None:
        try:
            self.loaded.emit(list_models(self.settings, self.key))
        except (OSError, ValueError, RuntimeError) as error:
            self.failed.emit(str(error))
