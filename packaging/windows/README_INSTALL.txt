Devin Mobile Dashboard - Instrucciones de Instalacion
======================================================

INSTALACION
-----------
1. Ejecuta DevinMobileSetup.exe como administrador.
2. Sigue el asistente. Por defecto se instala en:
   C:\Program Files\DevinMobile
3. Marca "Iniciar con Windows" si quieres que arranque automaticamente.

CONFIGURACION
-------------
1. Edita C:\Program Files\DevinMobile\config.json con tu usuario y contrasena:
   {
     "username": "tu_usuario",
     "password": "tu_contrasena"
   }

2. Asegurate de que Devin Desktop esta instalado y has iniciado sesion al menos una vez.

3. Asegurate de que Tailscale esta instalado y conectado para acceso desde el movil.

INICIAR
-------
- Manual: Ejecuta DevinMobile.exe
- Automatico: Si marcaste "Iniciar con Windows", se inicia como servicio.

ACCESO DESDE EL MOVIL
---------------------
1. Instala Tailscale en tu movil.
2. Conectate a la misma red Tailscale.
3. Abre el navegador y ve a: http://<IP-TAILSCALE-PC>:8787
4. Introduce tu usuario y contrasena.

INSTALAR COMO SERVICIO DE WINDOWS (opcional)
--------------------------------------------
Si quieres que corra como servicio de Windows (se reinicia solo):

1. Descarga NSSM desde https://nssm.cc/download
2. Abre CMD como administrador:
   nssm install DevinMobile "C:\Program Files\DevinMobile\DevinMobile.exe"
   nssm set DevinMobile AppDirectory "C:\Program Files\DevinMobile"
   nssm set DevinMobile AppEnvironmentExtra DEVIN_MOBILE_PORT=8787
   nssm set DevinMobile Start SERVICE_AUTO_START
   nssm start DevinMobile

ACTUALIZACION
-------------
El dashboard comprueba automaticamente si hay actualizaciones.
Tambien puedes descargar la nueva version y reinstalar encima.

SOPORTE
-------
Documentacion: https://github.com/devin-mobile
