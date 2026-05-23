from __future__ import annotations

import importlib.util
import sys
import time
import types
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

PLUGIN_PATH = Path(__file__).resolve().parents[1] / "main.py"


def _decorator(*args: Any, **kwargs: Any):
    def wrap(func):
        return func

    return wrap


def install_astrbot_stubs() -> dict[str, types.ModuleType]:
    astrbot_mod = types.ModuleType("astrbot")
    api_mod = types.ModuleType("astrbot.api")
    event_mod = types.ModuleType("astrbot.api.event")
    provider_mod = types.ModuleType("astrbot.api.provider")
    core_mod = types.ModuleType("astrbot.core")
    agent_mod = types.ModuleType("astrbot.core.agent")
    agent_message_mod = types.ModuleType("astrbot.core.agent.message")
    message_mod = types.ModuleType("astrbot.core.message")
    components_mod = types.ModuleType("astrbot.core.message.components")

    class Logger:
        def info(self, *args, **kwargs):
            pass

        def debug(self, *args, **kwargs):
            pass

        def warning(self, *args, **kwargs):
            pass

    class Star:
        def __init__(self, context):
            self.context = context

    StarNamespace = types.SimpleNamespace(Star=Star, Context=object)

    class EventMessageType:
        ALL = object()

    class PermissionType:
        ADMIN = object()

    FilterNamespace = types.SimpleNamespace(
        EventMessageType=EventMessageType,
        PermissionType=PermissionType,
        event_message_type=_decorator,
        on_llm_request=_decorator,
        on_using_llm_tool=_decorator,
        on_llm_response=_decorator,
        on_agent_done=_decorator,
        on_decorating_result=_decorator,
        llm_tool=_decorator,
        permission_type=_decorator,
        command=_decorator,
    )

    class TextPart:
        def __init__(self, text: str):
            self.text = text
            self.temp = False

        def mark_as_temp(self):
            self.temp = True
            return self

    class At:
        def __init__(self, qq: str, name: str = ""):
            self.qq = qq
            self.name = name

    api_mod.logger = Logger()
    api_mod.star = StarNamespace
    event_mod.AstrMessageEvent = object
    event_mod.filter = FilterNamespace
    provider_mod.LLMResponse = object
    provider_mod.ProviderRequest = object
    agent_message_mod.TextPart = TextPart
    components_mod.At = At

    return {
        "astrbot": astrbot_mod,
        "astrbot.api": api_mod,
        "astrbot.api.event": event_mod,
        "astrbot.api.provider": provider_mod,
        "astrbot.core": core_mod,
        "astrbot.core.agent": agent_mod,
        "astrbot.core.agent.message": agent_message_mod,
        "astrbot.core.message": message_mod,
        "astrbot.core.message.components": components_mod,
    }


def load_plugin_module():
    with patch.dict(sys.modules, install_astrbot_stubs()):
        spec = importlib.util.spec_from_file_location("suanle_main", PLUGIN_PATH)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        sys.modules["suanle_main"] = module
        spec.loader.exec_module(module)
        return module


class FakeConfig(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.saved = False

    def save_config(self):
        self.saved = True


class FakeContext:
    def __init__(self, core_config: dict | None = None):
        self.core_config = core_config or {
            "admins_id": [],
            "provider_settings": {
                "streaming_response": False,
                "show_tool_use_status": False,
            },
        }

    def get_config(self, umo: str | None = None):
        return self.core_config


class FakeMessageObj:
    def __init__(self, raw: dict | None = None, message_id: str = "m1", message=None):
        self.raw_message = raw
        self.message_id = message_id
        self.message = message or []


class FakeResult:
    def __init__(self, chain=None):
        self.chain = chain if chain is not None else []


class FakeEvent:
    def __init__(
        self,
        sender_id: str = "100",
        sender_name: str = "Alice",
        group_id: str = "200",
        umo: str = "aiocqhttp:group:200",
        message: str = "hello",
        raw: dict | None = None,
        message_id: str = "m1",
        components=None,
        self_id: str = "bot",
    ):
        self._sender_id = sender_id
        self._sender_name = sender_name
        self._group_id = group_id
        self.unified_msg_origin = umo
        self.message_str = message
        self.message_obj = FakeMessageObj(raw, message_id, components or [])
        self._self_id = self_id
        self.extras: dict[str, Any] = {}
        self.stopped = False
        self.result = None

    def get_sender_id(self):
        return self._sender_id

    def get_sender_name(self):
        return self._sender_name

    def get_group_id(self):
        return self._group_id

    def get_self_id(self):
        return self._self_id

    def get_message_outline(self):
        return self.message_str

    def get_message_str(self):
        return self.message_str

    def get_messages(self):
        return self.message_obj.message

    def set_extra(self, key, value):
        self.extras[key] = value

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def stop_event(self):
        self.stopped = True

    def is_stopped(self):
        return self.stopped

    def set_result(self, result):
        self.result = result

    def get_result(self):
        return self.result

    def clear_result(self):
        self.result = None

    def plain_result(self, text):
        return FakeResult([text])


class FakeReq:
    def __init__(self):
        self.extra_user_content_parts = []
        self.system_prompt = ""


class FakeResp:
    def __init__(self, text: str):
        self.result_chain = None
        self._completion_text = text
        self.reasoning_content = "thinking"

    @property
    def completion_text(self):
        return self._completion_text

    @completion_text.setter
    def completion_text(self, value):
        self._completion_text = value


class FakeTool:
    def __init__(self, name: str):
        self.name = name


class SuanlePluginTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mod = load_plugin_module()

    def make_plugin(self, config: dict | None = None, core_config: dict | None = None):
        cfg = FakeConfig(config or {})
        plugin = self.mod.Main(FakeContext(core_config), cfg)
        return plugin, cfg

    async def test_load_plugin_module_restores_sys_modules(self):
        missing = object()
        previous_astrbot = sys.modules.get("astrbot", missing)
        sentinel_astrbot = types.ModuleType("astrbot")
        sys.modules["astrbot"] = sentinel_astrbot
        try:
            load_plugin_module()
            self.assertIs(sys.modules.get("astrbot"), sentinel_astrbot)
            self.assertNotIn("suanle_main", sys.modules)
        finally:
            if previous_astrbot is missing:
                sys.modules.pop("astrbot", None)
            else:
                sys.modules["astrbot"] = previous_astrbot

    async def test_blacklisted_message_is_stopped_and_recorded(self):
        plugin, _ = self.make_plugin({"blacklist_qq": ["100"]})
        event = FakeEvent(sender_id="100", message="bot 你说句话", message_id="42")

        await plugin.on_all_message(event)

        self.assertTrue(event.stopped)
        self.assertEqual(len(plugin._blocked_messages[event.unified_msg_origin]), 1)

    async def test_enable_false_does_not_block_blacklisted_message(self):
        plugin, _ = self.make_plugin({"enable": False, "blacklist_qq": ["100"]})
        event = FakeEvent(sender_id="100", message="bot 你说句话", message_id="42")

        await plugin.on_all_message(event)

        self.assertFalse(event.stopped)
        self.assertNotIn(event.unified_msg_origin, plugin._blocked_messages)

    async def test_must_reply_user_overrides_blacklist(self):
        plugin, _ = self.make_plugin(
            {"blacklist_qq": ["100"], "must_reply_uid": ["100"]}
        )
        event = FakeEvent(sender_id="100", message="必须回复的人")

        await plugin.on_all_message(event)

        self.assertFalse(event.stopped)
        self.assertNotIn(event.unified_msg_origin, plugin._blocked_messages)

    async def test_protected_admin_config_blacklist_is_ignored(self):
        core_config = {"admins_id": ["100"], "provider_settings": {}}
        plugin, _ = self.make_plugin(
            {"blacklist_qq": ["100"], "protected_admins": True},
            core_config,
        )
        event = FakeEvent(sender_id="100", message="管理员被误填黑名单")

        await plugin.on_all_message(event)

        self.assertFalse(event.stopped)
        self.assertNotIn(event.unified_msg_origin, plugin._blocked_messages)

    async def test_protected_admin_does_not_make_keep_silent_must_reply(self):
        core_config = {"admins_id": ["100"], "provider_settings": {}}
        plugin, _ = self.make_plugin({"protected_admins": True}, core_config)
        event = FakeEvent(sender_id="100", message="管理员普通闲聊")

        result = await plugin.keep_silent(event, reason="没有必要回复")

        self.assertIn("ok", result)
        self.assertTrue(event.get_extra("_suanle_silent_requested", False))

    async def test_group_uid_blacklist_matches_umo_and_group_id_forms(self):
        plugin, _ = self.make_plugin(
            {
                "blacklist_group_uid": [
                    "aiocqhttp:group:200:100",
                    "201:101",
                ]
            }
        )
        event_by_umo = FakeEvent(
            sender_id="100", group_id="200", umo="aiocqhttp:group:200"
        )
        event_by_gid = FakeEvent(
            sender_id="101", group_id="201", umo="aiocqhttp:group:201"
        )

        await plugin.on_all_message(event_by_umo)
        await plugin.on_all_message(event_by_gid)

        self.assertTrue(event_by_umo.stopped)
        self.assertTrue(event_by_gid.stopped)

    async def test_recall_notice_does_not_stop_and_removes_cache(self):
        plugin, _ = self.make_plugin({"blacklist_qq": ["100"]})
        original = FakeEvent(sender_id="100", message="will recall", message_id="42")
        await plugin.on_all_message(original)
        recall = FakeEvent(
            raw={
                "post_type": "notice",
                "notice_type": "group_recall",
                "message_id": "42",
            },
            message_id="42",
        )

        await plugin.on_all_message(recall)

        self.assertFalse(recall.stopped)
        self.assertNotIn(original.unified_msg_origin, plugin._blocked_messages)

    async def test_friend_recall_notice_does_not_stop(self):
        plugin, _ = self.make_plugin({"blacklist_qq": ["100"]})
        recall = FakeEvent(
            raw={
                "post_type": "notice",
                "notice_type": "friend_recall",
                "message_id": "42",
            },
            message_id="42",
        )

        await plugin.on_all_message(recall)

        self.assertFalse(recall.stopped)

    async def test_llm_request_injects_blocked_context(self):
        plugin, _ = self.make_plugin(
            {"blacklist_qq": ["100"], "blocked_context_window": 5}
        )
        blocked = FakeEvent(
            sender_id="100", sender_name="Blocked", message="secret", message_id="42"
        )
        await plugin.on_all_message(blocked)
        normal = FakeEvent(sender_id="101", message="正常触发")
        req = FakeReq()

        await plugin.on_llm_request(normal, req)

        texts = [part.text for part in req.extra_user_content_parts]
        self.assertTrue(any("<blocked_messages>" in text for text in texts))
        self.assertTrue(any("secret" in text for text in texts))

    async def test_blocked_context_respects_window_and_ttl(self):
        plugin, _ = self.make_plugin(
            {"blocked_context_window": 2, "blocked_context_ttl_seconds": 10}
        )
        umo = "aiocqhttp:group:200"
        now = time.time()
        plugin._blocked_messages[umo] = [
            self.mod.BlockedMessage(umo, "100", "Old", "1", "expired", now - 99),
            self.mod.BlockedMessage(umo, "100", "A", "2", "first", now - 3),
            self.mod.BlockedMessage(umo, "100", "B", "3", "second", now - 2),
            self.mod.BlockedMessage(umo, "100", "C", "4", "third", now - 1),
        ]

        text = plugin._blocked_context_text(umo)

        self.assertNotIn("expired", text)
        self.assertNotIn("first", text)
        self.assertIn("second", text)
        self.assertIn("third", text)

    async def test_blocked_context_cache_has_umo_capacity_limit(self):
        plugin, _ = self.make_plugin({"blocked_context_ttl_seconds": 86400})
        now = time.time()
        for index in range(self.mod.MAX_BLOCKED_UMO_CACHE + 1):
            umo = f"aiocqhttp:group:{index}"
            plugin._blocked_messages[umo] = [
                self.mod.BlockedMessage(umo, "100", "A", str(index), "msg", now)
            ]

        plugin._cleanup_blocked_cache()

        self.assertEqual(len(plugin._blocked_messages), self.mod.MAX_BLOCKED_UMO_CACHE)
        self.assertNotIn("aiocqhttp:group:0", plugin._blocked_messages)
        self.assertIn(
            f"aiocqhttp:group:{self.mod.MAX_BLOCKED_UMO_CACHE}",
            plugin._blocked_messages,
        )

    async def test_blocked_context_access_refreshes_cache_order(self):
        plugin, _ = self.make_plugin({"blocked_context_ttl_seconds": 86400})
        now = time.time()
        for index in range(self.mod.MAX_BLOCKED_UMO_CACHE + 1):
            umo = f"aiocqhttp:group:{index}"
            plugin._blocked_messages[umo] = [
                self.mod.BlockedMessage(umo, "100", "A", str(index), "msg", now)
            ]

        text = plugin._blocked_context_text("aiocqhttp:group:0")

        self.assertIn("msg", text)
        self.assertIn("aiocqhttp:group:0", plugin._blocked_messages)
        self.assertNotIn("aiocqhttp:group:1", plugin._blocked_messages)

    async def test_warned_runtime_umo_cache_has_capacity_limit(self):
        plugin, _ = self.make_plugin()
        for index in range(self.mod.MAX_WARNED_RUNTIME_UMO_CACHE + 1):
            await plugin.on_llm_request(
                FakeEvent(umo=f"aiocqhttp:group:{index}"),
                FakeReq(),
            )

        self.assertEqual(
            len(plugin._warned_runtime_umo), self.mod.MAX_WARNED_RUNTIME_UMO_CACHE
        )
        self.assertNotIn("aiocqhttp:group:0", plugin._warned_runtime_umo)
        self.assertIn(
            f"aiocqhttp:group:{self.mod.MAX_WARNED_RUNTIME_UMO_CACHE}",
            plugin._warned_runtime_umo,
        )

    async def test_agent_stop_requested_makes_llm_request_return(self):
        plugin, _ = self.make_plugin({"blacklist_qq": ["100"]})
        event = FakeEvent()
        event.set_extra("agent_stop_requested", True)
        req = FakeReq()

        await plugin.on_llm_request(event, req)

        self.assertEqual(req.extra_user_content_parts, [])

    async def test_on_using_llm_tool_marks_keep_silent(self):
        plugin, _ = self.make_plugin()
        event = FakeEvent(sender_id="101")

        await plugin.on_using_llm_tool(
            event, FakeTool("keep_silent"), {"reason": "插不上话"}
        )

        self.assertTrue(event.get_extra("_suanle_silent_requested", False))
        self.assertEqual(event.get_extra("_suanle_silent_reason"), "插不上话")

    async def test_recall_cancel_extra_prevents_silent_response_clear(self):
        plugin, _ = self.make_plugin()
        event = FakeEvent(sender_id="101")
        event.set_extra("_suanle_silent_requested", True)
        event.set_extra("agent_stop_requested", True)
        resp = FakeResp("recall_cancel 负责处理")

        await plugin.on_llm_response(event, resp)

        self.assertEqual(resp.completion_text, "recall_cancel 负责处理")

    async def test_keep_silent_clears_llm_response(self):
        plugin, _ = self.make_plugin()
        event = FakeEvent(sender_id="101")
        resp = FakeResp("应该消失")

        result = await plugin.keep_silent(event, reason="无关", confidence=0.9)
        await plugin.on_llm_response(event, resp)

        self.assertIn("ok", result)
        self.assertEqual(resp.completion_text, "")
        self.assertEqual(resp.reasoning_content, "")

    async def test_keep_silent_clears_result_chain_without_empty_plain(self):
        plugin, _ = self.make_plugin()
        event = FakeEvent(sender_id="101")
        resp = FakeResp("应该消失")
        resp.result_chain = FakeResult(["text"])

        await plugin.keep_silent(event, reason="无关", confidence=0.9)
        await plugin.on_llm_response(event, resp)

        self.assertIsNone(resp.result_chain)
        self.assertEqual(resp.completion_text, "")

    async def test_must_reply_refuses_keep_silent(self):
        plugin, _ = self.make_plugin({"must_reply_uid": ["101"]})
        event = FakeEvent(sender_id="101")

        result = await plugin.keep_silent(event, reason="无关")

        self.assertIn("error", result)
        self.assertFalse(event.get_extra("_suanle_silent_requested", False))

    async def test_decorating_result_clears_silent_output(self):
        plugin, _ = self.make_plugin()
        event = FakeEvent(sender_id="101")
        event.set_extra("_suanle_silent_requested", True)
        event.set_result(FakeResult(["text"]))

        await plugin.on_decorating_result(event)

        self.assertIsNone(event.get_result())

    async def test_block_and_unblock_commands_persist_config(self):
        plugin, cfg = self.make_plugin()
        event = FakeEvent(sender_id="admin")

        await plugin.block_user(event, "123456789")
        self.assertIn("123456789", cfg["blacklist_qq"])
        self.assertTrue(cfg.saved)

        await plugin.unblock_user(event, "123456789")
        self.assertNotIn("123456789", cfg["blacklist_qq"])

    async def test_protected_admin_cannot_be_blocked(self):
        core_config = {"admins_id": ["999"], "provider_settings": {}}
        plugin, cfg = self.make_plugin({"protected_admins": True}, core_config)
        event = FakeEvent(sender_id="admin")

        await plugin.block_user(event, "999")

        self.assertNotIn("blacklist_qq", cfg)

    async def test_block_command_extracts_at_target(self):
        plugin, cfg = self.make_plugin()
        event = FakeEvent(sender_id="admin", components=[self.mod.At(qq="222222")])

        await plugin.block_user(event, "")

        self.assertIn("222222", cfg["blacklist_qq"])


if __name__ == "__main__":
    unittest.main()
