# 算了不说了

让 AstrBot 在不该回复时真的保持沉默, 同时让黑名单用户的消息只进入上下文, 不能触发任何回复。

这个插件适合群聊人格 Bot: 她可以判断"现在没必要说话", 也可以对被拉黑的人做到"我看得见你说了啥, 但我就是不理你"。这样既不会强行插话破坏群聊节奏, 也不会因为屏蔽某个人导致后续上下文断裂。

## 适合场景

- 群聊里经常有人 @ Bot, 但很多时候其实不需要回复。
- 已安装 wakepro 等主动回复插件, 但希望 Bot 能判断"不适合插话"。
- 想拉黑某些用户, 同时又不想让群聊上下文因为屏蔽消息而断裂。
- 希望和 `context_aware`, `wakepro`, `recall_cancel` 一起使用, 并保持各插件职责边界清晰。

## 功能亮点

- LLM 自主沉默: 提供 `keep_silent` tool, 模型可以在不想回复, 不该插话, 或当前话题与自己无关时保持沉默。
- 必须回复白名单: 支持 `must_reply_umo`, `must_reply_uid`, `must_reply_gid`, 命中后模型不能使用沉默工具。
- 黑名单强阻断: 支持 QQ 号, 通用 UID, UMO 会话, 群内定向黑名单, 命中后 @ Bot, 指令, wakepro 主动唤醒都不会触发回复。
- 黑名单上下文注入: 被拉黑用户的消息仍会以 `<blocked_messages>` 临时上下文提供给 LLM, 避免群聊信息割裂。
- 撤回兼容: 对 `group_recall` / `friend_recall` 只清理本插件缓存, 不吞通知, 不影响 `astrbot_plugin_recall_cancel`。
- 管理员命令: 支持 `/拉黑 @用户`, `/拉黑 QQ号`, `/取消拉黑 @用户`, `/黑名单`。

## 兼容性

| 项目 | 状态 | 说明 |
| --- | --- | --- |
| AstrBot | `>=4.24.0,<5.0.0` | 依赖 `extra_user_content_parts.mark_as_temp()` 避免临时上下文写入历史 |
| context_aware | 兼容 | 黑名单上下文由本插件独立维护, 不调用 context_aware 清理接口 |
| wakepro | 兼容 | 本插件黑名单监听优先级为 `100000`, 高于 wakepro 当前 `99999` |
| recall_cancel | 兼容 | LLM hook 优先级为 `90`, 低于 recall_cancel 的 `100`; 撤回 notice 不会被吞 |

## 安装

在 AstrBot WebUI 的插件市场或插件管理页安装本仓库:

```text
https://github.com/muyouzhi6/astrbot_plugin_suanle_bushuo
```

也可以手动安装:

```bash
cd AstrBot/data/plugins
git clone https://github.com/muyouzhi6/astrbot_plugin_suanle_bushuo.git
```

安装后在 WebUI 重载插件或重启 AstrBot。

## 推荐配置

严格沉默建议关闭 AstrBot provider 设置里的两项:

- `streaming_response`: 流式输出已经发出的 token 无法被插件撤回。
- `show_tool_use_status`: 如果开启, AstrBot 可能在执行 `keep_silent` 前发送工具调用状态。

黑名单强阻断不受这两项影响, 但 LLM 自主沉默想做到完全无痕, 就应关闭流式输出和工具调用状态展示。

## 配置项

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `enable` | `true` | 插件总开关 |
| `silence_tool_enable` | `true` | 是否启用 `keep_silent` tool |
| `must_reply_umo` | `[]` | 必须回复的 UMO 会话白名单 |
| `must_reply_uid` | `[]` | 必须回复的用户 ID 白名单 |
| `must_reply_gid` | `[]` | 必须回复的群 ID 白名单 |
| `blacklist_qq` | `[]` | QQ 号黑名单, 面板和命令都会写这里 |
| `blacklist_uid` | `[]` | 通用 sender ID 黑名单, 适合非 QQ 平台 |
| `blacklist_umo` | `[]` | 会话级黑名单, 命中后整个会话普通消息都无法触发回复 |
| `blacklist_group_uid` | `[]` | 群内定向黑名单, 格式 `<UMO>:<UID>`, 也兼容 `<群号>:<UID>` |
| `protected_admins` | `true` | 保护 AstrBot 管理员, 防止误拉黑和误阻断 |
| `blocked_context_window` | `20` | 后续 LLM 请求注入最近多少条黑名单消息 |
| `blocked_context_ttl_seconds` | `86400` | 黑名单上下文缓存保留时间 |
| `respect_recall_notice` | `true` | 收到撤回通知时只清理本插件缓存, 不阻断通知传播 |
| `strict_non_streaming_warning` | `true` | 检测到流式输出或工具状态展示时输出 warning |
| `debug_log` | `false` | 输出调试日志 |

## 获取 UMO

在目标会话发送 AstrBot 内置命令:

```text
/sid
```

复制输出里的 `UMO` 值, 填入 `must_reply_umo` 或 `blacklist_umo`。UMO 是 AstrBot 的统一会话标识, 比单纯群号更适合跨平台区分会话。

## 命令

以下命令仅 AstrBot 管理员可用:

```text
/拉黑 @用户
/拉黑 123456789
/取消拉黑 @用户
/取消拉黑 123456789
/黑名单
```

说明:

- `/拉黑` 会写入 `blacklist_qq` 并持久化配置。
- `/取消拉黑` 会从 `blacklist_qq` 移除目标。
- `/黑名单` 会显示当前 `blacklist_qq`, `blacklist_uid`, `blacklist_umo`, `blacklist_group_uid`。
- 若目标用户在 `must_reply_uid`, 或目标是 AstrBot 管理员且 `protected_admins=true`, 插件会拒绝拉黑。

## 行为边界

- 黑名单用户的消息会被记录为 `<blocked_messages>`, 只作为事实背景, 不作为触发源。
- 黑名单用户撤回消息后, 本插件会按 `UMO + message_id` 删除对应缓存。
- `keep_silent` 依赖模型支持 function-calling/tools-use; 不支持 tool 的模型无法保证自主沉默。
- 如果其他插件在本插件之前直接 `event.send()`, 本插件无法撤回已经发送的平台消息。
- `respect_recall_notice` 不建议关闭, 除非你明确知道自己在破坏撤回兼容链路。

## 测试

本仓库包含独立单元测试, 可在插件目录运行:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile main.py tests/test_suanle_bushuo.py
python3 -m json.tool _conf_schema.json
ruff check main.py tests/test_suanle_bushuo.py
ruff format --check main.py tests/test_suanle_bushuo.py
```

已覆盖:

- 黑名单消息阻断与上下文记录。
- must-reply 白名单覆盖黑名单。
- AstrBot 管理员保护。
- `group_recall` / `friend_recall` 不阻断传播。
- 撤回后清理本插件黑名单缓存。
- `<blocked_messages>` 注入 window 和 TTL。
- `keep_silent` 请求标记, LLMResponse 清空, 发送前结果清空。
- `recall_cancel` 的 `agent_stop_requested` 让路。
- `/拉黑`, `/取消拉黑`, `/黑名单` 相关配置持久化。

## 版本

当前版本: `v0.1.0`

这是首个公开版本, 目标是稳定兼容 AstrBot `4.24.x` 到 `4.x` 系列。

## 发布到 AstrBot 插件市场

本插件代码托管在 GitHub。若需要提交到 AstrBot 插件市场, 在 [AstrBot 插件市场](https://plugins.astrbot.app) 点击右下角 `+`, 填写仓库地址和插件信息后提交即可。
