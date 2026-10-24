//! Движок обновлений лаунчера: проверка релизов GitHub, скачивание с
//! прогрессом, проверка SHA-256, тихая установка. Порт C++ LauncherWindow
//! (checkUpdates / downloadUpdate / maybeAutoUpdate / расписание).

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use crate::platform::{self, Balloon};
use crate::settings::Settings;

const RELEASES_API: &str = "https://api.github.com/repos/Dima-Xox/Hox-Music-Player/releases/latest";

pub enum EngineMsg {
    Log(String),
    CheckDone { tag: String, notes: String, player_ver: String, player_exists: bool },
    DownloadProgress(f32),
    Balloon(Balloon),
    NotesAvailable(bool, String),
    Busy(bool),
    UpdateDone,
}

pub enum EngineCmd {
    Check,
    Download,
    Play,
    SetAuto(bool),
    SetDelay(u32),
    AfterUpdate,
    Quit,
}

pub struct EngineState {
    pub remote_tag: String,
    pub remote_notes: String,
    pub player_version: String,
    pub player_exists: bool,
    pub busy: bool,
    pub check_busy: bool,
    pub auto_update: bool,
    pub auto_attempts: u32,
    pub update_delay_hours: u32,
    pub scheduled_tag: String,
    pub scheduled_at_ms: i64,
    pub notified_tag: String,
    pub tray_hint_shown: bool,
    pub defer_active: bool,
    pub periodic_due: SystemTime,
}

pub struct Engine {
    pub state: Mutex<EngineState>,
    pub msg_tx: Sender<EngineMsg>,
    pub cmd_rx: Receiver<EngineCmd>,
    pub quit: Arc<AtomicBool>,
}

impl Engine {
    pub fn spawn() -> (Sender<EngineCmd>, Receiver<EngineMsg>, Arc<AtomicBool>) {
        let (msg_tx, msg_rx) = std::sync::mpsc::channel();
        let (cmd_tx, cmd_rx) = std::sync::mpsc::channel();
        let quit = Arc::new(AtomicBool::new(false));
        let quit2 = quit.clone();

        std::thread::Builder::new()
            .name("hox-update".into())
            .spawn(move || {
                let st = Settings::open();
                let mut s = EngineState {
                    remote_tag: String::new(),
                    remote_notes: String::new(),
                    player_version: String::new(),
                    player_exists: false,
                    busy: false,
                    check_busy: false,
                    auto_update: st.get_bool("autoUpdate", true),
                    auto_attempts: 0,
                    update_delay_hours: st.get_i64("updateDelayHours", 0).clamp(0, 24) as u32,
                    scheduled_tag: String::new(),
                    scheduled_at_ms: 0,
                    notified_tag: String::new(),
                    tray_hint_shown: false,
                    defer_active: false,
                    periodic_due: SystemTime::now() + Duration::from_secs(4 * 3600),
                };
                restore_schedule(&mut s, &msg_tx);

                let _ = msg_tx.send(EngineMsg::Log(format!("Лаунчер v{}", hox_common::APP_VERSION)));

                // первая проверка через 400 мс
                let mut next_check = Some(SystemTime::now() + Duration::from_millis(400));
                loop {
                    if quit2.load(Ordering::Relaxed) {
                        return;
                    }
                    // команды
                    match cmd_rx.recv_timeout(Duration::from_millis(250)) {
                        Ok(EngineCmd::Quit) => return,
                        Ok(EngineCmd::Check) => {
                            check_updates(&mut s, &msg_tx);
                        }
                        Ok(EngineCmd::Play) => {
                            launch_player(&msg_tx);
                        }
                        Ok(EngineCmd::Download) => {
                            download_update(&mut s, &msg_tx);
                        }
                        Ok(EngineCmd::SetAuto(on)) => {
                            s.auto_update = on;
                            Settings::open().set_bool("autoUpdate", on);
                            if on {
                                let _ = msg_tx.send(EngineMsg::Log(
                                    "Авто-обновление включено: новые релизы ставятся сами".into(),
                                ));
                                maybe_auto_update(&mut s, &msg_tx, false);
                            } else {
                                let _ = msg_tx.send(EngineMsg::Log(
                                    "Авто-обновление выключено: обновление по кнопке".into(),
                                ));
                                cancel_schedule(&mut s, "Запланированная установка отменена", &msg_tx);
                                s.defer_active = false;
                            }
                        }
                        Ok(EngineCmd::SetDelay(h)) => {
                            if s.update_delay_hours != h {
                                s.update_delay_hours = h;
                                Settings::open().set_i64("updateDelayHours", h as i64);
                                if h == 0 {
                                    cancel_schedule(&mut s, "", &msg_tx);
                                    let _ = msg_tx.send(EngineMsg::Log(
                                        "Отложенный запуск выключен — обновляю сразу, как найду релиз".into(),
                                    ));
                                    maybe_auto_update(&mut s, &msg_tx, false);
                                } else {
                                    let _ = msg_tx.send(EngineMsg::Log(format!(
                                        "Отложенный запуск: ставлю обновление через {h} ч после обнаружения релиза"
                                    )));
                                    if !s.scheduled_tag.is_empty() {
                                        let tag = s.scheduled_tag.clone();
                                        schedule_update(&mut s, &tag, &msg_tx);
                                    }
                                }
                            }
                        }
                        Ok(EngineCmd::AfterUpdate) => {
                            let _ = msg_tx.send(EngineMsg::Log(
                                "Обновление установлено — запускаю плеер…".into(),
                            ));
                            let _ = msg_tx.send(EngineMsg::Balloon(Balloon {
                                title: "Hox".into(),
                                body: "Плеер обновлён — запускаю".into(),
                            }));
                            std::thread::sleep(Duration::from_millis(500));
                            launch_player(&msg_tx);
                            check_updates(&mut s, &msg_tx);
                        }
                        Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {}
                        Err(_) => return,
                    }

                    // одноразовая стартовая проверка
                    if let Some(t) = next_check {
                        if SystemTime::now() >= t {
                            next_check = None;
                            check_updates(&mut s, &msg_tx);
                        }
                    }

                    // периодическая перепроверка раз в 4 часа
                    if SystemTime::now() >= s.periodic_due {
                        s.periodic_due = SystemTime::now() + Duration::from_secs(4 * 3600);
                        check_updates(&mut s, &msg_tx);
                    }

                    // отложенная установка: подошло время
                    if s.scheduled_at_ms > 0
                        && now_ms() >= s.scheduled_at_ms
                        && !s.check_busy
                        && !s.busy
                    {
                        s.scheduled_at_ms = 0;
                        Settings::open().remove("scheduledAtMs");
                        let _ = msg_tx.send(EngineMsg::Log(format!(
                            "Отложенное авто-обновление: подошло время ставить {}",
                            s.scheduled_tag
                        )));
                        maybe_auto_update(&mut s, &msg_tx, false);
                    }

                    // играющий плеер закрыли — можно ставить
                    if s.defer_active && !player_running() && !s.busy && !s.check_busy {
                        s.defer_active = false;
                        maybe_auto_update(&mut s, &msg_tx, false);
                    }
                }
            })
            .ok();
        (cmd_tx, msg_rx, quit)
    }
}

fn now_ms() -> i64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis() as i64).unwrap_or(0)
}

/// Путь к установленному плееру (bin рядом с лаунчером).
pub fn player_path() -> String {
    let exe = std::env::current_exe()
        .map(|p| p.to_string_lossy().to_string())
        .unwrap_or_default();
    std::path::Path::new(&exe)
        .parent()
        .map(|d| d.join("HoxPlayer.exe").to_string_lossy().to_string())
        .unwrap_or_else(|| "HoxPlayer.exe".into())
}

fn install_base() -> String {
    let exe = std::env::current_exe()
        .map(|p| p.to_string_lossy().to_string())
        .unwrap_or_default();
    std::path::Path::new(&exe)
        .parent()
        .and_then(|d| d.parent())
        .map(|d| d.to_string_lossy().to_string())
        .unwrap_or_else(|| ".".into())
}

pub fn player_exists() -> bool {
    std::path::Path::new(&player_path()).exists()
}

pub fn player_running() -> bool {
    #[cfg(windows)]
    unsafe {
        platform::process_running("HoxPlayer.exe")
    }
    #[cfg(not(windows))]
    {
        false
    }
}

pub fn launch_player(msg_tx: &Sender<EngineMsg>) {
    let exe = player_path();
    if !std::path::Path::new(&exe).exists() {
        let _ = msg_tx.send(EngineMsg::Log(format!("Плеер не найден: {exe}")));
        return;
    }
    #[cfg(windows)]
    unsafe {
        if platform::spawn_detached(&exe) {
            let _ = msg_tx.send(EngineMsg::Log(format!("Запуск: {exe}")));
        } else {
            let _ = msg_tx.send(EngineMsg::Log("Не удалось запустить плеер".into()));
        }
    }
}

/// Версия установленного плеера: сам HoxPlayer.exe (--version), резерв — реестр.
pub fn detect_player_version() -> String {
    let exe = player_path();
    if !std::path::Path::new(&exe).exists() {
        return String::new();
    }
    #[cfg(windows)]
    {
        use std::io::Read;
        use std::os::windows::process::CommandExt;
        use std::process::Command;
        if let Ok(mut child) = Command::new(&exe)
            .arg("--version")
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::null())
            .stdin(std::process::Stdio::null())
            .creation_flags(platform::CREATE_NO_WINDOW_FLAGS)
            .spawn()
        {
            let mut out = String::new();
            if let Some(mut so) = child.stdout.take() {
                let _ = so.read_to_string(&mut out);
            }
            let _ = child.wait();
            let re = regex::Regex::new(r"\d+\.\d+\.\d+").unwrap();
            if let Some(m) = re.find(&out) {
                return m.as_str().to_string();
            }
        }
        // резерв: запись установщика в реестре
        use winreg::enums::HKEY_CURRENT_USER;
        if let Ok(k) = winreg::RegKey::predef(HKEY_CURRENT_USER)
            .open_subkey(r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Hoxmusicp")
        {
            if let Ok(v) = k.get_value::<String, _>("DisplayVersion") {
                return v.trim().to_string();
            }
        }
    }
    String::new()
}

fn http_get_json(url: &str) -> Result<serde_json::Value, String> {
    let resp = ureq::get(url)
        .set("User-Agent", &format!("Hox-Launcher/{}", hox_common::APP_VERSION))
        .set("Accept", "application/vnd.github+json")
        .timeout(Duration::from_secs(30))
        .call()
        .map_err(|e| format!("{e}"))?;
    resp.into_json::<serde_json::Value>()
        .map_err(|e| format!("JSON: {e}"))
}

fn check_updates(s: &mut EngineState, msg_tx: &Sender<EngineMsg>) {
    if s.check_busy {
        return;
    }
    s.check_busy = true;
    let _ = msg_tx.send(EngineMsg::Busy(true));
    let _ = msg_tx.send(EngineMsg::Log("Проверяю обновления…".into()));

    let result = http_get_json(RELEASES_API);
    s.check_busy = false;
    let _ = msg_tx.send(EngineMsg::Busy(false));
    let rel = match result {
        Ok(r) => r,
        Err(e) => {
            let _ = msg_tx.send(EngineMsg::Log(format!("Проверка обновлений: ошибка сети ({e})")));
            return;
        }
    };
    let tag = rel["tag_name"].as_str().unwrap_or("").to_string();
    let notes = rel["body"].as_str().unwrap_or("").to_string();
    if tag.is_empty() {
        let _ = msg_tx.send(EngineMsg::Log("Не удалось прочитать ответ GitHub".into()));
        return;
    }
    if tag != s.remote_tag {
        s.auto_attempts = 0; // новый релиз — счётчик авто-попыток заново
    }
    s.remote_tag = tag.clone();
    s.remote_notes = notes.clone();

    // версия именно плеера
    s.player_exists = player_exists();
    if s.player_exists {
        let v = detect_player_version();
        s.player_version = v;
    }
    let _ = msg_tx.send(EngineMsg::CheckDone {
        tag: tag.clone(),
        notes: notes.clone(),
        player_ver: s.player_version.clone(),
        player_exists: s.player_exists,
    });

    let base = if s.player_exists {
        if s.player_version.is_empty() {
            hox_common::APP_VERSION.to_string()
        } else {
            s.player_version.clone()
        }
    } else {
        "0.0.0".to_string()
    };
    let rel_ver = hox_common::rel_ver_of(&tag).to_string();
    let newer = hox_common::parse_version(&tag) > hox_common::parse_version(&base);

    if newer {
        let _ = msg_tx.send(EngineMsg::Log(format!(
            "Доступна новая версия: {tag} — нажмите «Обновить»"
        )));
        if !notes.is_empty() {
            let _ = msg_tx.send(EngineMsg::Log(format!(
                "Изменения:\n{}",
                notes_to_plain(&notes).chars().take(600).collect::<String>()
            )));
        }
        if !notes.is_empty() {
            let label = format!("Что нового в v{rel_ver}");
            let _ = msg_tx.send(EngineMsg::NotesAvailable(true, label));
        }
        if s.notified_tag != tag {
            s.notified_tag = tag.clone();
            let body = notes_preview(&notes, 180);
            if !s.auto_update {
                let _ = msg_tx.send(EngineMsg::Balloon(Balloon {
                    title: format!("Hox v{rel_ver} — что нового"),
                    body: if body.is_empty() {
                        "Доступно обновление — кнопка в окне лаунчера".into()
                    } else {
                        body
                    },
                }));
            } else if player_running() {
                let _ = msg_tx.send(EngineMsg::Balloon(Balloon {
                    title: format!("Hox v{rel_ver} — обновлю после закрытия плеера"),
                    body: if body.is_empty() {
                        "Установка начнётся, когда закроете плеер".into()
                    } else {
                        body
                    },
                }));
            }
        }
    } else if !s.player_exists {
        let _ = msg_tx.send(EngineMsg::Log(format!(
            "Плеер не установлен — можно скачать релиз {tag}"
        )));
    } else {
        let ver = if s.player_version.is_empty() {
            hox_common::APP_VERSION.to_string()
        } else {
            s.player_version.clone()
        };
        let _ = msg_tx.send(EngineMsg::Log(format!(
            "У вас самая свежая версия (плеер v{ver}), релиз на GitHub: {tag}"
        )));
    }

    maybe_auto_update(s, msg_tx, true);
}

fn download_update(s: &mut EngineState, msg_tx: &Sender<EngineMsg>) {
    if s.remote_tag.is_empty() || s.busy {
        return;
    }
    s.busy = true;
    let _ = msg_tx.send(EngineMsg::Busy(true));

    let result = http_get_json(RELEASES_API);
    let rel = match result {
        Ok(r) => r,
        Err(e) => {
            fail_update(s, msg_tx, &format!("Ошибка сети: {e}"));
            return;
        }
    };
    let mut setup_url = String::new();
    let mut sha_url = String::new();
    if let Some(assets) = rel["assets"].as_array() {
        for a in assets {
            let name = a["name"].as_str().unwrap_or("");
            let url = a["browser_download_url"].as_str().unwrap_or("");
            if name.starts_with("Hox-Setup-")
                && name.ends_with(".exe")
                && !name.contains("Win7")
            {
                setup_url = url.to_string();
            } else if name.starts_with("Hox-Setup-")
                && name.ends_with(".sha256")
                && !name.contains("Win7")
            {
                sha_url = url.to_string();
            }
        }
    }
    if setup_url.is_empty() {
        fail_update(s, msg_tx, "В релизе нет установщика Hox-Setup-*.exe");
        return;
    }
    let _ = msg_tx.send(EngineMsg::Log("Скачиваю установщик…".into()));
    let data = match http_download(&setup_url, msg_tx) {
        Ok(d) => d,
        Err(e) => {
            fail_update(s, msg_tx, &format!("Ошибка скачивания: {e}"));
            return;
        }
    };

    let mut sha_sidecar = String::new();
    if !sha_url.is_empty() {
        match http_download(&sha_url, msg_tx) {
            Ok(d) => sha_sidecar = String::from_utf8_lossy(&d).to_string(),
            Err(_) => {
                let _ = msg_tx.send(EngineMsg::Log(
                    "Сайдкар .sha256 не скачался — пропускаю проверку суммы".into(),
                ));
            }
        }
    } else {
        let _ = msg_tx.send(EngineMsg::Log(
            "Сайдкар .sha256 не найден — пропускаю проверку суммы".into(),
        ));
    }

    // SHA-256
    if !sha_sidecar.trim().is_empty() {
        let expected: String = sha_sidecar.split_whitespace().next().unwrap_or("").to_lowercase();
        use sha2::Digest;
        let mut h = sha2::Sha256::new();
        h.update(&data);
        let actual = format!("{:x}", h.finalize());
        if expected != actual {
            fail_update(s, msg_tx, "SHA-256 не совпал — файл повреждён");
            return;
        }
        let _ = msg_tx.send(EngineMsg::Log("SHA-256 совпал".into()));
    }

    // сохранить во временную папку
    let tmp = std::env::temp_dir();
    let setup_path = tmp.join(format!(
        "Hox-Setup-{}.exe",
        hox_common::rel_ver_of(&s.remote_tag)
    ));
    if std::fs::write(&setup_path, &data).is_err() {
        fail_update(s, msg_tx, &format!("Не удалось сохранить {}", setup_path.display()));
        return;
    }
    let _ = msg_tx.send(EngineMsg::Log("Запускаю тихую установку…".into()));
    let params = format!("/S /D={}", install_base());
    let res = {
        #[cfg(windows)]
        unsafe {
            platform::run_installer_wait(&setup_path.to_string_lossy(), &params)
        }
        #[cfg(not(windows))]
        {
            Err::<(), String>("только Windows".into())
        }
    };
    if let Err(e) = res {
        fail_update(s, msg_tx, &e);
        return;
    }
    // Обычно сюда не дойти: установщик taskkill'ом закрывает лаунчер и сам
    // перезапускает его с --after-update. Ветка нужна, если taskkill не сработал.
    let _ = msg_tx.send(EngineMsg::Log("Обновление установлено. Перезапускаю лаунчер…".into()));
    s.busy = false;
    let _ = msg_tx.send(EngineMsg::Busy(false));
    let _ = msg_tx.send(EngineMsg::UpdateDone);
}

fn fail_update(s: &mut EngineState, msg_tx: &Sender<EngineMsg>, why: &str) {
    s.busy = false;
    let _ = msg_tx.send(EngineMsg::Busy(false));
    let _ = msg_tx.send(EngineMsg::Log(format!("Обновление не выполнено: {why}")));
}

fn http_download(url: &str, msg_tx: &Sender<EngineMsg>) -> Result<Vec<u8>, String> {
    let resp = ureq::get(url)
        .set("User-Agent", &format!("Hox-Launcher/{}", hox_common::APP_VERSION))
        .timeout(Duration::from_secs(600))
        .call()
        .map_err(|e| format!("{e}"))?;
    let total = resp
        .header("Content-Length")
        .and_then(|v| v.parse::<usize>().ok())
        .unwrap_or(0);
    let mut reader = resp.into_reader();
    let mut buf: Vec<u8> = Vec::with_capacity(total.min(256 * 1024 * 1024));
    let mut chunk = [0u8; 65536];
    let mut last_pct = -1i32;
    loop {
        use std::io::Read;
        let n = reader.read(&mut chunk).map_err(|e| format!("{e}"))?;
        if n == 0 {
            break;
        }
        buf.extend_from_slice(&chunk[..n]);
        if total > 0 {
            let pct = (buf.len() * 100 / total) as i32;
            if pct != last_pct {
                last_pct = pct;
                let _ = msg_tx.send(EngineMsg::DownloadProgress(pct as f32));
            }
        }
    }
    Ok(buf)
}

/// Авто-обновление: релиз новее плеера — качаем и ставим сами. Порт C++.
fn maybe_auto_update(s: &mut EngineState, msg_tx: &Sender<EngineMsg>, from_check: bool) {
    if !s.auto_update || s.busy || s.check_busy || s.remote_tag.is_empty() {
        return;
    }
    if s.auto_attempts >= 3 {
        return;
    }
    if !player_exists() {
        return; // первую установку оставляем пользователю — по кнопке
    }
    let base = if s.player_version.is_empty() {
        hox_common::APP_VERSION.to_string()
    } else {
        s.player_version.clone()
    };
    if hox_common::parse_version(&s.remote_tag) <= hox_common::parse_version(&base) {
        return;
    }
    let rel_ver = hox_common::rel_ver_of(&s.remote_tag).to_string();

    // отложенный запуск по расписанию
    if s.update_delay_hours > 0 {
        if s.scheduled_tag != s.remote_tag {
            let tag = s.remote_tag.clone();
            schedule_update(s, &tag, msg_tx);
            return;
        }
        if s.scheduled_at_ms > 0 {
            return; // запланировано, время ещё не подошло
        }
    }

    if player_running() {
        if !s.defer_active {
            if from_check {
                let _ = msg_tx.send(EngineMsg::Log(format!(
                    "Плеер сейчас играет — обновлю до v{rel_ver} автоматически, как только он будет закрыт."
                )));
            }
            s.defer_active = true;
        }
        return;
    }
    s.defer_active = false;
    s.auto_attempts += 1;
    let _ = msg_tx.send(EngineMsg::Log(format!(
        "Авто-обновление: скачиваю v{rel_ver} и тихо устанавливаю…"
    )));
    let body = notes_preview(&s.remote_notes, 160);
    let _ = msg_tx.send(EngineMsg::Balloon(Balloon {
        title: format!("Hox v{rel_ver} — обновляюсь"),
        body: if body.is_empty() {
            "Скачиваю и устанавливаю новую версию".into()
        } else {
            body
        },
    }));
    std::thread::sleep(Duration::from_millis(1200));
    if !s.busy && !s.check_busy {
        download_update(s, msg_tx);
    }
}

// ------------------------------------------------------------ расписание
fn schedule_update(s: &mut EngineState, tag: &str, msg_tx: &Sender<EngineMsg>) {
    s.scheduled_tag = tag.to_string();
    s.scheduled_at_ms = now_ms() + (s.update_delay_hours as i64) * 3600 * 1000;
    let st = Settings::open();
    st.set_str("scheduledTag", tag);
    st.set_i64("scheduledAtMs", s.scheduled_at_ms);
    let when = hox_common::fmt_when(s.scheduled_at_ms);
    let _ = msg_tx.send(EngineMsg::Log(format!(
        "Отложенное авто-обновление: v{} установлю в {}",
        hox_common::rel_ver_of(tag),
        when
    )));
    let _ = msg_tx.send(EngineMsg::Balloon(Balloon {
        title: "Hox".into(),
        body: format!("Обновление до v{} запланировано на {}", hox_common::rel_ver_of(tag), when),
    }));
}

fn restore_schedule(s: &mut EngineState, msg_tx: &Sender<EngineMsg>) {
    let st = Settings::open();
    let tag = st.get_str("scheduledTag", "");
    let at = st.get_i64("scheduledAtMs", 0);
    if tag.is_empty() || at <= 0 {
        return;
    }
    if hox_common::parse_version(&tag) <= hox_common::parse_version(hox_common::APP_VERSION) {
        // обновление уже установлено — чистим устаревшее расписание
        st.remove("scheduledTag");
        st.remove("scheduledAtMs");
        return;
    }
    s.scheduled_tag = tag.clone();
    s.scheduled_at_ms = at;
    let when = hox_common::fmt_when(at);
    let _ = msg_tx.send(EngineMsg::Log(format!(
        "Восстановлено расписание: v{} установлю в {}",
        hox_common::rel_ver_of(&tag),
        when
    )));
}

fn cancel_schedule(s: &mut EngineState, why: &str, msg_tx: &Sender<EngineMsg>) {
    let had = s.scheduled_at_ms > 0 || !s.scheduled_tag.is_empty();
    s.scheduled_at_ms = 0;
    s.scheduled_tag.clear();
    let st = Settings::open();
    st.remove("scheduledTag");
    st.remove("scheduledAtMs");
    if had && !why.is_empty() {
        let _ = msg_tx.send(EngineMsg::Log(why.to_string()));
    }
}

// ------------------------------------------------------------ markdown -> текст
/// Порт notesToPlain: тело релиза markdown -> простой текст.
pub fn notes_to_plain(md: &str) -> String {
    let mut out = String::new();
    for raw in md.split('\n') {
        let mut line = raw.trim().to_string();
        if line.is_empty() {
            if !out.is_empty() && !out.ends_with('\n') {
                out.push('\n');
            }
            continue;
        }
        if line.starts_with("##") {
            continue;
        }
        if line.starts_with("- ") || line.starts_with("* ") {
            line = format!("• {}", &line[2..]);
        }
        line = line.replace("**", "").replace('`', "");
        while line.starts_with('#') {
            line.remove(0);
        }
        let line = line.trim().to_string();
        if line.is_empty() {
            continue;
        }
        out.push_str(&line);
        out.push('\n');
    }
    out.trim().to_string()
}

/// Короткая строка для балуна (~256 символов в Windows szInfo).
pub fn notes_preview(md: &str, max_chars: usize) -> String {
    let one = notes_to_plain(md).replace('\n', "  ");
    if one.chars().count() > max_chars {
        let s: String = one.chars().take(max_chars).collect();
        return format!("{}…", s.trim_end());
    }
    one
}
