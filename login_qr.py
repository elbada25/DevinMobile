#!/usr/bin/env python3
"""Script para iniciar login de Devin CLI y mostrar QR en consola.

Uso: python login_qr.py
"""
import subprocess, time, re, sys, os, webbrowser

DEVIN_EXE = os.environ.get("DEVIN_EXE", "")
if not DEVIN_EXE:
    # Auto-detectar
    if os.name == "nt":
        DEVIN_EXE = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                                 "devin", "cli", "bin", "devin.exe")
    else:
        DEVIN_EXE = os.path.expanduser("~/.local/bin/devin")

if not os.path.exists(DEVIN_EXE):
    print(f"Error: No se encontro devin en {DEVIN_EXE}")
    sys.exit(1)

print(f"Iniciando login con {DEVIN_EXE}...")
proc = subprocess.Popen(
    [DEVIN_EXE, "auth", "login", "--force-manual-token-flow"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    text=True, encoding="utf-8", errors="replace", bufsize=1,
    creationflags=0x08000000 if os.name == "nt" else 0,
)

url = None
start = time.time()
while time.time() - start < 15:
    line = proc.stdout.readline()
    if not line:
        break
    line = line.strip()
    if "http" in line:
        m = re.search(r'(https?://[^\s]+)', line)
        if m:
            url = m.group(1)
            break

if not url:
    print("Error: No se pudo obtener la URL de login")
    proc.terminate()
    sys.exit(1)

print(f"\nURL de login: {url}\n")

# Intentar abrir el navegador
try:
    webbrowser.open(url)
    print("Se abrio el navegador automaticamente.\n")
except Exception:
    pass

# Mostrar QR en consola si qrcode esta disponible
try:
    import qrcode
    qr = qrcode.QRCode(box_size=1, border=1)
    qr.add_data(url)
    qr.make(fit=True)
    print("Escanea este QR con tu movil:\n")
    qr.print_ascii(invert=True)
    print()
except ImportError:
    print("Tip: Instala 'qrcode' (pip install qrcode) para mostrar QR en consola.\n")

print("Despues de iniciar sesion, copia el codigo y pegalo aqui:")
code = input("Codigo: ").strip()

if code:
    proc.stdin.write(code + "\n")
    proc.stdin.flush()
    # Esperar respuesta
    start = time.time()
    while time.time() - start < 20:
        line = proc.stdout.readline()
        if not line:
            break
        line = line.strip()
        print(f"  {line}")
        if "logged in" in line.lower() or "success" in line.lower() or "credentials" in line.lower():
            print("\nLogin exitoso!")
            proc.wait(timeout=5)
            sys.exit(0)
        if "error" in line.lower() or "invalid" in line.lower():
            print(f"\nError: {line}")
            sys.exit(1)

    rc = proc.poll()
    if rc == 0:
        print("\nLogin exitoso!")
    else:
        print(f"\nProceso termino con codigo {rc}")
