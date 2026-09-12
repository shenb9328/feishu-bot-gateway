import os
import re
import json
import time
from typing import Dict, Any, List, Optional, Tuple

class SessionManager:
    """
    Session & Workspace Manager with support for Feishu Team Channel-to-Project 
    and Topic-to-Session tree structure mapping.
    """
    def __init__(self, storage_path: str, default_root: str, bindings_path: Optional[str] = None):
        self.storage_path = storage_path
        self.default_root = default_root
        self.home_dir = os.path.expanduser("~")
        if bindings_path:
            self.bindings_path = bindings_path
        else:
            base_name = os.path.basename(storage_path)
            if base_name.startswith("sessions.") and base_name.endswith(".json"):
                tag = base_name[9:-5]
                self.bindings_path = os.path.join(os.path.dirname(storage_path), f"chat_bindings.{tag}.json")
            else:
                self.bindings_path = os.path.join(os.path.dirname(storage_path), "chat_bindings.json")
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.chat_bindings: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self):
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    self.sessions = json.load(f)
            except Exception as e:
                print(f"[SessionManager] Load sessions failed: {e}")
                self.sessions = {}

        if os.path.exists(self.bindings_path):
            try:
                with open(self.bindings_path, "r", encoding="utf-8") as f:
                    self.chat_bindings = json.load(f)
            except Exception as e:
                print(f"[SessionManager] Load bindings failed: {e}")
                self.chat_bindings = {}

    def _save(self):
        try:
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(self.sessions, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[SessionManager] Save sessions failed: {e}")

        try:
            with open(self.bindings_path, "w", encoding="utf-8") as f:
                json.dump(self.chat_bindings, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[SessionManager] Save bindings failed: {e}")

    def list_available_workspaces(self) -> List[str]:
        """Lists local project folders in /home/shenb9328_gmail_com."""
        projects = []
        ignores = {"systemd", "node-compile-cache", "lo_profile", "pdf_check", "tmp"}
        if os.path.exists(self.home_dir):
            for item in os.listdir(self.home_dir):
                if not item.startswith(".") and item.lower() not in ignores:
                    if os.path.isdir(os.path.join(self.home_dir, item)):
                        projects.append(item)
        return sorted(projects)

    def match_project_from_name(self, raw_name: str) -> Tuple[str, str]:
        """
        Smart match Feishu channel/chat name to a local workspace directory.
        e.g. '飞书 测试项目' -> ('飞书', '/home/shenb9328_gmail_com/飞书')
             '#光储Token机' -> ('光储token机', '/home/shenb9328_gmail_com/光储token机')
        """
        # Clean name: strip emojis, brackets, hashes, spaces
        clean = re.sub(r"[#\[\]【】()（）📁🤖💬🚀\s]+", " ", raw_name).strip()
        if not clean:
            return ("default", self.default_root)

        available = self.list_available_workspaces()
        clean_lower = clean.lower()

        # 1. Exact match
        for d in available:
            if clean_lower == d.lower():
                return (d, os.path.join(self.home_dir, d))

        # 2. Substring match: check if any project name is contained in the channel name
        matches = []
        for d in available:
            if d.lower() in clean_lower or clean_lower in d.lower():
                matches.append(d)
        if matches:
            best = max(matches, key=len)
            return (best, os.path.join(self.home_dir, best))

        # 3. If clean name is a specific new project name, auto create workspace
        generics = {"default", "临时", "测试", "笑话", "general", "未命名", "通用"}
        if clean_lower not in generics and len(clean) >= 2:
            target_dir = os.path.join(self.home_dir, clean)
            os.makedirs(target_dir, exist_ok=True)
            return (clean, target_dir)

        return ("default", self.default_root)

    def resolve_project_dir(self, clean_name: str) -> str:
        if clean_name == "default":
            return self.default_root
        home_target = os.path.join(self.home_dir, clean_name)
        if os.path.isdir(home_target):
            return home_target
        scratch_target = os.path.join(self.default_root, clean_name)
        if os.path.isdir(scratch_target):
            return scratch_target
        return home_target

    def bind_chat(self, chat_id: str, chat_name: str = "", project_name: Optional[str] = None, chat_mode: str = "group") -> Dict[str, Any]:
        """
        Binds a channel/group to a project workspace directory.
        """
        self._load()
        binding = self.chat_bindings.get(chat_id, {})

        if project_name:
            clean = os.path.basename(project_name.strip())
            p_dir = self.resolve_project_dir(clean)
            os.makedirs(p_dir, exist_ok=True)
            binding["project_name"] = clean
            binding["project_dir"] = p_dir
            binding["custom_bound"] = True
        elif not binding.get("project_name") or (not binding.get("custom_bound") and chat_name and chat_name != binding.get("chat_name")):
            p_name, p_dir = self.match_project_from_name(chat_name or binding.get("chat_name", ""))
            binding["project_name"] = p_name
            binding["project_dir"] = p_dir
            binding["custom_bound"] = False

        if chat_name:
            binding["chat_name"] = chat_name
        if chat_mode:
            binding["chat_mode"] = chat_mode
        binding["updated_at"] = time.time()

        self.chat_bindings[chat_id] = binding
        self._save()
        return binding

    def get_topic_session(self, chat_id: str, topic_id: str, chat_name: str = "", chat_mode: str = "topic", first_prompt: str = "") -> Dict[str, Any]:
        """
        Retrieves or creates an isolated session for a specific topic in a channel.
        Inherits project workspace from the parent channel binding.
        """
        self._load()
        binding = self.bind_chat(chat_id, chat_name=chat_name, chat_mode=chat_mode)
        session_key = f"topic:{chat_id}:{topic_id}"

        if session_key not in self.sessions:
            title = first_prompt.strip().replace("\n", " ")[:30] if first_prompt else "新话题会话"
            self.sessions[session_key] = {
                "chat_id": session_key,
                "parent_chat_id": chat_id,
                "topic_id": topic_id,
                "topic_title": title,
                "channel_name": binding.get("chat_name", chat_name or "项目频道"),
                "project_name": binding.get("project_name", "default"),
                "project_dir": binding.get("project_dir", self.default_root),
                "conversation_id": None,
                "conversations": [],
                "history": [],
                "created_at": time.time(),
                "updated_at": time.time()
            }
            self._save()
        else:
            sess = self.sessions[session_key]
            sess.setdefault("parent_chat_id", chat_id)
            sess.setdefault("topic_id", topic_id)
            sess.setdefault("topic_title", "话题会话")
            sess.setdefault("channel_name", binding.get("chat_name", chat_name or "项目频道"))
            sess.setdefault("conversations", [])
            sess.setdefault("history", [])
            # If the parent channel was manually rebound, update session project
            if binding.get("custom_bound") and sess.get("project_name") != binding.get("project_name"):
                sess["project_name"] = binding["project_name"]
                sess["project_dir"] = binding["project_dir"]

        return self.sessions[session_key]

    def get_session(self, session_key: str, parent_chat_id: Optional[str] = None) -> Dict[str, Any]:
        """Direct chat session or general lookup."""
        self._load()
        if session_key not in self.sessions:
            proj_name, proj_dir = "default", self.default_root
            if parent_chat_id and parent_chat_id in self.chat_bindings:
                proj_name = self.chat_bindings[parent_chat_id].get("project_name", "default")
                proj_dir = self.chat_bindings[parent_chat_id].get("project_dir", self.default_root)

            self.sessions[session_key] = {
                "chat_id": session_key,
                "parent_chat_id": parent_chat_id,
                "project_name": proj_name,
                "project_dir": proj_dir,
                "conversation_id": None,
                "conversations": [],
                "history": [],
                "updated_at": time.time()
            }
            self._save()
        else:
            self.sessions[session_key].setdefault("conversations", [])
            self.sessions[session_key].setdefault("history", [])
        return self.sessions[session_key]

    def set_project(self, session_key: str, project_name: str, parent_chat_id: Optional[str] = None) -> Dict[str, Any]:
        """Sets project for current session, and parent channel if in group."""
        sess = self.get_session(session_key, parent_chat_id)
        clean_name = os.path.basename(project_name.strip())
        target_dir = self.resolve_project_dir(clean_name)
        os.makedirs(target_dir, exist_ok=True)

        self.archive_current_conversation(session_key)
        sess["project_name"] = clean_name
        sess["project_dir"] = target_dir
        sess["conversation_id"] = None
        sess["updated_at"] = time.time()

        if parent_chat_id:
            # Bind parent channel
            self.bind_chat(parent_chat_id, project_name=clean_name)

        self._save()
        return sess

    def archive_current_conversation(self, session_key: str):
        sess = self.get_session(session_key)
        current_id = sess.get("conversation_id")
        if not current_id:
            return

        for c in sess["conversations"]:
            if c["id"] == current_id:
                return

        title = sess["history"][0].get("prompt", "会话")[:30] if sess.get("history") else "未命名会话"
        sess["conversations"].insert(0, {
            "id": current_id,
            "title": title,
            "project_name": sess["project_name"],
            "turn_count": len(sess.get("history", [])),
            "archived_at": time.strftime("%Y-%m-%d %H:%M:%S")
        })
        sess["conversations"] = sess["conversations"][:20]

    def reset_conversation(self, session_key: str) -> Dict[str, Any]:
        sess = self.get_session(session_key)
        self.archive_current_conversation(session_key)
        sess["conversation_id"] = None
        sess["history"] = []
        sess["updated_at"] = time.time()
        self._save()
        return sess

    def update_conversation(self, session_key: str, conversation_id: str, user_prompt: str = ""):
        sess = self.get_session(session_key)
        sess["conversation_id"] = conversation_id
        sess["updated_at"] = time.time()
        if user_prompt:
            hist = sess.setdefault("history", [])
            hist.append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "prompt": user_prompt[:80]})
            if len(hist) > 30:
                sess["history"] = hist[-30:]
        self._save()

    def get_history_list(self, session_key: str, limit: int = 8) -> List[Dict[str, Any]]:
        self._load()
        sess = self.get_session(session_key)
        self.archive_current_conversation(session_key)
        results = []
        cur_id = sess.get("conversation_id")

        for c in sess.get("conversations", []):
            cid = c.get("id")
            if cid:
                results.append({
                    "id": cid,
                    "title": c.get("title", "历史对话"),
                    "time": c.get("archived_at", ""),
                    "turn_count": c.get("turn_count", 1),
                    "is_current": (cid == cur_id),
                    "source": "飞书"
                })
        return results[:limit]

    def switch_to_conversation(self, session_key: str, target: str) -> Optional[Dict[str, Any]]:
        history = self.get_history_list(session_key, limit=20)
        target_conv = None

        if target.isdigit():
            idx = int(target) - 1
            if 0 <= idx < len(history):
                target_conv = history[idx]
        else:
            clean = target.strip()
            for h in history:
                if h["id"].startswith(clean):
                    target_conv = h
                    break

        if target_conv:
            sess = self.get_session(session_key)
            self.archive_current_conversation(session_key)
            sess["conversation_id"] = target_conv["id"]
            sess["updated_at"] = time.time()
            self._save()
            return target_conv
        return None

    def list_projects(self) -> List[str]:
        projects = ["default"]
        projects.extend(self.list_available_workspaces())
        return sorted(list(set(projects)))
