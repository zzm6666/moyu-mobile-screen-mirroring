<#
.SYNOPSIS
    魔芋 免安装包安装脚本（PowerShell 版安装器）

.DESCRIPTION
    当 Inno Setup 安装包在个别机器上被拒绝（例如 “Setup was unable to create the
    directory ... Error 5: 拒绝访问”）时，用这个脚本做同样的事：

      1. 把程序文件复制到 %LOCALAPPDATA%\Programs\魔芋
      2. 创建开始菜单 / 桌面快捷方式
      3. 写入“应用和功能”里的卸载项（HKCU，不需要管理员）
      4. 生成卸载脚本 uninstall.ps1 放到安装目录

.PARAMETER Source
    程序文件所在目录（包含 魔芋.exe 和 _internal）。默认找脚本所在目录下的
    “魔芋” 子目录，或当前目录。

.PARAMETER Destination
    安装目录。默认 %LOCALAPPDATA%\Programs\魔芋。

.PARAMETER NoDesktopIcon
    不创建桌面快捷方式。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install.ps1 -Source "D:\下载\魔芋-1.0.0"
#>
[CmdletBinding()]
param(
    [string]$Source,
    [string]$Destination = (Join-Path $env:LOCALAPPDATA 'Programs\魔芋'),
    [switch]$NoDesktopIcon
)

$ErrorActionPreference = 'Stop'
$AppName = '魔芋'
$ExeName = '魔芋.exe'
$AppId = '{A7E5C1D2-4B8F-4C3A-9E62-1D4F8B0C7A53}'
$AppIdFull = "$AppId" + '_is1'
$AppURL = 'https://github.com/zzm6666/moyu-mobile-screen-mirroring'

function Say([string]$msg) { Write-Host "  $msg" }

Write-Host "魔芋 安装程序（PowerShell 版）" -ForegroundColor Cyan
Write-Host ("  安装目录：" + $Destination)

# ---- 1. 定位源文件 ----
if (-not $Source) {
    $candidates = @(
        (Join-Path $PSScriptRoot '魔芋'),
        $PSScriptRoot,
        (Get-Location).Path
    )
    foreach ($c in $candidates) {
        if (Test-Path (Join-Path $c $ExeName)) { $Source = $c; break }
    }
}
if (-not $Source -or -not (Test-Path (Join-Path $Source $ExeName))) {
    throw "找不到 $ExeName，请用 -Source 指定程序文件目录。"
}
Say "源目录：$Source"

# ---- 2. 退出正在运行的实例 ----
$running = Get-Process -Name '魔芋' -ErrorAction SilentlyContinue
if ($running) {
    Say "正在关闭已运行的魔芋…"
    $running | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 600
}
# 升级覆盖时旧版自带的 adb 服务会占着 adb.exe，不关掉就会复制失败
Get-Process -Name 'scrcpy', 'adb' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
$oldAdb = Join-Path $Destination '_internal\scrcpy\adb.exe'
if (Test-Path $oldAdb) {
    try { & $oldAdb kill-server 2>$null | Out-Null } catch { }
    Start-Sleep -Milliseconds 400
}

# ---- 3. 复制文件 ----
if (Test-Path $Destination) {
    Say "清理旧版本…"
    Remove-Item $Destination -Recurse -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Path $Destination -Force | Out-Null
Copy-Item (Join-Path $Source '*') -Destination $Destination -Recurse -Force
foreach ($extra in 'LICENSE.txt', 'README.md') {
    $from = Join-Path $Source $extra
    if (Test-Path $from) { Copy-Item $from -Destination $Destination -Force }
}
$count = (Get-ChildItem $Destination -Recurse -File | Measure-Object).Count
Say "已复制 $count 个文件"

$exePath = Join-Path $Destination $ExeName
$version = '1.0.0'
$verFile = Join-Path $Destination '_internal\version.txt'
if (Test-Path $verFile) { $version = (Get-Content $verFile -Raw).Trim() }

# ---- 4. 快捷方式 ----
function New-Shortcut([string]$LinkPath) {
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($LinkPath)
    $lnk.TargetPath = $exePath
    $lnk.WorkingDirectory = $Destination
    $lnk.IconLocation = "$exePath,0"
    $lnk.Description = '魔芋 手机投屏'
    $lnk.Save()
}

$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$smLink = Join-Path $startMenu "$AppName.lnk"
New-Shortcut $smLink
Say "开始菜单快捷方式：$smLink"

if (-not $NoDesktopIcon) {
    $desktop = [Environment]::GetFolderPath('Desktop')
    $dtLink = Join-Path $desktop "$AppName.lnk"
    New-Shortcut $dtLink
    Say "桌面快捷方式：$dtLink"
}

# ---- 5. 卸载信息（HKCU，出现在“设置 → 应用”） ----
$uninstallScript = Join-Path $Destination 'uninstall.ps1'
$uninstallCmd = Join-Path $Destination '卸载.cmd'
$psExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$cmdLine = '"{0}" -ExecutionPolicy Bypass -NoProfile -File "{1}"' -f $psExe, $uninstallScript

$key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$AppIdFull"
New-Item -Path $key -Force | Out-Null
$sizeKb = [int]((Get-ChildItem $Destination -Recurse -File | Measure-Object Length -Sum).Sum / 1KB)
Set-ItemProperty -Path $key -Name 'DisplayName' -Value $AppName
Set-ItemProperty -Path $key -Name 'DisplayVersion' -Value $version
Set-ItemProperty -Path $key -Name 'Publisher' -Value 'zzm6666'
Set-ItemProperty -Path $key -Name 'DisplayIcon' -Value $exePath
Set-ItemProperty -Path $key -Name 'InstallLocation' -Value $Destination
Set-ItemProperty -Path $key -Name 'UninstallString' -Value $cmdLine
Set-ItemProperty -Path $key -Name 'QuietUninstallString' -Value ($cmdLine + ' -Quiet')
Set-ItemProperty -Path $key -Name 'URLInfoAbout' -Value $AppURL
Set-ItemProperty -Path $key -Name 'NoModify' -Value 1 -Type DWord
Set-ItemProperty -Path $key -Name 'NoRepair' -Value 1 -Type DWord
Set-ItemProperty -Path $key -Name 'EstimatedSize' -Value $sizeKb -Type DWord
Say "已写入卸载信息（设置 → 应用 里可见）"

# ---- 6. 卸载脚本 ----
$uninstallBody = @'
param([switch]$Quiet)
$ErrorActionPreference = 'SilentlyContinue'
$dest = Split-Path -Parent $MyInvocation.MyCommand.Path
$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$desktop = [Environment]::GetFolderPath('Desktop')
Get-Process -Name '魔芋' | Stop-Process -Force
Start-Sleep -Milliseconds 600
Remove-Item (Join-Path $startMenu '魔芋.lnk') -Force
Remove-Item (Join-Path $desktop '魔芋.lnk') -Force
Remove-Item 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{A7E5C1D2-4B8F-4C3A-9E62-1D4F8B0C7A53}_is1' -Recurse -Force
$data = Join-Path $env:APPDATA '魔芋'
if (-not $Quiet -and (Test-Path $data)) {
    $answer = Read-Host "是否同时删除配置和日志（$data）？[y/N]"
    if ($answer -match '^(y|Y)') { Remove-Item $data -Recurse -Force }
}
Set-Location $env:TEMP
Remove-Item $dest -Recurse -Force
if (-not $Quiet) { Write-Host "已卸载。" -ForegroundColor Green; Start-Sleep -Seconds 1 }
'@
Set-Content -Path $uninstallScript -Value $uninstallBody -Encoding UTF8

$cmdBody = "@echo off`r`n`"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe`" -ExecutionPolicy Bypass -NoProfile -File `"%~dp0uninstall.ps1`"`r`npause`r`n"
Set-Content -Path $uninstallCmd -Value $cmdBody -Encoding OEM

Say "卸载脚本：$uninstallCmd"

Write-Host ""
Write-Host "安装完成：$Destination" -ForegroundColor Green
Write-Host "直接运行：$exePath"
if (-not $Quiet) {
    $answer = Read-Host "现在启动魔芋？[y/N]"
    if ($answer -match '^(y|Y)') { Start-Process $exePath }
}
