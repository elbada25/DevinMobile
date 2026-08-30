; Inno Setup script para Devin Mobile Dashboard
; Genera un instalador Windows profesional

#define MyAppName "Devin Mobile Dashboard"
#define MyAppVersion "3.1.0"
#define MyAppPublisher "Devin Mobile"
#define MyAppExeName "DevinMobile.exe"
#define MyAppURL "https://github.com/devin-mobile"

[Setup]
AppId={{DEVIN-MOBILE-2026-0001}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\DevinMobile
DefaultGroupName=Devin Mobile
AllowNoIcons=yes
OutputDir=Output
OutputBaseFilename=DevinMobileSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64
ArchitecturesAllowed=x64
PrivilegesRequired=admin
; Instalar como servicio automatico
DisableProgramGroupPage=yes

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear icono en el escritorio"; GroupDescription: "Iconos:"
Name: "startup"; Description: "Iniciar con Windows (servicio en segundo plano)"; GroupDescription: "Inicio:"

[Files]
Source: "..\..\dist\DevinMobile.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\config.json.example"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist
Source: "..\..\index.html"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\README_INSTALL.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Devin Mobile"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Desinstalar Devin Mobile"; Filename: "{uninstallexe}"
Name: "{commondesktop}\Devin Mobile"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Abrir navegador con el dashboard tras instalar
Filename: "http://localhost:8787"; Description: "Abrir dashboard en el navegador"; Flags: postinstall nowait shellexec
; Mostrar instrucciones
Filename: "notepad.exe"; Parameters: "{app}\README_INSTALL.txt"; Description: "Ver instrucciones de instalacion"; Flags: postinstall nowait skipifsilent

[UninstallRun]
; Detener el servicio antes de desinstalar
Filename: "{app}\DevinMobile.exe"; Parameters: "--stop-service"; Flags: runhidden

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;
