//! Windows-платформенная часть плеера: одиночный экземпляр (named pipe),
//! AttachConsole для CLI-вывода, иконка окна.

#![allow(clippy::missing_safety_doc)]

#[cfg(windows)]
use std::sync::mpsc::Sender;

#[cfg(windows)]
mod win {
    use std::sync::mpsc::Sender;

    use windows_sys::Win32::Foundation::{CloseHandle, GENERIC_WRITE, HANDLE, INVALID_HANDLE_VALUE};
    use windows_sys::Win32::Storage::FileSystem::{
        CreateFileW, ReadFile, WriteFile, FILE_FLAG_FIRST_PIPE_INSTANCE, OPEN_EXISTING,
        PIPE_ACCESS_INBOUND,
    };
    use windows_sys::Win32::System::Console::{AttachConsole, ATTACH_PARENT_PROCESS};
    use windows_sys::Win32::System::Pipes::{
        ConnectNamedPipe, CreateNamedPipeW, DisconnectNamedPipe, PIPE_TYPE_BYTE, PIPE_WAIT,
    };

    pub const PIPE_NAME: &str = "\\\\.\\pipe\\HoxPlayerInstance";
    pub const ERROR_PIPE_CONNECTED: u32 = 535;

    pub unsafe fn attach_console() {
        let _ = AttachConsole(ATTACH_PARENT_PROCESS);
    }

    pub unsafe fn spawn_server(tx: Sender<Vec<String>>) -> Option<std::thread::JoinHandle<()>> {
        std::thread::Builder::new()
            .name("hox-instance".into())
            .spawn(move || loop {
                let wide: Vec<u16> =
                    PIPE_NAME.encode_utf16().chain(std::iter::once(0)).collect();
                let pipe: HANDLE = CreateNamedPipeW(
                    wide.as_ptr(),
                    PIPE_ACCESS_INBOUND | FILE_FLAG_FIRST_PIPE_INSTANCE,
                    PIPE_TYPE_BYTE | PIPE_WAIT,
                    1,
                    8192,
                    8192,
                    0,
                    std::ptr::null_mut(),
                );
                if pipe == INVALID_HANDLE_VALUE || pipe.is_null() {
                    return; // сервер уже поднят другой копией
                }
                let connected = ConnectNamedPipe(pipe, std::ptr::null_mut()) != 0
                    || windows_sys::Win32::Foundation::GetLastError() == ERROR_PIPE_CONNECTED;
                if connected {
                    let mut buf = [0u8; 16384];
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
                    let paths: Vec<String> = text
                        .split('\n')
                        .map(|s| s.trim().to_string())
                        .filter(|s| !s.is_empty())
                        .collect();
                    if !paths.is_empty() {
                        let _ = tx.send(paths);
                    }
                }
                DisconnectNamedPipe(pipe);
                CloseHandle(pipe);
            })
            .ok()
    }

    pub unsafe fn send_to_instance(payload: &str) -> bool {
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
        if payload.is_empty() {
            CloseHandle(pipe);
            return true; // probe: экземпляр существует
        }
        let bytes = payload.as_bytes();
        let mut written: u32 = 0;
        let ok = WriteFile(
            pipe,
            bytes.as_ptr(),
            bytes.len() as u32,
            &mut written,
            std::ptr::null_mut(),
        );
        CloseHandle(pipe);
        ok != 0
    }
}

#[cfg(windows)]
pub fn attach_console() {
    unsafe { win::attach_console() }
}

#[cfg(not(windows))]
pub fn attach_console() {}

#[cfg(windows)]
pub fn spawn_instance_server(tx: Sender<Vec<String>>) -> Option<std::thread::JoinHandle<()>> {
    unsafe { win::spawn_server(tx) }
}

#[cfg(not(windows))]
pub fn spawn_instance_server(_tx: Sender<Vec<String>>) -> Option<std::thread::JoinHandle<()>> {
    None
}

#[cfg(windows)]
pub fn send_to_instance(payload: &str) -> bool {
    unsafe { win::send_to_instance(payload) }
}

#[cfg(not(windows))]
pub fn send_to_instance(_payload: &str) -> bool {
    false
}

/// RGBA-данные иконки для окна (assets/hox.ico, вшит в бинарь).
pub fn window_icon() -> Option<egui::IconData> {
    const ICO: &[u8] = include_bytes!("../assets/hox.ico");
    let img = image::load_from_memory_with_format(ICO, image::ImageFormat::Ico).ok()?;
    let img = img.to_rgba8();
    let (w, h) = (img.width(), img.height());
    Some(egui::IconData { width: w, height: h, rgba: img.into_raw() })
}
