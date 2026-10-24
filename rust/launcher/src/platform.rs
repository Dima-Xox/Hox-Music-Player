//! Windows-платформенная часть лаунчера: иконка в трее с меню и балунами,
//! одиночный экземпляр (named pipe), версии/процессы, тихая установка,
//! часовой пояс.

#![allow(clippy::missing_safety_doc)]

#[cfg(windows)]
pub mod win {
    use std::sync::mpsc::{Receiver, Sender};
    use std::sync::{Arc, Mutex, OnceLock};

    use windows_sys::Win32::Foundation::{
        CloseHandle, GetLastError, GENERIC_WRITE, HANDLE, INVALID_HANDLE_VALUE, LPARAM, LRESULT,
        POINT, WPARAM,
    };
    use windows_sys::Win32::Graphics::Gdi::{BeginPaint, EndPaint, PAINTSTRUCT};
    use windows_sys::Win32::System::Console::{AttachConsole, ATTACH_PARENT_PROCESS};
    use windows_sys::Win32::System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W,
        TH32CS_SNAPPROCESS,
    };
    use windows_sys::Win32::System::LibraryLoader::GetModuleHandleW;
    use windows_sys::Win32::System::Pipes::{
        ConnectNamedPipe, CreateNamedPipeW, DisconnectNamedPipe, PIPE_TYPE_BYTE, PIPE_WAIT,
    };
    use windows_sys::Win32::System::Threading::{
        CreateProcessW, GetExitCodeProcess, WaitForSingleObject, CREATE_NEW_PROCESS_GROUP,
        CREATE_NO_WINDOW, INFINITE, PROCESS_INFORMATION, STARTUPINFOW,
    };
    use windows_sys::Win32::System::Time::{GetTimeZoneInformation, TIME_ZONE_INFORMATION};
    use windows_sys::Win32::UI::Input::KeyboardAndMouse::SetFocus;
    use windows_sys::Win32::UI::Shell::{
        ShellExecuteExW, Shell_NotifyIconW, NIF_INFO, NIF_ICON, NIF_MESSAGE, NIF_TIP, NIIF_INFO,
        NIM_ADD, NIM_DELETE, NIM_MODIFY, NOTIFYICONDATAW, SEE_MASK_FLAG_DDEWAIT,
        SEE_MASK_NOCLOSEPROCESS, SEE_MASK_NOASYNC, SHELLEXECUTEINFOW,
    };
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        AppendMenuW, CreatePopupMenu, CreateWindowExW, DefWindowProcW, DestroyMenu, DispatchMessageW,
        GetCursorPos, LoadImageW, PeekMessageW, PostQuitMessage, RegisterClassW, SetForegroundWindow,
        ShowWindow, TrackPopupMenu, TranslateMessage, DestroyIcon, HIDE_WINDOW, IMAGE_ICON,
        LR_LOADFROMFILE, MF_CHECKED, MF_DISABLED, MF_POPUP, MF_SEPARATOR, MF_STRING, PM_REMOVE,
        SM_CXSMICON, SM_CYSMICON, SW_HIDE, SW_RESTORE, SW_SHOW, TPM_NONOTIFY, TPM_RETURNCMD,
        TPM_RIGHTBUTTON, WM_APP, WM_CLOSE, WM_LBUTTONDBLCLK, WM_LBUTTONUP, WM_PAINT, WM_QUIT,
        WM_RBUTTONUP, WNDCLASSW, WS_OVERLAPPED,
    };
    use windows_sys::Win32::Storage::FileSystem::{
        CreateFileW, ReadFile, WriteFile, FILE_FLAG_FIRST_PIPE_INSTANCE, OPEN_EXISTING,
        PIPE_ACCESS_INBOUND,
    };

    pub const TRAY_MSG: u32 = WM_APP + 1;

    pub unsafe fn attach_console() {
        let _ = AttachConsole(ATTACH_PARENT_PROCESS);
    }

    // ---------- команды/события трея ----------
    #[derive(Debug, Clone)]
    pub enum TrayCmd {
        ShowWindow,
        Play,
        Check,
        WhatsNew,
        SetAuto(bool),
        SetDelay(u32),
        Quit,
    }

    #[derive(Debug, Clone)]
    pub struct Balloon {
        pub title: String,
        pub body: String,
    }

    /// Состояние, из которого трей строит меню при открытии.
    #[derive(Debug, Clone, Default)]
    pub struct TrayShared {
        pub auto_update: bool,
        pub delay_hours: u32,
        pub has_notes: bool,
        pub notes_label: String,
    }

    static TRAY_CTX: OnceLock<(Arc<Mutex<TrayShared>>, Sender<TrayCmd>)> = OnceLock::new();
    static TRAY_HWND: OnceLock<isize> = OnceLock::new();

    // меню-идентификаторы
    const ID_SHOW: u32 = 1001;
    const ID_PLAY: u32 = 1002;
    const ID_CHECK: u32 = 1003;
    const ID_NOTES: u32 = 1004;
    const ID_AUTO: u32 = 1005;
    const ID_SCHED_BASE: u32 = 1010;
    const ID_QUIT: u32 = 1020;
    const SCHED_HOURS: [u32; 4] = [0, 1, 6, 24];

    unsafe fn append_item(menu: HANDLE, id: u32, text: &str, checked: bool, enabled: bool) {
        let mut wide: Vec<u16> = text.encode_utf16().chain(std::iter::once(0)).collect();
        let mut flags: u32 = MF_STRING;
        if checked {
            flags |= MF_CHECKED;
        }
        if !enabled {
            flags |= MF_DISABLED;
        }
        AppendMenuW(menu, flags, id as usize, wide.as_ptr());
    }

    unsafe fn tray_menu(hl: &Arc<Mutex<TrayShared>>, tx: &Sender<TrayCmd>) {
        let sh = hl.lock().unwrap().clone();
        let menu = CreatePopupMenu();
        append_item(menu, ID_SHOW, "Показать окно", false, true);
        append_item(menu, ID_PLAY, "Запустить плеер", false, true);
        AppendMenuW(menu, MF_SEPARATOR, 0, std::ptr::null());
        append_item(menu, ID_CHECK, "Проверить обновления", false, true);
        append_item(
            menu,
            ID_NOTES,
            &if sh.has_notes { sh.notes_label.clone() } else { "Что нового".into() },
            false,
            sh.has_notes,
        );
        append_item(menu, ID_AUTO, "Авто-обновление с GitHub", sh.auto_update, true);
        let sub = CreatePopupMenu();
        for (i, h) in SCHED_HOURS.iter().enumerate() {
            let label = if *h == 0 { "Сразу".to_string() } else { format!("Через {h} ч") };
            append_item(sub, ID_SCHED_BASE + i as u32, &label, sh.delay_hours == *h, true);
        }
        let sub_title: Vec<u16> = "Отложенное авто-обновление\0".encode_utf16().collect();
        AppendMenuW(menu, MF_POPUP, sub as usize, sub_title.as_ptr());
        AppendMenuW(menu, MF_SEPARATOR, 0, std::ptr::null());
        append_item(menu, ID_QUIT, "Выход", false, true);

        let hwnd = (*TRAY_HWND.get().unwrap_or(&0)) as HANDLE;
        SetForegroundWindow(hwnd);
        SetFocus(hwnd);
        let mut pt = POINT { x: 0, y: 0 };
        GetCursorPos(&mut pt);
        let chosen = TrackPopupMenu(
            menu,
            TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
            pt.x,
            pt.y,
            0,
            hwnd,
            std::ptr::null(),
        );
        DestroyMenu(menu);
        let id = chosen as u32;
        match id {
            ID_SHOW => { let _ = tx.send(TrayCmd::ShowWindow); }
            ID_PLAY => { let _ = tx.send(TrayCmd::Play); }
            ID_CHECK => { let _ = tx.send(TrayCmd::Check); }
            ID_NOTES => { let _ = tx.send(TrayCmd::WhatsNew); }
            ID_AUTO => { let _ = tx.send(TrayCmd::SetAuto(!sh.auto_update)); }
            ID_QUIT => { let _ = tx.send(TrayCmd::Quit); }
            v if (ID_SCHED_BASE..ID_SCHED_BASE + 4).contains(&v) => {
                let h = SCHED_HOURS[(v - ID_SCHED_BASE) as usize];
                let _ = tx.send(TrayCmd::SetDelay(h));
            }
            _ => {}
        }
    }

    unsafe extern "system" fn wnd_proc(
        hwnd: HANDLE,
        msg: u32,
        wparam: WPARAM,
        lparam: LPARAM,
    ) -> LRESULT {
        if msg == TRAY_MSG {
            match (lparam & 0xFFFF) as u32 {
                WM_LBUTTONUP | WM_LBUTTONDBLCLK => {
                    if let Some((_, tx)) = TRAY_CTX.get() {
                        let _ = tx.send(TrayCmd::ShowWindow);
                    }
                }
                WM_RBUTTONUP => {
                    if let Some((hl, tx)) = TRAY_CTX.get() {
                        tray_menu(hl, tx);
                    }
                }
                _ => {}
            }
            return 0;
        }
        if msg == WM_PAINT {
            let mut ps: PAINTSTRUCT = std::mem::zeroed();
            let hdc = BeginPaint(hwnd, &mut ps);
            EndPaint(hwnd, &ps);
            return 0;
        }
        if msg == WM_CLOSE {
            PostQuitMessage(0);
            return 0;
        }
        DefWindowProcW(hwnd, msg, wparam, lparam)
    }

    /// Запустить трей. Возвращает поток обслуживания.
    pub fn spawn_tray(
        shared: Arc<Mutex<TrayShared>>,
        cmd_tx: Sender<TrayCmd>,
        balloon_rx: Receiver<Balloon>,
        ico_path: String,
    ) -> Option<std::thread::JoinHandle<()>> {
        if TRAY_CTX.set((shared, cmd_tx)).is_err() {
            return None;
        }
        std::thread::Builder::new().name("hox-tray".into()).spawn(move || unsafe {
            let hinst = GetModuleHandleW(std::ptr::null());
            let class_name: Vec<u16> = "HoxTrayWnd\0".encode_utf16().collect();
            let mut wc: WNDCLASSW = std::mem::zeroed();
            wc.lpfnWndProc = Some(wnd_proc);
            wc.hInstance = hinst;
            wc.lpszClassName = class_name.as_ptr();
            RegisterClassW(&wc);

            let ico_wide: Vec<u16> = ico_path.encode_utf16().chain(std::iter::once(0)).collect();
            let hicon = LoadImageW(
                hinst,
                ico_wide.as_ptr(),
                IMAGE_ICON,
                GetSystemMetrics(SM_CXSMICON).max(16),
                GetSystemMetrics(SM_CYSMICON).max(16),
                LR_LOADFROMFILE,
            );

            let hwnd = CreateWindowExW(
                0,
                class_name.as_ptr(),
                std::ptr::null(),
                WS_OVERLAPPED,
                0,
                0,
                0,
                0,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                hinst,
                std::ptr::null(),
            );
            if hwnd.is_null() {
                return;
            }
            let _ = TRAY_HWND.set(hwnd as isize);

            let mut tip: [u16; 128] = [0; 128];
            for (i, u) in "Hox — лаунчер".encode_utf16().take(127).enumerate() {
                tip[i] = u;
            }
            let mut nid: NOTIFYICONDATAW = std::mem::zeroed();
            nid.cbSize = std::mem::size_of::<NOTIFYICONDATAW>() as u32;
            nid.hWnd = hwnd;
            nid.uID = 1;
            nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP;
            nid.uCallbackMessage = TRAY_MSG;
            nid.hIcon = hicon;
            nid.szTip = tip;
            if Shell_NotifyIconW(NIM_ADD, &nid) == 0 {
                return;
            }

            // цикл сообщений + поллинг балунов
            let mut msg: windows_sys::Win32::UI::WindowsAndMessaging::MSG = std::mem::zeroed();
            loop {
                let has_msg = PeekMessageW(&mut msg, std::ptr::null_mut(), 0, 0, PM_REMOVE);
                if has_msg != 0 {
                    if msg.message == WM_QUIT {
                        break;
                    }
                    let _ = TranslateMessage(&msg);
                    DispatchMessageW(&msg);
                }
                while let Ok(b) = balloon_rx.try_recv() {
                    let mut title: [u16; 64] = [0; 64];
                    for (i, u) in b.title.encode_utf16().take(63).enumerate() {
                        title[i] = u;
                    }
                    let mut body: [u16; 256] = [0; 256];
                    for (i, u) in b.body.encode_utf16().take(255).enumerate() {
                        body[i] = u;
                    }
                    let mut nid: NOTIFYICONDATAW = std::mem::zeroed();
                    nid.cbSize = std::mem::size_of::<NOTIFYICONDATAW>() as u32;
                    nid.hWnd = hwnd;
                    nid.uID = 1;
                    nid.uFlags = NIF_INFO;
                    nid.szInfoTitle = title;
                    nid.szInfo = body;
                    nid.dwInfoFlags = NIIF_INFO;
                    Shell_NotifyIconW(NIM_MODIFY, &nid);
                }
                std::thread::sleep(std::time::Duration::from_millis(120));
            }
            let mut nid: NOTIFYICONDATAW = std::mem::zeroed();
            nid.cbSize = std::mem::size_of::<NOTIFYICONDATAW>() as u32;
            nid.hWnd = hwnd;
            nid.uID = 1;
            Shell_NotifyIconW(NIM_DELETE, &nid);
            if !hicon.is_null() {
                DestroyIcon(hicon);
            }
        }).ok()
    }

    unsafe fn GetSystemMetrics(i: i32) -> i32 {
        windows_sys::Win32::UI::WindowsAndMessaging::GetSystemMetrics(i)
    }

    // ---------- одиночный экземпляр ----------
    pub const PIPE_NAME: &str = "\\\\.\\pipe\\HoxLauncherInstance";
    pub const MUTEX_NAME: &str = "HoxLauncherInstanceMutex";

    pub unsafe fn try_lock_mutex() -> Option<HANDLE> {
        let wide: Vec<u16> = MUTEX_NAME.encode_utf16().chain(std::iter::once(0)).collect();
        let h = windows_sys::Win32::System::Threading::CreateMutexW(
            std::ptr::null(),
            0,
            wide.as_ptr(),
        );
        if h.is_null() || h == INVALID_HANDLE_VALUE {
            return None;
        }
        if GetLastError() == 183 {
            CloseHandle(h);
            return None;
        }
        Some(h)
    }

    pub fn spawn_instance_server(tx: Sender<String>) -> Option<std::thread::JoinHandle<()>> {
        std::thread::Builder::new().name("hox-instance".into()).spawn(move || unsafe {
            loop {
                let wide: Vec<u16> = PIPE_NAME.encode_utf16().chain(std::iter::once(0)).collect();
                let pipe: HANDLE = CreateNamedPipeW(
                    wide.as_ptr(),
                    PIPE_ACCESS_INBOUND | FILE_FLAG_FIRST_PIPE_INSTANCE,
                    PIPE_TYPE_BYTE | PIPE_WAIT,
                    1,
                    4096,
                    4096,
                    0,
                    std::ptr::null_mut(),
                );
                if pipe == INVALID_HANDLE_VALUE || pipe.is_null() {
                    return;
                }
                let connected = ConnectNamedPipe(pipe, std::ptr::null_mut()) != 0
                    || GetLastError() == 535;
                if connected {
                    let mut buf = [0u8; 4096];
                    let mut total = 0usize;
                    loop {
                        let mut n: u32 = 0;
                        let ok = ReadFile(
                            pipe,
                            buf[total..].as_mut_ptr(),
                            (buf.len() - total) as u32,
                            &mut n,
                            std::ptr::null_mut(),
                        );
                        if ok == 0 || n == 0 || total >= buf.len() {
                            break;
                        }
                        total += n as usize;
                    }
                    let text = String::from_utf8_lossy(&buf[..total]).to_string();
                    if !text.is_empty() {
                        let _ = tx.send(text);
                    }
                }
                DisconnectNamedPipe(pipe);
                CloseHandle(pipe);
            }
        }).ok()
    }

    pub unsafe fn send_to_instance(_payload: &str) -> bool {
        let wide: Vec<u16> = PIPE_NAME.encode_utf16().chain(std::iter::once(0)).collect();
        let pipe: HANDLE = CreateFileW(
            wide.as_ptr(),
            GENERIC_WRITE,
            0,
            std::ptr::null(),
            OPEN_EXISTING,
            0,
            std::ptr::null_mut(),
        );
        if pipe == INVALID_HANDLE_VALUE || pipe.is_null() {
            return false;
        }
        CloseHandle(pipe);
        true
    }

    // ---------- процессы / версии ----------
    pub unsafe fn process_running(exe_name: &str) -> bool {
        let snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        if snap == INVALID_HANDLE_VALUE || snap.is_null() {
            return false;
        }
        let mut pe: PROCESSENTRY32W = std::mem::zeroed();
        pe.dwSize = std::mem::size_of::<PROCESSENTRY32W>() as u32;
        let mut found = false;
        if Process32FirstW(snap, &mut pe) != 0 {
            loop {
                let name = String::from_utf16_lossy(
                    &pe.szExeFile.iter().take_while(|&&c| c != 0).copied().collect::<Vec<u16>>(),
                );
                if name.eq_ignore_ascii_case(exe_name) {
                    found = true;
                    break;
                }
                if Process32NextW(snap, &mut pe) == 0 {
                    break;
                }
            }
        }
        CloseHandle(snap);
        found
    }

    pub const CREATE_NO_WINDOW_FLAGS: u32 = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP;

    unsafe fn spawn_process(path: &str, args: &str) -> bool {
        let mut exe_w: Vec<u16> = path.encode_utf16().chain(std::iter::once(0)).collect();
        let mut cmd_w: Vec<u16> = format!("\"{path}\" {args}\0").encode_utf16().collect();
        let mut si: STARTUPINFOW = std::mem::zeroed();
        si.cb = std::mem::size_of::<STARTUPINFOW>() as u32;
        let mut pi: PROCESS_INFORMATION = std::mem::zeroed();
        let ok = CreateProcessW(
            exe_w.as_mut_ptr(),
            cmd_w.as_mut_ptr(),
            std::ptr::null(),
            std::ptr::null(),
            0,
            CREATE_NO_WINDOW_FLAGS,
            std::ptr::null(),
            std::ptr::null(),
            &si,
            &mut pi,
        );
        if ok != 0 {
            CloseHandle(pi.hProcess);
            CloseHandle(pi.hThread);
        }
        ok != 0
    }

    pub unsafe fn spawn_detached(path: &str) -> bool {
        spawn_process(path, "")
    }

    pub unsafe fn spawn_detached_after_update(path: &str) -> bool {
        spawn_process(path, "--after-update")
    }

    pub unsafe fn open_url(url: &str) {
        let verb: Vec<u16> = "open\0".encode_utf16().collect();
        let file: Vec<u16> = url.encode_utf16().chain(std::iter::once(0)).collect();
        let mut sei: SHELLEXECUTEINFOW = std::mem::zeroed();
        sei.cbSize = std::mem::size_of::<SHELLEXECUTEINFOW>() as u32;
        sei.lpVerb = verb.as_ptr();
        sei.lpFile = file.as_ptr();
        sei.nShow = SW_SHOW as i32;
        ShellExecuteExW(&mut sei);
    }

    /// Тихая установка: ShellExecuteEx open + ожидание до 30 минут.
    pub unsafe fn run_installer_wait(path: &str, params: &str) -> Result<(), String> {
        let verb: Vec<u16> = "open\0".encode_utf16().collect();
        let file: Vec<u16> = path.encode_utf16().chain(std::iter::once(0)).collect();
        let args: Vec<u16> = params.encode_utf16().chain(std::iter::once(0)).collect();
        let mut sei: SHELLEXECUTEINFOW = std::mem::zeroed();
        sei.cbSize = std::mem::size_of::<SHELLEXECUTEINFOW>() as u32;
        sei.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC | SEE_MASK_FLAG_DDEWAIT;
        sei.lpVerb = verb.as_ptr();
        sei.lpFile = file.as_ptr();
        sei.lpParameters = args.as_ptr();
        sei.nShow = SW_HIDE as i32;
        if ShellExecuteExW(&mut sei) == 0 {
            return Err("ShellExecuteEx не смог запустить установщик".into());
        }
        if !sei.hProcess.is_null() && sei.hProcess != INVALID_HANDLE_VALUE {
            WaitForSingleObject(sei.hProcess, 30 * 60 * 1000);
            let mut code: u32 = 0;
            GetExitCodeProcess(sei.hProcess, &mut code);
            CloseHandle(sei.hProcess);
        }
        Ok(())
    }

    pub unsafe fn local_utc_offset_secs() -> i64 {
        let mut tzi: TIME_ZONE_INFORMATION = std::mem::zeroed();
        let r = GetTimeZoneInformation(&mut tzi);
        let bias = match r {
            2 => tzi.Bias + tzi.DaylightBias,
            1 => tzi.Bias + tzi.StandardBias,
            _ => tzi.Bias,
        };
        -(bias as i64) * 60
    }
}

#[cfg(windows)]
pub use win::*;

#[cfg(not(windows))]
pub mod stubs {
    use std::sync::mpsc::{Receiver, Sender};

    #[derive(Debug, Clone)]
    pub enum TrayCmd { ShowWindow, Play, Check, WhatsNew, SetAuto(bool), SetDelay(u32), Quit }

    #[derive(Debug, Clone)]
    pub struct Balloon { pub title: String, pub body: String }

    #[derive(Debug, Clone, Default)]
    pub struct TrayShared { pub auto_update: bool, pub delay_hours: u32, pub has_notes: bool, pub notes_label: String }

    pub fn spawn_tray(
        _shared: std::sync::Arc<std::sync::Mutex<TrayShared>>,
        _cmd_tx: Sender<TrayCmd>,
        _balloon_rx: Receiver<Balloon>,
        _ico_path: &str,
    ) -> Option<std::thread::JoinHandle<()>> {
        None
    }

    pub fn attach_console() {}
    pub fn try_lock_mutex() -> Option<isize> { None }
    pub fn spawn_instance_server(_tx: Sender<String>) -> Option<std::thread::JoinHandle<()>> { None }
    pub fn send_to_instance(_payload: &str) -> bool { false }
    pub fn process_running(_exe: &str) -> bool { false }
    pub const CREATE_NO_WINDOW_FLAGS: u32 = 0;
    pub fn spawn_detached(_path: &str) -> bool { false }
    pub fn spawn_detached_after_update(_path: &str) -> bool { false }
    pub fn open_url(_url: &str) {}
    pub fn run_installer_wait(_path: &str, _params: &str) -> Result<(), String> { Err("только Windows".into()) }
    pub fn local_utc_offset_secs() -> i64 { 0 }
}

#[cfg(not(windows))]
pub use stubs::*;

/// Иконка для окна/трея: ico пишется во временный файл (LoadImage требует путь).
#[cfg(windows)]
pub fn write_ico_temp() -> Option<String> {
    const ICO: &[u8] = include_bytes!("../assets/hox.ico");
    let p = std::env::temp_dir().join("hox-launcher.ico");
    if std::fs::write(&p, ICO).is_ok() {
        Some(p.to_string_lossy().to_string())
    } else {
        None
    }
}

#[cfg(not(windows))]
pub fn write_ico_temp() -> Option<String> {
    None
}
