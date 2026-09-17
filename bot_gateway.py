import argparse
import os
import re
import sys
import json
import time
import datetime
import requests
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from collections import deque
from typing import Any, Dict

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
DEFAULT_ROOT = config.get("projects_root", os.path.expanduser("~/.gemini/antigravity-cli/scratch"))
DEFAULT_MODEL = config.get("default_model", "gemini-3.8-flash-medium")
ALLOWED_OPEN_IDS = set(config.get("allowed_open_ids", []))
BOT_OPEN_ID = config.get("bot_open_id", "")
BOT_NAME = config.get("bot_name", "")

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
    # 1. CLI flag: --model <name> or -m <name>
    flag_match = re.search(r'(?:--model|-m)\s+([a-zA-Z0-9_.-]+)', prompt)
    if flag_match:
        m_str = flag_match.group(1).lower()
        if m_str in MODEL_ALIASES:
            cleaned = re.sub(r'(?:--model|-m)\s+([a-zA-Z0-9_.-]+)', '', prompt).strip()
            return MODEL_ALIASES[m_str], cleaned

    # 2. Natural language pattern: "用pro模型...", "使用claude sonnet回答...", "用高档思考..."
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
executor = ThreadPoolExecutor(max_workers=5)

# Deduplication cache
processed_msg_ids = set()
msg_id_queue = deque(maxlen=500)
lock = threading.Lock()

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
def reply_message(message_id: str, content: Any, msg_type: str = "text"):
    """Unified message replier for text and cards."""
    try:
        if msg_type == "interactive":
            body = im_v1.ReplyMessageRequestBody.builder().content(json.dumps(content)).msg_type("interactive").build()
            resp = client.im.v1.message.reply(im_v1.ReplyMessageRequest.builder().message_id(message_id).request_body(body).build())
            if not resp.success():
                print(f"[Feishu] Card reply error: {resp.code} {resp.msg}, fallback to text")
                reply_message(message_id, "⚠️ 卡片渲染失败，请直接使用纯文本指令。")
        else:
            text = str(content)
            chunks = [text[i:i+3800] for i in range(0, len(text), 3800)] or ["(空输出)"]
            for chunk in chunks:
                body = im_v1.ReplyMessageRequestBody.builder().content(json.dumps({"text": chunk})).msg_type("text").build()
                client.im.v1.message.reply(im_v1.ReplyMessageRequest.builder().message_id(message_id).request_body(body).build())
    except Exception as e:
        print(f"[Feishu] reply_message failed: {e}")

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

    msg = (
        f"📊 【工作台状态】\n"
        f"{channel_line}"
        f"• 绑定项目: 📁 {sess['project_name']}\n"
        f"• 工作目录: `{sess['project_dir']}`\n"
        f"• 运行模型: ⚡ `{cur_model}`\n"
        f"{topic_line}"
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
    sess = session_mgr.reset_conversation(session_key)
    reply_message(message_id, f"🔄 已在当前话题内开启全新会话！\n当前项目仍为【📁 {sess['project_name']}】，上一会话已自动归档至 `/history`。")

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
    """
    通过本地最快模型 gemini-3-flash 秒级提取结构化排盘参数及经度
    """
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
    # 剥离并/并且/顺便/帮我/解读/分析等修饰词，避免误当成城市名称
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
            
    cmd = ["/home/shenb9328_gmail_com/.gemini/antigravity-cli/bin/mingli", "-f", "markdown", "-c", city]
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

    # 1. 判定是否需要走 AI 辅助解析（包含相对时间或口语化历法）
    if needs_ai_parsing(clean_text):
        print(f"[Paipan Route] Detected relative/complex time expression, using AI parser: {clean_text}")
        ai_res = ai_parse_paipan_args(clean_text)
        if ai_res:
            city, time_str, ai_need_interp, question, longitude = ai_res
            need_interpret = need_interpret or ai_need_interp
        else:
            city, time_str, _ = parse_paipan_args(clean_text)
    else:
        # 2. 常规或明确指令走 0.08 秒极速规则解析
        city, time_str, _ = parse_paipan_args(clean_text)

    cmd = ["/home/shenb9328_gmail_com/.gemini/antigravity-cli/bin/mingli", "-f", "markdown", "-c", city]
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
            # 缓存本次排盘结果（多层级关联，供后续跨话题'解读'秒级直接调取）
            cache = load_paipan_cache()
            cache[session_key] = output
            if parent_chat_id:
                cache[parent_chat_id] = output
            cache["_latest_"] = output
            save_paipan_cache(cache)
            reply_message(message_id, output)
            print(f"[FastPath Paipan] Done in {elapsed}s")

            # 若用户在同一句话中要求"并且解读/分析"，自动触发 Agent 解读
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

# ----------------- Agent Task Execution -----------------
def execute_agent_task(session_key: str, message_id: str, prompt: str, parent_chat_id: str = None):
    """Executes prompt via agy CLI and returns response."""
    sess = session_mgr.get_session(session_key)
    project_dir = sess["project_dir"]
    conv_id = sess.get("conversation_id")

    # 模型选择判定：单次提问特别指定 > 话题绑定模型 > 全局默认 (DEFAULT_MODEL: gemini-3.8-flash-medium)
    override_model, clean_prompt = extract_model_override(prompt)
    target_model = override_model or sess.get("model") or DEFAULT_MODEL

    # 如果用户请求解读，自动注入最近一次排盘结果（支持本话题、本群聊、全局最新三级继承）
    cache = load_paipan_cache()
    is_interpret = any(k in clean_prompt for k in ["解读", "分析", "看盘", "看下盘", "占断", "断卦", "测", "算"])
    chart = None
    if is_interpret:
        chart = cache.get(session_key) or (cache.get(parent_chat_id) if parent_chat_id else None) or cache.get("_latest_")

    if chart and is_interpret:
        actual_prompt = (
            f"【待解读排盘数据】:\n{chart}\n\n"
            f"【用户诉求】: {clean_prompt}\n\n"
            f"【系统严格指引】:\n"
            f"1. 绝不要调用任何工具（严禁 run_command、view_file、grep 等），严禁在硬盘查找文件！\n"
            f"2. 请直接基于上述排盘数据进行专业易理推演：排盘中的八字为起盘占断时刻天时干支，非生辰八字。\n"
            f"3. 紧密结合梅花易数体用生克、奇门遁甲星门神仪格局吉凶、小六壬落宫断语，直接输出条理清晰、专业深刻的解读与决策建议。"
        )
    else:
        actual_prompt = clean_prompt

    cmd = [
        "/usr/local/bin/agy",
        "-p", actual_prompt,
        "--model", target_model,
        "--output-format", "json",
        "--dangerously-skip-permissions"
    ]
    if conv_id:
        cmd.extend(["--conversation", conv_id])

    model_tag = f" [特别说明: {override_model}]" if override_model else ""
    print(f"[Agent] Running in {project_dir} [{conv_id or 'NEW'}] (Model: {target_model}{model_tag}): {clean_prompt[:50]}...")
    start_t = time.time()
    try:
        proc = subprocess.run(cmd, cwd=project_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=300)
        elapsed = round(time.time() - start_t, 1)

        if proc.returncode != 0:
            err = proc.stderr.strip() or proc.stdout.strip() or f"Code: {proc.returncode}"
            print(f"[Agent Error] {err}")
            reply_message(message_id, f"❌ 执行异常 ({elapsed}s):\n{err}")
            return

        try:
            res_json = json.loads(proc.stdout.strip())
            new_cid = res_json.get("conversation_id")
            if new_cid:
                session_mgr.update_conversation(session_key, new_cid, prompt)
            reply_message(message_id, res_json.get("response", "").strip() or "✅ 任务执行完毕。")
        except json.JSONDecodeError:
            reply_message(message_id, proc.stdout.strip() or "✅ 任务完成。")

    except subprocess.TimeoutExpired:
        reply_message(message_id, "⏱️ 任务执行超时（超过 5 分钟），已自动中止。")
    except Exception as e:
        reply_message(message_id, f"⚠️ 处理异常: {str(e)}")

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

        if msg.message_type != "text":
            reply_message(msg_id, "ℹ️ 目前支持文本指令和任务。")
            return

        raw_text = json.loads(msg.content).get("text", "").strip()
        clean_text = re.sub(r"@_user_\d+\s*", "", raw_text).strip()
        if not clean_text:
            return

        # Topic Groups vs Private Chats
        if chat_type == "group":
            parent_chat_id = chat_id
            chat_info = get_chat_info(chat_id)
            topic_id = msg.root_id if msg.root_id else msg_id
            session_key = f"topic:{chat_id}:{topic_id}"
            session_mgr.get_topic_session(
                chat_id=chat_id,
                topic_id=topic_id,
                chat_name=chat_info.get("name", ""),
                chat_mode=chat_info.get("chat_mode", "topic"),
                first_prompt=clean_text
            )
        else:
            parent_chat_id = None
            session_key = chat_id
            session_mgr.get_session(session_key)

        parts = clean_text.split()
        cmd = parts[0].lower() if parts else ""

        # Route Command or Dispatch Agent Task
        if is_paipan_command(clean_text):
            cmd_paipan(session_key, msg_id, clean_text, parent_chat_id)
        elif cmd in COMMANDS:
            COMMANDS[cmd](session_key, msg_id, parts, parent_chat_id)
        else:
            executor.submit(execute_agent_task, session_key, msg_id, clean_text, parent_chat_id)

    except Exception as e:
        print(f"[Feishu Error] {e}")

# ----------------- Main Entrypoint -----------------
def main():
    print("=" * 60)
    print(f"🚀 Antigravity 飞书全能网关 (Feishu Agent Gateway v4.6 - 多实例/多租户支持版)")
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
