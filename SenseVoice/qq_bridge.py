"""Conservative OneBot v11 bridge for SnowLuma; run beside the voice UI."""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import tempfile
import time
import urllib.request
import uuid
from collections import defaultdict, deque
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from activity_log import log_event
from pathlib import Path
from urllib.parse import urlparse

from llm import ROOT, load_key, load_settings
from model_routes import invoke_stage, can_attempt_stage, ModelRoutesFailed
from dialogue_context import DialogueContext, DialogueStore
from turn_planner import PlannerSettings, TurnPlan, plan_turn
from member_memory import MemberKey, MemberMemoryStore, REFLECT_AFTER_TURNS, reflect_member
from fangyi_skill import guidance as fangyi_guidance
from gpt_sovits import load_settings as load_speech_settings
from qq_reply_formatter import format_qq_reply
from emotion import EmotionState
from llm import LlmSettings


SETTINGS_FILE = ROOT / "qq_settings.json"
LOCK_FILE = ROOT / ".qq_bridge.lock"
IDENTITY_FILE = ROOT / "qq_identity.json"
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
    voice_private_ids: frozenset[int] = frozenset()

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
        private_ids = _ids(raw.get("allowed_private_ids", []))
        voice_ids = _ids(raw.get("voice_private_ids", []))
        if not voice_ids.issubset(private_ids):
            raise ValueError("QQ 语音回复账号必须包含在私聊白名单内")
        return cls(url, private_ids, _ids(raw.get("allowed_group_ids", [])),
                   voice_private_ids=voice_ids, **values)


MAX_VOICE_CHARS = 160
MAX_VOICE_BYTES = 12_000_000
VOICE_STAGE_CUE = re.compile(
    r"(?:我|她|这就|现在)?(?:给你)?发(?:一条|个)?语音(?:了|吧|给你)?"
    r"|(?:用语音|语音)(?:回复|说)"
    r"|(?:她|庄方宜)?(?:(?:轻声|低声|柔声|温柔地|缓缓地|清楚地|清楚的|清晰地|清晰的)[\s、，,]*){1,3}"
    r"(?:说|说道|开口|讲)"
)
VOICE_STAGE_END = re.compile(r"[，,：:。！!]\s*")


def spoken_reply(text: str) -> str:
    """Remove only short, leading delivery directions from a voice reply."""
    spoken = text.strip()
    for _ in range(3):
        if spoken.startswith(("（", "(")):
            closing = "）" if spoken[0] == "（" else ")"
            end = spoken.find(closing, 1, 65)
            if end > 0 and VOICE_STAGE_CUE.fullmatch(spoken[1:end].strip()):
                spoken = spoken[end + 1:].lstrip(" ，,：:")
                continue
        match = VOICE_STAGE_END.search(spoken[:65])
        if not match or not VOICE_STAGE_CUE.fullmatch(spoken[:match.start()].strip()):
            break
        spoken = spoken[match.end():].lstrip()
    return spoken


def synthesize_qq_voice(text: str) -> bytes:
    """Generate a short WAV with the same saved local voice as the desktop UI."""
    settings = load_speech_settings()
    if settings.engine != "gpt_sovits":
        raise ValueError("尚未启用本地 GPT-SoVITS 语音")
    request = urllib.request.Request(
        settings.endpoint.rstrip("/") + "/tts",
        data=json.dumps(settings.payload(text), ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=60) as response:
        if response.headers.get("Content-Length") and int(response.headers["Content-Length"]) > MAX_VOICE_BYTES:
            raise ValueError("生成的语音文件过大")
        audio = response.read(MAX_VOICE_BYTES + 1)
    if len(audio) > MAX_VOICE_BYTES or not (audio.startswith(b"RIFF") and audio[8:12] == b"WAVE"):
        raise ValueError("本地语音服务未返回有效 WAV")
    return audio


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
class PartialReply:
    """One model answer, possibly delivered as several QQ bubbles.

    This does not persist across restarts. Retain only not-yet-confirmed parts
    so /continue cannot inadvertently re-send acknowledged messages.
    """
    user_text: str
    canonical_reply: str
    parts: tuple[str, ...]
    delivered: int
    emotion: EmotionState
    settings: LlmSettings
    api_key: str
    member_key: MemberKey | None
    turn_id: str
    uncertain: bool = False
    voice: bool = False


@dataclass
class Conversation:
    incoming: Incoming
    pending: list[str] = field(default_factory=list)
    first_arrival: float = 0
    last_arrival: float = 0
    context: DialogueContext = field(default_factory=DialogueContext)
    last_failed_generation: str = ""
    partial_replies: deque[PartialReply] = field(default_factory=deque)
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
        # Persistence is separate from Mem0 and only enabled for explicitly
        # selected, already-whitelisted private accounts. Groups never persist.
        self.planner_settings = PlannerSettings.load(ROOT / "cognition_settings.json")
        if not settings.voice_private_ids.issubset(settings.allowed_private_ids):
            raise ValueError("QQ 语音回复账号必须包含在私聊白名单内")
        if not self.planner_settings.persist_private_ids.issubset(settings.allowed_private_ids):
            raise ValueError("可持久化账号必须包含在 QQ 私聊白名单内")
        self.context_store = (DialogueStore(ROOT / "conversation_data" / "qq_context.sqlite3")
                              if self.planner_settings.persist_private_ids else None)
        if not self.planner_settings.memory_private_ids.issubset(settings.allowed_private_ids):
            raise ValueError("成员记忆账号必须先加入 QQ 私聊白名单")
        # Separate, explicit consent for extracting personal profiles and events.
        self.member_store = (MemberMemoryStore(ROOT / "conversation_data" / "qq_members.sqlite3")
                             if self.planner_settings.memory_private_ids else None)
        self.reflection_tasks: dict[MemberKey, asyncio.Task] = {}
        self.reflection_retry_at: dict[MemberKey, float] = {}
        self.reflection_lock = asyncio.Semaphore(1)

    async def action(self, name: str, params: dict, *, timeout: float = 15) -> dict:
        echo = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending_actions[echo] = future
        try:
            await self.ws.send(json.dumps({"action": name, "params": params, "echo": echo}, ensure_ascii=False))
            result = await asyncio.wait_for(future, timeout=timeout)
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
            key = f"bot:{self.bot_id}:{incoming.scope}:{incoming.message_id}"
            if key in self.seen:
                return False
            self.seen[key] = now
            if len(self.seen) > 2048:
                self.seen = {item: seen_at for item, seen_at in self.seen.items() if now - seen_at < 3600}
        identity_scope = f"bot:{self.bot_id}:{incoming.scope}"
        state = self.conversations.get(identity_scope)
        if state is None:
            context = (self.context_store.load(identity_scope)
                       if self.context_store and incoming.kind == "private"
                       and incoming.user_id in self.planner_settings.persist_private_ids
                       else DialogueContext())
            state = Conversation(incoming=incoming, context=context)
            self.conversations[identity_scope] = state
        state.incoming = incoming
        if not state.pending:
            state.first_arrival = now
        state.pending.append(incoming.text)
        log_event("QQ", "收到", f"{incoming.scope} · {incoming.text}")
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
            log_event("QQ", "消息", f"{current.scope} · {message}")
            try:
                if message.strip() == "/retry":
                    if not state.last_failed_generation:
                        await self.send_reply(current, "系统提示：当前会话没有需要重新生成的失败消息。")
                        continue
                    message = state.last_failed_generation
                    log_event("QQ", "手动重试", f"{current.scope} · 重新提交上次生成失败的文字")
                if (current.kind == "private" and
                        message.strip() in {"/forget_me", "/删除我的记忆"}):
                    # Wait for pending background writes before erasing. Otherwise
                    # a late reflection could recreate deleted personal records.
                    if (self.member_store is not None and
                            current.user_id in self.planner_settings.memory_private_ids):
                        async with self.reflection_lock:
                            await asyncio.to_thread(
                                self.member_store.erase,
                                MemberKey(self.bot_id, current.user_id))
                    if (self.context_store is not None and
                            current.user_id in self.planner_settings.persist_private_ids):
                        self.context_store.delete(f"bot:{self.bot_id}:{current.scope}")
                        self.context_store.delete(current.scope)  # legacy pre-bot scope
                    state.context = DialogueContext()
                    await self.send_reply(current, "已清除这个 QQ 账号在本机器人中的会话与成员记忆。")
                    continue
                async with self.generation_lock:
                    settings = load_settings()
                    key = load_key(settings.provider)
                    if not can_attempt_stage("reply", settings, key):
                        raise RuntimeError("回复阶段没有可用的模型服务，请检查 API 候选及密钥")
                    settings = replace(settings, memory_enabled=False,
                                       system_prompt=settings.system_prompt + "\n你现在通过 QQ 聊天。简短回应当前发消息的人；不要把其他 QQ 用户当作桌面主人的长期记忆来源。"
                                       "\n只输出准备发送给对方的对白；不要用（语气……）、（轻声）、（动作……）或其他括号写表演、神情、心理和镜头旁白。"
                                       "平常闲聊使用自然中文，不使用多余 Markdown 双空格、标题或加粗。"
                                       "稍长的聊天可以自然分为两小段，以空行隔开；不要刻意拉长。代码、链接或步骤等技术内容可保留必要格式。")
                    candidate = state.context.observe(message)
                    plan = TurnPlan()
                    if self.planner_settings.enabled:
                        try:
                            plan = await asyncio.to_thread(
                                plan_turn, settings, key, state.context.history, message,
                                self.planner_settings,
                                allow_voice=(current.kind == "private" and
                                             current.user_id in self.settings.voice_private_ids))
                            log_event("QQ", "思考", f"{current.scope} · 语气：{plan.tone}；关注：{plan.attention or '当前话题'}；形式：{plan.delivery}；置信度：{plan.confidence:.2f}")
                        except Exception as error:
                            print(f"可选思考阶段失败，改为直接回复：{type(error).__name__}")
                            log_event("QQ", "思考", f"{current.scope} · 阶段跳过：{type(error).__name__}")
                    else:
                        log_event("QQ", "思考", f"{current.scope} · 可选思考阶段未启用，直接生成回复")
                    emotion_prompt = candidate.to_prompt()
                    if plan.prompt():
                        emotion_prompt += "\n" + plan.prompt()
                    if (current.kind == "private" and
                            current.user_id in self.settings.voice_private_ids and
                            plan.delivery == "voice" and plan.confidence >= 0.5):
                        emotion_prompt += ("\n本轮回复会直接作为语音内容播放。只输出你真正要对用户说的话，"
                                           "不要写‘发语音了’、‘轻声说’等动作、语气或发送方式的旁白；"
                                           "不用括号、角色名、Markdown 或表情符号，尽量不超过 160 字。")
                    scene = fangyi_guidance(settings.system_prompt, message,
                                            enabled=self.planner_settings.fangyi_scene_skill)
                    if scene:
                        emotion_prompt += "\n" + scene
                        log_event("QQ", "角色场景", f"{current.scope} · {scene}")
                    member_key = None
                    member_snapshot = ""
                    if (self.member_store is not None and current.kind == "private"
                            and current.user_id in self.planner_settings.memory_private_ids):
                        member_key = MemberKey(self.bot_id, current.user_id)
                        member_snapshot = await asyncio.to_thread(self.member_store.context_for, member_key)
                        log_event("记忆", "读取", f"{current.scope} · QQ 个人记忆 {'已提供' if member_snapshot else '暂无'}")
                    notes = state.context.prompt()
                    if member_snapshot:
                        # LLMClient caps extra context; keep the member identity
                        # snapshot first rather than truncating it off the end.
                        notes = "\n".join([member_snapshot, notes])
                    turn_id = uuid.uuid4().hex[:12]
                    log_event("QQ", "生成开始", f"turn:{turn_id} · {current.scope}")
                    reply = await asyncio.to_thread(
                        invoke_stage, "reply", settings, key,
                        state.context.history, message, emotion_context=emotion_prompt,
                        context_notes=notes, trace_id=turn_id)
                    log_event("QQ", "已生成", f"turn:{turn_id} · {current.scope}")
                if not reply.strip():
                    continue
                # One outbound message per turn. Never retry an unknown delivery result.
                reply = reply[:1200]
                use_voice = (current.kind == "private" and
                             current.user_id in self.settings.voice_private_ids and
                             plan.delivery == "voice" and plan.confidence >= 0.5)
                if use_voice:
                    cleaned = spoken_reply(reply)
                    if cleaned != reply and cleaned:
                        log_event("QQ", "语音文本", f"{current.scope} · 已省略开头的动作描述")
                        reply = cleaned
                    use_voice = bool(cleaned) and len(reply) <= MAX_VOICE_CHARS
                if await self.send_reply(current, reply, voice=use_voice):
                    state.last_failed_generation = ""
                    log_event("QQ", "已投递", f"turn:{turn_id} · {current.scope}")
                    state.context.commit(message, reply, candidate)
                    if (self.context_store and current.kind == "private"
                            and current.user_id in self.planner_settings.persist_private_ids):
                        try:
                            self.context_store.save(f"bot:{self.bot_id}:{current.scope}", state.context)
                        except Exception as error:
                            print(f"QQ 会话上下文保存失败：{type(error).__name__}")
                    if member_key is not None:
                        try:
                            count = await asyncio.to_thread(
                                self.member_store.record_turn, member_key, message, reply)
                            if count >= REFLECT_AFTER_TURNS:
                                log_event("记忆", "排队", f"{current.scope} · 已完成 {count} 轮，准备后台反思")
                                self.queue_member_reflection(member_key, settings, key)
                        except Exception as error:
                            print(f"QQ 用户记忆排队失败：{type(error).__name__}")
            except ModelRoutesFailed as error:
                state.last_failed_generation = message
                log_event("QQ", "模型全部失败",
                          f"{current.scope} · 已保留本轮文字，发送 /retry 可再试；{error}")
                # A status notice is not an AI reply and must not enter
                # dialogue history or memory. Never retry uncertain delivery.
                if current.kind == "private":
                    try:
                        await self.send_reply(
                            current, "系统提示：这句话暂时没能生成回复。稍后发送 /retry 可重试，无需重新输入。")
                    except Exception as delivery_error:
                        log_event("QQ", "错误通知投递失败",
                                  f"{current.scope} · {type(delivery_error).__name__}")
            except Exception as error:
                print(f"QQ 回复失败（{current.scope}）：{type(error).__name__}: {error}")
                log_event("QQ", "失败", f"{current.scope} · {type(error).__name__}: {error}")

    async def _send_reply_parts(self, incoming: Incoming, parts: tuple[str, ...],
                                *, voice: bool = False, continuation: bool = False
                                ) -> tuple[int, bool]:
        """Number of confirmed sends and whether the failed action was uncertain."""
        for index, part in enumerate(parts):
            options = {}
            if voice and index == 0:
                options["voice"] = True
            if continuation or index > 0:
                options["continuation"] = True
            try:
                delivered = await self.send_reply(incoming, part, **options)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # OneBot may have received this physical message before its
                # acknowledgement was lost: never automatically retry it.
                log_event("QQ", "投递状态不明",
                          f"{incoming.scope} · 第 {index + 1}/{len(parts)} 段 · {type(error).__name__}")
                return index, True
            if not delivered:
                log_event("QQ", "投递受限",
                          f"{incoming.scope} · 第 {index + 1}/{len(parts)} 段未被接受")
                return index, False
        return len(parts), False

    async def _finish_reply(self, state: Conversation, current: Incoming,
                            delivery: PartialReply) -> None:
        """Commit exactly one dialogue turn after ALL physical parts have an ACK."""
        if delivery in state.partial_replies:
            state.partial_replies.remove(delivery)
        state.last_failed_generation = ""
        log_event("QQ", "已投递",
                  f"turn:{delivery.turn_id} · {current.scope} · {len(delivery.parts)} 段")
        state.context.commit(delivery.user_text, delivery.canonical_reply, delivery.emotion)
        if (self.context_store and current.kind == "private"
                and current.user_id in self.planner_settings.persist_private_ids):
            try:
                self.context_store.save(f"bot:{self.bot_id}:{current.scope}", state.context)
            except Exception as error:
                print(f"QQ 会话上下文保存失败：{type(error).__name__}")
        if delivery.member_key is not None:
            try:
                count = await asyncio.to_thread(self.member_store.record_turn,
                                                delivery.member_key,
                                                delivery.user_text, delivery.canonical_reply)
                if count >= REFLECT_AFTER_TURNS:
                    log_event("记忆", "排队",
                              f"{current.scope} · 已完成 {count} 轮，准备后台反思")
                    self.queue_member_reflection(delivery.member_key,
                                                 delivery.settings, delivery.api_key)
            except Exception as error:
                print(f"QQ 用户记忆排队失败：{type(error).__name__}")

    def queue_member_reflection(self, identity: MemberKey, settings, api_key: str) -> None:
        previous = self.reflection_tasks.get(identity)
        if previous is not None and not previous.done():
            return
        if time.monotonic() < self.reflection_retry_at.get(identity, 0):
            return
        task = asyncio.create_task(self._run_member_reflection(identity, settings, api_key))
        self.reflection_tasks[identity] = task

    async def _run_member_reflection(self, identity: MemberKey, settings, api_key: str) -> None:
        try:
            async with self.reflection_lock:
                await asyncio.to_thread(reflect_member, self.member_store, identity, settings, api_key)
            self.reflection_retry_at.pop(identity, None)
            log_event("记忆", "完成", f"bot:{identity.bot_id}:private:{identity.sender_id} · 个人记忆反思完成")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.reflection_retry_at[identity] = time.monotonic() + 120
            print(f"QQ 用户记忆反思失败，保留待处理消息：{type(error).__name__}")
            log_event("记忆", "失败", f"bot:{identity.bot_id}:private:{identity.sender_id} · 反思失败：{type(error).__name__}")

    async def send_reply(self, incoming: Incoming, reply: str, *, voice: bool = False,
                         continuation: bool = False) -> bool:
        audio = None
        if voice and incoming.kind == "private" and incoming.user_id in self.settings.voice_private_ids:
            try:
                audio = await asyncio.to_thread(synthesize_qq_voice, reply)
            except Exception as error:
                log_event("QQ", "语音回退", f"{incoming.scope} · {type(error).__name__}: {error}")
                print(f"QQ 语音生成失败，改发文字：{type(error).__name__}")
        async with self.send_lock:
            now = time.monotonic()
            await asyncio.sleep(max(0.0, self.next_send_at - now))
            # A multi-bubble response is ONE logical turn for hourly/cooldown
            # limits. Every physical action remains spaced by next_send_at.
            if not continuation:
                wait, capped = self.limiter.check(incoming, time.monotonic())
                if capped:
                    return False
                if wait:
                    await asyncio.sleep(wait)
            self.next_send_at = time.monotonic() + 1.2
            if incoming.kind == "private":
                if audio is not None:
                    # SnowLuma resolves a local OneBot record segment and transcodes WAV to SILK.
                    # Keep the file until its action returns; never retry an uncertain delivery.
                    with tempfile.TemporaryDirectory(prefix="zhuangfy-qq-") as directory:
                        path = Path(directory) / "reply.wav"
                        path.write_bytes(audio)
                        await self.action("send_private_msg", {"user_id": incoming.user_id,
                            "message": [{"type": "record", "data": {"file": path.as_uri()}}]}, timeout=60)
                else:
                    await self.action("send_private_msg", {"user_id": incoming.user_id,
                                                           "message": reply, "auto_escape": True})
            else:
                await self.action("send_group_msg", {"group_id": incoming.group_id,
                    "message": [{"type": "at", "data": {"qq": str(incoming.user_id)}},
                                {"type": "text", "data": {"text": " " + reply}}]})
            if not continuation:
                self.limiter.mark_sent(incoming, time.monotonic())
            print(("已投递 QQ 私聊语音" if audio is not None else "已投递 QQ 私聊回复")
                  if incoming.kind == "private" else "已投递 QQ 群回复", flush=True)
            log_event("QQ", "语音回复" if audio is not None else "回复", f"{incoming.scope} · {reply}")
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
                    try:
                        identity_path = IDENTITY_FILE
                        temporary = identity_path.with_suffix(".tmp")
                        temporary.write_text(json.dumps({"bot_id": self.bot_id}) + "\n", encoding="utf-8")
                        os.replace(temporary, identity_path)
                    except OSError as error:
                        print(f"无法保存当前机器人账号供语音关联：{error}")
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
