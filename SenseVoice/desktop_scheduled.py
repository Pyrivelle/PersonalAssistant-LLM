"""Desktop timer adapter: model generation off the Qt event loop."""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal
import os

from llm import load_key, load_settings
from zhuangfy.agent_v2.scheduled_generation import generate_due_reply
from zhuangfy.agent_v2.scheduled_tasks import ScheduledTask
from zhuangfy.agent_v2.scheduled_tasks import ScheduledTaskStore
from zhuangfy.memory.client import MemoryChannel, MemoryClient


class DesktopScheduledWorker(QThread):
    generated = Signal(str, str)
    failed = Signal(str, str)

    def __init__(self, task: ScheduledTask, recent: list[dict[str, str]], parent=None):
        super().__init__(parent)
        self.task = task
        self.recent = recent

    def run(self) -> None:
        try:
            settings = load_settings()
            key = load_key(settings.provider)
            reply = generate_due_reply(self.task, self.recent, settings, key)
            self.generated.emit(self.task.id, reply)
        except Exception as error:
            self.failed.emit(self.task.id, type(error).__name__)


class DesktopPersonLinkWorker(QThread):
    """Attach PersonID only after the local Memory Service verifies it."""

    def __init__(self, task: ScheduledTask, store: ScheduledTaskStore, parent=None):
        super().__init__(parent)
        self.task, self.store = task, store

    def run(self) -> None:
        try:
            client = MemoryClient.from_environment(enabled_var="ZHUANGFY_MEMORY_DESKTOP")
            external_id = os.environ.get("ZHUANGFY_MEMORY_DESKTOP_USER_ID", "").strip()
            if client is None or not external_id:
                return
            person_id = client.resolve_identity(
                MemoryChannel("desktop", "local", external_id))
            self.store.set_verified_person(
                self.task.id, channel="desktop", channel_account_id="local",
                external_user_id=self.task.external_user_id, person_id=person_id)
        except Exception:
            # Missing link or missing Timeline consent keeps the timer local.
            return
