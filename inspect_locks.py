import sqlite3, os, json
import psutil

APPDATA = os.environ.get("APPDATA", r"C:\Users\EduardoBadaRuano\AppData\Roaming")
SESSION_LOCKS_DIR = os.path.join(APPDATA, "devin", "cli", "session_locks")

# Para cada lock, intentar encontrar que proceso lo tiene
for fname in os.listdir(SESSION_LOCKS_DIR):
    if not fname.endswith(".lock"):
        continue
    sid = fname[:-5]
    lock_path = os.path.join(SESSION_LOCKS_DIR, fname)

    # Intentar leer el PID
    pid = None
    try:
        with open(lock_path, "r") as f:
            pid = int(f.read().strip())
    except (PermissionError, OSError):
        # Archivo bloqueado - buscar que proceso lo tiene
        holder = None
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                for item in proc.info.get("open_files", []) or []:
                    if sid + ".lock" in str(item.path).lower():
                        holder = proc.info
                        break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        print(f"{sid:30s} LOCKED-HELD  holder={holder}")
        continue
    except (ValueError, TypeError):
        print(f"{sid:30s} invalid pid")
        continue

    # Verificar si el PID esta vivo
    try:
        p = psutil.Process(pid)
        print(f"{sid:30s} pid={pid} alive={p.name()}")
    except psutil.NoSuchProcess:
        print(f"{sid:30s} pid={pid} dead")
