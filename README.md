# DevinMobile

Controla tus sesiones locales de **Devin CLI** desde el móvil.

DevinMobile es un puente (bridge) que expone las sesiones de Devin CLI/Desktop a través de una API HTTP y un dashboard web optimizado para móvil. Permite ver todas tus sesiones, enviar prompts, leer el historial y observar la actividad en streaming — todo desde el teléfono, a través de [Tailscale](https://tailscale.com/) para acceso seguro.

> **Aviso**: Este proyecto no es oficial de Cognition/Devin. Es una herramienta independiente creada por la comunidad.

## Cómo funciona

```
┌─────────────┐     Tailscale      ┌──────────────┐     ACP/stdio     ┌────────────┐
│   Móvil     │ ◄───────────────► │  Bridge HTTP │ ◄───────────────► │  Devin CLI │
│  (APK/Web)  │   HTTP + Auth     │  (Python)    │   JSON-RPC         │  (local)   │
└─────────────┘                   └──────────────┘                    └────────────┘
```

- El **bridge** (`server.py`) se ejecuta en el PC donde tienes Devin CLI/Desktop.
- Se comunica con Devin via **ACP** (Agent Client Protocol) sobre stdio.
- El **móvil** se conecta al bridge via HTTP con autenticación Basic Auth.
- **Tailscale** proporciona conectividad segura entre el móvil y el PC sin exponer puertos a internet.

## Requisitos

- **Devin CLI** instalado y autenticado ([docs.devin.ai/cli](https://docs.devin.ai/cli))
- **Python 3.10+**
- **Tailscale** instalado en el PC y en el móvil (opcional pero recomendado)
- Para compilar el APK: **Node.js 18+**, **JDK 17**, **Android SDK**

## Instalación rápida (PC)

### 1. Clonar el repositorio

```bash
git clone https://github.com/elbada25/DevinMobile.git
cd DevinMobile
```

### 2. Instalar dependencias de Python

```bash
pip install -r requirements.txt
```

### 3. Configurar credenciales del dashboard

Copia el archivo de ejemplo y edita con tu usuario y contraseña:

```bash
cp config.json.example config.json
```

Edita `config.json` con un usuario y contraseña seguros:

```json
{
  "username": "tu_usuario",
  "password": "tu_contrasena_segura"
}
```

> **Importante**: `config.json` está en `.gitignore` y no se sube a GitHub. Nunca compartas este archivo.

### 4. Verificar que Devin CLI está autenticado

```bash
devin auth status
```

Si no estás logueado:

```bash
devin auth login
```

En servidores headless (sin navegador):

```bash
devin auth login --force-manual-token-flow
```

### 5. Iniciar el bridge

```bash
python server.py
```

Verás algo como:

```
  Sirviendo en http://0.0.0.0:8787
  Usuario: tu_usuario
  Credenciales Devin: OK
  Devin exe: /ruta/a/devin
  Tailscale IP: http://100.x.y.z:8787
```

### 6. Abrir el dashboard

- **Desde el mismo PC**: `http://localhost:8787`
- **Desde el móvil (vía Tailscale)**: `http://100.x.y.z:8787` (usa la IP de Tailscale de tu PC)

Introduce el usuario y contraseña que configuraste en `config.json`.

## Instalación como servicio

### Windows (NSSM)

```powershell
# Descargar NSSM: https://nssm.cc/
nssm install DevinMobile "C:\Python\python.exe" "C:\ruta\a\DevinMobile\server.py"
nssm start DevinMobile
```

### Linux (systemd)

```bash
sudo cp packaging/linux/devin-mobile.service /etc/systemd/system/
sudo sed -i 's|/opt/devin-mobile|/ruta/a/DevinMobile|g' /etc/systemd/system/devin-mobile.service
sudo systemctl daemon-reload
sudo systemctl enable devin-mobile
sudo systemctl start devin-mobile
```

O usa el script de instalación:

```bash
sudo bash packaging/linux/install.sh
```

## App de Android (APK)

### Descargar el APK precompilado

Ve a [Releases](https://github.com/elbada25/DevinMobile/releases) y descarga `DevinMobile.apk`.

### Compilar el APK desde código

```bash
# Requisitos: Node.js 18+, JDK 17, Android SDK
cd packaging/android
npm install
node copy-assets.js
npx cap sync android
cd android
./gradlew assembleDebug     # Linux/Mac
.\gradlew.bat assembleDebug  # Windows
```

El APK se genera en:
```
packaging/android/android/app/build/outputs/apk/debug/app-debug.apk
```

### Instalar el APK

Transfiere el APK a tu móvil e instálalo. Necesitas activar "Orígenes desconocidos" en los ajustes de Android.

## Configurar el móvil

### Opción A: Tailscale (recomendado)

1. Instala **Tailscale** en tu móvil ([Play Store](https://play.google.com/store/apps/details?id=com.tailscale.ipn)).
2. Inicia sesión con la misma cuenta que en el PC.
3. Abre la app DevinMobile.
4. En la pantalla de login, introduce el usuario y contraseña de `config.json`.
5. Pulsa **PCs** → **Añadir PC**.
6. Introduce:
   - **Nombre**: el que quieras (ej: "PC Oficina")
   - **URL**: la IP de Tailscale de tu PC (ej: `http://100.x.y.z:8787`)
   - **Usuario y contraseña**: los de `config.json`
7. Pulsa **Guardar**.

### Opción B: Red local (LAN)

Si no quieres usar Tailscale, puedes usar la IP local de tu PC:

1. Averigua la IP local de tu PC: `ipconfig` (Windows) o `hostname -I` (Linux).
2. En el móvil, conéctate a la misma red WiFi.
3. Usa `http://192.168.x.x:8787` como URL del PC.

> **Nota**: En LAN, el tráfico no está encriptado. Usa Tailscale para mayor seguridad.

### Emparejamiento por QR

1. Abre el dashboard en el PC (`http://localhost:8787`).
2. Pulsa el botón **Emparejar**.
3. Se mostrará un código QR con la configuración del PC.
4. En el móvil, pulsa **Emparejar** y escanea el QR.

## Añadir más PCs

Puedes añadir tantos PCs como quieras:

1. Instala el bridge en cada PC (sigue los pasos de instalación).
2. En el móvil, pulsa **PCs** → **Añadir PC**.
3. Introduce los datos de cada PC.

Las sesiones se agrupan por PC en la lista principal.

## Funcionalidades

- Lista de sesiones agrupadas por PC
- Crear sesiones nuevas con directorio personalizado
- Ver historial completo de conversaciones
- Enviar prompts a sesiones existentes
- Streaming de respuestas en tiempo real
- Cancelar sesiones en curso
- Liberar sesiones activas en Devin Desktop (con confirmación)
- Renombrar y archivar sesiones
- Navegador de directorios para elegir `cwd`
- Emparejamiento por QR
- Soporte multi-PC

## Endpoints de la API

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| GET | `/api/version` | Versión del bridge |
| GET | `/api/health` | Estado del bridge y Devin |
| POST | `/api/login` | Autenticación |
| GET | `/api/sessions` | Lista de sesiones |
| GET | `/api/sessions/{id}/history` | Historial de una sesión |
| GET | `/api/sessions/{id}/stream` | Stream SSE de eventos |
| POST | `/api/sessions/{id}/prompt` | Enviar prompt |
| POST | `/api/sessions/{id}/stop` | Detener sesión |
| POST | `/api/sessions/new` | Crear sesión nueva |
| GET | `/api/directories` | Directorios comunes |
| GET | `/api/browse?path=...` | Navegar directorios |
| POST | `/api/mkdir` | Crear directorio |
| GET | `/api/pairing` | Datos de emparejamiento QR |

## Tests

```bash
pip install pytest pytest-asyncio
pytest tests/ -v
```

## Estructura del proyecto

```
DevinMobile/
├── server.py              # Bridge FastAPI
├── index.html             # Dashboard web (PWA)
├── requirements.txt       # Dependencias Python
├── config.json.example    # Plantilla de configuración
├── tests/                 # Tests de integración
├── packaging/
│   ├── android/           # Proyecto Capacitor (APK)
│   ├── windows/           # Instalador Windows (Inno Setup)
│   └── linux/             # Servicio systemd + install.sh
└── .gitignore
```

## Solución de problemas

### "Credenciales Devin: FALTA"

El bridge no encuentra el archivo de credenciales de Devin. Verifica:

```bash
devin auth status
```

Si no estás logueado, ejecuta `devin auth login`.

### "Could not open browser for authentication"

Esto ocurre en servidores headless. Usa:

```bash
devin auth login --force-manual-token-flow
```

Y pega tu token de API de Devin manualmente.

### El móvil no puede conectar al PC

1. Verifica que ambos están en la misma red Tailscale.
2. Comprueba que el bridge está corriendo: `curl http://localhost:8787/api/version`
3. Verifica que el firewall permite conexiones en el puerto 8787.
4. Asegúrate de que la URL en la app usa la IP correcta de Tailscale.

### Las páginas de OAuth se abren en el navegador

El bridge inyecta `WINDSURF_API_KEY` automáticamente para evitar esto. Si sigue ocurriendo, verifica que el archivo de credenciales de Devin existe y es legible.

## Licencia

MIT — ver [LICENSE](LICENSE).

## Aviso

Este proyecto no está afiliado a Cognition, Devin AI ni Windsurf. Usa el protocolo ACP (Agent Client Protocol) público para comunicarse con Devin CLI. Respeta los [términos de servicio de Devin](https://devin.ai/terms).
