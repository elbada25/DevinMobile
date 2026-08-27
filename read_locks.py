import ctypes
from ctypes import wintypes
import os

APPDATA = os.environ.get("APPDATA", r"C:\Users\EduardoBadaRuano\AppData\Roaming")
SESSION_LOCKS_DIR = os.path.join(APPDATA, "devin", "cli", "session_locks")

# Locks que nos interesan (LOCKED-HELD)
target_sessions = ["regal-adasaurus", "capricious-primrose", "magical-quiver",
                   "deciduous-army", "soft-walk", "stone-aries"]

# Usar CreateFile con FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
GENERIC_READ = 0x80000000
FILE_SHARE_ALL = 0x07  # READ | WRITE | DELETE
OPEN_EXISTING = 3

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

for sid in target_sessions:
    lock_path = os.path.join(SESSION_LOCKS_DIR, f"{sid}.lock")
    handle = kernel32.CreateFileW(
        lock_path, GENERIC_READ, FILE_SHARE_ALL, None, OPEN_EXISTING, 0, None
    )
    if handle == -1 or handle == 0xFFFFFFFF:
        err = ctypes.get_last_error()
        print(f"{sid:30s} CreateFile failed err={err}")
        continue
    # Leer contenido
    buf = ctypes.create_string_buffer(64)
    bytes_read = wintypes.DWORD(0)
    ok = kernel32.ReadFile(handle, buf, 64, ctypes.byref(bytes_read), None)
    kernel32.CloseHandle(handle)
    if ok and bytes_read.value > 0:
        content = buf.raw[:bytes_read.value].decode("utf-8", errors="replace").strip()
        print(f"{sid:30s} pid={content}")
    else:
        print(f"{sid:30s} ReadFile failed")
