; ============================================================
; Hox Music Player — установщик (Rust Lite, NSIS 3, Unicode)
; Ставит ТОЛЬКО плеер: %LOCALAPPDATA%\Hoxmusicp\bin\HoxPlayer.exe
; Лаунчер и авто-обновление в этой версии отсутствуют: плеер
; самодостаточен (код не обрабатывает --version, поэтому старый
; лаунчер при проверке версий завис бы — удаляем его при миграции).
; Редистрибутивы не нужны: Rust-бинарь со статическим CRT.
; Ярлыки: рабочий стол «Hox» + меню «Пуск» \ Hox
; Тихий режим: /S  /D=<папка>
; Сборка: makensis /INPUTCHARSET UTF8 /DVERSION=0.7.0 /DPAYLOAD=<dir>
;         /DOUTFILE=<exe> /DESTSIZE=<KB> hox-setup-lite.nsi
; ============================================================

Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"

!ifndef VERSION
  !define VERSION "0.0.0"
!endif
!ifndef PAYLOAD
  !error "Не задан PAYLOAD — папка deploy (bin/)"
!endif
!ifndef OUTFILE
  !define OUTFILE "Hox-Setup.exe"
!endif
!ifndef DESTSIZE
  !define DESTSIZE 9000
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

!define MUI_ICON   "${PAYLOAD}\bin\hox.ico"
!define MUI_UNICON "${PAYLOAD}\bin\hox.ico"

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
  ; Закрываем плеер и старый лаунчер (после установки лаунчера больше нет).
  nsExec::Exec 'taskkill /F /IM HoxPlayer.exe'
  Pop $0
  nsExec::Exec 'taskkill /F /IM HoxLauncher.exe'
  Pop $0
  nsExec::Exec 'taskkill /F /IM Hox.exe'
  Pop $0
FunctionEnd

Function .onInstSuccess
  ${If} ${Silent}
    ; тихое обновление: taskkill на старте убил игравший плеер —
    ; запускаем свежий, чтобы пользователь не остался без плеера
    Exec '"$INSTDIR\bin\HoxPlayer.exe"'
  ${EndIf}
FunctionEnd

Section "Hox Music Player" SEC_MAIN
  SectionIn RO
  SetOutPath "$INSTDIR"

  ; ---- файлы приложения (плеер + иконка; без рантаймов и редистов)
  File /r "${PAYLOAD}\bin"

  ; ---- миграция: лаунчер v0.5.x–v0.6.0 несовместим с этой версией
  Delete "$INSTDIR\bin\HoxLauncher.exe"
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\runtime"
  RMDir /r "$INSTDIR\support"
  RMDir /r "$INSTDIR\redist"
  Delete "$INSTDIR\Uninstall-Hox.vbs"

  ; ---- ярлыки: рабочий стол + меню «Пуск» (иконка из hox.ico)
  CreateDirectory "$SMPROGRAMS\Hox"
  CreateShortCut "$DESKTOP\Hox.lnk" \
      "$INSTDIR\bin\HoxPlayer.exe" "" \
      "$INSTDIR\bin\hox.ico" 0
  CreateShortCut "$SMPROGRAMS\Hox\Hox Music Player.lnk" \
      "$INSTDIR\bin\HoxPlayer.exe" "" \
      "$INSTDIR\bin\hox.ico" 0
  Delete "$SMPROGRAMS\Hox\Hox Launcher.lnk"

  ; ---- деинсталлятор + реестр
  WriteUninstaller "$INSTDIR\uninst.exe"
  WriteRegStr   HKCU "${REG_HOME}" "InstallDir" "$INSTDIR"
  WriteRegStr   HKCU "${UNINST_KEY}" "DisplayName" "Hox Music Player"
  WriteRegStr   HKCU "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr   HKCU "${UNINST_KEY}" "Publisher" "Hox"
  WriteRegStr   HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\bin\hox.ico"
  WriteRegStr   HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr   HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\uninst.exe"'
  WriteRegStr   HKCU "${UNINST_KEY}" "QuietUninstallString" '"$INSTDIR\uninst.exe" /S'
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 0x00000001
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 0x00000001
  WriteRegDWORD HKCU "${UNINST_KEY}" "EstimatedSize" ${DESTSIZE}
SectionEnd

Section Uninstall
  nsExec::Exec 'taskkill /F /IM HoxPlayer.exe /T'
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
