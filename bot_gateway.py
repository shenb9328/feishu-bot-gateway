import argparse
import os
import re
import sys
import json
import time
import glob
import zipfile
import datetime
import requests
import subprocess
import threading
import shutil
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import deque
from typing import Any, Callable, Dict, List, Optional, Tuple

import lark_oapi as lark
import lark_oapi.api.im.v1 as im_v1

from session_manager import SessionManager
import card_builder

# Command-line & Configuration Parsing
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

parser = argparse.ArgumentParser(description="Feishu Agent Gateway (Antigravity Bridge)")
parser.add_argument("-c", "--config", default=os.getenv("FEISHU_AGENT_CONFIG", "config.json"), help="Path to config JSON file")
args = parser.parse_args()

config_arg = args.config
if not os.path.isabs(config_arg):
    if os.path.exists(config_arg):
        CONFIG_PATH = os.path.abspath(config_arg)
    else:
        CONFIG_PATH = os.path.join(BASE_DIR, config_arg)
else:
    CONFIG_PATH = config_arg

if not os.path.exists(CONFIG_PATH):
    print(f"❌ Error: Config file not found: {CONFIG_PATH}")
    sys.exit(1)

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    config = json.load(f)

# Instance Tag & Dynamic Storage Isolation
config_filename = os.path.basename(CONFIG_PATH)
if config_filename == "config.json":
    tag = "default"
elif config_filename.startswith("config.") and config_filename.endswith(".json"):
    tag = config_filename[7:-5]
elif config_filename.startswith("config_") and config_filename.endswith(".json"):
    tag = config_filename[7:-5]
else:
    tag = os.path.splitext(config_filename)[0]

if "sessions_path" in config:
    SESSIONS_PATH = os.path.expanduser(config["sessions_path"])
elif tag == "default":
    SESSIONS_PATH = os.path.join(BASE_DIR, "sessions.json")
else:
    SESSIONS_PATH = os.path.join(BASE_DIR, f"sessions.{tag}.json")

if "bindings_path" in config:
    BINDINGS_PATH = os.path.expanduser(config["bindings_path"])
elif tag == "default":
    BINDINGS_PATH = os.path.join(BASE_DIR, "chat_bindings.json")
else:
    BINDINGS_PATH = os.path.join(BASE_DIR, f"chat_bindings.{tag}.json")

APP_ID = config["app_id"]
APP_SECRET = config["app_secret"]
DEFAULT_ROOT = os.path.expanduser(config.get("projects_root", "~/.gemini/antigravity-cli/scratch"))
DEFAULT_PROJECT_DIR = os.path.expanduser(config.get("default_project_dir", DEFAULT_ROOT))
DEFAULT_MODEL = config.get("default_model", "gemini-3.8-flash-medium")
ALLOWED_OPEN_IDS = set(config.get("allowed_open_ids", []))
BOT_OPEN_ID = config.get("bot_open_id", "")
BOT_NAME = config.get("bot_name", "")
IS_LEGAL_BOT = (
    config.get("bot_role") == "legal_advisor"
    or tag == "chenjia"
    or "陈佳" in BOT_NAME
)

# Model Aliases & Override Matching
MODEL_ALIASES = {
    # Flash 系列
    "flash": "gemini-3.8-flash-medium",
    "flash-medium": "gemini-3.8-flash-medium",
    "flash-high": "gemini-3.8-flash-high",
    "flash-low": "gemini-3.8-flash-low",
    "flashmedium": "gemini-3.8-flash-medium",
    "flashhigh": "gemini-3.8-flash-high",
    "flashlow": "gemini-3.8-flash-low",
    "gemini-3.8-flash-medium": "gemini-3.8-flash-medium",
    "gemini-3.8-flash-high": "gemini-3.8-flash-high",
    "gemini-3.8-flash-low": "gemini-3.8-flash-low",
    "gemini-3.7-flash-medium": "gemini-3.7-flash-medium",
    "gemini-3.7-flash-high": "gemini-3.7-flash-high",
    "gemini-3.7-flash-low": "gemini-3.7-flash-low",

    # 思考档次别名
    "medium": "gemini-3.8-flash-medium",
    "中档": "gemini-3.8-flash-medium",
    "high": "gemini-3.8-flash-high",
    "高档": "gemini-3.8-flash-high",
    "深度思考": "gemini-3.8-flash-high",
    "low": "gemini-3.8-flash-low",
    "低档": "gemini-3.8-flash-low",
    "极速": "gemini-3.8-flash-low",

    # Pro 旗舰系列
    "pro": "gemini-3.1-pro-high",
    "pro-high": "gemini-3.1-pro-high",
    "pro-low": "gemini-3.1-pro-low",
    "gemini-3.1-pro-high": "gemini-3.1-pro-high",
    "gemini-3.1-pro-low": "gemini-3.1-pro-low",

    # Claude 系列
    "claude": "claude-sonnet-4-6",
    "sonnet": "claude-sonnet-4-6",
    "claude-sonnet-4-6": "claude-sonnet-4-6",
    "opus": "claude-opus-4-6-thinking",
    "claude-opus-4-6-thinking": "claude-opus-4-6-thinking",

    # 开源 / 其他
    "gpt": "gpt-oss-120b-medium",
    "gpt-oss-120b-medium": "gpt-oss-120b-medium",
}


def extract_model_override(prompt: str):
    """Extracts explicit or natural language model override from prompt. Returns (model_id or None, cleaned_prompt)."""
    flag_match = re.search(r'(?:--model|-m)\s+([a-zA-Z0-9_.-]+)', prompt)
    if flag_match:
        m_str = flag_match.group(1).lower()
        if m_str in MODEL_ALIASES:
            cleaned = re.sub(r'(?:--model|-m)\s+([a-zA-Z0-9_.-]+)', '', prompt).strip()
            return MODEL_ALIASES[m_str], cleaned

    nl_match = re.match(r'^(?:请?用|使用|换用|采用|调用)\s*([a-zA-Z0-9_\u4e00-\u9fa5\.-]+?)\s*(?:模型|回答|执行|推演|思考)?\s*[:：,，\s]\s*(.*)$', prompt, re.DOTALL)
    if nl_match:
        cand = nl_match.group(1).strip().lower()
        rest = nl_match.group(2).strip()
        if cand in MODEL_ALIASES:
            return MODEL_ALIASES[cand], rest
        for alias, target in MODEL_ALIASES.items():
            if alias in cand:
                return target, rest

    return None, prompt


session_mgr = SessionManager(SESSIONS_PATH, DEFAULT_ROOT, BINDINGS_PATH)
executor = ThreadPoolExecutor(max_workers=6)

# Deduplication cache & Per-session task execution locks (does NOT block file uploads)
processed_msg_ids = set()
msg_id_queue = deque(maxlen=500)
lock = threading.Lock()
task_locks: Dict[str, threading.Lock] = {}
task_locks_guard = threading.Lock()


def get_task_lock(session_key: str) -> threading.Lock:
    with task_locks_guard:
        if session_key not in task_locks:
            task_locks[session_key] = threading.Lock()
        return task_locks[session_key]


# Feishu API Client & REST Helper
client = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).log_level(lark.LogLevel.INFO).build()


class FeishuAPI:
    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self.token = ""
        self.expire_at = 0

    def get_token(self) -> str:
        if self.token and time.time() < self.expire_at:
            return self.token
        try:
            res = requests.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": self.app_id, "app_secret": self.app_secret},
                timeout=5
            ).json()
            self.token = res.get("tenant_access_token", "")
            self.expire_at = time.time() + res.get("expire", 7200) - 300
        except Exception as e:
            print(f"[FeishuAPI] get_token failed: {e}")
        return self.token

    def list_chats(self) -> list:
        token = self.get_token()
        if not token:
            return []
        try:
            res = requests.get(
                "https://open.feishu.cn/open-apis/im/v1/chats?page_size=50",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5
            ).json()
            return res.get("data", {}).get("items", []) or []
        except Exception as e:
            print(f"[FeishuAPI] list_chats failed: {e}")
            return []

    def get_chat(self, chat_id: str) -> dict:
        token = self.get_token()
        if not token:
            return {}
        try:
            res = requests.get(
                f"https://open.feishu.cn/open-apis/im/v1/chats/{chat_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5
            ).json()
            return res.get("data", {}) or {}
        except Exception as e:
            print(f"[FeishuAPI] get_chat({chat_id}) failed: {e}")
            return {}

    def get_bot_info(self) -> dict:
        token = self.get_token()
        if not token:
            return {}
        try:
            res = requests.get(
                "https://open.feishu.cn/open-apis/bot/v3/info",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5
            ).json()
            if res.get("code") == 0:
                return res.get("bot", {})
        except Exception as e:
            print(f"[FeishuAPI] get_bot_info failed: {e}")
        return {}

    def get_message(self, message_id: str) -> dict:
        """Fetches full message details via OpenAPI (used when post message event omits files array)."""
        token = self.get_token()
        if not token:
            return {}
        try:
            res = requests.get(
                f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10
            ).json()
            items = res.get("data", {}).get("items", [])
            if items:
                return items[0]
        except Exception as e:
            print(f"[FeishuAPI] get_message({message_id}) failed: {e}")
        return {}

    def download_resource(self, message_id: str, file_key: str, resource_type: str = "file") -> bytes:
        """Downloads file or image attachment binary stream via Feishu OpenAPI."""
        token = self.get_token()
        if not token:
            return b""
        try:
            url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/resources/{file_key}?type={resource_type}"
            res = requests.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=180,
                stream=True
            )
            if res.status_code == 200:
                return res.content
            else:
                print(f"[FeishuAPI] download_resource failed: status={res.status_code}, text={res.text[:200]}")
        except Exception as e:
            print(f"[FeishuAPI] download_resource exception: {e}")
        return b""

    def download_resource_to_file(
        self,
        message_id: str,
        file_key: str,
        dest_path: str,
        resource_type: str = "file",
        progress_cb: Optional[Callable[[int, int], None]] = None
    ) -> bool:
        """Downloads file/image attachment directly to dest_path via streaming with progress callback."""
        token = self.get_token()
        if not token:
            return False
        try:
            url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/resources/{file_key}?type={resource_type}"
            res = requests.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=180,
                stream=True
            )
            if res.status_code != 200:
                print(f"[FeishuAPI] download_resource_to_file failed: status={res.status_code}, text={res.text[:200]}")
                return False

            total_bytes = int(res.headers.get("Content-Length") or 0)
            downloaded_bytes = 0
            chunk_size = 512 * 1024  # 512 KB

            with open(dest_path, "wb") as f:
                for chunk in res.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
                        downloaded_bytes += len(chunk)
                        if progress_cb:
                            try:
                                progress_cb(downloaded_bytes, total_bytes)
                            except Exception as e:
                                print(f"[FeishuAPI] progress_cb error: {e}")

            return True
        except Exception as e:
            print(f"[FeishuAPI] download_resource_to_file exception: {e}")
            return False


feishu_api = FeishuAPI(APP_ID, APP_SECRET)

# Auto-detect BOT_OPEN_ID and BOT_NAME if not explicitly configured
if not BOT_OPEN_ID:
    _bot_info = feishu_api.get_bot_info()
    if _bot_info:
        BOT_OPEN_ID = _bot_info.get("open_id", "")
        if not BOT_NAME:
            BOT_NAME = _bot_info.get("app_name", "")

# ----------------- Chat / Channel Metadata Cache -----------------
chat_cache: Dict[str, Dict[str, Any]] = {}


def get_chat_info(chat_id: str) -> dict:
    """Fetches and caches chat metadata (name, mode) from Feishu."""
    if chat_id in chat_cache:
        return chat_cache[chat_id]
    data = feishu_api.get_chat(chat_id)
    info = {
        "name": data.get("name", ""),
        "chat_mode": data.get("chat_mode", "group"),
        "description": data.get("description", "")
    }
    chat_cache[chat_id] = info
    return info


def sync_all_chats():
    """Preloads and binds all channels/chats the bot belongs to."""
    items = feishu_api.list_chats()
    if items:
        print(f"[Feishu] 成功同步并检查 {len(items)} 个频道/群聊:")
        for item in items:
            cid = item.get("chat_id")
            cname = item.get("name", "")
            cmode = item.get("chat_mode", "group")
            chat_cache[cid] = {
                "name": cname,
                "chat_mode": cmode,
                "description": item.get("description", "")
            }
            binding = session_mgr.bind_chat(cid, chat_name=cname, chat_mode=cmode)
            print(f"  • 🏢 频道【{cname}】 ➔ 📁 项目【{binding['project_name']}】({binding['project_dir']})")


# ----------------- Message Sending -----------------
def reply_message(message_id: str, content: Any, msg_type: str = "text") -> Optional[str]:
    """Unified message replier for text and cards. Returns the message_id of the reply."""
    try:
        if msg_type == "interactive":
            body = im_v1.ReplyMessageRequestBody.builder().content(json.dumps(content)).msg_type("interactive").build()
            resp = client.im.v1.message.reply(im_v1.ReplyMessageRequest.builder().message_id(message_id).request_body(body).build())
            if not resp.success():
                print(f"[Feishu] Card reply error: {resp.code} {resp.msg}, fallback to text")
                return reply_message(message_id, "⚠️ 卡片渲染失败，请直接使用纯文本指令。")
            return resp.data.message_id if resp.data else None
        else:
            text = str(content)
            chunks = [text[i:i+3800] for i in range(0, len(text), 3800)] or ["(空输出)"]
            last_id = None
            for chunk in chunks:
                body = im_v1.ReplyMessageRequestBody.builder().content(json.dumps({"text": chunk})).msg_type("text").build()
                resp = client.im.v1.message.reply(im_v1.ReplyMessageRequest.builder().message_id(message_id).request_body(body).build())
                if not resp.success():
                    print(f"[Feishu] reply text error: {resp.code} {resp.msg}")
                elif resp.data:
                    last_id = resp.data.message_id
            return last_id
    except Exception as e:
        print(f"[Feishu] reply_message failed: {e}")
        return None


def patch_message_card(message_id: str, card_dict: dict) -> bool:
    """Updates an existing interactive card message in Feishu in real time."""
    if not message_id:
        return False
    try:
        token = feishu_api.get_token()
        if not token:
            return False
        url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
        body = {"content": json.dumps(card_dict)}
        res = requests.patch(url, headers=headers, json=body, timeout=10)
        data = res.json()
        if data.get("code") == 0:
            return True
        print(f"[Feishu] patch_message_card code={data.get('code')}: {data.get('msg')}")
    except Exception as e:
        print(f"[Feishu] patch_message_card exception: {e}")
    return False


# ----------------- Command Handlers -----------------
def cmd_help(session_key: str, message_id: str, parts: list, parent_chat_id: str = None):
    sess = session_mgr.get_session(session_key, parent_chat_id)
    conv_title = f"`{sess['conversation_id'][:8]}...`" if sess.get("conversation_id") else "新话题（等待首条指令）"
    card = card_builder.build_menu_card(
        project_name=sess["project_name"],
        project_dir=sess["project_dir"],
        conv_title=conv_title,
        channel_name=sess.get("channel_name")
    )
    reply_message(message_id, card, "interactive")


def cmd_status(session_key: str, message_id: str, parts: list, parent_chat_id: str = None):
    sess = session_mgr.get_session(session_key, parent_chat_id)
    cur_model = sess.get("model") or DEFAULT_MODEL
    recent = "".join([f"\n  • [{h['time']}] {h['prompt']}" for h in sess.get("history", [])[-3:]]) or " 暂无"
    channel_line = f"• 所属频道: 🏢 {sess.get('channel_name')}\n" if sess.get('channel_name') else ""
    topic_line = f"• 话题会话: 💬 {sess.get('topic_title')}\n" if sess.get('topic_title') else ""
    pending = sess.get("pending_files", [])
    files_line = f"• 本案在案材料: 📋 已归集 {len(pending)} 份文件\n" if pending else "• 本案在案材料: 暂无\n"
    case_dir_line = f"• 案件隔离目录: `{sess.get('case_dir')}`\n" if IS_LEGAL_BOT and sess.get("case_dir") else ""

    msg = (
        f"📊 【工作台状态】\n"
        f"{channel_line}"
        f"• 绑定项目: 📁 {sess['project_name']}\n"
        f"• 工作目录: `{sess['project_dir']}`\n"
        f"{case_dir_line}"
        f"• 运行模型: ⚡ `{cur_model}`\n"
        f"{topic_line}"
        f"{files_line}"
        f"• Agent 会话 ID: `{sess.get('conversation_id') or '🆕 新会话'}`\n"
        f"• 对话轮次: {len(sess.get('history', []))} 轮\n"
        f"• 最近任务记录:{recent}"
    )
    reply_message(message_id, msg)


def cmd_model(session_key: str, message_id: str, parts: list, parent_chat_id: str = None):
    sess = session_mgr.get_session(session_key, parent_chat_id)
    cur_model = sess.get("model") or DEFAULT_MODEL

    if len(parts) < 2 or parts[1].lower() in ["list", "show", "status", "ls"]:
        msg = (
            f"🤖 【模型配置状态】\n"
            f"• 当前生效模型: ⚡ `{cur_model}`\n"
            f"• 全局默认模型: `{DEFAULT_MODEL}` (Flash Medium)\n\n"
            f"📌 【支持的快捷切换】\n"
            f"1. 切换本话题模型：\n"
            f"   • `/model pro` ➔ Gemini 3.1 Pro (High)\n"
            f"   • `/model high` ➔ Gemini 3.8 Flash (High 深度思考)\n"
            f"   • `/model sonnet` ➔ Claude Sonnet 4.6\n"
            f"   • `/model opus` ➔ Claude Opus 4.6 (Thinking)\n"
            f"   • `/model reset` ➔ 恢复全局默认 (Flash Medium)\n\n"
            f"2. 单次提问临时指定（无需切换）：\n"
            f"   • “用pro模型 帮我优化这个算法”\n"
            f"   • “--model sonnet 请设计该系统架构”"
        )
        reply_message(message_id, msg)
        return

    arg = parts[1].lower().strip()
    if arg in ["reset", "default", "默认", "恢复"]:
        session_mgr.set_model(session_key, None)
        reply_message(message_id, f"✅ 已恢复为全局默认模型：`{DEFAULT_MODEL}`")
        return

    matched_model = MODEL_ALIASES.get(arg)
    if not matched_model:
        for k, v in MODEL_ALIASES.items():
            if k in arg:
                matched_model = v
                break

    if matched_model:
        session_mgr.set_model(session_key, matched_model)
        reply_message(message_id, f"✅ 成功将当前话题模型切换为：`{matched_model}`！\n后续在此话题下的对话将默认使用该模型。")
    else:
        reply_message(message_id, f"❌ 未知模型「{parts[1]}」。可用选项：`flash-medium`、`flash-high`、`pro`、`sonnet`、`opus`、`reset`。")


def cmd_projects(session_key: str, message_id: str, parts: list, parent_chat_id: str = None):
    projs = session_mgr.list_projects()
    sess = session_mgr.get_session(session_key, parent_chat_id)
    card = card_builder.build_projects_card(
        current_project=sess["project_name"],
        projects=projs,
        channel_name=sess.get("channel_name")
    )
    reply_message(message_id, card, "interactive")


def cmd_project(session_key: str, message_id: str, parts: list, parent_chat_id: str = None):
    if len(parts) < 2:
        reply_message(message_id, "⚠️ 请指定项目名，例如: `/project 飞书`")
        return
    proj_name = parts[1]
    sess = session_mgr.set_project(session_key, proj_name, parent_chat_id)
    if parent_chat_id:
        ch_name = sess.get("channel_name") or "当前频道"
        reply_message(message_id, f"✅ 已成功将频道【🏢 {ch_name}】绑定至项目【📁 {sess['project_name']}】！\n📂 本地工作目录: `{sess['project_dir']}`\n💡 本频道后续新建的所有话题均将在此项目目录下执行。")
    else:
        reply_message(message_id, f"✅ 已成功切换到项目【📁 {sess['project_name']}】！\n📂 工作区目录: `{sess['project_dir']}`\n（上一会话已归档，已开启新上下文）")


def cmd_history(session_key: str, message_id: str, parts: list, parent_chat_id: str = None):
    histories = session_mgr.get_history_list(session_key, limit=8)
    sess = session_mgr.get_session(session_key, parent_chat_id)
    card = card_builder.build_history_card(sess.get("conversation_id"), histories)
    reply_message(message_id, card, "interactive")


def cmd_switch(session_key: str, message_id: str, parts: list, parent_chat_id: str = None):
    if len(parts) < 2:
        reply_message(message_id, "⚠️ 请指定要切换的历史会话序号，例如: `/switch 1`")
        return
    target_conv = session_mgr.switch_to_conversation(session_key, parts[1])
    if target_conv:
        reply_message(message_id, f"✅ 成功切回历史会话！\n• 主题: 💬 {target_conv['title']}\n• 会话 ID: `{target_conv['id']}`\n接下来的对话将继承该会话的全部历史记忆！")
    else:
        reply_message(message_id, f"❌ 未找到序号或 ID 为「{parts[1]}」的历史会话。请输入 `/history` 查看可用列表。")


def cmd_new(session_key: str, message_id: str, parts: list, parent_chat_id: str = None):
    sess = session_mgr.reset_conversation(session_key, status="idle")
    reply_message(message_id, f"🔄 已在当前话题内开启全新会话！\n当前项目仍为【📁 {sess['project_name']}】，上一会话已自动归档至 `/history`。在案材料清单与隔离目录已重置。")


def cmd_materials(session_key: str, message_id: str, parts: list, parent_chat_id: str = None):
    sess = session_mgr.get_session(session_key, parent_chat_id)
    pending = sess.get("pending_files", [])
    if not pending:
        reply_message(message_id, "📋 当前案件暂未上传任何在案材料文件。\n您可以直接上传 Word (.docx)、PDF（支持多选批量发送）、压缩包 (.rar / .zip / .7z) 或证据图片，或者直接输入案情进行咨询。")
    else:
        file_list_str = "\n".join([f"{i+1}. 《{item['name']}》 ({item.get('size_kb', 0)} KB)" for i, item in enumerate(pending)])
        reply_message(
            message_id,
            f"📋【当前本案已归集材料清单】（共 {len(pending)} 份）：\n{file_list_str}\n\n"
            f"✅ 材料已全部就绪并锁定在本案独立隔离空间。请点击快捷按钮【分析】、【意见书】、【答辩词】、【代理词】，或直接发送您的具体办案指令（如“给被上诉人XXX公司出具答辩状”）。\n"
            f"💡 若需办理其他新案件，请先发送“新案件”以彻底隔离。"
        )


def cmd_reset_case(session_key: str, message_id: str, parts: list, parent_chat_id: str = None):
    session_mgr.reset_conversation(session_key, status="idle")
    reply_message(
        message_id,
        "⚖️【甘肃乾祐律师事务所 · 新案件已开启】\n\n"
        "上一案件的全部在案材料、工作目录与会话记忆已彻底清空隔离，新案件归集已从零开始。\n\n"
        "📎 请直接上传新案材料（支持 Word .docx、PDF 多文件批量发送、.rar / .zip / .7z 压缩包、证据图片），或直接输入案情诉求进行研判。"
    )


NEW_CASE_TRIGGERS = {
    "新案件", "新案", "新案开始", "案件开始", "重置案件",
    "开始新案件", "开始新案", "开启新案件", "开启新案", "新建案件", "新建案",
    "重新开始", "清空材料", "清空案件", "重置", "换个案件", "重新开始案件",
    "/newcase", "newcase"
}

ARCHIVE_TRIGGERS = {
    "结束归档", "案件结束", "结案归档", "归档", "结案", "结束案件", "案件归档", "结案归档确认"
}

UPLOAD_DONE_TRIGGERS = {
    "文件传输结束", "传输结束", "上传完毕", "材料上传完毕", "文件上传完毕",
    "发完了", "传完了", "材料发完了", "文件发完了", "上传结束", "发送完毕"
}

# Command Dispatch Map
COMMANDS = {
    "/help": cmd_help, "help": cmd_help, "帮助": cmd_help, "/menu": cmd_help, "menu": cmd_help, "/card": cmd_help,
    "/status": cmd_status, "status": cmd_status, "状态": cmd_status, "/状态": cmd_status,
    "/model": cmd_model, "model": cmd_model, "/模型": cmd_model, "模型": cmd_model,
    "/projects": cmd_projects, "projects": cmd_projects, "/项目": cmd_projects, "项目": cmd_projects,
    "/project": cmd_project, "project": cmd_project, "切项目": cmd_project,
    "/history": cmd_history, "history": cmd_history, "/历史": cmd_history, "历史": cmd_history,
    "/switch": cmd_switch, "switch": cmd_switch, "/resume": cmd_switch, "resume": cmd_switch, "切回": cmd_switch,
    "/new": cmd_new, "new": cmd_new, "/clear": cmd_new, "clear": cmd_new, "新建会话": cmd_new,
    "/materials": cmd_materials, "materials": cmd_materials, "材料清单": cmd_materials, "材料": cmd_materials, "查看材料清单": cmd_materials, "当前材料": cmd_materials, "待办材料": cmd_materials,
    "/newcase": cmd_reset_case, "newcase": cmd_reset_case, "新案开始": cmd_reset_case, "新案件": cmd_reset_case, "案件开始": cmd_reset_case, "清空材料": cmd_reset_case, "重置案件": cmd_reset_case, "新案": cmd_reset_case,
    "开始新案件": cmd_reset_case, "开始新案": cmd_reset_case, "开启新案件": cmd_reset_case, "开启新案": cmd_reset_case, "新建案件": cmd_reset_case, "清空案件": cmd_reset_case,
}

# ----------------- FastPath Paipan Execution -----------------
PAIPAN_CACHE_FILE = os.path.join(BASE_DIR, "latest_paipan.json")


def load_paipan_cache():
    if os.path.exists(PAIPAN_CACHE_FILE):
        try:
            with open(PAIPAN_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_paipan_cache(cache):
    try:
        with open(PAIPAN_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


INTERPRET_KEYWORDS = [
    "解读", "分析", "看下", "看一下", "帮看", "断卦", "占断", "测算",
    "运势", "财运", "事业", "婚姻", "健康", "能不能", "如何", "怎样", "好不好", "吉凶"
]

COMPLEX_TIME_WORDS = [
    "明天", "后天", "大后天", "昨天", "前天", "下周", "这周", "本周", "上周",
    "农历", "阴历", "中秋", "端午", "春节", "除夕", "清明", "重阳", "元宵", "冬至", "夏至",
    "子时", "丑时", "寅时", "卯时", "辰时", "巳时", "午时", "未时", "申时", "酉时", "戌时", "亥时",
    "半夜", "上午", "下午", "中午", "傍晚", "晚上", "早晨", "早上", "小时后", "天后"
]


def should_also_interpret(clean_text: str) -> bool:
    return any(k in clean_text for k in INTERPRET_KEYWORDS)


def needs_ai_parsing(clean_text: str) -> bool:
    """判断是否包含相对时间、节气或口语化历法，需要借助 AI 智能换算"""
    return any(w in clean_text for w in COMPLEX_TIME_WORDS)


PAIPAN_KEYWORDS = ["排盘", "起盘", "起卦", "算卦", "测卦", "排八字", "paipan"]


def is_paipan_command(text: str) -> bool:
    clean = text.strip().lower()
    return any(k in clean for k in PAIPAN_KEYWORDS)


def ai_parse_paipan_args(clean_text: str):
    """通过本地最快模型 gemini-3-flash 秒级提取结构化排盘参数及经度"""
    now_bj = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "model": "gemini-3-flash",
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是一个极速命理排盘参数抽取器。请根据用户输入提取以下字段：\n"
                    "1. city: 测算城市或区县名称（若用户未提及，填\"杭州\"；若有别名如帝都请转为标准城市如北京）\n"
                    "2. longitude: 该地点在中国境内的东经经度浮点数（例如高台县为99.82，乌鲁木齐为87.68，杭州为120.16；若未提及或不确定填空或120.16）\n"
                    "3. time: 公历标准时间字符串（格式必须为 YYYY-MM-DD HH:MM:SS；若为相对时间如明天、下周或农历节气，必须根据当前基准时间准确推算；若未提及时间，填空字符串\"\"）\n"
                    "4. need_interpret: 布尔值，用户是否表达了解读、分析、看盘、占断、算运势等诉求\n"
                    "5. question: 用户的具体占测问题（如财运、合作、婚姻等，无则填空字符串\"\"）\n"
                    f"当前基准北京时间为: {now_bj}。\n"
                    "请直接输出严格的纯 JSON 格式，禁止任何额外解释。"
                )
            },
            {"role": "user", "content": clean_text}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0
    }
    try:
        resp = requests.post("http://127.0.0.1:8877/v1/chat/completions", json=payload, timeout=4)
        if resp.status_code == 200:
            raw_content = resp.json()["choices"][0]["message"]["content"].strip()
            if raw_content.startswith("```"):
                raw_content = re.sub(r"^```(?:json)?\s*", "", raw_content)
                raw_content = re.sub(r"\s*```$", "", raw_content)
            parsed = json.loads(raw_content)
            city = parsed.get("city", "").strip() or "杭州"
            time_str = parsed.get("time", "").strip()
            need_interpret = bool(parsed.get("need_interpret", False))
            question = parsed.get("question", "").strip()
            longitude = parsed.get("longitude")
            try:
                longitude = float(longitude) if longitude else None
            except (ValueError, TypeError):
                longitude = None
            return city, time_str, need_interpret, question, longitude
    except Exception as e:
        print(f"[AI Parse Error] {e}, fallback to regex rules")
    return None


def parse_paipan_args(clean_text: str):
    text = re.sub(r'[，,\s]*(排盘|起盘|算卦|起卦|测卦)[，,\s]*', ' ', clean_text).strip()
    text = re.sub(r'(现在时刻|当前时刻|现在|当前)[，,\s]*', ' ', text).strip()
    clean_city_text = re.sub(r'(并且|并|顺便|帮我|请|进行|给|做|来个)?[，,\s]*(解读|分析|看下|看一下|断卦|占断|测算|运势|财运|事业|婚姻|健康|如何|怎样|吉凶).*$', '', text).strip()
    tokens = [t.strip() for t in re.split(r'[，,\s]+', clean_city_text) if t.strip()]
    city = '杭州'
    time_str = ''

    for tok in tokens:
        clean_tok = re.sub(r'^(时间|时刻|日期|公历|阳历)[:：\s]*', '', tok).strip()
        if re.search(r'\d{4}', clean_tok) or re.search(r'\d{1,2}[点时:]\d{1,2}', clean_tok):
            time_str = (time_str + ' ' + clean_tok).strip()
        elif tok and tok not in ["并且", "并", "顺便", "解读", "分析", "和", "跟", "时间", "时刻", "日期"]:
            city = tok

    cmd = ["/home/shenb/.gemini/antigravity-cli/bin/mingli", "-f", "markdown", "-c", city]
    if time_str:
        cmd.append(time_str)
    return city, time_str, cmd


def cmd_paipan(session_key: str, message_id: str, clean_text: str, parent_chat_id: str = None):
    """双轨极速排盘直通通道（日常0.08s直通 + 相对时间/区县AI智能换算）"""
    city = '杭州'
    time_str = ''
    longitude = None
    need_interpret = should_also_interpret(clean_text)
    question = ''

    if needs_ai_parsing(clean_text):
        print(f"[Paipan Route] Detected relative/complex time expression, using AI parser: {clean_text}")
        ai_res = ai_parse_paipan_args(clean_text)
        if ai_res:
            city, time_str, ai_need_interp, question, longitude = ai_res
            need_interpret = need_interpret or ai_need_interp
        else:
            city, time_str, _ = parse_paipan_args(clean_text)
    else:
        city, time_str, _ = parse_paipan_args(clean_text)

    cmd = ["/home/shenb/.gemini/antigravity-cli/bin/mingli", "-f", "markdown", "-c", city]
    if longitude:
        cmd.extend(["--lon", str(longitude)])
    if time_str:
        cmd.append(time_str)

    print(f"[FastPath Paipan] Executing mingli: city={city}, lon={longitude}, time={time_str}")
    start_t = time.time()
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        elapsed = round(time.time() - start_t, 2)
        if res.returncode == 0 and res.stdout.strip():
            output = res.stdout.strip()
            cache = load_paipan_cache()
            cache[session_key] = output
            if parent_chat_id:
                cache[parent_chat_id] = output
            cache["_latest_"] = output
            save_paipan_cache(cache)
            reply_message(message_id, output)
            print(f"[FastPath Paipan] Done in {elapsed}s")

            if need_interpret:
                interpret_prompt = clean_text
                if question and question not in interpret_prompt:
                    interpret_prompt += f"（重点分析：{question}）"
                print(f"[FastPath Paipan] Auto-triggering interpretation for: {interpret_prompt}")
                executor.submit(execute_agent_task, session_key, message_id, interpret_prompt, parent_chat_id)
        else:
            err = res.stderr.strip() or "未知错误"
            reply_message(message_id, f"❌ 排盘异常: {err}")
    except Exception as e:
        reply_message(message_id, f"⚠️ 排盘运行出错: {str(e)}")


# ----------------- File Handling, Post Message Parsing & Fast Text Pre-Extraction -----------------
def extract_docx_text_builtin(file_path: str) -> str:
    """Zero-dependency .docx text extractor using standard library zipfile + ElementTree."""
    try:
        import docx
        doc = docx.Document(file_path)
        lines = []
        for p in doc.paragraphs:
            t = p.text.strip()
            if t:
                lines.append(t)
        for tbl in doc.tables:
            for row in tbl.rows:
                row_txt = [cell.text.strip().replace('\n', ' ') for cell in row.cells if cell.text.strip()]
                if row_txt:
                    lines.append(" | ".join(row_txt))
        return "\n".join(lines)
    except Exception:
        pass

    # Standard-library XML fallback when python-docx is not installed
    lines = []
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            xml_names = [n for n in zf.namelist() if n == "word/document.xml" or n.startswith("word/header") or n.startswith("word/footer")]
            for xml_name in sorted(xml_names):
                xml_bytes = zf.read(xml_name)
                root = ET.fromstring(xml_bytes)
                for elem in root.iter():
                    tag_local = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                    if tag_local == "p":
                        p_texts = []
                        for child in elem.iter():
                            ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                            if ctag == "t" and child.text:
                                p_texts.append(child.text)
                            elif ctag == "tab":
                                p_texts.append("\t")
                            elif ctag in ("br", "cr"):
                                p_texts.append("\n")
                        para_str = "".join(p_texts).strip()
                        if para_str:
                            lines.append(para_str)
    except Exception as e:
        print(f"[DocxExtract] XML fallback failed for {file_path}: {e}")
    return "\n".join(lines)


TESSERACT_BIN = shutil.which("tesseract") or ("/usr/bin/tesseract" if os.path.exists("/usr/bin/tesseract") else None)


def ocr_single_image(img_path: str) -> str:
    """Performs fast OCR on a single scanned page image using tesseract chi_sim."""
    if not TESSERACT_BIN or not os.path.exists(img_path):
        return ""
    try:
        res = subprocess.run(
            [TESSERACT_BIN, img_path, "stdout", "-l", "chi_sim", "--oem", "1"],
            capture_output=True,
            text=True,
            timeout=25,
            check=False
        )
        return res.stdout.strip()
    except Exception as e:
        print(f"[OCR Error] {img_path}: {e}")
        return ""


def ocr_images_batch(
    image_paths: List[str],
    status_cb: Optional[Callable[[str, str], None]] = None,
    doc_name: str = "",
    max_workers: int = 2
) -> List[str]:
    """Runs parallel OCR across images and reports progress via status_cb."""
    total = len(image_paths)
    if not total or not TESSERACT_BIN:
        return []

    results = [""] * total
    completed = 0
    prefix = f"《{doc_name}》" if doc_name else "扫描件案卷"

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {executor.submit(ocr_single_image, path): i for i, path in enumerate(image_paths)}
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception:
                results[idx] = ""
            completed += 1
            if status_cb and (completed % 2 == 0 or completed == total):
                status_cb("rendering", f"{prefix}正在进行智能高精图文识别 (第 {completed}/{total} 页)...")

    return results


def render_pdf_to_images(pdf_path: str, max_pages: int = 100) -> List[str]:
    """Renders a scanned PDF into optimized JPEG images inside the current case directory so the LLM can read them directly."""
    base_dir = os.path.dirname(pdf_path)
    stem = re.sub(r'[^a-zA-Z0-9_\u4e00-\u9fa5-]', '_', os.path.splitext(os.path.basename(pdf_path))[0])
    out_dir = os.path.join(base_dir, f"pages_{stem}")
    os.makedirs(out_dir, exist_ok=True)
    prefix = os.path.join(out_dir, "page")
    try:
        subprocess.run(
            ["/usr/bin/pdftoppm", "-jpeg", "-r", "120", "-l", str(max_pages), pdf_path, prefix],
            timeout=120,
            check=False
        )
        pages = sorted(glob.glob(os.path.join(out_dir, "page*.jpg")))
        return pages
    except Exception as e:
        print(f"[PDFRender] failed for {pdf_path}: {e}")
        return []


def extract_text_from_file(file_path: str, status_cb: Optional[Callable[[str, str], None]] = None) -> Tuple[str, List[str], List[str]]:
    """
    Fast pre-extraction of text & scanned pages from .docx, .pdf, archives (.zip/.7z/.rar/...), or .txt files.
    Returns (extracted_text, rendered_page_paths, warnings_list).
    """
    ext = os.path.splitext(file_path)[1].lower()
    txt_path = f"{file_path}.txt"
    rendered_pages: List[str] = []
    warnings: List[str] = []

    try:
        if ext == ".lnk":
            warnings.append(f"⚠️ 《{os.path.basename(file_path)}》是 Windows 桌面快捷方式 (.lnk)，不含实际文档内容，请直接上传原文件！")
            return "", [], warnings

        if ext == ".docx":
            if status_cb:
                status_cb("extracting", f"正在解析 Word 文档《{os.path.basename(file_path)}》内容...")
            text = extract_docx_text_builtin(file_path).strip()
            if text:
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write(text)
            return text, [], warnings

        elif ext == ".pdf":
            if status_cb:
                status_cb("extracting", f"正在读取 PDF 文档《{os.path.basename(file_path)}》...")
            subprocess.run(["/usr/bin/pdftotext", file_path, txt_path], timeout=30, check=False)
            raw_text = ""
            if os.path.exists(txt_path):
                with open(txt_path, "r", encoding="utf-8", errors="ignore") as f:
                    raw_text = f.read()
            clean_text = re.sub(r'[\x0c\s]+', '', raw_text)

            # Fallback to pypdf text extraction if pdftotext extracted very few chars
            if len(clean_text) < 20:
                try:
                    import pypdf
                    reader = pypdf.PdfReader(file_path)
                    pypdf_lines = []
                    for page in reader.pages:
                        p_t = (page.extract_text() or "").strip()
                        if p_t:
                            pypdf_lines.append(p_t)
                    if pypdf_lines:
                        pypdf_full = "\n".join(pypdf_lines)
                        if len(re.sub(r'\s+', '', pypdf_full)) >= 20:
                            raw_text = pypdf_full
                            clean_text = re.sub(r'\s+', '', pypdf_full)
                except Exception:
                    pass

            if len(clean_text) >= 20:
                cleaned_out = raw_text.replace("\x0c", "\n").strip()
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write(cleaned_out)
                return cleaned_out, [], warnings
            else:
                # Scanned image PDF: clean empty txt file, render pages to optimized JPEG, and run parallel OCR
                doc_name = os.path.basename(file_path)
                if status_cb:
                    status_cb("rendering", f"识别《{doc_name}》为扫描件 PDF，正在高清逐页渲染中...")
                if os.path.exists(txt_path):
                    try:
                        os.remove(txt_path)
                    except OSError:
                        pass
                rendered_pages = render_pdf_to_images(file_path, max_pages=100)

                ocr_text = ""
                if rendered_pages and TESSERACT_BIN:
                    if status_cb:
                        status_cb("rendering", f"《{doc_name}》正在进行智能高精图文识别 (共 {len(rendered_pages)} 页)...")
                    page_texts = ocr_images_batch(rendered_pages, status_cb=status_cb, doc_name=doc_name, max_workers=2)
                    combined_ocr = []
                    for p_i, p_txt in enumerate(page_texts):
                        if p_txt.strip():
                            combined_ocr.append(f"--- 《{doc_name}》第 {p_i+1} 页 (扫描件图文识别) ---\n{p_txt.strip()}")
                    ocr_text = "\n\n".join(combined_ocr).strip()
                    if ocr_text:
                        with open(txt_path, "w", encoding="utf-8") as f:
                            f.write(ocr_text)

                return ocr_text, rendered_pages, warnings

        elif ext in (".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".cbr", ".r00"):
            if status_cb:
                status_cb("extracting", f"正在解压案卷归档包《{os.path.basename(file_path)}》...")
            base_dir = os.path.dirname(file_path)
            stem = re.sub(r'[^a-zA-Z0-9_\u4e00-\u9fa5-]', '_', os.path.splitext(os.path.basename(file_path))[0])
            unzip_dir = os.path.join(base_dir, f"unzipped_{stem}")
            os.makedirs(unzip_dir, exist_ok=True)

            # 1. Primary extractor: 7z (supports .rar, .rar5, .7z, .zip, .tar, etc.)
            subprocess.run(["/usr/bin/7z", "x", "-y", f"-o{unzip_dir}", file_path], capture_output=True, timeout=120, check=False)

            # Check if any files were unpacked
            extracted_files = []
            for root, dirs, files in os.walk(unzip_dir):
                for f in files:
                    if not f.startswith("._") and not f.startswith("~"):
                        extracted_files.append(os.path.join(root, f))

            # 2. Fallback: try unrar if 7z produced no files on a .rar file
            if not extracted_files and ext in (".rar", ".r00", ".cbr"):
                unrar_bin = "/usr/bin/unrar" if os.path.exists("/usr/bin/unrar") else ("/usr/bin/unrar-free" if os.path.exists("/usr/bin/unrar-free") else "")
                if unrar_bin:
                    subprocess.run([unrar_bin, "-x", "--force", file_path, unzip_dir], capture_output=True, timeout=120, check=False)

            combined_texts = []
            for root, dirs, files in os.walk(unzip_dir):
                for sub_name in sorted(files):
                    if not sub_name or sub_name.startswith("._") or sub_name.startswith("~"):
                        continue
                    sub_dest = os.path.join(root, sub_name)
                    sub_ext = os.path.splitext(sub_name)[1].lower()
                    if sub_ext == ".lnk":
                        warnings.append(f"⚠️ 压缩包内的《{sub_name}》是 Windows 快捷方式 (.lnk)，不含实际文件内容，请重新上传该文件的源文件！")
                        continue

                    sub_txt, sub_pages, sub_warns = extract_text_from_file(sub_dest, status_cb=status_cb)
                    warnings.extend(sub_warns)
                    rendered_pages.extend(sub_pages)
                    if sub_txt:
                        combined_texts.append(f"=== 【压缩包内文件：{sub_name}】 ===\n{sub_txt}")
                    elif sub_pages:
                        combined_texts.append(f"=== 【压缩包内扫描件/证据图片：{sub_name}（原图已就绪供AI视觉审读）】 ===")

            full_arch_text = "\n\n".join(combined_texts).strip()
            if full_arch_text:
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write(full_arch_text)
            return full_arch_text, rendered_pages, warnings

        elif ext in [".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"]:
            ocr_text = ""
            if TESSERACT_BIN:
                if status_cb:
                    status_cb("rendering", f"正在对证据图片《{os.path.basename(file_path)}》进行图文识别...")
                ocr_text = ocr_single_image(file_path)
                if ocr_text:
                    with open(txt_path, "w", encoding="utf-8") as f:
                        f.write(ocr_text)
            return ocr_text, [file_path], warnings

        elif ext in [".txt", ".md", ".json", ".csv"]:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read().strip()
            if text:
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write(text)
            return text, [], warnings
    except Exception as e:
        print(f"[TextExtract] failed for {file_path}: {e}")
    return "", rendered_pages, warnings


def parse_post_message(msg_id: str, raw_content_str: str) -> Tuple[List[Dict[str, str]], str]:
    """
    Parses Feishu 'post' (rich-text / multi-file / mixed text+file+image) message content.
    If the WebSocket event payload omits 'files' or has empty content, queries Feishu OpenAPI
    GET /open-apis/im/v1/messages/{msg_id} to retrieve the complete attachment list.
    Returns (attachments_list, combined_text).
    """
    attachments: List[Dict[str, str]] = []
    seen_keys = set()
    texts: List[str] = []

    def _walk_post_obj(obj: Any):
        if isinstance(obj, dict):
            tag_val = obj.get("tag")
            if tag_val in ("file", "media") and obj.get("file_key"):
                fk = obj["file_key"]
                if fk not in seen_keys:
                    seen_keys.add(fk)
                    attachments.append({
                        "key": fk,
                        "name": obj.get("file_name") or f"附件_{fk[:8]}",
                        "type": "file"
                    })
            elif tag_val == "img" and obj.get("image_key"):
                ik = obj["image_key"]
                if ik not in seen_keys:
                    seen_keys.add(ik)
                    attachments.append({
                        "key": ik,
                        "name": f"证据图片_{ik[:8]}.png",
                        "type": "image"
                    })
            elif tag_val in ("text", "a") and obj.get("text"):
                t = obj["text"].strip()
                if t:
                    texts.append(t)

            # Top-level "files" array in Feishu multi-file post messages
            if isinstance(obj.get("files"), list):
                for f_item in obj["files"]:
                    if isinstance(f_item, dict) and f_item.get("file_key"):
                        fk = f_item["file_key"]
                        if fk not in seen_keys:
                            seen_keys.add(fk)
                            attachments.append({
                                "key": fk,
                                "name": f_item.get("file_name") or f"附件_{fk[:8]}",
                                "type": "file"
                            })

            if obj.get("title") and isinstance(obj.get("title"), str):
                title_str = obj["title"].strip()
                if title_str and title_str not in texts:
                    texts.insert(0, title_str)

            for k, v in obj.items():
                if k not in ("files", "title"):
                    _walk_post_obj(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk_post_obj(item)

    try:
        parsed = json.loads(raw_content_str) if raw_content_str else {}
        _walk_post_obj(parsed)
    except Exception as e:
        print(f"[PostParse] Event JSON parse error: {e}")

    # Fallback: If event payload had empty content and no files, fetch full message from Feishu OpenAPI
    if not attachments and not texts:
        full_msg = feishu_api.get_message(msg_id)
        body_str = full_msg.get("body", {}).get("content", "")
        if body_str:
            try:
                _walk_post_obj(json.loads(body_str))
            except Exception as e:
                print(f"[PostParse] OpenAPI JSON parse error: {e}")

    clean_text = re.sub(r"@_user_\d+\s*", "", " ".join(texts)).strip()
    return attachments, clean_text


def handle_incoming_files_batch(
    session_key: str,
    message_id: str,
    attachments: List[Dict[str, str]],
    accompanying_text: str = "",
    parent_chat_id: Optional[str] = None
):
    """
    Processes single or multiple file/image attachments in a message,
    storing them inside the session's isolated case_dir, pre-extracting text/scanned pages,
    and providing dynamic real-time progress card updates.
    File upload runs concurrently without being blocked by long-running agent tasks.
    """
    try:
        case_dir = session_mgr.get_or_create_case_dir(session_key, for_upload=True)
        materials_dir = os.path.join(case_dir, "materials")
        os.makedirs(materials_dir, exist_ok=True)

        saved_notes = []
        all_warnings = []
        pending = []
        total_files = len(attachments)
        total_batch_bytes = 0

        # Send initial progress card immediately so the user sees instant feedback!
        first_att = attachments[0]
        init_card = card_builder.build_upload_progress_card(
            file_name=first_att["name"],
            status_stage="downloading",
            current_bytes=0,
            total_bytes=0,
            current_idx=1,
            total_files=total_files,
            is_legal_bot=IS_LEGAL_BOT
        )
        progress_msg_id = reply_message(message_id, init_card, "interactive")

        for file_idx, att in enumerate(attachments, 1):
            file_key = att["key"]
            file_name = att["name"]
            resource_type = att.get("type", "file")

            safe_name = re.sub(r'[\\/*?:"<>|]', '_', file_name)
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            saved_filename = f"{timestamp}_{safe_name}"
            dest_path = os.path.join(materials_dir, saved_filename)

            last_patch_time = 0.0
            last_patch_pct = -1

            def on_download_progress(cur: int, tot: int):
                nonlocal last_patch_time, last_patch_pct
                if not progress_msg_id:
                    return
                now = time.time()
                pct = int((cur / tot) * 100) if tot > 0 else 0
                time_diff = now - last_patch_time
                pct_step = (pct - last_patch_pct >= 20)
                if (time_diff >= 1.5 and pct_step) or cur == tot or (time_diff >= 3.0 and pct > 0):
                    last_patch_time = now
                    last_patch_pct = pct
                    card = card_builder.build_upload_progress_card(
                        file_name=file_name,
                        status_stage="downloading",
                        current_bytes=cur,
                        total_bytes=tot,
                        current_idx=file_idx,
                        total_files=total_files,
                        is_legal_bot=IS_LEGAL_BOT
                    )
                    patch_message_card(progress_msg_id, card)

            ok = feishu_api.download_resource_to_file(
                message_id=message_id,
                file_key=file_key,
                dest_path=dest_path,
                resource_type=resource_type,
                progress_cb=on_download_progress
            )
            if not ok or not os.path.exists(dest_path):
                all_warnings.append(f"⚠️ 文件《{file_name}》下载失败，请检查网络或重新上传。")
                continue

            file_size_bytes = os.path.getsize(dest_path)
            total_batch_bytes += file_size_bytes
            file_size_kb = round(file_size_bytes / 1024, 1)
            print(f"[Feishu File] Saved {dest_path} ({file_size_kb} KB) in isolated case_dir={case_dir}")

            def on_extract_status(stage: str, msg: str):
                if not progress_msg_id:
                    return
                card = card_builder.build_upload_progress_card(
                    file_name=file_name,
                    status_stage=stage,
                    current_bytes=file_size_bytes,
                    total_bytes=file_size_bytes,
                    current_idx=file_idx,
                    total_files=total_files,
                    detail_msg=msg,
                    is_legal_bot=IS_LEGAL_BOT
                )
                patch_message_card(progress_msg_id, card)

            # Update status to extracting/preprocessing
            on_extract_status("extracting", "正在解压并预处理文档内容...")

            txt_path = f"{dest_path}.txt"
            extracted_text, rendered_pages, warnings = extract_text_from_file(dest_path, status_cb=on_extract_status)
            all_warnings.extend(warnings)
            has_extracted = bool(extracted_text)

            if has_extracted:
                if rendered_pages:
                    extract_note = f"（已预解析纯文本 {len(extracted_text)} 字，含扫描件OCR）"
                else:
                    extract_note = f"（已预解析纯文本 {len(extracted_text)} 字）"
            elif rendered_pages:
                extract_note = f"（扫描件PDF，已自动渲染 {len(rendered_pages)} 页高清图片供AI直读）"
            elif resource_type == "image":
                extract_note = "（证据图片，已就绪供AI直读）"
            else:
                extract_note = ""

            pending = session_mgr.add_pending_file(session_key, {
                "name": file_name,
                "path": dest_path,
                "txt_path": txt_path if has_extracted and os.path.exists(txt_path) else "",
                "rendered_pages": rendered_pages,
                "resource_type": resource_type,
                "size_kb": file_size_kb,
                "char_count": len(extracted_text),
                "uploaded_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
            saved_notes.append(f"已接收《{file_name}》（{file_size_kb} KB）{extract_note}")

        if not saved_notes and all_warnings:
            err_card = card_builder.build_upload_progress_card(
                file_name=first_att["name"],
                status_stage="error",
                detail_msg="\n".join(all_warnings),
                is_legal_bot=IS_LEGAL_BOT
            )
            if progress_msg_id:
                patch_message_card(progress_msg_id, err_card)
            else:
                reply_message(message_id, err_card, "interactive")
            return

        if not pending:
            pending = session_mgr.get_session(session_key).get("pending_files", [])

        summary_name = first_att["name"] if total_files == 1 else f"《{first_att['name']}》等 {total_files} 个材料"
        completion_detail = "\n".join([f"• {note}" for note in saved_notes])
        if all_warnings:
            completion_detail += "\n\n" + "\n".join(all_warnings)

        final_card = card_builder.build_upload_progress_card(
            file_name=summary_name,
            status_stage="complete",
            current_bytes=total_batch_bytes,
            total_bytes=total_batch_bytes,
            current_idx=total_files,
            total_files=total_files,
            detail_msg=completion_detail,
            case_materials=pending,
            is_legal_bot=IS_LEGAL_BOT
        )

        if progress_msg_id:
            patch_message_card(progress_msg_id, final_card)
        else:
            reply_message(message_id, final_card, "interactive")

        # If user sent a substantive prompt together with the files in a 'post' message, execute it immediately
        norm_acc = accompanying_text.strip("。！!？? \t\r\n")
        if accompanying_text and norm_acc not in UPLOAD_DONE_TRIGGERS and norm_acc not in NEW_CASE_TRIGGERS:
            execute_agent_task(session_key, message_id, accompanying_text, parent_chat_id)

    except Exception as e:
        print(f"[Feishu File Batch Error] {e}")
        reply_message(message_id, f"⚠️ 保存文件时发生异常: {str(e)}")

    except Exception as e:
        print(f"[Feishu File Batch Error] {e}")
        reply_message(message_id, f"⚠️ 保存文件时发生异常: {str(e)}")


def handle_incoming_file(session_key: str, message_id: str, file_key: str, file_name: str, resource_type: str = "file"):
    """Wrapper for single-file upload."""
    handle_incoming_files_batch(
        session_key=session_key,
        message_id=message_id,
        attachments=[{"key": file_key, "name": file_name, "type": resource_type}]
    )


# ----------------- Agent Task Execution -----------------
def execute_agent_task(session_key: str, message_id: str, prompt: str, parent_chat_id: str = None):
    """Executes prompt via agy CLI serialized per session_key and returns response."""
    task_lock = get_task_lock(session_key)
    with task_lock:
        sess = session_mgr.get_session(session_key, parent_chat_id)
        project_dir = sess.get("project_dir") or DEFAULT_PROJECT_DIR
        if not os.path.isdir(project_dir):
            try:
                os.makedirs(project_dir, exist_ok=True)
            except Exception:
                project_dir = DEFAULT_PROJECT_DIR

        # For the legal bot, always run inside the isolated per-case directory so agy never sees historical files!
        if IS_LEGAL_BOT:
            work_dir = session_mgr.get_or_create_case_dir(session_key, for_upload=False)
        else:
            work_dir = project_dir

        conv_id = sess.get("conversation_id")
        pending_files = sess.get("pending_files", [])

        override_model, clean_prompt = extract_model_override(prompt)
        target_model = override_model or sess.get("model") or DEFAULT_MODEL

        cache = load_paipan_cache()
        is_interpret = any(k in clean_prompt for k in ["解读", "分析", "看盘", "看下盘", "占断", "断卦", "测", "算"])
        chart = None
        if is_interpret:
            chart = cache.get(session_key) or (cache.get(parent_chat_id) if parent_chat_id else None) or cache.get("_latest_")

        has_pending = bool(pending_files)
        if has_pending:
            materials_detail = []
            for i, item in enumerate(pending_files):
                size_str = f" ({item.get('size_kb', 0)} KB)" if item.get('size_kb') else ""
                txt_path = item.get("txt_path")
                rendered_pages = item.get("rendered_pages") or []
                txt_content = ""
                if txt_path and os.path.exists(txt_path):
                    try:
                        with open(txt_path, "r", encoding="utf-8", errors="ignore") as tf:
                            txt_content = tf.read().strip()
                    except Exception:
                        pass

                item_info = f"{i+1}. 《{item['name']}》{size_str} (原件路径: {item['path']})"
                if txt_content and len(txt_content) <= 35000:
                    item_info += f"\n   【该文件已自动预解析全文纯文本内容如下】:\n\"\"\"\n{txt_content}\n\"\"\""
                elif txt_content:
                    item_info += f"\n   (全文已自动预解析纯文本至: {txt_path}，共 {len(txt_content)} 字，请直接调用 read_file 阅读该 txt 文件)"
                elif rendered_pages:
                    pages_str = "\n     ".join(rendered_pages)
                    item_info += (
                        f"\n   【扫描件图片已自动渲染就绪（共 {len(rendered_pages)} 页）】:\n"
                        f"   请直接调用 read_file 依次阅读以下页面图片获取完整原文（无需再次运行 pdftoppm）：\n"
                        f"     {pages_str}"
                    )
                elif item.get("resource_type") == "image":
                    item_info += f"\n   (该文件为证据图片，请直接调用 read_file 阅读图片路径: {item['path']})"
                materials_detail.append(item_info)

            materials_str = "\n\n".join(materials_detail)

            if IS_LEGAL_BOT:
                actual_prompt = (
                    f"【本案唯一在案材料清单（共 {len(pending_files)} 份，案件独立隔离目录: {work_dir}）】:\n"
                    f"{materials_str}\n\n"
                    f"【用户指令/案情诉求】:\n"
                    f"{clean_prompt}\n\n"
                    f"【办案指引与铁律 (甘肃乾祐律师事务所)】:\n"
                    f"1. 严格以甘肃乾祐律师事务所专业法律智能助手身份办理。\n"
                    f"2. 严禁向用户反问任何问题（除非用户提及的当事人在本案材料中完全不存在），在案材料全文或已渲染页面图片已在上方直接提供，请直接通读融会贯通。\n"
                    f"3. 严格遵循民事诉讼举证责任分配规则（《民诉法解释》第90条）及实体法规定，进行事实穿透与要件对齐。\n"
                    f"4. 严格按照甘肃乾祐律师事务所标准体例出具完整文书（如模版A法律意见书、模版B答辩状、民事代理词等）。\n"
                    f"5. 【防串案与材料隔离铁律（最高优先级）】：\n"
                    f"   - 本次研判与文书起草，严格仅限依据上方【本案唯一在案材料清单】中列明的文件及本次对话事实！\n"
                    f"   - 绝对禁止访问父目录（..）或搜索 `/home/shenb/.gemini/antigravity-cli/scratch` 下的其他文件夹！\n"
                    f"   - 若用户指令中点名了特定当事人（例如某公司或某自然人），但上方【本案唯一在案材料清单】中完全没有该当事人的相关材料，请立即停止起草并明确告知用户：“当前在案材料为《...》，未包含您提及的【XXX】相关案卷，请确认是否漏传文件或重新上传”，绝对禁止拿其他无关案件当事人强行拼凑！\n"
                    f"6. 全文纯文本呈现，绝对禁止出现任何外部意见链接或URL网址。"
                )
            else:
                actual_prompt = (
                    f"【参考文件清单（共 {len(pending_files)} 份）】:\n"
                    f"{materials_str}\n\n"
                    f"【用户诉求】:\n"
                    f"{clean_prompt}"
                )
        elif chart and is_interpret and not IS_LEGAL_BOT:
            actual_prompt = (
                f"【待解读排盘数据】:\n{chart}\n\n"
                f"【用户诉求】: {clean_prompt}\n\n"
                f"【系统严格指引】:\n"
                f"1. 绝不要调用任何工具（严禁 run_command、view_file、grep 等），严禁在硬盘查找文件！\n"
                f"2. 请直接基于上述排盘数据进行专业易理推演：排盘中的八字为起盘占断时刻天时干支，非生辰八字。\n"
                f"3. 紧密结合梅花易数体用生克、奇门遁甲星门神仪格局吉凶、小六壬落宫断语，直接输出条理清晰、专业深刻的解读与决策建议。"
            )
        elif IS_LEGAL_BOT:
            actual_prompt = (
                f"【当前状态】: 本案当前尚未上传附件材料（案件独立隔离目录: {work_dir}）。\n\n"
                f"【用户指令/案情诉求】:\n"
                f"{clean_prompt}\n\n"
                f"【办案指引与铁律 (甘肃乾祐律师事务所)】:\n"
                f"1. 严格以甘肃乾祐律师事务所专业法律智能助手身份办理。\n"
                f"2. 【防串案最高铁律】：绝对禁止调用任何工具去读取或搜索本地磁盘上的历史文件夹或旧案材料！当前案件没有任何已上传附件，仅限依据用户在上方【用户指令/案情诉求】中直接输入的文字事实进行法律研判或文书起草。\n"
                f"3. 若用户要求“根据以上信息/上述材料”为某特定当事人出具答辩状/意见书/代理词，但对话中既未上传该案材料也未描述具体案情事实，请直接礼貌提示用户先上传该案的起诉状/上诉状/判决书或输入基本案情（提示：若刚发送了多份文件未识别，可重新发送），严禁凭空编造或引用任何历史案件！\n"
                f"4. 严格遵循民事诉讼举证责任分配规则及民商事实体法要件，全文纯文本呈现，严禁任何外部URL链接。"
            )
        else:
            actual_prompt = clean_prompt

        clean_title = re.sub(r'[\r\n\t]+', ' ', clean_prompt).strip()
        display_title = (clean_title[:38] + "...") if len(clean_title) > 38 else clean_title

        # Send initial progress card in millisecond 1
        init_card = card_builder.build_ai_progress_card(
            task_title=display_title,
            status_stage="running",
            elapsed_seconds=0,
            step_desc="已建立案卷隔离工作区，正在全案通读在案材料并建立事实脉络...",
            progress_pct=10,
            is_legal_bot=IS_LEGAL_BOT
        )
        progress_msg_id = reply_message(message_id, init_card, "interactive")

        cmd = [
            "/usr/local/bin/agy",
            "-p", actual_prompt,
            "--model", target_model,
            "--output-format", "stream-json",
            "--dangerously-skip-permissions"
        ]
        if conv_id:
            cmd.extend(["--conversation", conv_id])

        model_tag = f" [特别说明: {override_model}]" if override_model else ""
        print(f"[Agent] Running in {work_dir} [{conv_id or 'NEW'}] (Model: {target_model}{model_tag}): {clean_prompt[:50]}...")

        start_t = time.time()
        state = {
            "step_desc": "正在全案通读在案材料并梳理事实脉络...",
            "step_count": 0,
            "done": False,
            "new_cid": None,
            "final_response": "",
            "last_patch_t": start_t,
            "stderr_lines": []
        }

        # Background ticker thread: smoothly updates Feishu card progress & elapsed time every 3.5 seconds
        def ticker_worker():
            while not state["done"]:
                time.sleep(3.5)
                if state["done"] or not progress_msg_id:
                    break
                now = time.time()
                elapsed = int(now - start_t)
                if elapsed < 20:
                    pct = 10 + int((elapsed / 20) * 25)  # 10% -> 35%
                elif elapsed < 60:
                    pct = 35 + int(((elapsed - 20) / 40) * 35)  # 35% -> 70%
                elif elapsed < 120:
                    pct = 70 + int(((elapsed - 60) / 60) * 18)  # 70% -> 88%
                else:
                    pct = min(96, 88 + int(((elapsed - 120) / 60) * 4))

                card = card_builder.build_ai_progress_card(
                    task_title=display_title,
                    status_stage="running",
                    elapsed_seconds=elapsed,
                    step_desc=state["step_desc"],
                    progress_pct=pct,
                    is_legal_bot=IS_LEGAL_BOT
                )
                patch_message_card(progress_msg_id, card)

        ticker = threading.Thread(target=ticker_worker, daemon=True)
        ticker.start()

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=work_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )

            # Read stderr in background
            def read_stderr():
                for eline in proc.stderr:
                    if eline.strip():
                        state["stderr_lines"].append(eline.strip())
            stderr_thread = threading.Thread(target=read_stderr, daemon=True)
            stderr_thread.start()

            for line in iter(proc.stdout.readline, ""):
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                    ev_type = evt.get("event")
                    if ev_type == "init":
                        cid = evt.get("conversation_id")
                        if cid:
                            state["new_cid"] = cid
                    elif ev_type == "step_update":
                        state["step_count"] += 1
                        su = evt.get("step_update", {})
                        stype = su.get("step_type")
                        if stype == "tool":
                            tname = su.get("tool_name", "")
                            tinfo = su.get("tool_info", {}).get("parameters", {})
                            if tname in ("view_file", "read_file"):
                                p = tinfo.get("Path", "")
                                fn = os.path.basename(p) if p else "证据材料"
                                state["step_desc"] = f"正在精读在案证据《{fn}》..."
                            elif tname == "run_command":
                                summary = tinfo.get("toolSummary", "")
                                cmd_str = tinfo.get("CommandLine", "")
                                if summary:
                                    clean_s = summary.strip('\"')
                                    state["step_desc"] = f"正在执行分析: {clean_s}..."
                                elif "ocr" in cmd_str.lower() or "tesseract" in cmd_str.lower():
                                    state["step_desc"] = "正在对案卷扫描件进行多线程高精度图文识别..."
                                elif "pdftoppm" in cmd_str.lower():
                                    state["step_desc"] = "正在渲染案卷扫描件高清原件..."
                                else:
                                    state["step_desc"] = "正在核验案件事实与法条要件..."
                            elif tname == "write_to_file":
                                state["step_desc"] = "正在起草撰写实务文书正文..."
                            elif tname == "manage_task":
                                state["step_desc"] = "正在并行调度背景审读与比对任务..."
                            elif tname == "schedule":
                                state["step_desc"] = "正在等待证据审查与图文解析收尾..."
                        elif stype == "agent_response":
                            txt = su.get("content", "").strip()
                            if txt and not txt.startswith("`") and len(txt) > 2:
                                first_line = txt.split("\n")[0][:45]
                                state["step_desc"] = f"正在研判: {first_line}..."
                    elif ev_type == "result":
                        res_obj = evt.get("result", {})
                        state["final_response"] = res_obj.get("response", "")
                        cid = res_obj.get("conversation_id")
                        if cid:
                            state["new_cid"] = cid
                except Exception:
                    pass

            proc.wait(timeout=30)
            state["done"] = True
            total_elapsed = int(time.time() - start_t)

            if proc.returncode != 0 and not state["final_response"]:
                err = "\n".join(state["stderr_lines"][-5:]) or f"Exit code: {proc.returncode}"
                print(f"[Agent Error] {err}")
                if progress_msg_id:
                    err_card = card_builder.build_ai_progress_card(
                        task_title=display_title,
                        status_stage="error",
                        elapsed_seconds=total_elapsed,
                        step_desc=err,
                        is_legal_bot=IS_LEGAL_BOT
                    )
                    patch_message_card(progress_msg_id, err_card)
                reply_message(message_id, f"❌ 执行异常 ({total_elapsed}s):\n{err}")
                return

            # Update conversation memory
            new_cid = state["new_cid"]
            cur_sess = session_mgr.get_session(session_key)
            if cur_sess.get("case_dir") == work_dir and new_cid:
                session_mgr.update_conversation(session_key, new_cid, prompt)

            # Update progress card to complete
            if progress_msg_id:
                comp_card = card_builder.build_ai_progress_card(
                    task_title=display_title,
                    status_stage="complete",
                    elapsed_seconds=total_elapsed,
                    progress_pct=100,
                    is_legal_bot=IS_LEGAL_BOT
                )
                patch_message_card(progress_msg_id, comp_card)

            resp_text = state["final_response"].strip() or "✅ 任务执行完毕。"
            if IS_LEGAL_BOT:
                resp_text += "\n\n---\n💡 办案提示：您可继续针对本案追问、补充材料，或点击【意见书】【答辩词】【代理词】出具其他文书；若需办理其他新案件，请先点击或发送【新案件】。"
            elif has_pending:
                session_mgr.clear_pending_files(session_key)

            reply_message(message_id, resp_text)

        except subprocess.TimeoutExpired:
            state["done"] = True
            if progress_msg_id:
                err_card = card_builder.build_ai_progress_card(
                    task_title=display_title,
                    status_stage="error",
                    elapsed_seconds=int(time.time() - start_t),
                    step_desc="任务执行超时（超过 15 分钟），已自动中止。",
                    is_legal_bot=IS_LEGAL_BOT
                )
                patch_message_card(progress_msg_id, err_card)
            reply_message(message_id, "⏱️ 任务执行超时（超过 15 分钟），已自动中止。")
        except Exception as e:
            state["done"] = True
            reply_message(message_id, f"⚠️ 处理异常: {str(e)}")
        finally:
            state["done"] = True


# ----------------- Feishu Event Callback -----------------
def on_message_received(data: lark.im.v1.P2ImMessageReceiveV1) -> None:
    try:
        msg = data.event.message
        sender = data.event.sender
        msg_id, chat_id, chat_type = msg.message_id, msg.chat_id, msg.chat_type
        open_id = sender.sender_id.open_id if sender and sender.sender_id else ""
        sender_type = sender.sender_type if sender else ""

        # Ignore self & unauthorized
        if sender_type in ["app", "bot"] or open_id == BOT_OPEN_ID:
            return
        if ALLOWED_OPEN_IDS and open_id not in ALLOWED_OPEN_IDS:
            return

        # Deduplication
        with lock:
            if msg_id in processed_msg_ids:
                return
            processed_msg_ids.add(msg_id)
            msg_id_queue.append(msg_id)

        # Topic Groups vs Private Chats
        if chat_type == "group":
            parent_chat_id = chat_id
            chat_info = get_chat_info(chat_id)
            topic_id = msg.root_id if msg.root_id else msg_id
            session_key = f"topic:{chat_id}:{topic_id}"
            first_p = "[文件/图片]" if msg.message_type in ["file", "image", "post", "media"] else ""
            session_mgr.get_topic_session(
                chat_id=chat_id,
                topic_id=topic_id,
                chat_name=chat_info.get("name", ""),
                chat_mode=chat_info.get("chat_mode", "topic"),
                first_prompt=first_p
            )
        else:
            parent_chat_id = None
            session_key = chat_id
            session_mgr.get_session(session_key)

        # 1. Handle Single File / Media Upload (docx, pdf, zip, etc.)
        if msg.message_type in ("file", "media"):
            try:
                content_data = json.loads(msg.content)
                file_key = content_data.get("file_key")
                file_name = content_data.get("file_name", f"file_{msg_id}")
            except Exception as e:
                reply_message(msg_id, f"⚠️ 解析文件消息失败: {e}")
                return

            if not file_key:
                reply_message(msg_id, "⚠️ 未能获取到文件标识 (file_key)。")
                return

            executor.submit(
                handle_incoming_files_batch,
                session_key,
                msg_id,
                [{"key": file_key, "name": file_name, "type": "file"}],
                "",
                parent_chat_id
            )
            return

        # 2. Handle Single Image Upload (evidence, contracts)
        if msg.message_type == "image":
            try:
                content_data = json.loads(msg.content)
                image_key = content_data.get("image_key")
            except Exception as e:
                reply_message(msg_id, f"⚠️ 解析图片消息失败: {e}")
                return

            if not image_key:
                reply_message(msg_id, "⚠️ 未能获取到图片标识 (image_key)。")
                return

            img_name = f"证据图片_{image_key[:8]}.png"
            executor.submit(
                handle_incoming_files_batch,
                session_key,
                msg_id,
                [{"key": image_key, "name": img_name, "type": "image"}],
                "",
                parent_chat_id
            )
            return

        # 3. Handle Rich-Text / Multi-File Upload ('post' message type)
        if msg.message_type == "post":
            attachments, post_text = parse_post_message(msg_id, msg.content)
            if attachments:
                executor.submit(
                    handle_incoming_files_batch,
                    session_key,
                    msg_id,
                    attachments,
                    post_text,
                    parent_chat_id
                )
                return
            elif post_text:
                # Pure rich-text message without attachments: fall through as text prompt!
                clean_text = post_text
            else:
                reply_message(msg_id, "⚠️ 未能从该富文本/组合消息中解析出文件或文字内容，请尝试逐个发送文件。")
                return
        elif msg.message_type == "text":
            raw_text = json.loads(msg.content).get("text", "").strip()
            clean_text = re.sub(r"@_user_\d+\s*", "", raw_text).strip()
        else:
            reply_message(msg_id, f"ℹ️ 目前支持文本、Word/PDF 文档（支持多选批量发送）、压缩包 (.rar / .zip / .7z) 以及证据图片，暂不支持此类消息类型 ({msg.message_type})。")
            return

        if not clean_text:
            return

        parts = clean_text.split()
        cmd = parts[0].lower() if parts else ""
        norm_text = clean_text.strip("。！!？? \t\r\n")

        # Intercept upload-completion confirmation phrases ("文件传输结束", "上传完毕", etc.)
        if clean_text in UPLOAD_DONE_TRIGGERS or norm_text in UPLOAD_DONE_TRIGGERS:
            cmd_materials(session_key, msg_id, parts, parent_chat_id)
            return

        # Legal bot dedicated button & command dispatching (Isolated to chenjia / legal bot only)
        if IS_LEGAL_BOT:
            sess = session_mgr.get_session(session_key, parent_chat_id)
            pending_files = sess.get("pending_files", [])
            has_context = bool(pending_files or sess.get("conversation_id"))

            # 按钮 1:【新案件】
            if clean_text in NEW_CASE_TRIGGERS or norm_text in NEW_CASE_TRIGGERS:
                session_mgr.reset_conversation(session_key, status="idle")
                reply_message(
                    msg_id,
                    "⚖️【甘肃乾祐律师事务所 · 新案件已开启】\n\n"
                    "上一案件的全部在案材料、独立工作目录与会话记忆已彻底清空隔离，新案件归集已从零开始。\n\n"
                    "📎 请直接上传新案材料（支持 Word .docx、PDF 多文件批量发送、.rar / .zip / .7z 压缩包、合同/欠条等证据图片），或直接输入案情诉求进行研判。"
                )
                return

            # 按钮 2:【结束归档】
            if clean_text in ARCHIVE_TRIGGERS or norm_text in ARCHIVE_TRIGGERS:
                session_mgr.reset_conversation(session_key, status="concluded")
                reply_message(
                    msg_id,
                    "📁【甘肃乾祐律师事务所 · 结案归档确认】\n\n"
                    "本案已正式结案归档并闭环隔离，案件材料与上下文已归档重置。\n\n"
                    "甘肃乾祐律师事务所专业法律智能助手随时待命。后续如有新的案件咨询、合同审查或法律文书起草需求，请随时点击【新案件】或直接上传新案卷材料。"
                )
                return

            # 按钮 3:【意见书】
            if clean_text in ["意见书", "法律意见书", "出法律意见书", "出具法律意见书", "出具意见书"]:
                if not has_context:
                    reply_message(msg_id, "📋【提示】当前暂未上传案件材料或输入案情。\n\n请先上传案卷材料（Word、PDF 或证据图片），或在对话框中简要描述案件事实与争议，再点击【意见书】，我将立即为您出具甘肃乾祐律师事务所专业《法律意见书》。")
                    return
                opinion_prompt = (
                    "出具专业法律意见书。严格按照甘肃乾祐律师事务所标准体例（模版A）出具完整严谨的《法律意见书》。"
                    "全面梳理审查查明事实、针对争议焦点进行事实穿透与要件对齐（结合《民法典》、《民诉法解释》第90条举证责任分配逐项论证）、"
                    "进行客观诉讼风险评估，并出具明确的综合法律意见与办案实务操作方案建议。全文纯文本呈现，严禁任何外部URL链接。"
                )
                executor.submit(execute_agent_task, session_key, msg_id, opinion_prompt, parent_chat_id)
                return

            # 按钮 4:【答辩词】
            if clean_text in ["答辩词", "答辩状", "民事答辩状", "起草答辩词", "起草答辩状", "出答辩状"]:
                if not has_context:
                    reply_message(msg_id, "📋【提示】当前暂未上传起诉状或相关案卷材料。\n\n请先上传起诉状、上诉状或案卷材料（Word、PDF 或证据图片），或直接发送案件事实，再点击【答辩词】，我将为您起草专业规范的《民事答辩状》。")
                    return
                defense_prompt = (
                    "起草民事答辩状。严格以甘肃乾祐律师事务所出庭律师身份，站在我方答辩人/被上诉人立场，"
                    "按照甘肃乾祐律师事务所标准体例（模版B）起草完整正规的《民事答辩状》。"
                    "包含当事人身份信息、明确答辩请求、逐项针对原告/上诉人诉求反驳的事实与理由（穿透事实要件、抗辩事实与举证证明责任分配、精准援引法律条款）、送达法院与具名落款。全文纯文本呈现，严禁任何外部URL链接。"
                )
                executor.submit(execute_agent_task, session_key, msg_id, defense_prompt, parent_chat_id)
                return

            # 按钮 5:【代理词】
            if clean_text in ["代理词", "民事代理词", "起草代理词", "出代理词", "代理意见"]:
                if not has_context:
                    reply_message(msg_id, "📋【提示】当前暂未上传案件材料或输入案情。\n\n请先上传案卷材料（支持 Word .docx、PDF、证据图片）或在对话框中简要描述案件事实与争议，再点击【代理词】，我将为您起草专业规范的《民事代理词》。")
                    return
                agency_prompt = (
                    "起草民事代理词。严格以甘肃乾祐律师事务所出庭代理律师身份，站在我方委托人立场，"
                    "起草完整正规、论证严密的《民事代理词》。"
                    "内容包含：称谓（尊敬的审判长/审判员）、代理人身份声明、明确核心代理意见与主张、"
                    "紧扣争议焦点进行深入事实穿透与要件论证、详述证据链闭环情况、结合民事诉讼举证责任分配规则（《民诉法解释》第90条）及实体法条款进行充分抗辩与法理论证、"
                    "恳请法庭支持我方主张之总结及代理人具名落款。全文条理清晰、纯文本规范呈现，严禁任何外部URL链接。"
                )
                executor.submit(execute_agent_task, session_key, msg_id, agency_prompt, parent_chat_id)
                return

            # 辅助命令:【分析】
            if clean_text in ["分析", "案情分析", "法律分析", "研判", "深度分析", "开始分析"]:
                if not has_context:
                    reply_message(msg_id, "📋【提示】当前暂未上传案件材料或输入案情。\n\n请先上传案卷材料（支持 Word .docx、PDF、证据图片）或简要输入案情，再点击【分析】，我将为您进行深度事实穿透与法律综合研判。")
                    return
                analysis_prompt = (
                    "进行深度事实穿透与法律综合研判。严格以甘肃乾祐律师事务所专业法律智能助手身份办理，通读在案全部材料与案情，严禁反问。"
                    "全面梳理核心法律事实与证据链条（证实事实、存疑点、证据瑕疵）、争议焦点归纳、举证责任分配与证明标准评估（《民诉法解释》第90条）、诉讼法律风险与实体应对策略。全文条理清晰、纯文本呈现，严禁任何外部URL链接。"
                )
                executor.submit(execute_agent_task, session_key, msg_id, analysis_prompt, parent_chat_id)
                return

        # Fast matching for case reset / batch reset (non-legal bot fallback)
        if clean_text in NEW_CASE_TRIGGERS or norm_text in NEW_CASE_TRIGGERS:
            cmd_reset_case(session_key, msg_id, parts, parent_chat_id)
            return

        if clean_text in ["查看材料清单", "当前材料", "材料清单", "待办材料", "查看材料"]:
            cmd_materials(session_key, msg_id, parts, parent_chat_id)
            return

        # Route Command or Dispatch Agent Task
        if is_paipan_command(clean_text):
            cmd_paipan(session_key, msg_id, clean_text, parent_chat_id)
        elif cmd in COMMANDS:
            COMMANDS[cmd](session_key, msg_id, parts, parent_chat_id)
        elif clean_text in COMMANDS:
            COMMANDS[clean_text](session_key, msg_id, parts, parent_chat_id)
        else:
            executor.submit(execute_agent_task, session_key, msg_id, clean_text, parent_chat_id)

    except Exception as e:
        print(f"[Feishu Error] {e}")


# ----------------- Main Entrypoint -----------------
def main():
    print("=" * 60)
    print(f"🚀 Antigravity 飞书全能网关 (Feishu Agent Gateway v4.7 - 案件目录级物理隔离版)")
    print(f"• 实例标识: [{tag}]")
    print(f"• 机器人: {BOT_NAME or 'Feishu Bot'} (Open ID: {BOT_OPEN_ID or '自动识别'})")
    print(f"• App ID: {APP_ID} | 工作区根目录: {DEFAULT_ROOT}")
    print(f"• 默认模型: {DEFAULT_MODEL} (Flash Medium)")
    print(f"• 配置文件: {CONFIG_PATH}")
    print(f"• 会话存储: {SESSIONS_PATH}")
    print(f"• 频道映射: {BINDINGS_PATH}")
    print("=" * 60)

    # Sync and auto-bind all current channels to local projects
    sync_all_chats()

    handler = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(on_message_received).build()
    ws_client = lark.ws.Client(app_id=APP_ID, app_secret=APP_SECRET, event_handler=handler, log_level=lark.LogLevel.INFO)
    print("WebSocket 监听建立成功，服务持续运行中...")
    ws_client.start()


if __name__ == "__main__":
    main()
