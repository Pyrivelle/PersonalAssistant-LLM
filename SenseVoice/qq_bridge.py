"""Conservative OneBot v11 bridge for SnowLuma; run beside the voice UI."""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import time
import uuid
from collections import defaultdict, deque
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import urlparse

from llm import ROOT, chat, load_key, load_settings


SETTINGS_FILE = ROOT / "qq_settings.json"
LOCK_FILE = ROOT / ".qq_bridge.lock"
CQ_CODE = re.compile(r"\[CQ:[^\]]+\]")
CQ_AT = re.compile(r"\[CQ:at,qq=(\d+)(?:,[^\]]*)?\]")


def _ids(value: object) -> frozenset[int]:
    if not isinstance(value, list):
        raise ValueError("QQ 白名单必须是号码数组")
    try:
        ids = frozenset(int(item) for item in value)
    except (ValueError, TypeError) as error:
        raise ValueError("QQ 白名单必须只包含号码") from error
    if any(item <= 0 for item in ids):
        raise ValueError("QQ 号码必须大于零")
    return ids


@dataclass(frozen=True)
class QqSettings:
    ws_url: str = "ws://127.0.0.1:3001/"
    allowed_private_ids: frozenset[int] = frozenset()
    allowed_group_ids: frozenset[int] = frozenset()
    private_cooldown_seconds: float = 5
    group_cooldown_seconds: float = 12
    private_max_replies_per_hour: int = 30
    group_max_replies_per_hour: int = 8
    quiet_seconds: float = 1.2

    @classmethod
    def load(cls, path: Path = SETTINGS_FILE) -> "QqSettings":
        if not path.is_file():
            raise FileNotFoundError(f"请先从 qq_settings.example.json 创建 {path.name}，填写允许回复的 QQ 号")
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("QQ 配置必须是 JSON 对象")
        defaults = cls()
        url = str(raw.get("ws_url", defaults.ws_url)).strip()
        parsed = urlparse(url)
        if parsed.scheme != "ws" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("首版仅接受本机 ws:// OneBot 地址，且 URL 中不能包含 token")
        values = {key: raw.get(key, getattr(defaults, key)) for key in (
            "private_cooldown_seconds", "group_cooldown_seconds",
            "private_max_replies_per_hour", "group_max_replies_per_hour", "quiet_seconds")}
        for key in ("private_cooldown_seconds", "group_cooldown_seconds", "quiet_seconds"):
            values[key] = float(values[key])
            if not math.isfinite(values[key]) or not 0 <= values[key] <= 120:
                raise ValueError(f"{key} 必须在 0 到 120 秒之间")
        for key in ("private_max_replies_per_hour", "group_max_replies_per_hour"):
            values[key] = int(values[key])
            if not 1 <= values[key] <= 120:
                raise ValueError(f"{key} 必须在 1 到 120 之间")
        return cls(url, _ids(raw.get("allowed_private_ids", [])),
                   _ids(raw.get("allowed_group_ids", [])), **values)


def load_snowluma_token() -> str:
    """Reuse the ignored local env file without ever printing the token."""
    path = ROOT / ".env.local"
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        if separator and name.strip() == "SNOWLUMA_ACCESS_TOKEN":
            return value.strip().strip('"').strip("'")
    return ""


@contextmanager
def single_instance():
    """Prevent the standalone launcher and dashboard from replying twice."""
    with LOCK_FILE.open("r+b" if LOCK_FILE.exists() else "w+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SystemExit("QQ 桥接已经运行；请先停止现有进程，避免重复回复。") from None
        try:
            yield
        finally:
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@dataclass(frozen=True)
class Incoming:
    kind: str
    user_id: int
    group_id: int | None
    message_id: str
    text: str

    @property
    def scope(self) -> str:
        return f"{self.kind}:{self.group_id or 0}:{self.user_id}"


def parse_message(event: dict, bot_id: int, settings: QqSettings) -> Incoming | None:
    if bot_id <= 0:
        return None
    if event.get("post_type") != "message" or event.get("sub_type") == "self":
        return None
    kind = event.get("message_type")
    if kind not in {"private", "group"}:
        return None
    try:
        user_id = int(event["user_id"])
        group_id = int(event["group_id"]) if kind == "group" else None
    except (KeyError, ValueError, TypeError):
        return None
    if user_id == bot_id:
        return None
    if kind == "private" and user_id not in settings.allowed_private_ids:
        return None
    if kind == "group" and group_id not in settings.allowed_group_ids:
        return None
    message = event.get("message", event.get("raw_message", ""))
    mentioned = False
    if isinstance(message, list):
        texts = []
        for segment in message:
            if not isinstance(segment, dict):
                continue
            data = segment.get("data") or {}
            if segment.get("type") == "at" and isinstance(data, dict) and str(data.get("qq")) == str(bot_id):
                mentioned = True
            if segment.get("type") == "text" and isinstance(data, dict):
                texts.append(str(data.get("text", "")))
        text = "".join(texts).strip()
    elif isinstance(message, str):
        mentioned = str(bot_id) in CQ_AT.findall(message)
        text = CQ_CODE.sub("", message).strip()
    else:
        return None
    if kind == "group" and not mentioned:
        return None
    if not text:
        return None
    return Incoming(kind, user_id, group_id, str(event.get("message_id", "")), text[:1500])


class ReplyLimiter:
    def __init__(self, settings: QqSettings) -> None:
        self.settings = settings
        self.last_sent: dict[str, float] = {}
        self.sent_hours: dict[str, deque[float]] = defaultdict(deque)

    def check(self, incoming: Incoming, now: float) -> tuple[float, bool]:
        bucket = f"group:{incoming.group_id}" if incoming.kind == "group" else incoming.scope
        cooldown = self.settings.group_cooldown_seconds if incoming.kind == "group" else self.settings.private_cooldown_seconds
        limit = self.settings.group_max_replies_per_hour if incoming.kind == "group" else self.settings.private_max_replies_per_hour
        times = self.sent_hours[bucket]
        while times and now - times[0] >= 3600:
            times.popleft()
        if len(times) >= limit:
            return 0, True
        return max(0.0, self.last_sent.get(bucket, -float("inf")) + cooldown - now), False

    def mark_sent(self, incoming: Incoming, now: float) -> None:
        bucket = f"group:{incoming.group_id}" if incoming.kind == "group" else incoming.scope
        self.last_sent[bucket] = now
        self.sent_hours[bucket].append(now)


@dataclass
class Conversation:
    incoming: Incoming
    pending: list[str] = field(default_factory=list)
    first_arrival: float = 0
    last_arrival: float = 0
    history: list[dict[str, str]] = field(default_factory=list)
    task: asyncio.Task | None = None


class QqBridge:
    def __init__(self, settings: QqSettings) -> None:
        self.settings = settings
        self.bot_id = 0
        self.ws = None
        self.pending_actions: dict[str, asyncio.Future] = {}
        self.early_events: list[dict] = []
        self.conversations: dict[str, Conversation] = {}
        self.seen: dict[str, float] = {}
        self.limiter = ReplyLimiter(settings)
        self.generation_lock = asyncio.Lock()
        self.send_lock = asyncio.Lock()
        self.next_send_at = 0.0

    async def action(self, name: str, params: dict) -> dict:
        echo = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending_actions[echo] = future
        try:
            await self.ws.send(json.dumps({"action": name, "params": params, "echo": echo}, ensure_ascii=False))
            result = await asyncio.wait_for(future, timeout=15)
            if result.get("status") != "ok" or result.get("retcode") != 0:
                raise RuntimeError(f"OneBot 动作 {name} 失败，retcode={result.get('retcode')}")
            return result.get("data") or {}
        finally:
            self.pending_actions.pop(echo, None)

    async def receive(self) -> None:
        async for raw in self.ws:
            try:
                data = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            echo = data.get("echo")
            if echo is not None:
                future = self.pending_actions.get(str(echo))
                if future and not future.done():
                    future.set_result(data)
            else:
                if self.bot_id:
                    self.accept(data)
                elif data.get("post_type") == "message" and len(self.early_events) < 32:
                    self.early_events.append(data)

    def accept(self, event: dict) -> bool:
        incoming = parse_message(event, self.bot_id, self.settings)
        if incoming is None:
            return False
        now = time.monotonic()
        if incoming.message_id:
            key = f"{incoming.scope}:{incoming.message_id}"
            if key in self.seen:
                return False
            self.seen[key] = now
            if len(self.seen) > 2048:
                self.seen = {item: seen_at for item, seen_at in self.seen.items() if now - seen_at < 3600}
        state = self.conversations.setdefault(incoming.scope, Conversation(incoming))
        state.incoming = incoming
        if not state.pending:
            state.first_arrival = now
        state.pending.append(incoming.text)
        state.pending = state.pending[-4:]
        state.last_arrival = now
        if state.task is None or state.task.done():
            state.task = asyncio.create_task(self.process(state))
        print("已接收白名单私聊" if incoming.kind == "private" else "已接收白名单群 @", flush=True)
        return True

    async def process(self, state: Conversation) -> None:
        while state.pending:
            while True:
                now = time.monotonic()
                quiet = self.settings.quiet_seconds - (now - state.last_arrival)
                if quiet <= 0 or now - state.first_arrival >= 4:
                    break
                await asyncio.sleep(min(quiet, 4 - (now - state.first_arrival)))
            wait, capped = self.limiter.check(state.incoming, time.monotonic())
            if capped:
                state.pending.clear()
                print(f"跳过 {state.incoming.scope}：本小时回复已达上限")
                return
            if wait:
                await asyncio.sleep(wait)
                continue
            message = "\n".join(state.pending)
            state.pending.clear()
            current = state.incoming
            try:
                async with self.generation_lock:
                    settings = load_settings()
                    key = load_key(settings.provider)
                    if not key and settings.provider != "custom":
                        raise RuntimeError("对话模型 API Key 未配置")
                    settings = replace(settings, memory_enabled=False,
                                       system_prompt=settings.system_prompt + "\n你现在通过 QQ 文字聊天。简短回应当前发消息的人；不要把其他 QQ 用户当作桌面主人的长期记忆来源。")
                    reply = await asyncio.to_thread(chat, settings, key, state.history, message)
                if not reply.strip():
                    continue
                # One outbound message per turn. Never retry an unknown delivery result.
                reply = reply[:1200]
                if await self.send_reply(current, reply):
                    state.history.extend(({"role": "user", "content": message}, {"role": "assistant", "content": reply}))
                    state.history = state.history[-12:]
            except Exception as error:
                print(f"QQ 回复失败（{current.scope}）：{type(error).__name__}: {error}")

    async def send_reply(self, incoming: Incoming, reply: str) -> bool:
        async with self.send_lock:
            now = time.monotonic()
            await asyncio.sleep(max(0.0, self.next_send_at - now))
            wait, capped = self.limiter.check(incoming, time.monotonic())
            if capped:
                return False
            if wait:
                await asyncio.sleep(wait)
            self.next_send_at = time.monotonic() + 1.2
            if incoming.kind == "private":
                await self.action("send_private_msg", {"user_id": incoming.user_id,
                                                       "message": reply, "auto_escape": True})
            else:
                await self.action("send_group_msg", {"group_id": incoming.group_id,
                    "message": [{"type": "at", "data": {"qq": str(incoming.user_id)}},
                                {"type": "text", "data": {"text": " " + reply}}]})
            self.limiter.mark_sent(incoming, time.monotonic())
            print("已投递 QQ 私聊回复" if incoming.kind == "private" else "已投递 QQ 群回复", flush=True)
            return True

    async def run_forever(self) -> None:
        from websockets.asyncio.client import connect

        token = load_snowluma_token()
        headers = {"Authorization": f"Bearer {token}"} if token else None
        while True:
            receiver = None
            try:
                async with connect(self.settings.ws_url, additional_headers=headers,
                                   open_timeout=10, ping_interval=20, max_size=2_000_000,
                                   proxy=None) as self.ws:
                    receiver = asyncio.create_task(self.receive())
                    login = await self.action("get_login_info", {})
                    self.bot_id = int(login["user_id"])
                    for event in self.early_events:
                        self.accept(event)
                    self.early_events.clear()
                    print(f"已连接 SnowLuma；机器人账号 {self.bot_id}，私聊白名单 {len(self.settings.allowed_private_ids)} 个，群白名单 {len(self.settings.allowed_group_ids)} 个")
                    await receiver
            except asyncio.CancelledError:
                raise
            except Exception as error:
                print(f"SnowLuma 连接中断：{type(error).__name__}: {error}；5 秒后重连")
            finally:
                self.bot_id = 0
                if receiver:
                    receiver.cancel()
                    await asyncio.gather(receiver, return_exceptions=True)
                for future in self.pending_actions.values():
                    if not future.done():
                        future.cancel()
                self.pending_actions.clear()
                self.early_events.clear()
                for state in self.conversations.values():
                    if state.task and not state.task.done():
                        state.task.cancel()
                    state.pending.clear()
            await asyncio.sleep(5)


def main() -> None:
    settings = QqSettings.load()
    if not settings.allowed_private_ids and not settings.allowed_group_ids:
        raise SystemExit("QQ 白名单为空：请在 qq_settings.json 填入你的 QQ 号，默认不回复任何人。")
    print("QQ 桥接启动。仅处理白名单私聊及白名单群中的 @ 消息；按 Ctrl+C 停止。")
    with single_instance():
        try:
            asyncio.run(QqBridge(settings).run_forever())
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
