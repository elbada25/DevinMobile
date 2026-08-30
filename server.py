"""
Devin Mobile Dashboard v3

Arquitectura:
  - Lee sesiones e historial de sessions.db (read-only)
  - No escribe en sessions.db — el agente ACP persiste él mismo
  - Session Registry: un proceso ACP vivo por sesión activa
  - SSE con event IDs secuenciales + Last-Event-ID para resume
  - Handoff: mata el proceso del IDE con validación de nombre
  - session/load descarta el replay (el historial viene de la DB)
  - Maneja session/request_permission desde el móvil
"""
import json
import os
import queue as queue_mod
import re
import sqlite3
import subprocess
import threading
import time
from collections import deque
from ctypes import wintypes
from pathlib import Path
from typing import Optional

import psutil
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

# --- Config -----------------------------------------------------------------
HOST = "0.0.0.0"
PORT = int(os.environ.get("DEVIN_MOBILE_PORT", "8787"))
IS_WINDOWS = os.name == "nt"

if IS_WINDOWS:
    APPDATA = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
else:
    # Linux/Mac: usar ~/.config o ~/.local/share
    APPDATA = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))

# Buscar devin exe en ubicaciones conocidas (Windows y Linux)
def _find_devin_exe() -> str:
    if IS_WINDOWS:
        candidates = [
            os.path.join(os.environ.get("LOCALAPPDATA", ""),
                         "Programs", "Devin", "resources", "app", "extensions",
                         "windsurf", "devin", "bin", "devin.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""),
                         "devin", "cli", "bin", "devin.exe"),
        ]
    else:
        candidates = [
            os.path.join(str(Path.home()), ".local", "bin", "devin"),
            "/usr/local/bin/devin",
            "/usr/bin/devin",
        ]
    # Buscar en PATH
    import shutil as _shutil
    path_devin = _shutil.which("devin")
    if path_devin:
        candidates.insert(0, path_devin)
    for c in candidates:
        if os.path.isfile(c):
            return c
    return candidates[0] if candidates else "devin"

DEVIN_EXE = _find_devin_exe()

if IS_WINDOWS:
    CREDENTIALS = os.path.join(APPDATA, "devin", "credentials.toml")
    SESSION_LOCKS_DIR = os.path.join(APPDATA, "devin", "cli", "session_locks")
    DB_PATH = os.path.join(APPDATA, "devin", "cli", "sessions.db")
else:
    # Linux: el CLI oficial guarda creds en ~/.local/share/devin/credentials.toml
    CREDENTIALS = os.path.join(str(Path.home()), ".local", "share", "devin", "credentials.toml")
    SESSION_LOCKS_DIR = os.path.join(str(Path.home()), ".local", "share", "devin", "cli", "session_locks")
    DB_PATH = os.path.join(str(Path.home()), ".local", "share", "devin", "cli", "sessions.db")
WORKDIR = str(Path.home())
PROMPT_TIMEOUT = 600
MAX_RING_BUFFER = 500  # eventos en buffer para resume SSE

BASE_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = BASE_DIR / "config.json"
CONFIG_EXAMPLE = BASE_DIR / "config.json.example"
# Si no existe config.json, copiar desde example y avisar al usuario
if not CONFIG_PATH.exists():
    if CONFIG_EXAMPLE.exists():
        import shutil
        shutil.copy2(CONFIG_EXAMPLE, CONFIG_PATH)
        print("  AVISO: config.json creado desde config.json.example")
        print("  Edita config.json con tu usuario y contraseña antes de usar.")
    else:
        CONFIG_PATH.write_text('{"username":"admin","password":"cambia-esta-contrasena"}', encoding="utf-8")
        print("  AVISO: config.json creado con valores por defecto.")
        print("  Edita config.json con tu usuario y contraseña antes de usar.")
_config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
AUTH_USERNAME = _config.get("username", "")
AUTH_PASSWORD = _config.get("password", "")

# --- RestartManager API -----------------------------------------------------
_rstrtmgr = None
_RM_PROCESS_INFO = None

def _init_rm():
    global _rstrtmgr, _RM_PROCESS_INFO
    import ctypes as ct
    _rstrtmgr = ct.WinDLL("rstrtmgr")
    class RM_UNIQUE_PROCESS(ct.Structure):
        _fields_ = [("pid", wintypes.DWORD), ("StartTime", wintypes.FILETIME)]
    class RM_PROCESS_INFO(ct.Structure):
        _fields_ = [("Process", RM_UNIQUE_PROCESS), ("strAppName", wintypes.WCHAR * 256),
                     ("strServiceShortName", wintypes.WCHAR * 64),
                     ("ApplicationType", wintypes.DWORD), ("AppStatus", wintypes.DWORD),
                     ("TSSessionId", wintypes.DWORD), ("bRestartable", wintypes.BOOL)]
    _RM_PROCESS_INFO = RM_PROCESS_INFO
    _rstrtmgr.RmStartSession.restype = wintypes.DWORD
    _rstrtmgr.RmStartSession.argtypes = [ct.POINTER(wintypes.DWORD), wintypes.DWORD, ct.c_wchar_p]
    _rstrtmgr.RmRegisterResources.restype = wintypes.DWORD
    _rstrtmgr.RmRegisterResources.argtypes = [wintypes.DWORD, wintypes.UINT,
                                               ct.POINTER(ct.c_wchar_p), wintypes.UINT,
                                               ct.POINTER(wintypes.HANDLE), wintypes.UINT,
                                               ct.POINTER(ct.c_wchar_p)]
    _rstrtmgr.RmGetList.restype = wintypes.DWORD
    _rstrtmgr.RmGetList.argtypes = [wintypes.DWORD, ct.POINTER(wintypes.UINT),
                                     ct.POINTER(wintypes.UINT),
                                     ct.POINTER(RM_PROCESS_INFO),
                                     ct.POINTER(wintypes.DWORD)]
    _rstrtmgr.RmEndSession.restype = wintypes.DWORD
    _rstrtmgr.RmEndSession.argtypes = [wintypes.DWORD]

def _find_lock_holder_pid(lock_path: str) -> Optional[int]:
    if _rstrtmgr is None:
        _init_rm()
    import ctypes as ct
    session_handle = wintypes.DWORD(0)
    session_key = ct.create_unicode_buffer(256)
    if _rstrtmgr.RmStartSession(ct.byref(session_handle), 0, session_key) != 0:
        return None
    try:
        files = (ct.c_wchar_p * 1)(lock_path)
        if _rstrtmgr.RmRegisterResources(session_handle, 1, files, 0, None, 0, None) != 0:
            return None
        proc_count = wintypes.UINT(0)
        reboot_reasons = wintypes.DWORD(0)
        _rstrtmgr.RmGetList(session_handle, ct.byref(proc_count),
                            ct.byref(proc_count), None, ct.byref(reboot_reasons))
        if proc_count.value == 0:
            return None
        procs = (_RM_PROCESS_INFO * proc_count.value)()
        actual_count = wintypes.UINT(proc_count.value)
        _rstrtmgr.RmGetList(session_handle, ct.byref(actual_count),
                            ct.byref(proc_count), procs, ct.byref(reboot_reasons))
        for i in range(actual_count.value):
            p = procs[i]
            if p.strAppName and "devin" in p.strAppName.lower():
                return p.Process.pid
        if actual_count.value > 0:
            return procs[0].Process.pid
        return None
    finally:
        _rstrtmgr.RmEndSession(session_handle)

# --- Auth -------------------------------------------------------------------
def _check_auth(authorization: str | None):
    if not AUTH_USERNAME:
        return
    import base64
    if not authorization or not authorization.startswith("Basic "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        decoded = base64.b64decode(authorization[6:]).decode("utf-8")
        user, _, pwd = decoded.partition(":")
    except Exception:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if user != AUTH_USERNAME or pwd != AUTH_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")

# --- DB reader (read-only) --------------------------------------------------
def _db():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10)
    # No ejecutar PRAGMA journal_mode en read-only (es una escritura)
    conn.execute("PRAGMA busy_timeout=5000")
    return conn

def _is_session_active(session_id: str) -> tuple[bool, str]:
    lock_path = os.path.join(SESSION_LOCKS_DIR, f"{session_id}.lock")
    if not os.path.exists(lock_path):
        return False, "no lock"
    try:
        with open(lock_path, "r") as f:
            pid_str = f.read().strip()
    except (PermissionError, OSError):
        return True, "lock held"
    try:
        pid = int(pid_str)
    except (ValueError, TypeError):
        return False, "invalid pid"
    try:
        proc = psutil.Process(pid)
        name = proc.name().lower()
        if "devin" in name or "windsurf" in name or "electron" in name:
            return True, f"pid {pid} ({proc.name()})"
        return False, f"pid {pid} not devin ({proc.name()})"
    except Exception:
        return False, f"pid {pid} dead"

def _time_ago(ts: int) -> str:
    diff = int(time.time()) - ts
    if diff < 60: return "ahora"
    if diff < 3600: return f"hace {diff//60}m"
    if diff < 86400: return f"hace {diff//3600}h"
    return f"hace {diff//86400}d"

def _list_sessions_db() -> list[dict]:
    conn = _db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, working_directory, backend_type, model, agent_mode,
               created_at, last_activity_at, title, hidden, metadata, workspace_dirs
        FROM sessions WHERE hidden=0 ORDER BY last_activity_at DESC
    """)
    sessions = []
    for r in cur.fetchall():
        sid = r[0]
        active, detail = _is_session_active(sid)
        ts = r[6]
        sessions.append({
            "id": sid, "working_directory": r[1], "model": r[3], "agent_mode": r[4],
            "created_at": r[5], "last_activity_at": ts,
            "last_activity_iso": time.strftime("%Y-%m-%d %H:%M",
                                               time.localtime(ts)) if ts else "",
            "last_activity_ago": _time_ago(ts) if ts else "",
            "title": r[7] or "(sin titulo)", "active": active,
            "status_detail": detail,
            "workspace_dirs": json.loads(r[10]) if r[10] else [],
        })
    conn.close()
    return sessions

def _list_archived_sessions() -> list[dict]:
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT id, title, last_activity_at FROM sessions WHERE hidden=1 ORDER BY last_activity_at DESC")
    sessions = []
    for r in cur.fetchall():
        sessions.append({"id": r[0], "title": r[1] or "(sin titulo)",
                         "last_activity_ago": _time_ago(r[2]) if r[2] else ""})
    conn.close()
    return sessions

def _get_tool_states(conn, session_id: str) -> dict:
    cur = conn.cursor()
    cur.execute("SELECT tool_call_id, tool_call_json, tool_call_update_json FROM tool_call_state WHERE session_id=?", (session_id,))
    tool_states = {}
    for r in cur.fetchall():
        tc_id = r[0]
        tc = json.loads(r[1]) if r[1] else {}
        tcu = json.loads(r[2]) if r[2] else {}
        tool_states[tc_id] = {"call": tc, "update": tcu}
    return tool_states

def _extract_tool_result(tc_update: dict) -> str:
    content = tc_update.get("content", [])
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                c = item.get("content", {})
                if isinstance(c, dict):
                    text = c.get("text", "")
                    if text:
                        return text[:500]
                    resource = c.get("resource", {})
                    if isinstance(resource, dict):
                        return resource.get("text", "")[:500]
    return ""

def _parse_message(chat_msg_json: str, node_id: int, ts: int, tool_states: dict) -> dict | None:
    try:
        msg = json.loads(chat_msg_json)
    except json.JSONDecodeError:
        return None
    role = msg.get("role", "")
    if role == "system":
        return None
    content = msg.get("content", "")
    tool_calls = msg.get("tool_calls", [])
    tool_call_id = msg.get("tool_call_id", "")
    time_str = time.strftime("%H:%M:%S", time.localtime(ts)) if ts else ""
    entry = {"role": role, "time": time_str, "node_id": node_id}
    if role == "user":
        entry["content"] = content[:5000]
    elif role == "assistant":
        entry["content"] = content[:5000] if content else ""
        if tool_calls:
            entry["tool_calls"] = []
            for tc in tool_calls:
                tc_id = tc.get("id", "")
                tc_info = tool_states.get(tc_id, {})
                tc_call = tc_info.get("call", {})
                tc_update = tc_info.get("update", {})
                entry["tool_calls"].append({
                    "id": tc_id, "name": tc.get("name", "?"),
                    "args": tc.get("arguments", {}),
                    "title": tc_call.get("title", ""),
                    "status": tc_update.get("status", ""),
                    "result": _extract_tool_result(tc_update),
                })
    elif role == "tool":
        entry["content"] = content[:3000]
        entry["tool_call_id"] = tool_call_id
    return entry

def _get_history_db(session_id: str, limit: int = 200, before_node: int | None = None) -> dict:
    conn = _db()
    tool_states = _get_tool_states(conn, session_id)
    cur = conn.cursor()
    if before_node is not None:
        cur.execute("""
            SELECT node_id, chat_message, created_at FROM message_nodes
            WHERE session_id=? AND node_id < ? ORDER BY node_id DESC LIMIT ?
        """, (session_id, before_node, limit))
    else:
        cur.execute("""
            SELECT node_id, chat_message, created_at FROM message_nodes
            WHERE session_id=? ORDER BY node_id DESC LIMIT ?
        """, (session_id, limit))
    rows = cur.fetchall()
    # Total count for pagination
    cur.execute("SELECT COUNT(*) FROM message_nodes WHERE session_id=?", (session_id,))
    total = cur.fetchone()[0]
    conn.close()
    messages = []
    for row in reversed(rows):
        node_id, chat_msg, ts = row
        entry = _parse_message(chat_msg, node_id, ts, tool_states)
        if entry:
            messages.append(entry)
    has_more = total > len(messages) and (before_node is None or len(rows) == limit)
    return {"messages": messages, "total": total, "has_more": has_more,
            "oldest_node_id": messages[0]["node_id"] if messages else None}

# --- DB writes (session management only, not messages) ----------------------
def _db_write():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn

def _rename_session(session_id: str, title: str):
    conn = _db_write()
    conn.execute("UPDATE sessions SET title=? WHERE id=?", (title, session_id))
    conn.commit()
    conn.close()

def _archive_session(session_id: str):
    conn = _db_write()
    conn.execute("UPDATE sessions SET hidden=1 WHERE id=?", (session_id,))
    conn.commit()
    conn.close()

def _unarchive_session(session_id: str):
    conn = _db_write()
    conn.execute("UPDATE sessions SET hidden=0 WHERE id=?", (session_id,))
    conn.commit()
    conn.close()

# --- Handoff ----------------------------------------------------------------
def _validate_devin_process(pid: int) -> bool:
    """Valida que el PID corresponde a un proceso de Devin/Windsurf/Electron."""
    try:
        proc = psutil.Process(pid)
        name = proc.name().lower()
        cmdline = " ".join(proc.cmdline()).lower()
        return ("devin" in name or "windsurf" in name or "electron" in name
                or "devin" in cmdline or "windsurf" in cmdline)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False

def _kill_session_agent(session_id: str) -> dict:
    """Mata el proceso que tiene el lock de la sesión, con validación."""
    lock_path = os.path.join(SESSION_LOCKS_DIR, f"{session_id}.lock")
    pid = None
    try:
        with open(lock_path, "r") as f:
            pid = int(f.read().strip())
    except (PermissionError, OSError, ValueError, TypeError):
        pid = _find_lock_holder_pid(lock_path)
    if pid is None:
        # No hay lock o no se pudo identificar — asumimos que está libre
        try:
            os.remove(lock_path)
        except (OSError, FileNotFoundError):
            pass
        return {"ok": True, "killed_pid": None, "message": "no lock holder"}
    # Validar que es un proceso de Devin antes de matar
    if not _validate_devin_process(pid):
        # El PID podría haber sido reusado por otro proceso — no matar
        # Pero el lock es stale, lo borramos
        try:
            os.remove(lock_path)
        except (OSError, FileNotFoundError):
            pass
        return {"ok": True, "killed_pid": None, "message": "stale lock (pid not devin)"}
    try:
        proc = psutil.Process(pid)
        proc.kill()
        proc.wait(timeout=5)
    except psutil.NoSuchProcess:
        pass
    except psutil.TimeoutExpired:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            return {"ok": False, "error": f"pid {pid} no termino"}
    time.sleep(0.5)
    try:
        os.remove(lock_path)
    except (PermissionError, OSError, FileNotFoundError):
        pass
    return {"ok": True, "killed_pid": pid}

def _release_session_lock(session_id: str):
    """Libera el lock de una sesión sin matar procesos (para release explicito)."""
    lock_path = os.path.join(SESSION_LOCKS_DIR, f"{session_id}.lock")
    try:
        with open(lock_path, "r") as f:
            pid_str = f.read().strip()
        try:
            pid = int(pid_str)
            if _validate_devin_process(pid):
                proc = psutil.Process(pid)
                proc.kill()
                proc.wait(timeout=3)
        except (ValueError, psutil.NoSuchProcess, psutil.TimeoutExpired):
            pass
    except (PermissionError, OSError, FileNotFoundError):
        pass
    try:
        os.remove(lock_path)
    except (PermissionError, OSError, FileNotFoundError):
        pass

# --- ACP Client (JSON-RPC over stdio) ---------------------------------------
def _clean_env() -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("WINDSURF_")}
    env["PATH"] = os.path.dirname(DEVIN_EXE) + os.pathsep + env.get("PATH", "")
    # Inyectar la API key como variable de entorno para que el ACP no
    # abra el navegador para OAuth. El ACP lee WINDSURF_API_KEY si existe.
    api_key, _ = _read_api_key()
    if api_key:
        env["WINDSURF_API_KEY"] = api_key
    return env

def _read_api_key() -> tuple[str, str]:
    api_key = None
    api_server_url = "https://server.codeium.com"
    try:
        with open(CREDENTIALS, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("windsurf_api_key"):
                    api_key = line.split("=", 1)[1].strip().strip('"')
                elif line.startswith("api_server_url"):
                    api_server_url = line.split("=", 1)[1].strip().strip('"')
    except Exception:
        pass
    return api_key, api_server_url

class ACPClient:
    """Cliente ACP que lanza devin acp y se comunica via JSON-RPC sobre stdio."""

    def __init__(self, model: str = ""):
        self.proc = None
        self._id = 0
        self._queue = None
        self._reader_thread = None
        self._notifications = []
        self._lock = threading.Lock()
        self._authenticated = False
        self._model = model

    def start(self):
        self._queue = queue_mod.Queue()
        env = _clean_env()
        cmd = [DEVIN_EXE, "acp"]
        if self._model:
            cmd += ["--model", self._model]
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=env, cwd=WORKDIR,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        def reader():
            while True:
                try:
                    line = self.proc.stdout.readline()
                except Exception:
                    break
                if not line:
                    break
                try:
                    self._queue.put(json.loads(line))
                except json.JSONDecodeError:
                    pass
        self._reader_thread = threading.Thread(target=reader, daemon=True)
        self._reader_thread.start()
        resp = self._request("initialize", {
            "protocolVersion": 1, "capabilities": {},
            "clientInfo": {"name": "devin-mobile", "version": "3.0"},
        }, timeout=30)
        if "error" in resp:
            raise RuntimeError(f"ACP initialize failed: {resp.get('error', '')}")
        # Check auth methods from initialize response
        auth_methods = []
        if "result" in resp:
            auth_methods = resp["result"].get("authMethods", [])
        auth_method_ids = [m.get("id", "") for m in auth_methods]
        # Try windsurf-api-key auth (Devin Desktop / older CLI)
        if "windsurf-api-key" in auth_method_ids:
            api_key, api_server_url = _read_api_key()
            if api_key:
                auth_resp = self._request("authenticate", {
                    "methodId": "windsurf-api-key",
                    "meta": {"api_key": api_key, "api_server_url": api_server_url},
                }, timeout=30)
                if "error" not in auth_resp:
                    self._authenticated = True
                else:
                    print(f"  ACP authenticate warning: {auth_resp.get('error', '')}", flush=True)
                    self._authenticated = True
            else:
                self._authenticated = True
        else:
            # Newer CLI (v3000+): only supports devin-browser auth.
            # NO llamar authenticate — abre el navegador.
            # session/list y session/new funcionan sin autenticar si el CLI
            # tiene credenciales stored válidas.
            self._authenticated = True
        return resp

    def _next_id(self):
        self._id += 1
        return self._id

    def _send(self, msg):
        with self._lock:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()

    def _read_msg(self, timeout=30):
        try:
            return self._queue.get(timeout=timeout)
        except Exception:
            return None

    def _request(self, method: str, params: dict = None, timeout: int = 30) -> dict:
        rid = self._next_id()
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = max(1, int(deadline - time.time()))
            msg = self._read_msg(timeout=min(remaining, 60))
            if msg is None:
                continue
            if msg.get("id") == rid:
                return msg
            elif "method" in msg and "id" not in msg:
                self._notifications.append(msg)
        return {"error": "timeout"}

    def drain_notifications(self) -> list[dict]:
        notifs = self._notifications[:]
        self._notifications.clear()
        return notifs

    def load_session(self, session_id: str, cwd: str = WORKDIR) -> dict:
        return self._request("session/load", {
            "sessionId": session_id, "cwd": cwd, "mcpServers": [],
        }, timeout=120)

    def new_session(self, prompt: str, cwd: str = WORKDIR) -> dict:
        params = {
            "cwd": cwd,
            "mcpServers": [],
        }
        if prompt:
            params["prompt"] = [{"type": "text", "text": prompt}]
        return self._request("session/new", params, timeout=120)

    def prompt(self, session_id: str, text: str) -> dict:
        return self._request("session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": text}],
        }, timeout=PROMPT_TIMEOUT)

    def cancel(self, session_id: str):
        self._send({"jsonrpc": "2.0", "method": "session/cancel",
                    "params": {"sessionId": session_id}})

    def read_update(self, timeout: int = 30) -> dict | None:
        return self._read_msg(timeout)

    def stop(self):
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

# --- Session Registry -------------------------------------------------------
class SessionContext:
    """Estado de una sesión activa en el registry."""
    def __init__(self, session_id: str, client: ACPClient):
        self.session_id = session_id
        self.client = client
        self.event_id = 0
        self.ring_buffer: deque = deque(maxlen=MAX_RING_BUFFER)
        self.subscribers: list[queue_mod.Queue] = []
        self.lock = threading.Lock()
        self.status = "idle"  # idle, processing, crashed
        self.permission_requests: dict = {}  # tool_call_id -> {prompt, timestamp}

    def next_event_id(self) -> int:
        self.event_id += 1
        return self.event_id

    def emit(self, event: dict):
        """Asigna event_id, guarda en ring buffer, y notifica a subscribers."""
        eid = self.next_event_id()
        event["eid"] = eid
        self.ring_buffer.append(event)
        for sub in self.subscribers:
            try:
                sub.put_nowait(event)
            except queue_mod.Full:
                pass  # subscriber lento, descarta

    def subscribe(self) -> queue_mod.Queue:
        q = queue_mod.Queue(maxsize=1000)
        self.subscribers.append(q)
        return q

    def unsubscribe(self, q: queue_mod.Queue):
        try:
            self.subscribers.remove(q)
        except ValueError:
            pass

    def get_events_since(self, last_eid: int) -> list[dict]:
        return [e for e in self.ring_buffer if e.get("eid", 0) > last_eid]

class SessionRegistry:
    """Mantiene procesos ACP vivos por sesión activa."""
    def __init__(self):
        self._sessions: dict[str, SessionContext] = {}
        self._lock = threading.Lock()

    def get_or_create(self, session_id: str, model: str = "") -> SessionContext:
        with self._lock:
            ctx = self._sessions.get(session_id)
            if ctx and ctx.client.is_alive():
                return ctx
            if ctx:
                try:
                    ctx.client.stop()
                except Exception:
                    pass
                del self._sessions[session_id]
            # Crear nuevo cliente ACP
            client = ACPClient(model=model)
            client.start()
            ctx = SessionContext(session_id, client)
            self._sessions[session_id] = ctx
            return ctx

    def get(self, session_id: str) -> SessionContext | None:
        with self._lock:
            ctx = self._sessions.get(session_id)
            if ctx and ctx.client.is_alive():
                return ctx
            return None

    def remove(self, session_id: str):
        with self._lock:
            ctx = self._sessions.pop(session_id, None)
            if ctx:
                try:
                    ctx.client.stop()
                except Exception:
                    pass

    def stop_all(self):
        with self._lock:
            for ctx in self._sessions.values():
                try:
                    ctx.client.stop()
                except Exception:
                    pass
            self._sessions.clear()

    def list_active(self) -> list[str]:
        with self._lock:
            return [sid for sid, ctx in self._sessions.items() if ctx.client.is_alive()]

_registry = SessionRegistry()

# --- ACP notification mapping -----------------------------------------------
def _map_acp_notification(msg: dict) -> dict | None:
    if "method" not in msg:
        return None
    if msg["method"] != "session/update":
        return None
    params = msg.get("params", {})
    update = params.get("update", {})
    kind = update.get("sessionUpdate", "")
    event = {"type": "update", "kind": kind}

    if kind == "agent_message_chunk":
        event["content"] = update.get("content", {}).get("text", "")
    elif kind == "agent_message":
        event["content"] = update.get("content", {}).get("text", "")
        event["message_id"] = update.get("messageId", "")
    elif kind == "agent_thought_chunk":
        # NO enviar contenido de thoughts — solo señal de thinking
        event["thinking"] = True
    elif kind == "agent_thought":
        event["thinking"] = True
    elif kind == "tool_call":
        event["tool_call_id"] = update.get("toolCallId", "")
        event["title"] = update.get("title", "")
        event["status"] = update.get("status", "pending")
        event["kind_tool"] = update.get("kind", "other")
    elif kind == "tool_call_update":
        event["tool_call_id"] = update.get("toolCallId", "")
        event["title"] = update.get("title", "")
        event["status"] = update.get("status", "")
        event["kind_tool"] = update.get("kind", "")
    elif kind == "tool_call_content_chunk":
        event["tool_call_id"] = update.get("toolCallId", "")
        content = update.get("content", {})
        if isinstance(content, dict):
            event["content"] = content.get("text", str(content))[:500]
        else:
            event["content"] = str(content)[:500]
    elif kind == "state_update":
        event["state"] = update.get("state", "")
        event["stop_reason"] = update.get("stopReason", "")
        if update.get("state") == "idle":
            event["type"] = "done"
    elif kind == "plan_update":
        event["plan"] = update.get("plan", {})
    elif kind == "user_message":
        event["content"] = update.get("content", {}).get("text", "")
    elif kind == "session_info_update":
        event["title"] = update.get("title", "")
    else:
        event["raw"] = str(update)[:300]
    return event

# --- FastAPI app ------------------------------------------------------------
app = FastAPI(title="Devin Mobile Dashboard v3")

# CORS — permite que el frontend (servido desde un PC) haga requests a otros PCs
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def index():
    return HTMLResponse((BASE_DIR / "index.html").read_text(encoding="utf-8"))

@app.post("/api/login")
async def api_login(request: Request):
    body = await request.json()
    user = (body or {}).get("username", "")
    pwd = (body or {}).get("password", "")
    if user == AUTH_USERNAME and pwd == AUTH_PASSWORD:
        return {"ok": True}
    raise HTTPException(status_code=401, detail="Usuario o contrasena incorrectos")

@app.get("/api/sessions")
async def api_sessions(authorization: str | None = Header(None)):
    _check_auth(authorization)
    sessions = _list_sessions_db()
    # Marcar sesiones que están activas en el registry
    active_in_registry = _registry.list_active()
    for s in sessions:
        if s["id"] in active_in_registry:
            s["mobile_active"] = True
    return JSONResponse(sessions)

@app.get("/api/sessions/archived")
async def api_archived_sessions(authorization: str | None = Header(None)):
    _check_auth(authorization)
    return JSONResponse(_list_archived_sessions())

@app.get("/api/sessions/{session_id}/history")
async def api_history(session_id: str, authorization: str | None = Header(None),
                      limit: int = 200, before: int | None = None):
    _check_auth(authorization)
    return JSONResponse(_get_history_db(session_id, limit=limit, before_node=before))

@app.post("/api/sessions/{session_id}/rename")
async def api_rename(session_id: str, request: Request,
                     authorization: str | None = Header(None)):
    _check_auth(authorization)
    body = await request.json()
    title = (body or {}).get("title", "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title vacio")
    _rename_session(session_id, title)
    return {"ok": True}

@app.post("/api/sessions/{session_id}/archive")
async def api_archive(session_id: str, authorization: str | None = Header(None)):
    _check_auth(authorization)
    _archive_session(session_id)
    return {"ok": True}

@app.post("/api/sessions/{session_id}/unarchive")
async def api_unarchive(session_id: str, authorization: str | None = Header(None)):
    _check_auth(authorization)
    _unarchive_session(session_id)
    return {"ok": True}

@app.post("/api/sessions/{session_id}/release")
async def api_release(session_id: str, authorization: str | None = Header(None)):
    _check_auth(authorization)
    # Detener el proceso ACP del registry si existe
    _registry.remove(session_id)
    # Liberar el lock
    _release_session_lock(session_id)
    return {"ok": True}

@app.post("/api/sessions/{session_id}/cancel")
async def api_cancel(session_id: str, authorization: str | None = Header(None)):
    _check_auth(authorization)
    ctx = _registry.get(session_id)
    if ctx:
        ctx.client.cancel(session_id)
        return {"ok": True}
    return {"ok": False, "error": "session not active in registry"}

@app.post("/api/sessions/{session_id}/permission")
async def api_permission(session_id: str, request: Request,
                         authorization: str | None = Header(None)):
    """Responde a un request_permission del agente."""
    _check_auth(authorization)
    body = await request.json()
    # El frontend envía {call_id, outcome: "allow"|"deny"}
    # Esto se maneja en el stream via el response queue del ACP client
    ctx = _registry.get(session_id)
    if ctx:
        ctx.permission_requests[body.get("call_id", "")] = body.get("outcome", "deny")
        return {"ok": True}
    return {"ok": False, "error": "session not active"}

@app.post("/api/sessions/new")
async def api_new_session(request: Request, authorization: str | None = Header(None)):
    _check_auth(authorization)
    body = await request.json()
    prompt = (body or {}).get("prompt", "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt vacio")
    cwd = (body or {}).get("cwd", "").strip() or WORKDIR
    model = (body or {}).get("model", "").strip()
    try:
        ctx = _registry.get_or_create("__new__", model=model)
        client = ctx.client
        # Crear sesion SIN prompt — el prompt se enviara via stream
        # Esto mantiene el proceso ACP vivo
        resp = client.new_session("", cwd=cwd)
        result = resp.get("result", {})
        sid = result.get("sessionId")
        if not sid:
            return {"ok": False, "error": "no sessionId in response"}
        # Drenar notificaciones iniciales del session/new
        client.drain_notifications()
        # Mover el contexto al session_id real SIN parar el ACP process
        with _registry._lock:
            _registry._sessions.pop("__new__", None)  # Remover sin stop()
        ctx.session_id = sid
        ctx.pending_prompt = prompt  # Guardar prompt para el stream
        ctx.is_newly_created = True  # No recargar en el stream
        with _registry._lock:
            _registry._sessions[sid] = ctx
        return {"ok": True, "session_id": sid, "title": "Nueva sesion"}
    except Exception as e:
        _registry.remove("__new__")
        return {"ok": False, "error": str(e)[:200]}

@app.get("/api/sessions/{session_id}/stream")
async def api_stream(session_id: str, request: Request,
                     authorization: str | None = Header(None)):
    """SSE endpoint: handoff, load session, prompt, stream events con IDs."""
    _check_auth(authorization)
    prompt = request.query_params.get("prompt", "").strip()
    # Si no hay prompt en query, usar pending_prompt del contexto (sesion nueva)
    if not prompt:
        ctx_check = _registry.get(session_id)
        if ctx_check and getattr(ctx_check, "is_newly_created", False):
            prompt = getattr(ctx_check, "pending_prompt", "")
            if prompt:
                ctx_check.pending_prompt = ""
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt vacio")

    # Last-Event-ID para resume
    last_eid_str = request.headers.get("Last-Event-ID", "")
    last_eid = int(last_eid_str) if last_eid_str.isdigit() else 0

    def event_stream():
        try:
            # 1. Handoff si la sesión está bloqueada por el IDE
            # Saltar handoff para sesiones nuevas creadas via ACP
            ctx_check = _registry.get(session_id)
            is_new = ctx_check and getattr(ctx_check, "is_newly_created", False)
            if is_new:
                active = False
            else:
                active, detail = _is_session_active(session_id)
            if active:
                # Si no se fuerza el handoff, preguntar al usuario
                force = request.query_params.get("force", "0") == "1"
                if not force:
                    yield f"data: {json.dumps({'type': 'handoff_confirm', 'message': 'Esta sesion esta activa en Devin Desktop. Si continuas, se cerrara el agente del IDE. Continuar?', 'detail': detail}, ensure_ascii=False)}\n\n"
                    return
                kill_info = _kill_session_agent(session_id)
                yield f"data: {json.dumps({'type': 'handoff', 'data': kill_info}, ensure_ascii=False)}\n\n"
                if not kill_info.get("ok"):
                    yield f"data: {json.dumps({'type': 'error', 'message': kill_info.get('error', 'error')})}\n\n"
                    return
                time.sleep(1)

            # 2. Obtener o crear contexto de sesión (proceso ACP persistente)
            yield f"data: {json.dumps({'type': 'status', 'message': 'Conectando con Devin...'})}\n\n"
            try:
                ctx = _registry.get_or_create(session_id)
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)[:200]})}\n\n"
                return

            client = ctx.client

            # 3. Cargar la sesión si es necesaria
            # Si la sesión fue creada por este ACP client (is_newly_created), no recargar
            # Solo hacer session/load si vamos a enviar un prompt (no solo leer historial)
            is_new = getattr(ctx, "is_newly_created", False)
            if not is_new and ctx.status == "idle" and ctx.event_id == 0 and prompt:
                yield f"data: {json.dumps({'type': 'status', 'message': 'Cargando sesion...'})}\n\n"
                try:
                    load_resp = client.load_session(session_id)
                    if "error" in load_resp:
                        yield f"data: {json.dumps({'type': 'error', 'message': 'No se pudo cargar: ' + str(load_resp.get('error', ''))[:200]})}\n\n"
                        return
                    # Drenar notificaciones de replay — NO reenviar al SSE
                    # El historial viene de la DB, no del replay
                    client.drain_notifications()
                    ctx.client._authenticated = True
                except Exception as e:
                    yield f"data: {json.dumps({'type': 'error', 'message': 'Error al cargar sesion: ' + str(e)[:200]})}\n\n"
                    return

            # 4. Si hay eventos perdidos (Last-Event-ID), reenviarlos del ring buffer
            if last_eid > 0:
                missed = ctx.get_events_since(last_eid)
                for evt in missed:
                    eid = evt.pop("eid", 0)
                    yield f"id: {eid}\ndata: {json.dumps(evt, ensure_ascii=False)}\n\n"

            # 6. Enviar prompt SIN esperar respuesta (non-blocking)
            with ctx.lock:
                ctx.status = "processing"
                yield f"data: {json.dumps({'type': 'status', 'message': 'Devin esta trabajando...'})}\n\n"
                prompt_rid = client._next_id()
                client._send({"jsonrpc": "2.0", "id": prompt_rid,
                              "method": "session/prompt",
                              "params": {"sessionId": session_id,
                                         "prompt": [{"type": "text", "text": prompt}]}})

            # 7. Escuchar eventos de streaming en bucle
            deadline = time.time() + PROMPT_TIMEOUT
            done = False
            idle_received = False
            idle_time = 0
            while time.time() < deadline and not done:
                # Si ya recibimos idle, esperar respuesta al prompt max 15s
                if idle_received and time.time() - idle_time > 15:
                    done = True
                    continue

                # Leer del ACP (non-blocking con timeout corto)
                msg = client.read_update(timeout=10)
                if msg is None:
                    yield f": keepalive\n\n"
                    continue

                # Si es la respuesta al prompt, terminamos
                if msg.get("id") == prompt_rid:
                    result = msg.get("result", {})
                    stop_reason = result.get("stopReason", "")
                    if stop_reason and not idle_received:
                        done_evt = {"type": "done", "stop_reason": stop_reason}
                        ctx.emit(done_evt)
                        eid = done_evt.pop("eid", 0)
                        yield f"id: {eid}\ndata: {json.dumps(done_evt, ensure_ascii=False)}\n\n"
                    done = True
                    continue

                # Si es session/request_permission
                if msg.get("method") == "session/request_permission":
                    params = msg.get("params", {})
                    call_id = params.get("toolCallId", "")
                    perm_evt = {"type": "permission_request",
                                "tool_call_id": call_id,
                                "title": params.get("title", ""),
                                "kind": params.get("kind", "")}
                    ctx.emit(perm_evt)
                    eid = perm_evt.pop("eid", 0)
                    yield f"id: {eid}\ndata: {json.dumps(perm_evt, ensure_ascii=False)}\n\n"
                    # Esperar respuesta del frontend (timeout 60s)
                    deadline_perm = time.time() + 60
                    outcome = "deny"
                    while time.time() < deadline_perm:
                        outcome = ctx.permission_requests.pop(call_id, None)
                        if outcome:
                            break
                        time.sleep(0.5)
                    # Enviar respuesta al agente
                    rid = msg.get("id")
                    if rid:
                        client._send({"jsonrpc": "2.0", "id": rid,
                                     "result": {"outcome": outcome}})
                    continue

                # Si es notificación session/update
                sse = _map_acp_notification(msg)
                if sse:
                    ctx.emit(sse)
                    eid = sse.pop("eid", 0)
                    yield f"id: {eid}\ndata: {json.dumps(sse, ensure_ascii=False)}\n\n"
                    if sse.get("type") == "done":
                        # state_update idle — marcar y esperar respuesta al prompt
                        idle_received = True
                        idle_time = time.time()
                elif "id" in msg and "result" in msg:
                    # Response a otro request, ignorar
                    pass

            # 8. Fin del stream
            ctx.status = "idle"
            yield f"data: {json.dumps({'type': 'released'})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)[:200]})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})

@app.get("/api/health")
async def health(authorization: str | None = Header(None)):
    _check_auth(authorization)
    return {"ok": True, "devin_exe": DEVIN_EXE,
            "credentials": os.path.exists(CREDENTIALS),
            "active_sessions": _registry.list_active(),
            "hostname": os.environ.get("COMPUTERNAME", os.environ.get("HOSTNAME", "unknown")),
            "username": os.environ.get("USERNAME", os.environ.get("USER", "unknown")),
            "platform": "windows" if IS_WINDOWS else "linux"}

@app.get("/api/directories")
async def api_directories(authorization: str | None = Header(None)):
    """Lista directorios comunes y recientes de sesiones existentes."""
    _check_auth(authorization)
    dirs = set()
    home = str(Path.home())
    dirs.add(home)
    for sub in ["Documents", "Desktop", "Downloads", "Projects", "dev"]:
        p = os.path.join(home, sub)
        if os.path.isdir(p):
            dirs.add(p)
    try:
        conn = _db()
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT working_directory FROM sessions WHERE working_directory IS NOT NULL")
        for r in cur.fetchall():
            if r[0]:
                dirs.add(r[0])
        conn.close()
    except Exception:
        pass
    return JSONResponse(sorted(dirs))

@app.get("/api/browse")
async def api_browse(authorization: str | None = Header(None),
                     path: str | None = None):
    """Navega directorios del sistema. Devuelve subdirectorios de `path`."""
    _check_auth(authorization)
    if not path:
        # Raices: drives en Windows, / en Linux/Mac
        if os.name == "nt":
            import string
            drives = []
            for letter in string.ascii_uppercase:
                drive = f"{letter}:\\"
                if os.path.exists(drive):
                    drives.append({"name": f"{letter}:", "path": drive, "type": "drive"})
            return JSONResponse({"path": "", "items": drives, "parent": None})
        else:
            path = "/"
    # Normalizar path
    path = os.path.normpath(path)
    if not os.path.isdir(path):
        return JSONResponse({"error": "not a directory"}, status_code=400)
    items = []
    try:
        for entry in sorted(os.listdir(path)):
            full = os.path.join(path, entry)
            if os.path.isdir(full):
                items.append({"name": entry, "path": full, "type": "dir"})
    except PermissionError:
        return JSONResponse({"error": "permission denied"}, status_code=403)
    except OSError as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    # Parent
    parent = os.path.dirname(path)
    if parent == path:
        parent = None  # raiz
    elif os.name == "nt" and len(parent) <= 3:
        parent = None  # raiz del drive
    return JSONResponse({"path": path, "items": items, "parent": parent})

@app.post("/api/mkdir")
async def api_mkdir(request: Request, authorization: str | None = Header(None)):
    """Crea un directorio nuevo."""
    _check_auth(authorization)
    body = await request.json()
    dir_path = (body or {}).get("path", "").strip()
    if not dir_path:
        raise HTTPException(status_code=400, detail="path vacio")
    try:
        os.makedirs(dir_path, exist_ok=False)
        return {"ok": True, "path": dir_path}
    except FileExistsError:
        raise HTTPException(status_code=409, detail="el directorio ya existe")
    except (PermissionError, OSError) as e:
        raise HTTPException(status_code=403, detail=str(e)[:200])

# --- Version / auto-update ---
APP_VERSION = "3.1.0"

@app.get("/api/version")
async def api_version():
    """Version del server para auto-update. No requiere auth."""
    return {"version": APP_VERSION, "name": "devin-mobile",
            "hostname": os.environ.get("COMPUTERNAME", "unknown")}

# --- Modelos disponibles ---
AVAILABLE_MODELS = [
    {"id": "", "name": "Por defecto"},
    {"id": "opus", "name": "Claude Opus"},
    {"id": "sonnet", "name": "Claude Sonnet"},
    {"id": "haiku", "name": "Claude Haiku"},
    {"id": "gpt-5", "name": "GPT-5"},
    {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro"},
    {"id": "devin", "name": "Devin (auto)"},
]

@app.get("/api/models")
async def api_models(authorization: str | None = Header(None)):
    _check_auth(authorization)
    return {"models": AVAILABLE_MODELS}

# --- Login silencioso ---
_auth_login_proc = None
_auth_login_lock = threading.Lock()

@app.post("/api/auth/start-login")
async def api_start_login(authorization: str | None = Header(None)):
    """Inicia devin auth login --force-manual-token-flow y captura la URL."""
    global _auth_login_proc
    _check_auth(authorization)
    with _auth_login_lock:
        if _auth_login_proc and _auth_login_proc.poll() is None:
            _auth_login_proc.terminate()
            _auth_login_proc = None
        try:
            _auth_login_proc = subprocess.Popen(
                [DEVIN_EXE, "auth", "login", "--force-manual-token-flow"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env=_clean_env(), cwd=WORKDIR,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
            # Leer salida hasta encontrar la URL
            url = None
            start = time.time()
            while time.time() - start < 15:
                line = _auth_login_proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if "http" in line:
                    # Extraer URL
                    import re as _re
                    m = _re.search(r'(https?://[^\s]+)', line)
                    if m:
                        url = m.group(1)
                        break
            if url:
                return {"ok": True, "url": url}
            return {"ok": False, "error": "No se pudo obtener URL de login"}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

@app.post("/api/auth/submit-token")
async def api_submit_token(request: Request, authorization: str | None = Header(None)):
    """Envia el token/code al proceso de login."""
    global _auth_login_proc
    _check_auth(authorization)
    body = await request.json()
    token = (body or {}).get("token", "").strip()
    if not token:
        return {"ok": False, "error": "token vacio"}
    with _auth_login_lock:
        if not _auth_login_proc or _auth_login_proc.poll() is not None:
            return {"ok": False, "error": "No hay proceso de login activo"}
        try:
            _auth_login_proc.stdin.write(token + "\n")
            _auth_login_proc.stdin.flush()
            # Esperar respuesta
            start = time.time()
            while time.time() - start < 15:
                line = _auth_login_proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if "logged in" in line.lower() or "success" in line.lower():
                    return {"ok": True, "message": "Login exitoso"}
                if "error" in line.lower() or "invalid" in line.lower():
                    return {"ok": False, "error": line[:200]}
            # Si el proceso termino sin error, asumir exito
            if _auth_login_proc.poll() == 0:
                return {"ok": True, "message": "Login exitoso"}
            return {"ok": False, "error": "Respuesta no confirmada"}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

@app.get("/api/auth/status")
async def api_auth_status(authorization: str | None = Header(None)):
    """Verifica si Devin CLI esta autenticado."""
    _check_auth(authorization)
    try:
        r = subprocess.run([DEVIN_EXE, "auth", "status"], capture_output=True,
                          text=True, timeout=10, env=_clean_env())
        output = r.stdout + r.stderr
        logged_in = "logged in" in output.lower() and "not logged in" not in output.lower()
        return {"ok": True, "logged_in": logged_in, "detail": output[:500]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

@app.get("/api/pairing")
async def api_pairing(authorization: str | None = Header(None)):
    """Genera la informacion de emparejamiento para el movil (QR code).
    Requiere auth — el QR contiene la URL + credenciales para auto-configurar el movil."""
    _check_auth(authorization)
    # Obtener IPs disponibles (Tailscale + localhost)
    urls = [f"http://localhost:{PORT}"]
    try:
        result = subprocess.run(["tailscale", "ip", "-4"],
                                capture_output=True, text=True, timeout=5)
        for ip in result.stdout.strip().split("\n"):
            if ip:
                urls.append(f"http://{ip}:{PORT}")
    except Exception:
        pass
    # Hostname
    hostname = os.environ.get("COMPUTERNAME", os.environ.get("HOSTNAME", "unknown"))
    # Informacion de emparejamiento
    pairing = {
        "name": f"Devin - {hostname}",
        "urls": urls,
        "username": AUTH_USERNAME,
        "version": APP_VERSION,
        "hostname": hostname,
    }
    return JSONResponse(pairing)

# --- Main -------------------------------------------------------------------
def _print_tailscale_hint():
    try:
        result = subprocess.run(["tailscale", "ip", "-4"],
                                capture_output=True, text=True, timeout=5)
        ip = result.stdout.strip().split("\n")[0] if result.stdout else ""
        if ip:
            print(f"\n  Tailscale IP: http://{ip}:{PORT}\n", flush=True)
    except Exception:
        pass

if __name__ == "__main__":
    print(f"  Sirviendo en http://0.0.0.0:{PORT}", flush=True)
    print(f"  Usuario: {AUTH_USERNAME or '(sin auth)'}", flush=True)
    print(f"  Credenciales Devin: {'OK' if os.path.exists(CREDENTIALS) else 'FALTA'}", flush=True)
    print(f"  Devin exe: {DEVIN_EXE}", flush=True)
    _print_tailscale_hint()
    try:
        uvicorn.run(app, host=HOST, port=PORT, log_level="info")
    finally:
        _registry.stop_all()
