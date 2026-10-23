; ============================================================
; Hox Music Player — установщик (C++-версия, NSIS 3, Unicode)
; Ставит: %LOCALAPPDATA%\Hoxmusicp\bin  (HoxPlayer.exe + HoxLauncher.exe + Qt)
; Redists: VC++ 2015-2022 x64 + x86 — проверка по реестру,
;          отсутствующие ставятся тихо через UAC (runas).
; Ярлыки: рабочий стол «Hox» + меню «Пуск» \ Hox
; Тихий режим: /S  /D=<папка>
; Сборка: makensis -INPUTCHARSET UTF8 -DVERSION=0.5.0 -DPAYLOAD=<dir>
;         -DOUTFILE=<exe> -DESTSIZE=<KB> -DASSETS=<dir> hox-setup-cpp.nsi
; ============================================================

Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"

!ifndef VERSION
  !define VERSION "0.0.0"
!endif
!ifndef PAYLOAD
  !error "Не задан PAYLOAD — папка deploy (bin/, redist/)"
!endif
!ifndef OUTFILE
  !define OUTFILE "Hox-Setup.exe"
!endif
!ifndef ESTSIZE
  !define ESTSIZE 300000
!endif
!ifndef ASSETS
  !define ASSETS "assets"
!endif

!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\Hoxmusicp"
!define REG_HOME   "Software\Hoxmusicp"

Name "Hox Music Player ${VERSION}"
BrandingText "Hox Music Player ${VERSION}"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\Hoxmusicp"
InstallDirRegKey HKCU "${REG_HOME}" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
ShowInstDetails show
ShowUninstDetails show

!define MUI_ICON   "${PAYLOAD}/bin/hox.ico"
!define MUI_UNICON "${PAYLOAD}/bin/hox.ico"
; welcome-страница MUI — стандартная (кастомный битмап отключён: File-глоб
; на Windows-сборке нестабильно матчит абсолютный путь)

!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION "LaunchPlayer"
!define MUI_FINISHPAGE_RUN_TEXT "Запустить Hox Music Player"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Russian"

Function LaunchPlayer
  Exec '"$INSTDIR\bin\HoxPlayer.exe"'
FunctionEnd

Function .onInit
  ; Закрываем плеер/лаунчер (старые и новые образы). Без /T — чтобы не убить
  ; сам установщик при запуске из старого лаунчера (Popen-ребёнок).
  nsExec::Exec 'taskkill /F /IM Hox.exe'
  Pop $0
  nsExec::Exec 'taskkill /F /IM HoxPlayer.exe'
  Pop $0
  nsExec::Exec 'taskkill /F /IM HoxLauncher.exe'
  Pop $0
FunctionEnd

Function .onInstSuccess
  ${If} ${Silent}
    ; тихая установка (самообновление) — открываем плеер сами
    Exec '"$INSTDIR\bin\HoxPlayer.exe"'
  ${EndIf}
FunctionEnd

; ---------- проверка и тихая установка VC++ Redistributable ----------
; Оба пакета (x64+x86) ставятся за ОДИН UAC-запрос через cmd-обёртку.
!macro EnsureVCRedists
  SetRegView 64
  ClearErrors
  ReadRegStr $R1 HKLM "SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" "Version"
  SetRegView 32
  ReadRegStr $R2 HKLM "SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x86" "Version"
  SetRegView lastused
  ${If} $R1 == ""
  ${OrIf} $R2 == ""
    DetailPrint "VC++ Redistributable отсутствует (x64: '$R1', x86: '$R2') — устанавливаю (запрос UAC)…"
    Delete "$INSTDIR\redist\install-redists.cmd"
    FileOpen $R3 "$INSTDIR\redist\install-redists.cmd" w
    FileWrite $R3 "@echo off$\r$\n"
    FileWrite $R3 "$\"%~dp0vc_redist.x64.exe$\" /install /quiet /norestart$\r$\n"
    FileWrite $R3 "$\"%~dp0vc_redist.x86.exe$\" /install /quiet /norestart$\r$\n"
    FileClose $R3
    ExecShellWait "runas" "$INSTDIR\redist\install-redists.cmd" "" SW_HIDE
    Pop $R4
    Delete "$INSTDIR\redist\install-redists.cmd"
    SetRegView 64
    ReadRegStr $R1 HKLM "SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" "Version"
    SetRegView 32
    ReadRegStr $R2 HKLM "SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x86" "Version"
    SetRegView lastused
    ${If} $R1 == ""
    ${OrIf} $R2 == ""
      DetailPrint "VC++ Redistributable: не установлен (UAC отклонён или ошибка)"
      ${IfNot} ${Silent}
        MessageBox MB_ICONEXCLAMATION|MB_OK \
          "Не удалось установить VC++ Redistributable.$\nПриложению нужны эти библиотеки для запуска.$\nУстановите файлы из папки redist вручную."
      ${EndIf}
    ${Else}
      DetailPrint "VC++ Redistributable: установлен (x64: $R1, x86: $R2)"
    ${EndIf}
  ${Else}
    DetailPrint "VC++ Redistributable: уже установлен (x64: $R1, x86: $R2)"
  ${EndIf}
!macroend

Section "Hox Music Player" SEC_MAIN
  SectionIn RO
  SetOutPath "$INSTDIR"

  ; ---- файлы приложения
  File /r "${PAYLOAD}/bin"
  File /r "${PAYLOAD}/redist"

  ; ---- VC++ Redistributable (актуальные 2015-2022, x64 и x86, один UAC)
  !insertmacro EnsureVCRedists

  ; ---- миграция со старой Python-версии 0.4.x: убираем рантайм и лаунчер-файлы
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\runtime"
  RMDir /r "$INSTDIR\support"
  Delete "$INSTDIR\Uninstall-Hox.vbs"

  ; ---- ярлыки: рабочий стол + меню «Пуск»
  CreateDirectory "$SMPROGRAMS\Hox"
  CreateShortCut "$DESKTOP\Hox.lnk" \
      "$INSTDIR\bin\HoxPlayer.exe" "" \
      "$INSTDIR\bin\HoxPlayer.exe" 0
  CreateShortCut "$SMPROGRAMS\Hox\Hox Music Player.lnk" \
      "$INSTDIR\bin\HoxPlayer.exe" "" \
      "$INSTDIR\bin\HoxPlayer.exe" 0
  CreateShortCut "$SMPROGRAMS\Hox\Hox Launcher.lnk" \
      "$INSTDIR\bin\HoxLauncher.exe" "" \
      "$INSTDIR\bin\HoxLauncher.exe" 0

  ; ---- деинсталлятор + реестр
  WriteUninstaller "$INSTDIR\uninst.exe"
  WriteRegStr   HKCU "${REG_HOME}" "InstallDir" "$INSTDIR"
  WriteRegStr   HKCU "${UNINST_KEY}" "DisplayName" "Hox Music Player"
  WriteRegStr   HKCU "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr   HKCU "${UNINST_KEY}" "Publisher" "Hox"
  WriteRegStr   HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\bin\HoxPlayer.exe,0"
  WriteRegStr   HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr   HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\uninst.exe"'
  WriteRegStr   HKCU "${UNINST_KEY}" "QuietUninstallString" '"$INSTDIR\uninst.exe" /S'
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 0x00000001
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 0x00000001
  WriteRegDWORD HKCU "${UNINST_KEY}" "EstimatedSize" ${ESTSIZE}
SectionEnd

Section Uninstall
  nsExec::Exec 'taskkill /F /IM HoxPlayer.exe /T'
  Pop $0
  nsExec::Exec 'taskkill /F /IM HoxLauncher.exe /T'
  Pop $0
  nsExec::Exec 'taskkill /F /IM Hox.exe /T'
  Pop $0
  RMDir /r "$INSTDIR\bin"
  RMDir /r "$INSTDIR\redist"
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\runtime"
  RMDir /r "$INSTDIR\support"
  Delete "$INSTDIR\Uninstall-Hox.vbs"
  Delete "$INSTDIR\uninst.exe"
  RMDir "$INSTDIR"
  Delete "$DESKTOP\Hox.lnk"
  RMDir /r "$SMPROGRAMS\Hox"
  DeleteRegKey HKCU "${UNINST_KEY}"
  DeleteRegKey HKCU "${REG_HOME}"
  SetAutoClose true
SectionEnd
