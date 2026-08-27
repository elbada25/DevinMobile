import ctypes
from ctypes import wintypes
import os

APPDATA = os.environ.get("APPDATA", r"C:\Users\EduardoBadaRuano\AppData\Roaming")
SESSION_LOCKS_DIR = os.path.join(APPDATA, "devin", "cli", "session_locks")

# Windows RestartManager API para encontrar procesos que tienen un archivo abierto
RmStartSession = ctypes.WinDLL("rstrtmgr").RmStartSession
RmRegisterResources = ctypes.WinDLL("rstrtmgr").RmRegisterResources
RmGetList = ctypes.WinDLL("rstrtmgr").RmGetList
RmEndSession = ctypes.WinDLL("rstrtmgr").RmEndSession

RmStartSession.restype = wintypes.DWORD
RmStartSession.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, wintypes.LPWSTR]

RmRegisterResources.restype = wintypes.DWORD
RmRegisterResources.argtypes = [wintypes.DWORD, wintypes.UINT, ctypes.POINTER(ctypes.c_wchar_p),
                                 wintypes.UINT, ctypes.POINTER(wintypes.HANDLE), wintypes.UINT, ctypes.POINTER(ctypes.c_wchar_p)]

RmGetList.restype = wintypes.DWORD
RmGetList.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT),
                       ctypes.POINTER(RM_PROCESS_INFO := type("RM_PROCESS_INFO", (ctypes.Structure,), {
                           "_fields_": [("Process", type("RM_UNIQUE_PROCESS", (ctypes.Structure,), {
                               "_fields_": [("pid", wintypes.DWORD), ("StartTime", wintypes.FILETIME)]
                           })),
                               ("strAppName", wintypes.WCHAR * 256),
                               ("strServiceShortName", wintypes.WCHAR * 64),
                               ("ApplicationType", wintypes.DWORD),
                               ("AppStatus", wintypes.DWORD),
                               ("TSSessionId", wintypes.DWORD),
                               ("bRestartable", wintypes.BOOL)]
                       })),
                       ctypes.POINTER(wintypes.DWORD)]

RmEndSession.restype = wintypes.DWORD
RmEndSession.argtypes = [wintypes.DWORD]

target_sessions = ["regal-adasaurus", "capricious-primrose", "magical-quiver",
                   "deciduous-army", "soft-walk", "stone-aries"]

for sid in target_sessions:
    lock_path = os.path.join(SESSION_LOCKS_DIR, f"{sid}.lock")

    session_handle = wintypes.DWORD(0)
    session_key = ctypes.create_unicode_buffer(256, "\0" * 256)

    res = RmStartSession(ctypes.byref(session_handle), 0, session_key)
    if res != 0:
        print(f"{sid:30s} RmStartSession failed: {res}")
        continue

    files = (ctypes.c_wchar_p * 1)(lock_path)
    res = RmRegisterResources(session_handle, 1, files, 0, None, 0, None)
    if res != 0:
        print(f"{sid:30s} RmRegisterResources failed: {res}")
        RmEndSession(session_handle)
        continue

    proc_count = wintypes.UINT(0)
    reboot_reasons = wintypes.DWORD(0)
    # Primera llamada para obtener el count
    RmGetList(session_handle, ctypes.byref(proc_count), ctypes.byref(proc_count), None, ctypes.byref(reboot_reasons))

    if proc_count.value == 0:
        print(f"{sid:30s} no process found")
        RmEndSession(session_handle)
        continue

    # Segunda llamada con buffer
    procs = (RM_PROCESS_INFO * proc_count.value)()
    actual_count = wintypes.UINT(proc_count.value)
    res = RmGetList(session_handle, ctypes.byref(actual_count), ctypes.byref(proc_count), procs, ctypes.byref(reboot_reasons))

    for i in range(actual_count.value):
        p = procs[i]
        print(f"{sid:30s} pid={p.Process.pid} app={p.strAppName}")

    RmEndSession(session_handle)
