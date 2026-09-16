# 🤖 Feishu Agent Gateway (飞书全能智能体网关)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Lark Open Platform](https://img.shields.io/badge/Feishu-Open%20Platform-00D6B9.svg)](https://open.feishu.cn/)
[![Antigravity Compatible](https://img.shields.io/badge/Antigravity-Skill%20Ready-4285F4.svg)](https://antigravity.google)

基于飞书官方 **WebSocket 长连接（无需公网 IP、无需域名、无需内网穿透）**，将本地 **Google Antigravity 编码智能体** 深度接入飞书客户端的高可用网关。

支持在手机或电脑飞书随时下发 Linux 运维命令、编写与调试代码、切换不同项目工作区，并在话题群中享受与 IDE 1:1 对应的原生分栏体验。

---

## 🌟 核心特性

- ⚡ **零公网依赖**：采用官方 WebSocket 长连接模式，任何处于内网、云开发机或私有 VPS 的主机均可秒级接入，彻底告别域名、SSL 证书与反向代理。
- 🤖 **单内核并发多租户 / 多机器人**：支持 1 个 Antigravity 智能体（agy）同时链接 \(n\) 个不同飞书企业/个人账户下的 \(m\) 个机器人，多实例进程级隔离、会话与存储天然物理独立。
- 🔍 **零配置机器人自动探测**：启动时自动调用飞书官方 OpenAPI 获取机器人名称与 Open ID，告别繁杂的硬编码配置。
- 📁 **多项目工作区隔离**：自动扫描主目录与工作区项目（如 `飞书`、`腾讯云` 等），使用 `/project <名>` 即可秒级切换工作目录。
- 🌳 **飞书话题群（Topic Group）1:1 映射**：
  - **群组** = 本地项目（Project / Workspace）
  - **话题（Topic）** = 独立会话（Conversation）
  - 左侧话题列表一目了然，右侧独立承接上下文，互不串台。
- 💬 **彻底免 @ 直接交互**：群内无需每次艾特机器人，像与真人聊天一样直接打字发回车，机器人秒级响应。
- ⚡ **0.1 秒高频应用直通（FastPath）**：针对无需大模型繁琐思考的确定性工具（如 `mingli` 命理排盘），支持关键字直通执行与亚秒级即时响应，并自动缓存盘面供后续大模型深度“解读”。
- 📜 **多轮会话回溯管理**：内置 `/new`（开新会话）、`/history`（历史清单）与 `/switch <序号>`（一秒切回），随时承接不同上下文。
- 🎨 **飞书原生富文本卡片**：状态面板、项目列表、历史会话全以交互卡片形式精致展现。
- 🛡️ **开机与崩溃自愈守护**：通过 Linux Systemd 模板服务托管，配置 Linger 免登录自启，服务永久在线。

---

## 🚀 架构设计

```mermaid
flowchart LR
    subgraph 飞书多租户平台
        Bot1[账户 A: 研发助手]
        Bot2[账户 B: 运维助手]
    end

    subgraph 网关服务集群 [Linux 守护进程]
        GW1[feishu-agent 默认实例]
        GW2[feishu-agent@bot2 实例]
    end

    subgraph 本地核心引擎 [Antigravity Core]
        Agy[统一 Antigravity Agent 内核\n(/usr/local/bin/agy)]
        Workspaces[隔离项目工作区 / Linux 环境]
    end

    Bot1 ==WebSocket 长连接==> GW1
    Bot2 ==WebSocket 长连接==> GW2

    GW1 -->|并发上下文调度| Agy
    GW2 -->|并发上下文调度| Agy

    Agy <--> Workspaces
    Agy -->|执行输出| GW1
    Agy -->|执行输出| GW2
    GW1 ==推送信令/卡片==> Bot1
    GW2 ==推送信令/卡片==> Bot2
```

---

## 🛠️ 飞书开放平台准备（3 分钟）

在 [飞书开放平台开发者后台](https://open.feishu.cn/app) 进入或创建自建应用：

1. **添加机器人能力**：在「添加应用能力」中添加「机器人」。
2. **开通核心权限**（「开发配置 -> 权限管理」）：
   - `获取单聊消息` (`im:message.p2p_msg:readonly`)
   - `获取群聊中被@的消息` (`im:message.group_at_msg:readonly`)
   - `获取群聊消息` (`im:message.group_msg:readonly`，实现免 @ 必开)
   - `以应用的身份发消息` (`im:message:send_as_bot`)
3. **配置事件订阅**：
   - 进入「开发配置 -> 事件与回调」；
   - 订阅方式选择 **「长连接模式」**；
   - 添加事件：**`接收消息 (im.message.receive_v1)`**。
4. **创建并发布版本**：
   - 在「版本管理与发布」中提交并发布一次版本生效。

---

## 📦 新电脑 / 新服务器一键安装

在新机器上克隆本项目，运行自动安装脚本即可：

```bash
git clone git@github.com:shenb9328/feishu-bot-gateway.git ~/.feishu-agent
cd feishu-agent
chmod +x install.sh
./install.sh
```

> **自动完成事项**：
> 1. 安装 `lark-oapi` 依赖；
> 2. 检查或引导生成 `config.json`；
> 3. 注册为当前环境全局 Antigravity Skill；
> 4. 配置 Systemd 开机守护进程并立即启动连接。

---

## 📖 交互指令指南

在飞书单聊或群聊中，直接发送以下指令（支持斜杠或纯汉字）：

| 指令 | 别名 | 作用说明 |
| :--- | :--- | :--- |
| **`/help`** | `帮助`、`/menu`、`/card` | 弹出 **🤖 工作台控制面板** 交互卡片 |
| **`/status`** | `状态`、`/状态` | 查看当前绑定的工作区目录、运行模型、会话 ID 与近期任务记录 |
| **`/model`** | `模型`、`/模型` | 查看或切换当前话题的模型（如 `/model pro`、`/model high`、`/model reset`） |
| **`/projects`**| `项目`、`/项目列表` | 弹出 **📂 项目工作区列表** 卡片，展示所有可用项目 |
| **`/project <名>`** | `切项目` | 切换工作目录至目标项目（如 `/project 飞书`），并重置上下文 |
| **`/new`** | `新建会话`、`/clear` | 开启全新会话，上一会话自动归档至历史记录 |
| **`/history`** | `历史`、`历史会话` | 弹出 **📜 历史会话列表** 卡片，列出近期会话及轮次 |
| **`/switch <序号>`** | `切回`、`/resume` | 一秒切回指定序号的历史会话，继承该会话的全部记忆 |
| **`排盘`** | `起盘`、`paipan` | **⚡ 0.1 秒极速直通**：免大模型开销直接调用 `mingli` 计算并返回完整 Markdown 排盘表（支持自然语言解析城市与时刻，如 `排盘，现在时刻，乌鲁木齐`），并自动缓存盘面 |
| **`解读`** | `分析`、`看盘` | 自动调取上一条排盘结果，注入 Agent 核心进行结合易理的深度专业综合推演（干支天时、奇门格局、梅花体用） |
| **直接发文本** | - | 本地 Agent 自动调用工具执行任务、写代码并回报。默认使用 **Gemini 3.8 Flash (Medium)**，除非特别说明（如“用pro模型...”或“--model sonnet”） |

---

## ⚙️ 运维管理命令

服务由 Linux 用户级 Systemd 托管：

```bash
# 查看默认实例运行状态
systemctl --user status feishu-agent

# 查看实时日志
journalctl --user -u feishu-agent -f

# 重启服务
systemctl --user restart feishu-agent

# 停止服务
systemctl --user stop feishu-agent
```

---

## 🤖 多租户与多机器人多开指南 (1个 agy 驱动多个机器人)

如果你有多个飞书租户（不同企业或个人账号），或者需要在同一台服务器上运行多个不同用途的飞书机器人（如：`研发助手`、`运维助手`、`客服测试`），只需利用内置的 **Systemd 模板服务**，无需重复安装：

### 1. 创建新机器人的配置文件
在项目目录下复制模板（如新机器人代号为 `ops`）：
```bash
cp config.example.json config.ops.json
```
编辑 `config.ops.json` 填入新机器人的 `app_id` 与 `app_secret`：
```json
{
  "app_id": "cli_xxxxxxxxxxxxxxxx",
  "app_secret": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "bot_name": "运维助手",
  "projects_root": "/home/shenb9328_gmail_com",
  "allowed_open_ids": []
}
```

### 2. 一键启动并托管新实例
```bash
# 启动并设置开机自启 (实例名与 config.<实例名>.json 一一对应)
systemctl --user enable --now feishu-agent@ops

# 查看该机器人状态与日志
systemctl --user status feishu-agent@ops
journalctl --user -u feishu-agent@ops -f
```

各机器人的会话数据将自动隔离存入 `sessions.ops.json` 与 `chat_bindings.ops.json`，独立管理，互不干扰！

---

## 📄 开源许可证

本项目基于 [MIT License](LICENSE) 开源。
