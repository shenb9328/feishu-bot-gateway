import os
import json
import time
from typing import Dict, Any, List, Optional

class SessionManager:
    """Lightweight self-contained session and workspace manager for Feishu bot."""
    def __init__(self, storage_path: str, default_root: str):
        self.storage_path = storage_path
        self.default_root = default_root
        self.home_dir = os.path.expanduser("~")
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self):
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    self.sessions = json.load(f)
            except Exception as e:
                print(f"[SessionManager] Load failed: {e}")
                self.sessions = {}

    def _save(self):
        try:
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(self.sessions, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[SessionManager] Save failed: {e}")

    def get_session(self, session_key: str, parent_chat_id: Optional[str] = None) -> Dict[str, Any]:
        self._load()
        if session_key not in self.sessions:
            proj_name, proj_dir = "default", self.default_root
            if parent_chat_id and parent_chat_id in self.sessions:
                proj_name = self.sessions[parent_chat_id].get("project_name", "default")
                proj_dir = self.sessions[parent_chat_id].get("project_dir", self.default_root)

            self.sessions[session_key] = {
                "chat_id": session_key,
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

    def set_project(self, session_key: str, project_name: str, parent_chat_id: Optional[str] = None) -> Dict[str, Any]:
        sess = self.get_session(session_key)
        clean_name = os.path.basename(project_name.strip())
        target_dir = self.resolve_project_dir(clean_name)
        os.makedirs(target_dir, exist_ok=True)

        self.archive_current_conversation(session_key)
        sess["project_name"] = clean_name
        sess["project_dir"] = target_dir
        sess["conversation_id"] = None
        sess["updated_at"] = time.time()

        if parent_chat_id and parent_chat_id != session_key:
            parent_sess = self.get_session(parent_chat_id)
            parent_sess["project_name"] = clean_name
            parent_sess["project_dir"] = target_dir
            parent_sess["updated_at"] = time.time()

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
        ignores = ["systemd", "node-compile-cache", "lo_profile", "pdf_check", "tmp"]
        def is_clean(name):
            return not name.startswith(".") and not any(ig in name.lower() for ig in ignores)

        for base in [self.home_dir, self.default_root]:
            if os.path.exists(base):
                for item in os.listdir(base):
                    if is_clean(item) and os.path.isdir(os.path.join(base, item)):
                        projects.append(item)
        return sorted(list(set(projects)))
