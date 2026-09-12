---
name: feishu-agent
description: 全局飞书全能智能体网关（Feishu Agent Gateway）。基于飞书官方 WebSocket 长连接（无需公网 IP / 域名 / 端口映射），将 Antigravity 智能体深度接入飞书客户端。支持私聊与话题群双工对话、多项目工作区自动发现与切换、多轮会话记忆隔离、富文本交互卡片、免 @ 直接交互以及 Systemd 守护进程开机自启。
---

# 飞书全能智能体网关 (Feishu Agent Gateway) 技能指南

## 1. 核心定位与架构

`feishu-agent` 是连接 **飞书客户端（手机 / 桌面端）** 与 **本地 Antigravity 智能体引擎** 的高可用长连接网关。

```mermaid
flowchart LR
    User[你（手机 / 电脑飞书）]
    LarkCloud[飞书消息网关]
    Gateway[feishu-agent 网关服务\n(Python WebSocket)]
    AgyEngine[Antigravity 智能体\n(agy CLI / Agent 内核)]
    Workspace[项目工作区 / Linux 环境]

    User -->|发送指令| LarkCloud
    LarkCloud ==WebSocket长连接 (无需公网IP)==> Gateway
    Gateway -->|多轮上下文调度| AgyEngine
    AgyEngine -->|执行命令 / 读写文件| Workspace
    AgyEngine -->|产出回复| Gateway
    Gateway ==推送交互卡片/文本==> LarkCloud
    LarkCloud --> User
```

### 核心亮点
1. **无需公网 IP / 域名 / 内网穿透**：基于飞书官方 WebSocket 长连接，云开发机、本地虚拟机均可秒级接入。
2. **多项目工作区隔离**：自动识别主目录下项目目录（如 `~/项目A`、`~/项目B`），指令无缝切换工作目录。
3. **话题群（Topic Group）1:1 映射**：
   - 群组 = 项目（Project / Workspace）；
   - 群内话题（Topic）= 会话（Conversation）；
   - 左侧栏话题列表随时点选切换，右侧独立承接上下文。
4. **彻底免 @ 交互**：群内像与真人对话一样直接发文本按回车，机器人秒级响应。
5. **开机与崩溃自愈守护**：通过 Linux Systemd 用户级服务托管，配置 Linger 免登录自启，服务永久在线。

---

## 2. 飞书开放平台前置配置（仅需 3 分钟）

在 [飞书开放平台后台](https://open.feishu.cn/app) 准备或创建企业自建应用：

1. **添加应用能力**：添加 **「机器人」**。
2. **开通权限**（开发配置 -> 权限管理）：
   - `获取单聊消息` (`im:message.p2p_msg:readonly`)
   - `获取群聊中被@的消息` (`im:message.group_at_msg:readonly`)
   - `获取群聊消息` (`im:message.group_msg:readonly`，实现免 @ 必开)
   - `以应用的身份发消息` (`im:message:send_as_bot`)
3. **配置事件订阅**：
   - 进入 **「开发配置 -> 事件与回调」**；
   - 订阅方式选择 **「长连接模式」**；
   - 添加事件：**`接收消息`** (`im.message.receive_v1`)。
4. **发布版本**：
   - 在 **「版本管理与发布」** 中创建并发布版本。

---

## 3. 在新设备上一键部署与安装

在任意 Linux 环境（Debian / Ubuntu / CentOS 等）克隆本仓库并执行：

```bash
git clone git@github.com:shenb9328/feishu-bot-gateway.git ~/.feishu-agent
cd feishu-agent
chmod +x install.sh
./install.sh
```

`install.sh` 脚本将自动完成：
* Python 依赖检查与安装（`lark-oapi`）；
* 凭据检测与配置（自动复用 `~/.config/feishu/config.json` 或交互式生成）；
* 注册 Antigravity 本地全局技能（链接至 `~/.gemini/config/skills/feishu-agent`）；
* 配置 Systemd 用户级开机自启动服务（`feishu-agent.service`）；
* 启动网关并打印实时连接日志。

---

## 4. 指令体系速查

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
| **任意自然语言** | - | 自动调用本地 Antigravity Agent 执行任务、写脚本并回报结果 |

---

## 5. 常用运维与服务管理

网关由 Linux Systemd 用户级守护进程管理：

```bash
# 查看服务运行状态
systemctl --user status feishu-agent

# 查看实时日志
journalctl --user -u feishu-agent -f

# 重启网关服务
systemctl --user restart feishu-agent

# 停止网关服务
systemctl --user stop feishu-agent
```
