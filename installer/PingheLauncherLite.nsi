; Pinghe Launcher Lite 的独立 NSIS 安装器；旧 WiX / tkinter 安装器只供历史版本参考。
; 与 PH Launcher 2.0.13 的 per-user NSIS 默认目录相同，两个程序通过 data/ 共用资料。
; 安装和卸载绝不递归清理共用目录；PH Launcher 的 resources/locales 等文件不属于 Lite。
Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"
; nsExec 是 NSIS 自带插件（Plugins\x86-unicode\nsExec.dll），直接用插件命令即可；
; electron-builder 的 NSIS 发行包没有 nsExec.nsh，不能 include 它。

!ifndef VERSION
  !error "构建脚本必须传入 /DVERSION=<installer.py 与 updater.py 一致的版本>"
!endif
!ifndef APP_EXE_SOURCE
  !error "构建脚本必须传入 /DAPP_EXE_SOURCE=<PyInstaller 单文件 exe>"
!endif
!ifndef OUTPUT_FILE
  !error "构建脚本必须传入 /DOUTPUT_FILE=<安装包输出路径>"
!endif

Name "Pinghe Launcher Lite ${VERSION}"
OutFile "${OUTPUT_FILE}"
InstallDir "$LOCALAPPDATA\Programs\PH Launcher"
RequestExecutionLevel user
SetCompressor /SOLID lzma
ShowInstDetails show
ShowUninstDetails show

!define LITE_EXE "PingheLauncherLite.exe"
!define LITE_UNINSTALLER "Uninstall Pinghe Launcher Lite.exe"
!define LITE_SHORTCUT "Pinghe Launcher Lite.lnk"
!define LITE_UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\PingheLauncherLite"

; 标准 MUI2 辅助安装的目录页：明确提示共用数据所在目录，用户可浏览修改。
!define MUI_DIRECTORYPAGE_TEXT_TOP "默认与 PH Launcher 同目录，共用 data。旧 MSI Lite 的数据不会自动合并，原目录保留，请勿删除旧数据；卸载 Lite 也不会删除共用数据。"
!define MUI_ABORTWARNING
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH
!insertmacro MUI_LANGUAGE "SimpChinese"

; 目录页的检查也会在 Section 执行，防止 /S 静默安装绕过页面检查。
Function VerifyInstallDirectory
  StrCmp "$INSTDIR" "" invalid_directory
  ClearErrors
  CreateDirectory "$INSTDIR"
  IfErrors invalid_directory
  ; 临时文件只在选中目录中创建，证明当前用户能写入；随后立即删除。
  ClearErrors
  GetTempFileName $0 "$INSTDIR"
  IfErrors invalid_directory
  StrCmp "$0" "" invalid_directory
  Delete "$0"
  Return
invalid_directory:
  MessageBox MB_ICONSTOP|MB_OK "所选安装目录无法写入：$INSTDIR。请改选当前用户有写入权限的目录。" /SD IDOK
  Abort
FunctionEnd

Section "Pinghe Launcher Lite" SecLite
  SetShellVarContext current
  Call VerifyInstallDirectory
  ; 如果旧 MSI 默认目录有配置且目标 data 已有 PHL 数据，安装前提示需人工核对。
  ; 绝不自动合并、覆盖或静默卸载旧 MSI 数据（静默模式也在日志中留痕）。
  ; 自动更新时 /D 就是旧 MSI 目录本身，此时"旧数据"与"目标数据"是同一份，
  ; 不能弹"两份数据不会合并"的误导提示。
  StrCmp "$INSTDIR" "$LOCALAPPDATA\Programs\PingheLauncherLite" same_as_legacy
  IfFileExists "$LOCALAPPDATA\Programs\PingheLauncherLite\data\settings.yaml" 0 old_data_checked
  IfFileExists "$INSTDIR\data\*.*" 0 old_data_checked
  DetailPrint "旧 Lite 数据不会自动合并；原目录保留，请勿删除旧数据。"
  MessageBox MB_ICONEXCLAMATION|MB_OK "检测到旧 Lite 数据和目标目录现有共用数据。旧 Lite 数据不会自动合并，原目录保留；切勿删除旧数据。" /SD IDOK
old_data_checked:
  ; 目标 data 为空（没有 PHL/其它数据）时才提供「复制旧 MSI 数据」选项：
  ; 只有用户明确点「是」才复制；绝不覆盖、绝不删除旧目录；静默模式默认不复制。
  IfFileExists "$LOCALAPPDATA\Programs\PingheLauncherLite\data\settings.yaml" 0 install_files
  IfFileExists "$INSTDIR\data\settings.yaml" install_files
  IfFileExists "$INSTDIR\data\*.*" install_files
  MessageBox MB_YESNO|MB_ICONQUESTION "检测到旧版 Lite 的数据（$LOCALAPPDATA\Programs\PingheLauncherLite\data）。$\r$\n是否把它复制到新目录 $INSTDIR\data？（旧目录会保留，不会删除。）" /SD IDNO IDNO install_files
  DetailPrint "用户选择复制旧 Lite 数据到新目录（源目录保留）。"
  nsExec::ExecToLog 'xcopy /E /I /Y "$LOCALAPPDATA\Programs\PingheLauncherLite\data\*" "$INSTDIR\data\"'
  Pop $0
  DetailPrint "复制旧数据的退出码：$0（0=成功）"
  Goto install_files
same_as_legacy:
  DetailPrint "安装目录就是旧 MSI 目录，沿用现有 data。"
install_files:
  SetOutPath "$INSTDIR"
  ; 只覆盖 Lite 自己的 exe。不要调用旧 MSI 卸载程序，更不要清理其用户数据。
  File /oname=${LITE_EXE} "${APP_EXE_SOURCE}"
  IfErrors install_failed
  ; 标记让 Lite 使用安装目录的 data/，与同目录 PH Launcher 共用。
  ; portable.flag 可能被其他版本依赖，所以卸载时也保留它。
  ClearErrors
  FileOpen $0 "$INSTDIR\portable.flag" a
  IfErrors install_failed
  FileClose $0
  CreateDirectory "$INSTDIR\data"
  IfErrors install_failed
  WriteUninstaller "$INSTDIR\${LITE_UNINSTALLER}"
  IfErrors install_failed

  ; 只创建名称带 Lite 的快捷方式，绝不覆盖 PH Launcher 的快捷方式。
  CreateShortcut "$SMPROGRAMS\${LITE_SHORTCUT}" "$INSTDIR\${LITE_EXE}" "" "$INSTDIR\${LITE_EXE}" 0
  CreateShortcut "$DESKTOP\${LITE_SHORTCUT}" "$INSTDIR\${LITE_EXE}" "" "$INSTDIR\${LITE_EXE}" 0
  WriteRegStr HKCU "${LITE_UNINSTALL_KEY}" "DisplayName" "Pinghe Launcher Lite"
  WriteRegStr HKCU "${LITE_UNINSTALL_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${LITE_UNINSTALL_KEY}" "Publisher" "Pinghe Launcher Lite"
  WriteRegStr HKCU "${LITE_UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${LITE_UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\${LITE_EXE}"
  WriteRegStr HKCU "${LITE_UNINSTALL_KEY}" "UninstallString" "$\"$INSTDIR\${LITE_UNINSTALLER}$\""
  WriteRegDWORD HKCU "${LITE_UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${LITE_UNINSTALL_KEY}" "NoRepair" 1
  DetailPrint "Lite 与 PH Launcher 共用数据：$INSTDIR\data（卸载时保留）"
  Goto install_done
install_failed:
  MessageBox MB_ICONSTOP|MB_OK "无法写入 Lite 安装文件；请关闭运行中的 Lite 并检查目录权限：$INSTDIR" /SD IDOK
  Abort
install_done:
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  ; 旧目录残留的卸载器不应误删后来安装在另一目录的新快捷方式和卸载键。
  ReadRegStr $0 HKCU "${LITE_UNINSTALL_KEY}" "InstallLocation"
  StrCmp "$0" "$INSTDIR" 0 skip_global_entries
  Delete "$SMPROGRAMS\${LITE_SHORTCUT}"
  Delete "$DESKTOP\${LITE_SHORTCUT}"
  DeleteRegKey HKCU "${LITE_UNINSTALL_KEY}"
skip_global_entries:
  ; 禁止 RMDir /r、通配删除、删除 portable.flag/data；只删 Lite 文件。
  Delete "$INSTDIR\${LITE_EXE}"
  Delete "$INSTDIR\${LITE_UNINSTALLER}"
  ; 不尝试删除共用目录，既保留用户数据也保留 PHL 的 resources/locales。
  DetailPrint "共用数据和 PH Launcher 文件保留在：$INSTDIR"
SectionEnd
