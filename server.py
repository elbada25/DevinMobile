"""
Devin Mobile Dashboard - Servidor web para controlar sesiones de Devin CLI desde el movil.
Acceso via Tailscale. Protegido por token simple.

Cuando una sesion esta bloqueada por el IDE, el servidor hace un "handoff":
  1. Identifica el proceso que tiene el lock (via Windows RestartManager API)
  2. Mata ese proceso (el agente ACP del IDE)
  3. Reanuda la sesion con devin -r
  4. La sesion sigue viva en la BD compartida; al volver al PC, se reabre en el IDE.
"""
import ctypes
import json
import os
import re
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

import psutil
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

# --- Configuracion ---------------------------------------------------------
HOST = "0.0.0.0"
PORT = int(os.environ.get("DEVIN_MOBILE_PORT", "8787"))
DEVIN_EXE = r"C:\Users\EduardoBadaRuano\AppData\Local\devin\cli\bin\devin.exe"
APPDATA = os.environ.get("APPDATA", r"C:\Users\EduardoBadaRuano\AppData\Roaming")
CREDENTIALS = os.path.join(APPDATA, "devin", "credentials.toml")
SESSION_LOCKS_DIR = os.path.join(APPDATA, "devin", "cli", "session_locks")
WORKDIR = os.environ.get("DEVIN_MOBILE_WORKDIR", r"C:\Users\EduardoBadaRuano")
PROMPT_TIMEOUT = int(os.environ.get("DEVIN_MOBILE_PROMPT_TIMEOUT", "180"))

BASE_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = BASE_DIR / "config.json"

# Cargar usuario/contraseña de config.json (persistente entre reinicios)
_config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
AUTH_USERNAME = _config["username"]
AUTH_PASSWORD = _config["password"]

# --- RestartManager API (Windows) -----------------------------------------
_rstrtmgr = ctypes = None
_RM_PROCESS_INFO = None

def _init_restart_manager():
    global _rstrtmgr, _RM_PROCESS_INFO
    import ctypes as ct
    _rstrtmgr = ct.WinDLL("rstrtmgr")

    class RM_UNIQUE_PROCESS(ct.Structure):
        _fields_ = [("pid", wintypes.DWORD), ("StartTime", wintypes.FILETIME)]

    class RM_PROCESS_INFO(ct.Structure):
        _fields_ = [
            ("Process", RM_UNIQUE_PROCESS),
            ("strAppName", wintypes.WCHAR * 256),
            ("strServiceShortName", wintypes.WCHAR * 64),
            ("ApplicationType", wintypes.DWORD),
            ("AppStatus", wintypes.DWORD),
            ("TSSessionId", wintypes.DWORD),
            ("bRestartable", wintypes.BOOL),
        ]

    _RM_PROCESS_INFO = RM_PROCESS_INFO
    _rstrtmgr.RmStartSession.restype = wintypes.DWORD
    _rstrtmgr.RmStartSession.argtypes = [ct.POINTER(wintypes.DWORD), wintypes.DWORD, ct.c_wchar_p]
    _rstrtmgr.RmRegisterResources.restype = wintypes.DWORD
    _rstrtmgr.RmRegisterResources.argtypes = [wintypes.DWORD, wintypes.UINT, ct.POINTER(ct.c_wchar_p),
                                               wintypes.UINT, ct.POINTER(wintypes.HANDLE), wintypes.UINT, ct.POINTER(ct.c_wchar_p)]
    _rstrtmgr.RmGetList.restype = wintypes.DWORD
    _rstrtmgr.RmGetList.argtypes = [wintypes.DWORD, ct.POINTER(wintypes.UINT), ct.POINTER(wintypes.UINT),
                                     ct.POINTER(RM_PROCESS_INFO), ct.POINTER(wintypes.DWORD)]
    _rstrtmgr.RmEndSession.restype = wintypes.DWORD
    _rstrtmgr.RmEndSession.argtypes = [wintypes.DWORD]


def _find_lock_holder_pid(lock_path: str) -> int | None:
    """Usa Windows RestartManager para encontrar el PID que tiene un archivo bloqueado."""
    if _rstrtmgr is None:
        _init_restart_manager()
    import ctypes as ct

    session_handle = wintypes.DWORD(0)
    session_key = ct.create_unicode_buffer(256)

    res = _rstrtmgr.RmStartSession(ct.byref(session_handle), 0, session_key)
    if res != 0:
        return None

    try:
        files = (ct.c_wchar_p * 1)(lock_path)
        res = _rstrtmgr.RmRegisterResources(session_handle, 1, files, 0, None, 0, None)
        if res != 0:
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
        # Si no hay devin, devolver el primero
        if actual_count.value > 0:
            return procs[0].Process.pid
        return None
    finally:
        _rstrtmgr.RmEndSession(session_handle)


# --- App -------------------------------------------------------------------
app = FastAPI(title="Devin Mobile Dashboard")


def _clean_env() -> dict:
    return {
        "APPDATA": APPDATA,
        "LOCALAPPDATA": os.environ.get("LOCALAPPDATA", r"C:\Users\EduardoBadaRuano\AppData\Local"),
        "USERPROFILE": os.environ.get("USERPROFILE", r"C:\Users\EduardoBadaRuano"),
        "HOME": os.environ.get("USERPROFILE", r"C:\Users\EduardoBadaRuano"),
        "HOMEDRIVE": "C:",
        "HOMEPATH": r"\Users\EduardoBadaRuano",
        "USERNAME": os.environ.get("USERNAME", "EduardoBadaRuano"),
        "USERDOMAIN": os.environ.get("USERDOMAIN", "bada"),
        "PATH": os.path.dirname(DEVIN_EXE),
        "SystemRoot": r"C:\WINDOWS",
        "TEMP": os.environ.get("TEMP", r"C:\Users\EduardoBadaRuano\AppData\Local\Temp"),
        "TMP": os.environ.get("TMP", r"C:\Users\EduardoBadaRuano\AppData\Local\Temp"),
    }


def _check_auth(authorization: str | None):
    """Auth via Basic (usuario:contraseña en Base64)."""
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
            return True, f"pid {pid} alive ({proc.name()})"
        return False, f"pid {pid} not devin ({proc.name()})"
    except Exception:
        return False, f"pid {pid} dead"


def _list_sessions() -> list[dict]:
    env = _clean_env()
    try:
        result = subprocess.run(
            [DEVIN_EXE, "list", "--format", "json"],
            capture_output=True, text=True, timeout=30, env=env, cwd=WORKDIR,
        )
    except subprocess.TimeoutExpired:
        return []
    if result.returncode != 0:
        return []
    try:
        sessions = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    for s in sessions:
        active, detail = _is_session_active(s.get("id", ""))
        s["active"] = active
        s["status_detail"] = detail
        ts = s.get("last_activity_at")
        s["last_activity_iso"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if isinstance(ts, (int, float)) else ""
    return sessions


def _clean_ansi(text: str) -> str:
    text = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)
    text = re.sub(r"\x1b\[\?[0-9]*[a-zA-Z]", "", text)
    return text.strip()


def _kill_session_agent(session_id: str) -> dict:
    """Mata el proceso agente que tiene el lock de una sesion.
    Retorna info sobre lo que paso."""
    lock_path = os.path.join(SESSION_LOCKS_DIR, f"{session_id}.lock")

    # Metodo 1: intentar leer el PID del lock
    pid = None
    try:
        with open(lock_path, "r") as f:
            pid = int(f.read().strip())
    except (PermissionError, OSError, ValueError, TypeError):
        pass

    # Metodo 2: si no se pudo leer, usar RestartManager
    if pid is None:
        pid = _find_lock_holder_pid(lock_path)

    if pid is None:
        return {"ok": False, "error": "no se pudo identificar el proceso que tiene el lock"}

    # Verificar que es un proceso devin antes de matar
    try:
        proc = psutil.Process(pid)
        name = proc.name().lower()
        cmdline = " ".join(proc.cmdline()).lower()
        if "devin" not in name and "devin" not in cmdline:
            return {"ok": False, "error": f"pid {pid} no es devin ({proc.name()})"}
    except psutil.NoSuchProcess:
        # El proceso ya murio - intentar borrar el lock
        try:
            os.remove(lock_path)
        except OSError:
            pass
        return {"ok": True, "killed_pid": pid, "note": "proceso ya estaba muerto, lock borrado"}

    # Matar el proceso
    try:
        proc.kill()
        proc.wait(timeout=5)
    except psutil.TimeoutExpired:
        return {"ok": False, "error": f"pid {pid} no termino tras kill"}
    except psutil.NoSuchProcess:
        pass  # Ya murto, fine

    # Esperar a que el lock se libere
    for _ in range(10):
        try:
            with open(lock_path, "r"):
                pass
            # Si pudimos abrirlo, el lock se libero
            break
        except (PermissionError, OSError):
            time.sleep(0.3)

    # Borrar el lock file
    try:
        os.remove(lock_path)
    except (PermissionError, OSError):
        pass  # Si no se puede borrar, devin -r deberia poder crear uno nuevo

    return {"ok": True, "killed_pid": pid, "process_name": proc.name()}


def _release_session_lock(session_id: str):
    """Limpia el lock de una sesion para que el IDE pueda reanudarla."""
    lock_path = os.path.join(SESSION_LOCKS_DIR, f"{session_id}.lock")
    # Si hay un proceso CLI vivo con este lock, matarlo
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
    # Borrar el lock file
    try:
        os.remove(lock_path)
    except (PermissionError, OSError, FileNotFoundError):
        pass


def _get_session_history(session_id: str, limit: int = 20) -> list[dict]:
    """Lee los ultimos mensajes de una sesion desde sessions.db."""
    import sqlite3
    db_path = os.path.join(APPDATA, "devin", "cli", "sessions.db")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = conn.cursor()
    cur.execute(
        "SELECT node_id, chat_message, created_at FROM message_nodes "
        "WHERE session_id=? ORDER BY node_id DESC LIMIT ?",
        (session_id, limit),
    )
    rows = cur.fetchall()
    conn.close()
    messages = []
    for row in reversed(rows):  # orden cronologico
        node_id, chat_msg, ts = row
        try:
            msg = json.loads(chat_msg)
        except json.JSONDecodeError:
            continue
        role = msg.get("role", "")
        content = msg.get("content", "")
        # Si es un tool_call, extraer info util
        tool_calls = msg.get("tool_calls", [])
        if tool_calls and not content:
            parts = []
            for tc in tool_calls:
                name = tc.get("name", "?")
                args = tc.get("arguments", {})
                if isinstance(args, dict):
                    cmd = args.get("command", args.get("file_path", args.get("pattern", str(args)[:80])))
                    parts.append(f"[{name}: {cmd}]")
                else:
                    parts.append(f"[{name}]")
            content = " ".join(parts)
        if role in ("user", "assistant") and content:
            messages.append({
                "role": role,
                "content": content[:2000],
                "timestamp": ts,
                "time_iso": time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "",
            })
    return messages


def _send_prompt(session_id: str, prompt: str) -> dict:
    """Envia un prompt. Si la sesion esta bloqueada, hace handoff (mata agente del IDE)."""
    env = _clean_env()
    cmd = [DEVIN_EXE, "-r", session_id, "--respect-workspace-trust", "false", "--print", prompt]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=PROMPT_TIMEOUT,
                                env=env, cwd=WORKDIR, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Timeout tras {PROMPT_TIMEOUT}s", "output": ""}

    clean_out = _clean_ansi(result.stdout or "")
    clean_err = _clean_ansi(result.stderr or "")

    # Si fallo por sesion bloqueada, hacer handoff y reintentar
    if result.returncode != 0 and "ACP agent session" in clean_err:
        kill_info = _kill_session_agent(session_id)
        if not kill_info.get("ok"):
            return {"ok": False, "error": f"Sesion bloqueada. No se pudo liberar: {kill_info.get('error')}", "output": ""}

        # Reintentar
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=PROMPT_TIMEOUT,
                                    env=env, cwd=WORKDIR, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"Timeout tras handoff ({PROMPT_TIMEOUT}s)", "output": ""}

        clean_out = _clean_ansi(result.stdout or "")
        clean_err = _clean_ansi(result.stderr or "")
        # Limpiar el lock tras usar la sesion para que el IDE pueda reanudarla
        _release_session_lock(session_id)
        return {
            "ok": result.returncode == 0,
            "exit_code": result.returncode,
            "output": clean_out,
            "error": clean_err,
            "handoff": kill_info,
        }

    # Limpiar el lock tras usar la sesion para que el IDE pueda reanudarla
    _release_session_lock(session_id)
    return {"ok": result.returncode == 0, "exit_code": result.returncode, "output": clean_out, "error": clean_err}


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
    raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos")


@app.get("/api/sessions")
async def api_sessions(authorization: str | None = Header(None)):
    _check_auth(authorization)
    return JSONResponse(_list_sessions())


@app.post("/api/sessions/{session_id}/prompt")
async def api_send_prompt(session_id: str, request: Request, authorization: str | None = Header(None)):
    _check_auth(authorization)
    body = await request.json()
    prompt = (body or {}).get("prompt", "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt vacio")
    return JSONResponse(_send_prompt(session_id, prompt))


@app.get("/api/sessions/{session_id}/history")
async def api_session_history(session_id: str, authorization: str | None = Header(None)):
    _check_auth(authorization)
    return JSONResponse(_get_session_history(session_id))


@app.post("/api/sessions/{session_id}/release")
async def api_release_session(session_id: str, authorization: str | None = Header(None)):
    """Libera el lock de una sesion para que el IDE pueda reanudarla."""
    _check_auth(authorization)
    _release_session_lock(session_id)
    return {"ok": True}


@app.get("/api/health")
async def health(authorization: str | None = Header(None)):
    _check_auth(authorization)
    return {"ok": True, "devin_exe": DEVIN_EXE, "credentials": os.path.exists(CREDENTIALS)}


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
    cred_status = "OK" if os.path.exists(CREDENTIALS) else "FALTA"
    print(f"  Credenciales Devin: {cred_status}", flush=True)
    _print_tailscale_hint()
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
