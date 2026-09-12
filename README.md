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
- 📁 **多项目工作区隔离**：自动扫描主目录与工作区项目（如 `飞书`、`腾讯云` 等），使用 `/project <名>` 即可秒级切换工作目录。
- 🌳 **飞书话题群（Topic Group）1:1 映射**：
  - **群组** = 本地项目（Project / Workspace）
  - **话题（Topic）** = 独立会话（Conversation）
  - 左侧话题列表一目了然，右侧独立承接上下文，互不串台。
- 💬 **彻底免 @ 直接交互**：群内无需每次艾特机器人，像与真人聊天一样直接打字发回车，机器人秒级响应。
- 📜 **多轮会话回溯管理**：内置 `/new`（开新会话）、`/history`（历史清单）与 `/switch <序号>`（一秒切回），随时承接不同上下文。
- 🎨 **飞书原生富文本卡片**：状态面板、项目列表、历史会话全以交互卡片形式精致展现。
- 🛡️ **开机与崩溃自愈守护**：通过 Linux Systemd 用户级服务托管，配置 Linger 免登录自启，服务永久在线。

---

## 🚀 架构设计

```mermaid
flowchart LR
    subgraph 移动端 / 电脑端
        User[你（飞书客户端）]
    end

    subgraph 飞书开放平台
        LarkCloud[飞书消息网关]
    end

    subgraph 本地 / 服务器运行环境
        Gateway[feishu-agent 网关服务\n(WebSocket 消息中继)]
        Engine[Antigravity Agent 核心\n(agy CLI / 执行引擎)]
        FS[本地工作区 & 工具链\n(Bash / Python / 文件系统)]
    end

    User -->|发送指令| LarkCloud
    LarkCloud ==WebSocket 长连接==> Gateway
    Gateway -->|按会话上下文调度| Engine
    Engine -->|读写文件 / 运行命令| FS
    FS -->|执行结果| Engine
    Engine -->|产出回复| Gateway
    Gateway ==推送卡片/消息==> LarkCloud
    LarkCloud --> User
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
| **`/status`** | `状态`、`/状态` | 查看当前绑定的工作区目录、会话 ID 与近期任务记录 |
| **`/projects`**| `项目`、`/项目列表` | 弹出 **📂 项目工作区列表** 卡片，展示所有可用项目 |
| **`/project <名>`** | `切项目` | 切换工作目录至目标项目（如 `/project 飞书`），并重置上下文 |
| **`/new`** | `新建会话`、`/clear` | 开启全新会话，上一会话自动归档至历史记录 |
| **`/history`** | `历史`、`历史会话` | 弹出 **📜 历史会话列表** 卡片，列出近期会话及轮次 |
| **`/switch <序号>`** | `切回`、`/resume` | 一秒切回指定序号的历史会话，继承该会话的全部记忆 |
| **直接发文本** | - | 本地 Agent 自动调用工具执行代码、查系统负载并回报 |

---

## ⚙️ 运维管理命令

服务由 Linux 用户级 Systemd 托管：

```bash
# 查看运行状态
systemctl --user status feishu-agent

# 查看实时日志
journalctl --user -u feishu-agent -f

# 重启服务
systemctl --user restart feishu-agent

# 停止服务
systemctl --user stop feishu-agent
```

---

## 📄 开源许可证

本项目基于 [MIT License](LICENSE) 开源。
