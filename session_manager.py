import os
import re
import json
import time
import uuid
import threading
from typing import Dict, Any, List, Optional, Tuple


class SessionManager:
    """
    Thread-safe Session & Workspace Manager with support for Feishu Team Channel-to-Project,
    Topic-to-Session tree structure mapping, and strict per-case directory & context isolation.
    """
    # Auto-expire an unarchived case if idle for > 12 hours when new files are uploaded
    CASE_IDLE_RESET_SECONDS = 12 * 3600

    def __init__(self, storage_path: str, default_root: str, bindings_path: Optional[str] = None):
        self._lock = threading.RLock()
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
        with self._lock:
            if os.path.exists(self.storage_path):
                try:
                    with open(self.storage_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            self.sessions = data
                except Exception as e:
                    print(f"[SessionManager] Load sessions failed: {e}")

            if os.path.exists(self.bindings_path):
                try:
                    with open(self.bindings_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            self.chat_bindings = data
                except Exception as e:
                    print(f"[SessionManager] Load bindings failed: {e}")

    @staticmethod
    def _atomic_write_json(path: str, data: Any):
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        tmp_path = f"{path}.tmp.{os.getpid()}.{threading.get_ident()}"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    def _save(self):
        with self._lock:
            try:
                self._atomic_write_json(self.storage_path, self.sessions)
            except Exception as e:
                print(f"[SessionManager] Save sessions failed: {e}")

            try:
                self._atomic_write_json(self.bindings_path, self.chat_bindings)
            except Exception as e:
                print(f"[SessionManager] Save bindings failed: {e}")

    def _new_case_dir(self, base_project_dir: str) -> str:
        """Creates a fresh, physically isolated directory for a single case."""
        ts = time.strftime("%Y%m%d_%H%M%S")
        short_id = uuid.uuid4().hex[:6]
        case_dir = os.path.join(base_project_dir, "cases", f"case_{ts}_{short_id}")
        os.makedirs(case_dir, exist_ok=True)
        return case_dir

    def _ensure_case_dir_unlocked(self, sess: Dict[str, Any]) -> str:
        """Ensures the session has an existing isolated case_dir on disk."""
        base_dir = sess.get("project_dir") or self.default_root
        case_dir = sess.get("case_dir")
        if not case_dir or not os.path.isdir(case_dir):
            case_dir = self._new_case_dir(base_dir)
            sess["case_dir"] = case_dir
        return case_dir

    def get_or_create_case_dir(self, session_key: str, for_upload: bool = False) -> str:
        with self._lock:
            self._load()
            sess = self._get_session_unlocked(session_key)
            now = time.time()
            last_updated = sess.get("updated_at", 0)
            is_stale = bool(last_updated and (now - last_updated > self.CASE_IDLE_RESET_SECONDS))
            if for_upload and (sess.get("case_status") == "concluded" or is_stale):
                reason = "上一案件已结案归档" if sess.get("case_status") == "concluded" else "距离上次操作已超过12小时"
                print(f"[SessionManager] {reason}，检测到新文件上传，自动开启全新案件隔离目录")
                self._archive_current_conversation_unlocked(sess)
                sess["conversation_id"] = None
                sess["history"] = []
                sess["pending_files"] = []
                sess["case_dir"] = self._new_case_dir(sess.get("project_dir") or self.default_root)
                sess["case_status"] = "in_progress"
                sess["updated_at"] = now
            else:
                self._ensure_case_dir_unlocked(sess)
            self._save()
            return sess["case_dir"]

    def list_available_workspaces(self) -> List[str]:
        """Lists local project folders in home_dir."""
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
        """
        clean = re.sub(r"[#\[\]【】()（）📁🤖💬🚀\s]+", " ", raw_name).strip()
        if not clean:
            return ("default", self.default_root)

        available = self.list_available_workspaces()
        clean_lower = clean.lower()

        for d in available:
            if clean_lower == d.lower():
                return (d, os.path.join(self.home_dir, d))

        matches = []
        for d in available:
            if d.lower() in clean_lower or clean_lower in d.lower():
                matches.append(d)
        if matches:
            best = max(matches, key=len)
            return (best, os.path.join(self.home_dir, best))

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

    def _bind_chat_unlocked(self, chat_id: str, chat_name: str = "", project_name: Optional[str] = None, chat_mode: str = "group") -> Dict[str, Any]:
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
        return binding

    def bind_chat(self, chat_id: str, chat_name: str = "", project_name: Optional[str] = None, chat_mode: str = "group") -> Dict[str, Any]:
        """Binds a channel/group to a project workspace directory."""
        with self._lock:
            self._load()
            binding = self._bind_chat_unlocked(chat_id, chat_name=chat_name, project_name=project_name, chat_mode=chat_mode)
            self._save()
            return dict(binding)

    def get_topic_session(self, chat_id: str, topic_id: str, chat_name: str = "", chat_mode: str = "topic", first_prompt: str = "") -> Dict[str, Any]:
        """
        Retrieves or creates an isolated session for a specific topic in a channel.
        Inherits project workspace from the parent channel binding.
        """
        with self._lock:
            self._load()
            binding = self._bind_chat_unlocked(chat_id, chat_name=chat_name, chat_mode=chat_mode)
            session_key = f"topic:{chat_id}:{topic_id}"

            if session_key not in self.sessions:
                title = first_prompt.strip().replace("\n", " ")[:30] if first_prompt else "新话题会话"
                proj_dir = binding.get("project_dir", self.default_root)
                self.sessions[session_key] = {
                    "chat_id": session_key,
                    "parent_chat_id": chat_id,
                    "topic_id": topic_id,
                    "topic_title": title,
                    "channel_name": binding.get("chat_name", chat_name or "项目频道"),
                    "project_name": binding.get("project_name", "default"),
                    "project_dir": proj_dir,
                    "case_dir": self._new_case_dir(proj_dir),
                    "conversation_id": None,
                    "conversations": [],
                    "history": [],
                    "pending_files": [],
                    "case_status": "idle",
                    "created_at": time.time(),
                    "updated_at": time.time()
                }
            else:
                sess = self.sessions[session_key]
                sess.setdefault("parent_chat_id", chat_id)
                sess.setdefault("topic_id", topic_id)
                sess.setdefault("topic_title", "话题会话")
                sess.setdefault("channel_name", binding.get("chat_name", chat_name or "项目频道"))
                sess.setdefault("conversations", [])
                sess.setdefault("history", [])
                sess.setdefault("pending_files", [])
                if binding.get("custom_bound") and sess.get("project_name") != binding.get("project_name"):
                    sess["project_name"] = binding["project_name"]
                    sess["project_dir"] = binding["project_dir"]
                self._ensure_case_dir_unlocked(sess)

            self._save()
            return dict(self.sessions[session_key])

    def _get_session_unlocked(self, session_key: str, parent_chat_id: Optional[str] = None) -> Dict[str, Any]:
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
                "case_dir": self._new_case_dir(proj_dir),
                "conversation_id": None,
                "conversations": [],
                "history": [],
                "pending_files": [],
                "case_status": "idle",
                "updated_at": time.time()
            }
        else:
            sess = self.sessions[session_key]
            sess.setdefault("conversations", [])
            sess.setdefault("history", [])
            sess.setdefault("pending_files", [])
            self._ensure_case_dir_unlocked(sess)
        return self.sessions[session_key]

    def get_session(self, session_key: str, parent_chat_id: Optional[str] = None) -> Dict[str, Any]:
        """Direct chat session or general lookup."""
        with self._lock:
            self._load()
            existed = session_key in self.sessions
            had_case_dir = existed and bool(self.sessions[session_key].get("case_dir")) and os.path.isdir(self.sessions[session_key].get("case_dir", ""))
            sess = self._get_session_unlocked(session_key, parent_chat_id)
            if not existed or not had_case_dir:
                self._save()
            return dict(sess)

    def _archive_current_conversation_unlocked(self, sess: Dict[str, Any]):
        current_id = sess.get("conversation_id")
        if not current_id:
            return

        convs = sess.setdefault("conversations", [])
        for c in convs:
            if c.get("id") == current_id:
                return

        hist = sess.get("history", [])
        title = hist[0].get("prompt", "会话")[:30] if hist else "未命名会话"
        convs.insert(0, {
            "id": current_id,
            "title": title,
            "project_name": sess.get("project_name", "default"),
            "case_dir": sess.get("case_dir", ""),
            "turn_count": len(hist),
            "archived_at": time.strftime("%Y-%m-%d %H:%M:%S")
        })
        sess["conversations"] = convs[:20]

    def archive_current_conversation(self, session_key: str):
        with self._lock:
            self._load()
            sess = self._get_session_unlocked(session_key)
            self._archive_current_conversation_unlocked(sess)
            self._save()

    def set_project(self, session_key: str, project_name: str, parent_chat_id: Optional[str] = None) -> Dict[str, Any]:
        """Sets project for current session, and parent channel if in group."""
        with self._lock:
            self._load()
            sess = self._get_session_unlocked(session_key, parent_chat_id)
            clean_name = os.path.basename(project_name.strip())
            target_dir = self.resolve_project_dir(clean_name)
            os.makedirs(target_dir, exist_ok=True)

            self._archive_current_conversation_unlocked(sess)
            sess["project_name"] = clean_name
            sess["project_dir"] = target_dir
            sess["case_dir"] = self._new_case_dir(target_dir)
            sess["conversation_id"] = None
            sess["history"] = []
            sess["pending_files"] = []
            sess["case_status"] = "idle"
            sess["updated_at"] = time.time()

            if parent_chat_id:
                self._bind_chat_unlocked(parent_chat_id, project_name=clean_name)

            self._save()
            return dict(sess)

    def clear_pending_files(self, session_key: str):
        with self._lock:
            self._load()
            if session_key in self.sessions:
                sess = self.sessions[session_key]
                sess["pending_files"] = []
                sess["updated_at"] = time.time()
                self._save()

    def add_pending_file(self, session_key: str, file_info: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Adds an uploaded file to the current case's material list.
        Only resets conversation if the session was explicitly concluded/archived or idle > 12h.
        """
        with self._lock:
            self._load()
            sess = self._get_session_unlocked(session_key)
            now = time.time()
            last_updated = sess.get("updated_at", 0)
            is_stale = bool(last_updated and (now - last_updated > self.CASE_IDLE_RESET_SECONDS))

            sess["case_status"] = "in_progress"
            self._ensure_case_dir_unlocked(sess)
            pending = sess.setdefault("pending_files", [])
            # Avoid duplicate entries if same file_key or path is added
            existing_paths = {item.get("path") for item in pending if item.get("path")}
            if file_info.get("path") not in existing_paths:
                pending.append(file_info)
            sess["updated_at"] = now
            self._save()
            return list(pending)

    def reset_conversation(self, session_key: str, status: str = "idle") -> Dict[str, Any]:
        """
        Completely resets the current case/conversation:
        - Archives the previous conversation_id into history
        - Clears conversation_id, history, and pending_files
        - Creates a brand-new isolated case_dir on disk so the LLM cannot see old files
        """
        with self._lock:
            self._load()
            sess = self._get_session_unlocked(session_key)
            self._archive_current_conversation_unlocked(sess)
            base_dir = sess.get("project_dir") or self.default_root
            sess["conversation_id"] = None
            sess["history"] = []
            sess["pending_files"] = []
            sess["case_dir"] = self._new_case_dir(base_dir)
            sess["case_status"] = status
            sess["updated_at"] = time.time()
            self._save()
            return dict(sess)

    def update_conversation(self, session_key: str, conversation_id: str, user_prompt: str = ""):
        with self._lock:
            self._load()
            sess = self._get_session_unlocked(session_key)
            sess["conversation_id"] = conversation_id
            sess["case_status"] = "in_progress"
            sess["updated_at"] = time.time()
            if user_prompt:
                hist = sess.setdefault("history", [])
                hist.append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "prompt": user_prompt[:80]})
                if len(hist) > 30:
                    sess["history"] = hist[-30:]
            self._save()

    def get_history_list(self, session_key: str, limit: int = 8) -> List[Dict[str, Any]]:
        with self._lock:
            self._load()
            sess = self._get_session_unlocked(session_key)
            self._archive_current_conversation_unlocked(sess)
            self._save()
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
        with self._lock:
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
                self._load()
                sess = self._get_session_unlocked(session_key)
                self._archive_current_conversation_unlocked(sess)
                sess["conversation_id"] = target_conv["id"]
                sess["updated_at"] = time.time()
                self._save()
                return target_conv
            return None

    def set_model(self, session_key: str, model_name: Optional[str] = None) -> str:
        with self._lock:
            self._load()
            sess = self._get_session_unlocked(session_key)
            if model_name:
                sess["model"] = model_name
            else:
                sess.pop("model", None)
            sess["updated_at"] = time.time()
            self._save()
            return sess.get("model", "")

    def list_projects(self) -> List[str]:
        projects = ["default"]
        projects.extend(self.list_available_workspaces())
        return sorted(list(set(projects)))
