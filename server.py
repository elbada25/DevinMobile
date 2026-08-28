"""
Devin Mobile Dashboard v2 - Servidor con ACP streaming, DB directa, y gestion completa.

Arquitectura:
  - Lee sesiones directamente de sessions.db (todas, no filtradas por directorio)
  - Comunicacion con devin acp via JSON-RPC sobre stdio
  - Autenticacion via ACP authenticate (methodId=devin-browser)
  - Streaming de eventos ACP al cliente via SSE
  - Handoff: mata el agente del IDE antes de cargar la sesion
  - Libera el lock al terminar
"""
import json
import os
import queue as queue_mod
import re
import sqlite3
import subprocess
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Optional

import psutil
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

# --- Configuracion ---------------------------------------------------------
HOST = "0.0.0.0"
PORT = int(os.environ.get("DEVIN_MOBILE_PORT", "8787"))
DEVIN_EXE = r"C:\Users\EduardoBadaRuano\AppData\Local\devin\cli\bin\devin.exe"
APPDATA = os.environ.get("APPDATA", r"C:\Users\EduardoBadaRuano\AppData\Roaming")
CREDENTIALS = os.path.join(APPDATA, "devin", "credentials.toml")
SESSION_LOCKS_DIR = os.path.join(APPDATA, "devin", "cli", "session_locks")
DB_PATH = os.path.join(APPDATA, "devin", "cli", "sessions.db")
WORKDIR = r"C:\Users\EduardoBadaRuano"
PROMPT_TIMEOUT = 600

BASE_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = BASE_DIR / "config.json"
_config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
AUTH_USERNAME = _config["username"]
AUTH_PASSWORD = _config["password"]

# --- RestartManager API ---------------------------------------------------
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
                     ("strServiceShortName", wintypes.WCHAR * 64), ("ApplicationType", wintypes.DWORD),
                     ("AppStatus", wintypes.DWORD), ("TSSessionId", wintypes.DWORD), ("bRestartable", wintypes.BOOL)]
    _RM_PROCESS_INFO = RM_PROCESS_INFO
    _rstrtmgr.RmStartSession.restype = wintypes.DWORD
    _rstrtmgr.RmStartSession.argtypes = [ct.POINTER(wintypes.DWORD), wintypes.DWORD, ct.c_wchar_p]
    _rstrtmgr.RmRegisterResources.restype = wintypes.DWORD
    _rstrtmgr.RmRegisterResources.argtypes = [wintypes.DWORD, wintypes.UINT, ct.POINTER(ct.c_wchar_p),
                                               wintypes.UINT, ct.POINTER(ct.c.HANDLE), wintypes.UINT, ct.POINTER(ct.c_wchar_p)]
    _rstrtmgr.RmGetList.restype = wintypes.DWORD
    _rstrtmgr.RmGetList.argtypes = [wintypes.DWORD, ct.POINTER(wintypes.UINT), ct.POINTER(wintypes.UINT),
                                     ct.POINTER(RM_PROCESS_INFO), ct.POINTER(wintypes.DWORD)]
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
        _rstrtmgr.RmGetList(session_handle, ct.byref(proc_count), ct.byref(proc_count), None, ct.byref(reboot_reasons))
        if proc_count.value == 0:
            return None
        procs = (_RM_PROCESS_INFO * proc_count.value)()
        actual_count = wintypes.UINT(proc_count.value)
        _rstrtmgr.RmGetList(session_handle, ct.byref(actual_count), ct.byref(proc_count), procs, ct.byref(reboot_reasons))
        for i in range(actual_count.value):
            p = procs[i]
            if p.strAppName and "devin" in p.strAppName.lower():
                return p.Process.pid
        if actual_count.value > 0:
            return procs[0].Process.pid
        return None
    finally:
        _rstrtmgr.RmEndSession(session_handle)

# --- App -------------------------------------------------------------------
app = FastAPI(title="Devin Mobile Dashboard v2")

def _clean_env() -> dict:
    """Entorno completo sin variables WINDSURF que interfieren con el CLI."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("WINDSURF_")}
    env["PATH"] = os.path.dirname(DEVIN_EXE) + os.pathsep + env.get("PATH", "")
    return env

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

def _clean_ansi(text: str) -> str:
    text = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)
    text = re.sub(r"\x1b\[\?[0-9]*[a-zA-Z]", "", text)
    return text.strip()

# --- DB helpers ------------------------------------------------------------

def _db():
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)

def _db_write():
    return sqlite3.connect(DB_PATH)

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
        if "devin" in proc.name().lower():
            return True, f"pid {pid}"
        return False, f"pid {pid} not devin"
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
            "last_activity_iso": time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "",
            "last_activity_ago": _time_ago(ts) if ts else "",
            "title": r[7] or "(sin titulo)", "active": active, "status_detail": detail,
            "workspace_dirs": json.loads(r[10]) if r[10] else [],
        })
    conn.close()
    return sessions

def _parse_message(chat_msg_json: str, node_id: int, ts: int, tool_states: dict) -> dict | None:
    try:
        msg = json.loads(chat_msg_json)
    except json.JSONDecodeError:
        return None
    role = msg.get("role", "")
    if role == "system":
        return None
    content = msg.get("content", "")
    thinking = msg.get("thinking", {})
    tool_calls = msg.get("tool_calls", [])
    tool_call_id = msg.get("tool_call_id", "")
    time_str = time.strftime("%H:%M:%S", time.localtime(ts)) if ts else ""
    entry = {"role": role, "time": time_str, "node_id": node_id}
    if role == "user":
        entry["content"] = content[:3000]
    elif role == "assistant":
        entry["content"] = content[:3000] if content else ""
        if thinking and isinstance(thinking, dict):
            entry["thinking"] = thinking.get("thinking", "")[:1000]
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
        entry["content"] = content[:2000]
        entry["tool_call_id"] = tool_call_id
    return entry

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

def _get_history_db(session_id: str, limit: int = 100) -> list[dict]:
    conn = _db()
    tool_states = _get_tool_states(conn, session_id)
    cur = conn.cursor()
    cur.execute("""
        SELECT node_id, chat_message, created_at FROM message_nodes
        WHERE session_id=? ORDER BY node_id DESC LIMIT ?
    """, (session_id, limit))
    rows = cur.fetchall()
    conn.close()
    messages = []
    for row in reversed(rows):
        node_id, chat_msg, ts = row
        entry = _parse_message(chat_msg, node_id, ts, tool_states)
        if entry:
            messages.append(entry)
    return messages

# --- Session management (DB writes) ----------------------------------------

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

# --- Lock management -------------------------------------------------------

def _kill_session_agent(session_id: str) -> dict:
    lock_path = os.path.join(SESSION_LOCKS_DIR, f"{session_id}.lock")
    pid = None
    try:
        with open(lock_path, "r") as f:
            pid = int(f.read().strip())
    except (PermissionError, OSError, ValueError, TypeError):
        pid = _find_lock_holder_pid(lock_path)
    if pid is None:
        return {"ok": False, "error": "no se pudo identificar el proceso"}
    try:
        proc = psutil.Process(pid)
        if "devin" not in proc.name().lower():
            return {"ok": False, "error": f"pid {pid} no es devin"}
        proc.kill()
        proc.wait(timeout=5)
    except psutil.NoSuchProcess:
        pass
    except psutil.TimeoutExpired:
        return {"ok": False, "error": f"pid {pid} no termino"}
    time.sleep(0.5)
    try:
        os.remove(lock_path)
    except (PermissionError, OSError, FileNotFoundError):
        pass
    return {"ok": True, "killed_pid": pid}

def _release_session_lock(session_id: str):
    lock_path = os.path.join(SESSION_LOCKS_DIR, f"{session_id}.lock")
    try:
        with open(lock_path, "r") as f:
            pid = int(f.read().strip())
        try:
            proc = psutil.Process(pid)
            if "devin" in proc.name().lower():
                proc.kill()
                proc.wait(timeout=3)
        except (psutil.NoSuchProcess, psutil.TimeoutExpired):
            pass
    except (PermissionError, OSError, ValueError, TypeError):
        pass
    try:
        os.remove(lock_path)
    except (PermissionError, OSError, FileNotFoundError):
        pass

# --- ACP client (JSON-RPC over stdio) --------------------------------------

class ACPClient:
    """Cliente ACP que lanza devin acp y se comunica via JSON-RPC sobre stdio."""

    def __init__(self):
        self.proc = None
        self._id = 0
        self._queue = None
        self._reader_thread = None
        self._notifications = []

    def start(self):
        self._queue = queue_mod.Queue()
        env = _clean_env()
        self.proc = subprocess.Popen(
            [DEVIN_EXE, "acp"],
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
        # Initialize
        resp = self._request("initialize", {
            "protocolVersion": 1, "capabilities": {},
            "clientInfo": {"name": "devin-mobile", "version": "2.0"},
        })
        return resp

    def authenticate(self) -> dict:
        return self._request("authenticate", {"methodId": "devin-browser"}, timeout=30)

    def _next_id(self):
        self._id += 1
        return self._id

    def _send(self, msg):
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
            msg = self._read_msg(timeout=min(remaining, 30))
            if msg is None:
                continue
            if msg.get("id") == rid:
                return msg
            elif "method" in msg and "id" not in msg:
                self._notifications.append(msg)
        return {"error": "timeout"}

    def drain_notifications(self):
        notifs = self._notifications[:]
        self._notifications.clear()
        return notifs

    def load_session(self, session_id: str, cwd: str = WORKDIR) -> dict:
        return self._request("session/load", {
            "sessionId": session_id, "cwd": cwd, "mcpServers": [],
        }, timeout=60)

    def new_session(self, prompt: str, cwd: str = WORKDIR) -> dict:
        return self._request("session/new", {
            "cwd": cwd,
            "prompt": [{"type": "text", "text": prompt}],
            "mcpServers": [],
        }, timeout=60)

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

def _map_acp_notification(msg: dict) -> dict | None:
    """Convierte una notificacion ACP a un evento SSE para el frontend."""
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
        event["thinking"] = update.get("content", {}).get("text", "")
    elif kind == "agent_thought":
        event["thinking"] = update.get("content", {}).get("text", "")
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
    else:
        event["raw"] = str(update)[:300]
    return event

# --- Endpoints -------------------------------------------------------------

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
    return JSONResponse(_list_sessions_db())

@app.get("/api/sessions/archived")
async def api_archived_sessions(authorization: str | None = Header(None)):
    _check_auth(authorization)
    return JSONResponse(_list_archived_sessions())

@app.get("/api/sessions/{session_id}/history")
async def api_history(session_id: str, authorization: str | None = Header(None)):
    _check_auth(authorization)
    return JSONResponse(_get_history_db(session_id))

@app.post("/api/sessions/{session_id}/rename")
async def api_rename(session_id: str, request: Request, authorization: str | None = Header(None)):
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
    _release_session_lock(session_id)
    return {"ok": True}

@app.post("/api/sessions/new")
async def api_new_session(request: Request, authorization: str | None = Header(None)):
    _check_auth(authorization)
    body = await request.json()
    prompt = (body or {}).get("prompt", "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt vacio")
    # Crear sesion via ACP
    client = ACPClient()
    try:
        client.start()
        client.authenticate()
        resp = client.new_session(prompt)
        result = resp.get("result", {})
        sid = result.get("sessionId")
        # Esperar a que termine el prompt
        deadline = time.time() + 120
        while time.time() < deadline:
            msg = client.read_update(timeout=10)
            if msg is None:
                continue
            if msg.get("method") == "session/update":
                update = msg.get("params", {}).get("update", {})
                if update.get("sessionUpdate") == "state_update" and update.get("state") == "idle":
                    break
        conn = _db()
        cur = conn.cursor()
        cur.execute("SELECT title FROM sessions WHERE id=?", (sid,))
        r = cur.fetchone()
        conn.close()
        return {"ok": True, "session_id": sid, "title": r[0] if r else "Nueva sesion"}
    finally:
        client.stop()

@app.get("/api/sessions/{session_id}/stream")
async def api_stream(session_id: str, request: Request, authorization: str | None = Header(None)):
    """SSE endpoint: autentica ACP, carga sesion, envia prompt, reenvia eventos."""
    _check_auth(authorization)
    prompt = request.query_params.get("prompt", "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt vacio")

    def event_stream():
        # 1. Handoff si la sesion esta bloqueada
        active, _ = _is_session_active(session_id)
        if active:
            kill_info = _kill_session_agent(session_id)
            yield f"data: {json.dumps({'type': 'handoff', 'data': kill_info}, ensure_ascii=False)}\n\n"
            if not kill_info.get("ok"):
                yield f"data: {json.dumps({'type': 'error', 'message': kill_info.get('error', 'error')})}\n\n"
                return
            time.sleep(1)

        # 2. Lanzar ACP
        yield f"data: {json.dumps({'type': 'status', 'message': 'Conectando con Devin...'})}\n\n"
        client = ACPClient()
        try:
            init_resp = client.start()
            if "error" in init_resp:
                yield f"data: {json.dumps({'type': 'error', 'message': 'No se pudo iniciar ACP'})}\n\n"
                return

            # 3. Autenticar
            auth_resp = client.authenticate()
            if "error" in auth_resp:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Auth fallida: ' + str(auth_resp.get('error', ''))[:200]})}\n\n"
                return

            # 4. Cargar sesion
            load_resp = client.load_session(session_id)
            if "error" in load_resp:
                yield f"data: {json.dumps({'type': 'error', 'message': 'No se pudo cargar la sesion: ' + str(load_resp.get('error', ''))[:200]})}\n\n"
                return

            # Drenar notificaciones acumuladas durante load
            for notif in client.drain_notifications():
                sse = _map_acp_notification(notif)
                if sse:
                    yield f"data: {json.dumps(sse, ensure_ascii=False)}\n\n"

            # 5. Enviar prompt
            yield f"data: {json.dumps({'type': 'status', 'message': 'Devin esta trabajando...'})}\n\n"
            prompt_resp = client.prompt(session_id, prompt)
            print(f"[ACP] session/prompt: {prompt_resp.get('result', prompt_resp.get('error', '?'))}", flush=True)

            # Drenar notificaciones acumuladas durante prompt
            for notif in client.drain_notifications():
                sse = _map_acp_notification(notif)
                if sse:
                    yield f"data: {json.dumps(sse, ensure_ascii=False)}\n\n"
                    if sse.get("type") == "done":
                        break

            # 6. Escuchar eventos de streaming
            deadline = time.time() + PROMPT_TIMEOUT
            while time.time() < deadline:
                msg = client.read_update(timeout=30)
                if msg is None:
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
                    continue
                sse = _map_acp_notification(msg)
                if sse:
                    yield f"data: {json.dumps(sse, ensure_ascii=False)}\n\n"
                    if sse.get("type") == "done":
                        break
                elif "id" in msg and "result" in msg:
                    pass  # response, ignorar

            # 7. Liberar la sesion
            _release_session_lock(session_id)
            yield f"data: {json.dumps({'type': 'released'})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        finally:
            client.stop()

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.get("/api/health")
async def health(authorization: str | None = Header(None)):
    _check_auth(authorization)
    return {"ok": True, "devin_exe": DEVIN_EXE, "credentials": os.path.exists(CREDENTIALS)}

# --- Main ------------------------------------------------------------------

def _print_tailscale_hint():
    try:
        result = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5)
        ip = result.stdout.strip().split("\n")[0] if result.stdout else ""
        if ip:
            print(f"\n  Tailscale IP: http://{ip}:{PORT}\n", flush=True)
    except Exception:
        pass

if __name__ == "__main__":
    print(f"  Sirviendo en http://0.0.0.0:{PORT}", flush=True)
    print(f"  Usuario: {AUTH_USERNAME}", flush=True)
    print(f"  Credenciales Devin: {'OK' if os.path.exists(CREDENTIALS) else 'FALTA'}", flush=True)
    _print_tailscale_hint()
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
