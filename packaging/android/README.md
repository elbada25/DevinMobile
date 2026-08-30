# Devin Mobile - Build APK Android

## Prerequisitos

1. **Node.js** 18+ instalado
2. **Android Studio** instalado (con Android SDK)
3. **Java JDK** 17+

## Pasos

### 1. Instalar dependencias

```bash
cd packaging/android
npm install
```

### 2. Inicializar Capacitor (solo la primera vez)

```bash
npx cap init DevinMobile com.devinmobile.app --web-dir=www
npx cap add android
```

### 3. Copiar assets y sincronizar

```bash
npm run build
npm run sync
```

### 4. Abrir en Android Studio

```bash
npm run open
```

### 5. Generar APK debug

```bash
npm run build:apk
```

El APK estara en: `android/app/build/outputs/apk/debug/app-debug.apk`

### 6. Generar APK release (firmado)

1. Crear keystore:
```bash
keytool -genkey -v -keystore devin-mobile.keystore -alias devin-mobile -keyalg RSA -keysize 2048 -validity 10000
```

2. Configurar `android/app/build.gradle`:
```gradle
android {
    signingConfigs {
        release {
            storeFile file('../../devin-mobile.keystore')
            storePassword 'tu_password'
            keyAlias 'devin-mobile'
            keyPassword 'tu_password'
        }
    }
    buildTypes {
        release {
            signingConfig signingConfigs.release
        }
    }
}
```

3. Build:
```bash
npm run build:release
```

El APK estara en: `android/app/build/outputs/apk/release/app-release.apk`

## Instalar APK en el movil

```bash
adb install android/app/build/outputs/apk/debug/app-debug.apk
```

O copia el APK al movil e instalalo manualmente.

## Configuracion

El archivo `capacitor.config.json` configura:
- `cleartext: true` — permite HTTP (necesario para Tailscale IPs sin HTTPS)
- `allowMixedContent: true` — permite contenido mixto

La app carga `www/index.html` que es una copia del frontend.
Para actualizar el frontend, ejecuta `npm run build && npm run sync`.
