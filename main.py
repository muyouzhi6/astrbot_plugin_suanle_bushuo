from __future__ import annotations

import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Final

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.core.agent.message import TextPart
from astrbot.core.message.components import At

PLUGIN_VERSION: Final[str] = "0.1.1"

EXTRA_SILENT_REQUESTED: Final[str] = "_suanle_silent_requested"
EXTRA_SILENT_REASON: Final[str] = "_suanle_silent_reason"
EXTRA_BLOCKED_CONTEXT_INJECTED: Final[str] = "_suanle_blocked_context_injected"
EXTRA_SILENCE_POLICY_INJECTED: Final[str] = "_suanle_silence_policy_injected"
EXTRA_RECALL_STOP_REQUESTED: Final[str] = "agent_stop_requested"

NOTICE_GROUP_RECALL: Final[str] = "group_recall"
NOTICE_FRIEND_RECALL: Final[str] = "friend_recall"
RECALL_NOTICE_TYPES: Final[set[str]] = {NOTICE_GROUP_RECALL, NOTICE_FRIEND_RECALL}
MAX_BLOCKED_UMO_CACHE: Final[int] = 1024
MAX_WARNED_RUNTIME_UMO_CACHE: Final[int] = 1024

DEFAULTS: Final[dict[str, Any]] = {
    "enable": True,
    "silence_tool_enable": True,
    "must_reply_umo": [],
    "must_reply_uid": [],
    "must_reply_gid": [],
    "blacklist_qq": [],
    "blacklist_uid": [],
    "blacklist_umo": [],
    "blacklist_group_uid": [],
    "protected_admins": True,
    "blocked_context_window": 20,
    "blocked_context_ttl_seconds": 86400,
    "respect_recall_notice": True,
    "strict_non_streaming_warning": True,
    "debug_log": False,
}


def _optional_filter_hook(name: str, **kwargs: Any):
    hook = getattr(filter, name, None)
    if callable(hook):
        return hook(**kwargs)

    def decorator(func):
        return func

    return decorator


@dataclass(slots=True)
class BlockedMessage:
    umo: str
    sender_id: str
    sender_name: str
    message_id: str | None
    content: str
    timestamp: float

    def format_for_prompt(self) -> str:
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp))
        name = self.sender_name or self.sender_id or "unknown"
        uid = self.sender_id or "unknown"
        return f"- [{when}] {name}({uid}): {self.content}"


class Main(star.Star):
    """算了不说了: LLM 自主沉默 + 黑名单触发阻断."""

    def __init__(self, context: star.Context, config: Any | None = None) -> None:
        super().__init__(context)
        self._config = config if config is not None else {}
        self._blocked_messages: dict[str, list[BlockedMessage]] = {}
        self._warned_runtime_umo: OrderedDict[str, None] = OrderedDict()
        logger.info(f"[算了不说了] 插件 v{PLUGIN_VERSION} 已加载")

    # ------------------------------------------------------------------
    # 配置读取
    # ------------------------------------------------------------------

    def _cfg(self, key: str) -> Any:
        if isinstance(self._config, dict):
            return self._config.get(key, DEFAULTS.get(key))
        getter = getattr(self._config, "get", None)
        if callable(getter):
            try:
                return getter(key, DEFAULTS.get(key))
            except TypeError as e:
                self._debug_compat(f"读取配置项 {key} 时回退到属性访问", e)
        return getattr(self._config, key, DEFAULTS.get(key))

    def _cfg_bool(self, key: str) -> bool:
        return bool(self._cfg(key))

    def _cfg_int(self, key: str) -> int:
        try:
            return int(self._cfg(key))
        except (TypeError, ValueError):
            return int(DEFAULTS[key])

    def _cfg_list(self, key: str) -> list[str]:
        value = self._cfg(key)
        if isinstance(value, list | tuple | set):
            return [
                self._normalize_id(item) for item in value if self._normalize_id(item)
            ]
        if value:
            return [self._normalize_id(value)]
        return []

    def _set_cfg_list(self, key: str, values: list[str]) -> None:
        values = sorted(
            {self._normalize_id(item) for item in values if self._normalize_id(item)}
        )
        if isinstance(self._config, dict):
            self._config[key] = values
        else:
            setattr(self._config, key, values)
        self._save_config()

    def _save_config(self) -> None:
        saver = getattr(self._config, "save_config", None)
        if callable(saver):
            saver()

    def _debug_enabled(self) -> bool:
        if isinstance(self._config, dict):
            return bool(self._config.get("debug_log", DEFAULTS["debug_log"]))
        getter = getattr(self._config, "get", None)
        if callable(getter):
            try:
                return bool(getter("debug_log", DEFAULTS["debug_log"]))
            except TypeError:
                try:
                    return bool(getter("debug_log"))
                except Exception:
                    return bool(
                        getattr(self._config, "debug_log", DEFAULTS["debug_log"])
                    )
            except Exception:
                return bool(getattr(self._config, "debug_log", DEFAULTS["debug_log"]))
        return bool(getattr(self._config, "debug_log", DEFAULTS["debug_log"]))

    def _debug(self, message: str) -> None:
        if self._debug_enabled():
            logger.debug(f"[算了不说了] {message}")

    def _debug_compat(self, message: str, error: BaseException) -> None:
        self._debug(f"{message}: {type(error).__name__}: {error}")

    # ------------------------------------------------------------------
    # 事件与 ID 工具
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_id(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    def _raw_get(self, raw: Any, key: str, default: Any = None) -> Any:
        if raw is None:
            return default
        if isinstance(raw, dict):
            return raw.get(key, default)
        value = getattr(raw, key, default)
        if value is not default:
            return value
        getter = getattr(raw, "get", None)
        if callable(getter):
            try:
                return getter(key, default)
            except TypeError:
                try:
                    return getter(key)
                except Exception as e:
                    self._debug_compat(f"读取 raw_message.{key} 失败", e)
                    return default
            except Exception as e:
                self._debug_compat(f"读取 raw_message.{key} 失败", e)
                return default
        return default

    def _extract_message_id(self, event: AstrMessageEvent) -> str | None:
        try:
            raw = getattr(event.message_obj, "raw_message", None)
            raw_msg_id = self._normalize_id(self._raw_get(raw, "message_id"))
            if raw_msg_id:
                return raw_msg_id
            msg_id = self._normalize_id(getattr(event.message_obj, "message_id", None))
            return msg_id or None
        except Exception as e:
            self._debug_compat("提取 message_id 失败", e)
            return None

    def _is_recall_notice(self, event: AstrMessageEvent) -> tuple[bool, str | None]:
        try:
            raw = getattr(event.message_obj, "raw_message", None)
            if not raw:
                return False, None
            post_type = self._raw_get(raw, "post_type")
            if post_type not in (None, "notice"):
                return False, None
            notice_type = self._raw_get(raw, "notice_type")
            if notice_type not in RECALL_NOTICE_TYPES:
                return False, None
            msg_id = self._normalize_id(self._raw_get(raw, "message_id"))
            return bool(msg_id), msg_id or None
        except Exception as e:
            self._debug_compat("判断撤回 notice 失败", e)
            return False, None

    @staticmethod
    def _clean_one_line(value: Any) -> str:
        text = "" if value is None else str(value)
        return " ".join(text.replace("\r", " ").replace("\n", " ").split())

    def _message_outline(self, event: AstrMessageEvent) -> str:
        for getter_name in ("get_message_outline", "get_message_str"):
            getter = getattr(event, getter_name, None)
            if callable(getter):
                try:
                    outline = self._clean_one_line(getter())
                    if outline:
                        return outline
                except Exception as e:
                    self._debug_compat(f"调用 {getter_name} 获取消息摘要失败", e)
        return self._clean_one_line(getattr(event, "message_str", ""))

    def _get_messages(self, event: AstrMessageEvent) -> list[Any]:
        getter = getattr(event, "get_messages", None)
        if callable(getter):
            try:
                messages = getter()
                if isinstance(messages, list):
                    return messages
            except Exception as e:
                self._debug_compat("调用 get_messages 获取消息组件失败", e)
        message_obj = getattr(event, "message_obj", None)
        messages = getattr(message_obj, "message", None)
        return messages if isinstance(messages, list) else []

    def _admin_ids(self) -> set[str]:
        try:
            cfg = self.context.get_config()
            admins = cfg.get("admins_id", []) if isinstance(cfg, dict) else []
        except Exception as e:
            self._debug_compat("读取 AstrBot 管理员列表失败", e)
            admins = []
        return {self._normalize_id(item) for item in admins if self._normalize_id(item)}

    def _is_admin_protected(self, user_id: str) -> bool:
        return self._cfg_bool("protected_admins") and user_id in self._admin_ids()

    def _must_reply_event(self, event: AstrMessageEvent) -> bool:
        uid = self._normalize_id(event.get_sender_id())
        gid = self._normalize_id(event.get_group_id())
        umo = self._normalize_id(event.unified_msg_origin)
        return (
            bool(uid and uid in self._cfg_list("must_reply_uid"))
            or bool(gid and gid in self._cfg_list("must_reply_gid"))
            or bool(umo and umo in self._cfg_list("must_reply_umo"))
        )

    def _is_blacklisted(self, event: AstrMessageEvent) -> bool:
        if not self._cfg_bool("enable"):
            return False

        uid = self._normalize_id(event.get_sender_id())
        gid = self._normalize_id(event.get_group_id())
        umo = self._normalize_id(event.unified_msg_origin)
        if self._is_admin_protected(uid) or self._must_reply_event(event):
            return False
        group_uid_keys = {f"{umo}:{uid}", f"{gid}:{uid}"}

        return (
            bool(uid and uid in self._cfg_list("blacklist_qq"))
            or bool(uid and uid in self._cfg_list("blacklist_uid"))
            or bool(umo and umo in self._cfg_list("blacklist_umo"))
            or bool(group_uid_keys & set(self._cfg_list("blacklist_group_uid")))
        )

    # ------------------------------------------------------------------
    # 黑名单上下文缓存
    # ------------------------------------------------------------------

    def _cleanup_blocked_cache(self) -> None:
        ttl = max(1, self._cfg_int("blocked_context_ttl_seconds"))
        window = max(1, self._cfg_int("blocked_context_window"))
        max_keep = max(window * 4, 50)
        now = time.time()
        for umo in list(self._blocked_messages):
            kept = [
                item
                for item in self._blocked_messages[umo]
                if now - item.timestamp <= ttl
            ][-max_keep:]
            if kept:
                self._blocked_messages[umo] = kept
            else:
                del self._blocked_messages[umo]
        while len(self._blocked_messages) > MAX_BLOCKED_UMO_CACHE:
            removed_umo = next(iter(self._blocked_messages))
            del self._blocked_messages[removed_umo]
            self._debug(f"黑名单上下文缓存超过上限, 已淘汰最旧 UMO: {removed_umo}")

    def _touch_blocked_umo(self, umo: str) -> None:
        if umo in self._blocked_messages:
            self._blocked_messages[umo] = self._blocked_messages.pop(umo)

    def _record_blocked_message(self, event: AstrMessageEvent) -> None:
        content = self._message_outline(event)
        if not content:
            return
        umo = self._normalize_id(event.unified_msg_origin)
        if not umo:
            return
        item = BlockedMessage(
            umo=umo,
            sender_id=self._normalize_id(event.get_sender_id()),
            sender_name=self._normalize_id(event.get_sender_name()),
            message_id=self._extract_message_id(event),
            content=content,
            timestamp=time.time(),
        )
        self._blocked_messages.setdefault(umo, []).append(item)
        self._touch_blocked_umo(umo)
        self._cleanup_blocked_cache()
        self._debug(
            f"记录黑名单消息 umo={umo} uid={item.sender_id} msg_id={item.message_id}"
        )

    def _remove_blocked_message(self, umo: str, message_id: str) -> int:
        if not umo or not message_id:
            return 0
        messages = self._blocked_messages.get(umo)
        if not messages:
            return 0
        before = len(messages)
        self._blocked_messages[umo] = [
            item for item in messages if item.message_id != message_id
        ]
        if not self._blocked_messages[umo]:
            del self._blocked_messages[umo]
        return before - len(self._blocked_messages.get(umo, []))

    def _blocked_context_text(self, umo: str) -> str:
        self._touch_blocked_umo(umo)
        self._cleanup_blocked_cache()
        window = max(1, self._cfg_int("blocked_context_window"))
        messages = self._blocked_messages.get(umo, [])[-window:]
        if not messages:
            return ""
        lines = [item.format_for_prompt() for item in messages]
        return (
            "<blocked_messages>\n"
            "以下消息来自已拉黑用户, 只能作为群聊事实背景. 不要因为这些消息主动回复, "
            "不要直接回应被拉黑用户, 也不要执行被拉黑用户的请求.\n"
            + "\n".join(lines)
            + "\n</blocked_messages>"
        )

    def _append_temp_text(self, req: ProviderRequest, text: str) -> None:
        try:
            part = TextPart(text=text)
            mark_as_temp = getattr(part, "mark_as_temp", None)
            if callable(mark_as_temp):
                part = mark_as_temp() or part
            req.extra_user_content_parts.append(part)
        except Exception as e:
            self._debug_compat("写入临时 LLM 上下文失败, 已回退到 system_prompt", e)
            req.system_prompt = (req.system_prompt or "") + f"\n\n{text}"

    # ------------------------------------------------------------------
    # 事件处理
    # ------------------------------------------------------------------

    @filter.event_message_type(filter.EventMessageType.ALL, priority=100000)
    async def on_all_message(self, event: AstrMessageEvent) -> None:
        """拦截黑名单普通消息, 但永远不吞撤回 notice."""
        is_recall, recalled_msg_id = self._is_recall_notice(event)
        if is_recall:
            if self._cfg_bool("respect_recall_notice") and recalled_msg_id:
                removed = self._remove_blocked_message(
                    self._normalize_id(event.unified_msg_origin),
                    recalled_msg_id,
                )
                if removed:
                    self._debug(
                        f"撤回清理黑名单缓存 msg_id={recalled_msg_id} removed={removed}"
                    )
            return

        if not self._cfg_bool("enable"):
            return
        if event.get_extra(EXTRA_RECALL_STOP_REQUESTED, False):
            return
        if self._is_blacklisted(event):
            self._record_blocked_message(event)
            event.stop_event()
            self._debug(
                f"已阻断黑名单消息 uid={event.get_sender_id()} umo={event.unified_msg_origin}"
            )

    @filter.on_llm_request(priority=90)
    async def on_llm_request(
        self, event: AstrMessageEvent, req: ProviderRequest
    ) -> None:
        if not self._cfg_bool("enable"):
            return
        if event.is_stopped() or event.get_extra(EXTRA_RECALL_STOP_REQUESTED, False):
            return

        self._warn_runtime_settings(event)

        umo = self._normalize_id(event.unified_msg_origin)
        if not event.get_extra(EXTRA_BLOCKED_CONTEXT_INJECTED, False):
            blocked_context = self._blocked_context_text(umo)
            if blocked_context:
                self._append_temp_text(req, blocked_context)
                event.set_extra(EXTRA_BLOCKED_CONTEXT_INJECTED, True)

        if self._cfg_bool("silence_tool_enable") and not event.get_extra(
            EXTRA_SILENCE_POLICY_INJECTED, False
        ):
            must_reply = self._must_reply_event(event)
            policy = (
                "<suanle_silence_policy>\n"
                "你可以调用 keep_silent 工具来表示本轮不回复. 当群聊内容与你无关, "
                "没有合适插话时机, 或你不想理会当前请求时, 应优先调用 keep_silent.\n"
                f"当前发送者/会话是否属于必须回复白名单: {'是' if must_reply else '否'}.\n"
                "如果属于必须回复白名单, 禁止调用 keep_silent, 必须正常回复.\n"
                "如果调用 keep_silent, 本轮会立即结束, 不需要继续生成任何最终回复.\n"
                "</suanle_silence_policy>"
            )
            self._append_temp_text(req, policy)
            event.set_extra(EXTRA_SILENCE_POLICY_INJECTED, True)

    @filter.on_using_llm_tool(priority=90)
    async def on_using_llm_tool(
        self, event: AstrMessageEvent, tool: Any, tool_args: dict | None
    ) -> None:
        if getattr(tool, "name", "") != "keep_silent":
            return
        if not self._cfg_bool("enable") or not self._cfg_bool("silence_tool_enable"):
            return
        if event.get_extra(EXTRA_RECALL_STOP_REQUESTED, False):
            return
        if not self._must_reply_event(event):
            event.set_extra(EXTRA_SILENT_REQUESTED, True)
            reason = ""
            if isinstance(tool_args, dict):
                reason = self._normalize_id(tool_args.get("reason"))
            event.set_extra(EXTRA_SILENT_REASON, reason)

    @filter.on_llm_response(priority=90)
    async def on_llm_response(self, event: AstrMessageEvent, resp: LLMResponse) -> None:
        if self._should_keep_silent(event):
            self._clear_llm_response(resp)

    @_optional_filter_hook("on_agent_done", priority=90)
    async def on_agent_done(
        self, event: AstrMessageEvent, run_context: Any, resp: LLMResponse
    ) -> None:
        if self._should_keep_silent(event):
            self._clear_llm_response(resp)

    @filter.on_decorating_result(priority=90)
    async def on_decorating_result(self, event: AstrMessageEvent) -> None:
        if not self._should_keep_silent(event):
            return
        result = event.get_result()
        if result is not None and getattr(result, "chain", None) is not None:
            result.chain = []
        clear_result = getattr(event, "clear_result", None)
        if callable(clear_result):
            clear_result()
        self._debug(
            f"沉默生效, 已清空发送结果 reason={event.get_extra(EXTRA_SILENT_REASON, '')}"
        )

    @filter.llm_tool(name="keep_silent")
    async def keep_silent(
        self,
        event: AstrMessageEvent,
        reason: str = "",
        confidence: float = 1.0,
    ) -> str | None:
        """保持沉默, 本轮不发送任何回复.

        Args:
            reason(string): 保持沉默的原因, 简短说明即可.
            confidence(number): 你认为应该沉默的置信度, 0 到 1.
        """
        if not self._cfg_bool("enable") or not self._cfg_bool("silence_tool_enable"):
            return "error: keep_silent is disabled by plugin config."
        if event.get_extra(EXTRA_RECALL_STOP_REQUESTED, False):
            return "ignored: current event is already being stopped by recall_cancel."
        if self._must_reply_event(event):
            return (
                "error: 当前发送者或会话在必须回复白名单中, 不能保持沉默, 请正常回复."
            )
        event.set_extra(EXTRA_SILENT_REQUESTED, True)
        event.set_extra(EXTRA_SILENT_REASON, self._clean_one_line(reason))
        try:
            confidence_f = float(confidence)
        except (TypeError, ValueError):
            confidence_f = 1.0
        event.set_extra("_suanle_silent_confidence", max(0.0, min(1.0, confidence_f)))
        self._debug(
            f"沉默工具生效, Agent 将直接结束 reason={event.get_extra(EXTRA_SILENT_REASON, '')}"
        )
        return None

    # ------------------------------------------------------------------
    # 管理命令
    # ------------------------------------------------------------------

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("拉黑")
    async def block_user(self, event: AstrMessageEvent, target: str = "") -> None:
        user_id = self._extract_command_target(event, target)
        if not user_id:
            self._reply(event, "用法: /拉黑 @用户 或 /拉黑 QQ号")
            return
        if user_id == self._normalize_id(event.get_self_id()):
            self._reply(event, "不能拉黑 Bot 自己.")
            return
        if self._is_admin_protected(user_id):
            self._reply(event, f"用户 {user_id} 是管理员, 已按配置保护, 不会拉黑.")
            return
        if user_id in self._cfg_list("must_reply_uid"):
            self._reply(event, f"用户 {user_id} 在必须回复白名单中, 不能拉黑.")
            return
        values = self._cfg_list("blacklist_qq")
        if user_id not in values:
            values.append(user_id)
            self._set_cfg_list("blacklist_qq", values)
        self._reply(event, f"已拉黑 {user_id}. 之后会记录上下文, 但不会响应 ta 的触发.")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("取消拉黑")
    async def unblock_user(self, event: AstrMessageEvent, target: str = "") -> None:
        user_id = self._extract_command_target(event, target)
        if not user_id:
            self._reply(event, "用法: /取消拉黑 @用户 或 /取消拉黑 QQ号")
            return
        values = [item for item in self._cfg_list("blacklist_qq") if item != user_id]
        self._set_cfg_list("blacklist_qq", values)
        self._reply(event, f"已取消拉黑 {user_id}.")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("黑名单")
    async def show_blacklist(self, event: AstrMessageEvent) -> None:
        lines = [
            "算了不说了 黑名单配置",
            f"blacklist_qq: {', '.join(self._cfg_list('blacklist_qq')) or '(空)'}",
            f"blacklist_uid: {', '.join(self._cfg_list('blacklist_uid')) or '(空)'}",
            f"blacklist_umo: {', '.join(self._cfg_list('blacklist_umo')) or '(空)'}",
            "blacklist_group_uid: "
            + (", ".join(self._cfg_list("blacklist_group_uid")) or "(空)"),
        ]
        self._reply(event, "\n".join(lines))

    def _extract_command_target(self, event: AstrMessageEvent, explicit: str) -> str:
        for comp in self._get_messages(event):
            if isinstance(comp, At):
                qq = self._normalize_id(getattr(comp, "qq", ""))
                if qq and qq.lower() != "all":
                    return qq
        explicit = self._normalize_id(explicit)
        match = re.search(r"\d{5,20}", explicit)
        return match.group(0) if match else ""

    @staticmethod
    def _reply(event: AstrMessageEvent, text: str) -> None:
        plain_result = getattr(event, "plain_result", None)
        if callable(plain_result):
            event.set_result(plain_result(text))
        else:
            event.set_result(text)

    # ------------------------------------------------------------------
    # 沉默清理
    # ------------------------------------------------------------------

    def _should_keep_silent(self, event: AstrMessageEvent) -> bool:
        if event.get_extra(EXTRA_RECALL_STOP_REQUESTED, False):
            return False
        return bool(
            event.get_extra(EXTRA_SILENT_REQUESTED, False)
        ) and not self._must_reply_event(event)

    @staticmethod
    def _clear_llm_response(resp: LLMResponse) -> None:
        try:
            result_chain = getattr(resp, "result_chain", None)
            if result_chain is not None:
                result_chain.chain = []
                resp.result_chain = None
            resp.completion_text = ""
            if hasattr(resp, "_completion_text"):
                resp._completion_text = ""
            if hasattr(resp, "reasoning_content"):
                resp.reasoning_content = ""
        except Exception as e:
            logger.warning(f"[算了不说了] 清空 LLM 响应失败: {e}")

    def _warn_runtime_settings(self, event: AstrMessageEvent) -> None:
        if not self._cfg_bool("strict_non_streaming_warning"):
            return
        umo = self._normalize_id(event.unified_msg_origin)
        if not umo or umo in self._warned_runtime_umo:
            return
        try:
            cfg = self.context.get_config(umo=umo)
            settings = cfg.get("provider_settings", {}) if isinstance(cfg, dict) else {}
            streaming = bool(settings.get("streaming_response", False))
            show_tool_status = bool(settings.get("show_tool_use_status", True))
            show_tool_result = bool(settings.get("show_tool_call_result", False))
        except Exception as e:
            self._debug_compat("读取 provider 运行设置失败", e)
            streaming = False
            show_tool_status = False
            show_tool_result = False
        if streaming or show_tool_status or show_tool_result:
            logger.warning(
                "[算了不说了] 严格沉默建议关闭 provider_settings.streaming_response "
                "provider_settings.show_tool_use_status "
                "和 provider_settings.show_tool_call_result; 否则模型调用 keep_silent 前 "
                "可能已有流式内容, 工具状态或工具结果被发送."
            )
        self._warned_runtime_umo[umo] = None
        self._warned_runtime_umo.move_to_end(umo)
        while len(self._warned_runtime_umo) > MAX_WARNED_RUNTIME_UMO_CACHE:
            self._warned_runtime_umo.popitem(last=False)
