; Inno Setup script for the Provenance Wiki Windows installer.
;
; Built by .github/workflows/desktop-windows.yml:
;   iscc /DAppVersion=1.0.0-test.1 /DTesting=1 /DSourceDir=..\..\build\pyi-dist\ProvenanceWiki ^
;        /DOutputDir=..\..\build desktop\windows\installer.iss
;
; - Installs for the current user only; no administrator rights needed.
; - Program files: %LOCALAPPDATA%\Programs\Provenance Wiki
;   Research data (separate, never inside the program folder):
;   %LOCALAPPDATA%\ProvenanceWiki   (database, photographs, logs)
; - Upgrading installs over the previous version; data and keys are kept.
; - Uninstalling keeps the research data unless the user explicitly chooses
;   to delete it (the uninstaller asks; "No" is the default).
; - Does not start automatically at login.

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\build\pyi-dist\ProvenanceWiki"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\build"
#endif

#ifdef Testing
  #define AppName "Provenance Wiki (Testing)"
  #define OutputName "ProvenanceWiki-Setup-" + AppVersion + "-Windows-x64-TESTING-ONLY-unsigned"
#else
  #define AppName "Provenance Wiki"
  #define OutputName "ProvenanceWiki-Setup-" + AppVersion + "-Windows-x64"
#endif

[Setup]
; Keep this AppId fixed: it is how upgrades find the previous installation.
AppId={{6F4E2C1A-3B7D-4E59-9A0C-8D2B1F7E5A41}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Provenance Wiki project
AppPublisherURL=https://github.com/caomengge/provenance-wiki
AppSupportURL=https://github.com/caomengge/provenance-wiki/blob/main/docs/INSTALL_WINDOWS.md
VersionInfoVersion=1.0.0.0
PrivilegesRequired=lowest
DefaultDirName={autopf}\Provenance Wiki
DefaultGroupName=Provenance Wiki
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename={#OutputName}
SetupIconFile=..\..\build\icon.ico
UninstallDisplayIcon={app}\ProvenanceWiki.exe
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no
#ifdef Testing
InfoBeforeFile=README-TESTING.txt
#endif
LicenseFile=..\..\LICENSE

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\ProvenanceWiki.exe"; Comment: "Open your Provenance Wiki archive"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\ProvenanceWiki.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ProvenanceWiki.exe"; Description: "Open {#AppName} now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Stop a running copy so its files can be removed.
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM ProvenanceWiki.exe"; Flags: runhidden; RunOnceId: "StopApp"

[Code]
function DataDir(): String;
begin
  Result := ExpandConstant('{localappdata}\ProvenanceWiki');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and (not UninstallSilent) and DirExists(DataDir()) then
  begin
    if MsgBox('Provenance Wiki has been removed.' + #13#10 + #13#10 +
              'Your research archive (database and photographs) is still saved in:' + #13#10 +
              DataDir() + #13#10 + #13#10 +
              'Do you also want to PERMANENTLY DELETE your research archive?' + #13#10 +
              'This cannot be undone. Choose No to keep it (recommended).',
              mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
    begin
      DelTree(DataDir(), True, True, True);
    end;
  end;
end;
