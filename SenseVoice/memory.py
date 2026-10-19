"""Local Mem0 store, using SiliconFlow for extraction and semantic embeddings."""

from __future__ import annotations

import os
import threading
import atexit
import json
import re
import shutil

from llm import DEFAULT_MODEL, LlmSettings, ROOT, SILICONFLOW_URL, chat, load_key


DATA_DIR = ROOT / "memory_data"
USER_ID = "shimano-owner"
EMBEDDING_MODEL = "BAAI/bge-m3"
EMBEDDING_DIMS = 1024
MAX_RECALLED = 5
CHANGE_WORDS = ("更正", "纠正", "改成", "不再", "以后叫", "现在用", "现在喜欢", "现在住", "其实是")
SECRET_PATTERN = re.compile(r"(?i)(api[ _-]?key|password|密码|密钥|验证码|access[ _-]?token|sk-[A-Za-z0-9_-]{8,})")

MEMORY_INSTRUCTIONS = (
    "只从用户明确说出的内容和双方确实完成的对话中提取有长期价值的事实：称呼、偏好、"
    "持续中的项目、已经做过的决定或值得回顾的事件。不要把助手的猜测、建议、承诺或未执行的"
    "行为写成事实。不要记住 API Key、密码、验证码或其他凭据。对含糊、假设或转述的说法不要"
    "认定为用户事实。用户明确纠正旧事实时，以新说法为准，并保留时间背景。"
)


def _items(result: object) -> list[dict]:
    if isinstance(result, dict):
        result = result.get("results", [])
    return [item for item in result if isinstance(item, dict)] if isinstance(result, list) else []


def memory_command(text: str) -> tuple[str, str] | None:
    value = text.strip().rstrip("。！？!?")
    for prefix in ("请记住", "记住"):
        if value.startswith(prefix) and len(value) > len(prefix):
            return "remember", value[len(prefix):].strip("，, ：:")
    for prefix in ("请忘记我", "忘记我", "请忘记关于我", "忘记关于我"):
        if value.startswith(prefix) and len(value) > len(prefix):
            return "forget", "我" + value[len(prefix):].strip("，, ：:")
    return None


class MemoryService:
    """Serialize access to one local Qdrant client and its history database."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._backend = None

    def _memory(self):
        if self._backend is None:
            key = load_key("siliconflow")
            if not key:
                raise RuntimeError("长期记忆需要已保存的硅基流动 API Key")
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            # These must be set before Mem0 is imported: defaults write into the
            # user's home/temp directory and enable third-party telemetry.
            os.environ["MEM0_DIR"] = str(DATA_DIR)
            os.environ["MEM0_TELEMETRY"] = "false"
            from mem0 import Memory

            self._backend = Memory.from_config({
                "vector_store": {"provider": "qdrant", "config": {
                    "path": str(DATA_DIR / "vectors"),
                    "collection_name": "shimano_memories",
                    "embedding_model_dims": EMBEDDING_DIMS,
                }},
                "history_db_path": str(DATA_DIR / "history.db"),
                "llm": {"provider": "openai", "config": {
                    "model": DEFAULT_MODEL, "api_key": key,
                    "openai_base_url": SILICONFLOW_URL, "temperature": 0.1,
                }},
                "embedder": {"provider": "openai", "config": {
                    "model": EMBEDDING_MODEL, "api_key": key,
                    "openai_base_url": SILICONFLOW_URL,
                }},
                "custom_instructions": MEMORY_INSTRUCTIONS,
            })
        return self._backend

    def recall(self, query: str) -> list[dict]:
        if not query.strip():
            return []
        with self._lock:
            result = self._memory().search(query, filters={"user_id": USER_ID}, top_k=MAX_RECALLED)
        return _items(result)[:MAX_RECALLED]

    def list_all(self) -> list[dict]:
        with self._lock:
            result = self._memory().get_all(filters={"user_id": USER_ID}, top_k=1000)
        return _items(result)

    def remember(self, text: str) -> None:
        if not text.strip():
            raise ValueError("记忆内容不能为空")
        if SECRET_PATTERN.search(text):
            raise ValueError("不能把密钥、密码或验证码写入长期记忆")
        with self._lock:
            self._memory().add(text.strip(), user_id=USER_ID,
                               metadata={"source": "manual"}, infer=False)

    def record_turn(self, user_text: str, assistant_text: str) -> None:
        if not user_text.strip() or not assistant_text.strip():
            return
        if SECRET_PATTERN.search(user_text) or SECRET_PATTERN.search(assistant_text):
            return
        messages = [{"role": "user", "content": user_text},
                    {"role": "assistant", "content": assistant_text}]
        with self._lock:
            old = self.recall(user_text) if any(word in user_text for word in CHANGE_WORDS) else []
            try:
                result = self._memory().add(messages, user_id=USER_ID,
                                            metadata={"source": "conversation"}, infer=True)
            finally:
                # Mem0 2.2.1 persists raw turns in history.db by default.
                # The UI owns short context, so keep only extracted facts.
                self._memory().db.connection.execute("DELETE FROM messages")
                self._memory().db.connection.commit()
            if _items(result) and old:
                for memory_id in self._select_ids(user_text, old, mode="conflict"):
                    self.forget(memory_id)

    def _select_ids(self, request: str, candidates: list[dict], *, mode: str) -> list[str]:
        records = [{"id": str(item["id"]), "memory": str(item.get("memory", ""))}
                   for item in candidates if item.get("id")]
        if not records:
            return []
        prompt = (
            "你是记忆维护器。只输出 JSON 对象，格式为 {\"ids\":[\"候选ID\"]}。"
            "只选择与用户当前明确说法相矛盾的旧记忆；拿不准就返回空数组。"
            if mode == "conflict" else
            "你是记忆删除匹配器。只输出 JSON 对象，格式为 {\"ids\":[\"候选ID\"]}。"
            "只选择用户明确要求遗忘的记忆；拿不准就返回空数组。"
        )
        reply = chat(LlmSettings(model=DEFAULT_MODEL, system_prompt=prompt),
                     load_key("siliconflow"), [],
                     json.dumps({"用户当前说法": request, "候选旧记忆": records}, ensure_ascii=False), timeout=30)
        try:
            data = json.loads(reply.removeprefix("```json").removeprefix("```").removesuffix("```").strip())
            selected = data.get("ids", [])
        except (ValueError, AttributeError):
            return []
        allowed = {item["id"] for item in records}
        return [item for item in selected if isinstance(item, str) and item in allowed] if isinstance(selected, list) else []

    def forget_matching(self, query: str) -> int:
        with self._lock:
            candidates = self.recall(query)
            ids = self._select_ids(query, candidates, mode="forget")
            for memory_id in ids:
                self.forget(memory_id)
            return len(ids)

    def update(self, memory_id: str, text: str) -> None:
        if not text.strip():
            raise ValueError("记忆内容不能为空")
        if SECRET_PATTERN.search(text):
            raise ValueError("不能把密钥、密码或验证码写入长期记忆")
        with self._lock:
            self._memory().update(memory_id, text=text.strip())

    def forget(self, memory_id: str) -> None:
        with self._lock:
            self._memory().delete(memory_id)
            self._memory().db.connection.execute("DELETE FROM history WHERE memory_id = ?", (memory_id,))
            self._memory().db.connection.commit()

    def forget_all(self) -> None:
        with self._lock:
            self.close()
            target = DATA_DIR.resolve()
            if target.parent != ROOT.resolve() or target.name != "memory_data":
                raise RuntimeError("记忆目录不在项目内，拒绝删除")
            if target.exists():
                shutil.rmtree(target)

    def close(self) -> None:
        with self._lock:
            if self._backend is not None:
                self._backend.vector_store.client.close()
                self._backend.db.close()
                self._backend = None


_service = MemoryService()
atexit.register(_service.close)


def get_memory() -> MemoryService:
    return _service
