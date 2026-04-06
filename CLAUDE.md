# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

**nanobot**（PyPI 包名 `nanobot-ai`）是一个极简个人 AI 助手框架——受 OpenClaw 启发的参考实现，用约 1% 的代码量实现了同等核心 Agent 功能。支持通过 CLI、云端消息平台（Telegram、Discord、Slack、WhatsApp、微信、飞书、钉钉、QQ、Matrix、Email、企业微信、Mochat）以及本地 OpenAI 兼容 HTTP API 进行对话。

## 常用命令

### 安装

```bash
pip install -e ".[dev]"
# 推荐使用 uv：
uv sync --all-extras
```

### 测试

```bash
pytest
# 或：
uv run pytest tests/
# 运行单个测试文件：
uv run pytest tests/test_foo.py
# 运行单个测试：
uv run pytest tests/test_foo.py::test_bar
```

CI 在 Python 3.11、3.12、3.13 上运行测试。`pyproject.toml` 中已设置 `asyncio_mode = "auto"`。

### 代码检查 / 格式化

```bash
ruff check nanobot/
ruff format nanobot/
```

Ruff 配置：行长 100，规则 E/F/I/N/W，忽略 E501，目标 py311。

### WhatsApp Bridge（TypeScript/Node）

```bash
cd bridge && npm install && npm run build && npm start
```

## 架构

### 数据流

```
聊天平台
    ↓
BaseChannel 子类  (_handle_message → InboundMessage)
    ↓
MessageBus  (nanobot/bus/queue.py)  — 两个 asyncio.Queue
    ↓
AgentLoop  (nanobot/agent/loop.py)  — 核心编排器
    ↓
AgentRunner  (nanobot/agent/runner.py)  — 纯 LLM↔工具循环
    ↓
LLMProvider  (nanobot/providers/)
    ↓
AgentLoop 发布 OutboundMessage
    ↓
ChannelManager  (nanobot/channels/manager.py)  — 指数退避重试
    ↓
聊天平台
```

### 核心组件

| 组件 | 路径 | 职责 |
|------|------|------|
| `AgentLoop` | `nanobot/agent/loop.py` | 核心编排器：会话查找、命令分发、工具注册、LLM 调用委托、记忆压缩触发、Cron/心跳接入 |
| `AgentRunner` | `nanobot/agent/runner.py` | 纯工具型 LLM 循环（不含业务逻辑）。接受 `AgentRunSpec`，迭代 LLM → 工具调用 → 结果，直到停止条件（默认 max_tool_iterations=200） |
| `AgentHook` / `CompositeHook` | `nanobot/agent/hook.py` | 生命周期回调：`before_iteration`、`on_stream`、`on_stream_end`、`before_execute_tools`、`after_iteration`、`finalize_content` |
| `MessageBus` | `nanobot/bus/queue.py` | 两个 `asyncio.Queue`，将渠道与 Agent 解耦 |
| `BaseChannel` | `nanobot/channels/base.py` | 所有渠道的抽象基类；新渠道集成需继承此类 |
| `ChannelManager` | `nanobot/channels/manager.py` | 通过 pkgutil + entry_points 自动发现渠道，负责启停和出站消息分发 |
| `ToolRegistry` | `nanobot/agent/tools/registry.py` | `Tool` 对象字典；校验并执行工具调用；返回 OpenAI 格式的 schema |
| `CommandRouter` | `nanobot/command/router.py` | 三层分发：优先级（预锁定，如 /stop）→ 精确匹配 → 前缀匹配 → 拦截器 |
| `SessionManager` | `nanobot/session/manager.py` | 每个会话的对话历史 |
| `ContextBuilder` | `nanobot/agent/context.py` | 组装 Agent 系统提示词：合并身份、工作区引导文件（`AGENTS.md`、`SOUL.md`、`USER.md`、`TOOLS.md`）、长期记忆和 Skill |
| `MemoryConsolidator` | `nanobot/agent/memory.py` | 压缩长历史；持久化 `history_entry` 日志和长期 Markdown 记忆 |
| `SkillsLoader` | `nanobot/agent/skills.py` | 从工作区（优先）和内置 `nanobot/skills/` 加载 `SKILL.md` 文件 |
| `CronService` | `nanobot/cron/service.py` | 基于 croniter 的定时任务运行器 |
| `HeartbeatService` | `nanobot/heartbeat/service.py` | 每 30 分钟唤醒一次，检查工作区中的 `HEARTBEAT.md` |
| `SubagentManager` | `nanobot/agent/subagent.py` | 通过 `spawn` 工具启动后台 Agent 任务 |
| `Nanobot`（外观） | `nanobot/nanobot.py` | SDK 入口：`Nanobot.from_config()` → `await bot.run(message)` |
| API 服务器 | `nanobot/api/server.py` | 基于 aiohttp 的 OpenAI 兼容 API（`/v1/chat/completions`、`/v1/models`、`/health`） |
| Provider 注册表 | `nanobot/providers/registry.py` | `ProviderSpec` 数据类组成的 `PROVIDERS` 元组；`find_by_name()` 查找 |
| 配置 Schema | `nanobot/config/schema.py` | Pydantic v2 模型，使用 camelCase 别名；从 `~/.nanobot/config.json` 加载 |
| CLI | `nanobot/cli/commands.py` | Typer 应用；命令：`onboard`、`agent`、`gateway`、`serve`、`status`、`channels`、`provider` |

### 内置工具

在 `AgentLoop` 中注册：`ReadFileTool`、`WriteFileTool`、`EditFileTool`、`ListDirTool`、`ExecTool`（Shell）、`WebSearchTool`、`WebFetchTool`、`CronTool`、`MessageTool`、`SpawnTool`。

### Skills 系统

Skill 是包含 `SKILL.md` 文件的目录，用于扩展 Agent 的系统提示词。内置 Skill 位于 `nanobot/skills/`。用户可将自定义 Skill 添加到 `~/.nanobot/workspace/skills/`，工作区 Skill 优先级高于内置 Skill。

### MCP 集成

MCP 服务器在配置的 `tools.mcpServers` 下定义。启动时自动发现并注册到 `ToolRegistry`，与内置工具并列。

### WhatsApp Bridge

WhatsApp 通过 `bridge/` 中独立的 TypeScript/Node.js 进程处理（使用 `@whiskeysockets/baileys`），Python 侧通过本地 WebSocket 与其通信。

## 配置

配置文件位于 `~/.nanobot/config.json`（JSON，camelCase 键名）。主要配置节：
- `providers.*` — LLM provider API 密钥/地址
- `agents.defaults` — model、workspace、max_tokens、context_window_tokens、max_tool_iterations 等
- `channels.*` — 各渠道凭据和设置
- `tools.*` — 网络搜索、exec、MCP 服务器、工作区沙箱
- `gateway.*` — gateway 地址/端口（默认 18790）
- `api.*` — API 服务器地址/端口（默认 8900）

## 扩展项目

**新增 LLM Provider（2 步）：**
1. 在 `nanobot/providers/registry.py` 的 `PROVIDERS` 中添加 `ProviderSpec`
2. 在 `nanobot/config/schema.py` 的 `ProvidersConfig` 中添加对应 `ProviderConfig` 字段

**新增渠道：** 继承 `nanobot/channels/base.py` 中的 `BaseChannel`，实现 `start()`、`stop()`、`send()`，流式输出可选实现 `send_delta()`。参见 `docs/CHANNEL_PLUGIN_GUIDE.md`。

## 分支策略

- `main` — 稳定版本；Bug 修复和文档的目标分支
- `nightly` — 实验性；新功能、重构、API 变更的目标分支
- 稳定功能约每周从 `nightly` cherry-pick 到 `main`
