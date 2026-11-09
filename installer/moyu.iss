; 魔芋 手机投屏 · Inno Setup 6 安装脚本
; 用法（在仓库根目录）：
;   python tools\build_installer.py
; 或手动：
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DMyAppVersion=1.0.0 installer\moyu.iss

#define MyAppName "魔芋"
#define MyAppNameEn "Moyu"
#define MyAppExeName "魔芋.exe"
#define MyAppPublisher "zzm6666"
#define MyAppURL "https://github.com/zzm6666/moyu-mobile-screen-mirroring"

; 版本号由构建脚本用 /DMyAppVersion=1.2.3 注入（来源 tools\version.txt）
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

[Setup]
; AppId 固定不变：升级时才能识别成同一个程序（不要改动！）
AppId={{A7E5C1D2-4B8F-4C3A-9E62-1D4F8B0C7A53}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
VersionInfoVersion={#MyAppVersion}
VersionInfoDescription={#MyAppName} 手机投屏安装程序
VersionInfoProductName={#MyAppName}

; 默认装到当前用户目录：不需要管理员权限，也不污染 Program Files
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
AllowNoIcons=yes

OutputDir=..\dist-installer
OutputBaseFilename={#MyAppName}-Setup-{#MyAppVersion}
SetupIconFile=..\tools\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
MinVersion=10.0

[Languages]
; 有中文语言包就用中文，没有就退回英文（Inno 官方包不含简体中文）
#if FileExists(AddBackslash(SourcePath) + "ChineseSimplified.isl")
Name: "cn"; MessagesFile: "ChineseSimplified.isl"
#endif
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
Source: "..\tools\魔芋\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion isreadme

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[Code]
// 卸载时问一句：要不要连配置和日志一起删（%APPDATA%\魔芋）
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{userappdata}\{#MyAppName}');
    if DirExists(DataDir) then
    begin
      if MsgBox('是否同时删除配置和日志？' + #13#10 + #13#10 + DataDir, mbConfirmation, MB_YESNO) = IDYES then
        DelTree(DataDir, True, True, True);
    end;
  end;
end;
