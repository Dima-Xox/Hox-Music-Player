//! HoxLauncher — менеджер плеера (Rust + egui).
//! Проверка обновлений с GitHub, авто-обновление с SHA-256 и тихой
//! установкой, отложенный запуск по расписанию, трей с балунами.
//! CLI: --version, --selfcheck, --after-update.

#![cfg_attr(all(not(debug_assertions), target_os = "windows"), windows_subsystem = "windows")]

mod app;
mod platform;
mod settings;
mod updater;

use std::sync::atomic::AtomicBool;
use std::sync::mpsc;
use std::sync::{Arc, Mutex};

use hox_common::APP_VERSION;
use platform::{Balloon, TrayCmd, TrayShared};

const HELP: &str = "\
Hox Launcher — менеджер плеера

USAGE:
    HoxLauncher.exe [OPTIONS]

OPTIONS:
    --version         Версия
    --selfcheck       Проверка запуска (для CI)
    --after-update    Запуск после тихого обновления: в трей и старт плеера
    -h, --help        Справка
";

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();

    if args.iter().any(|a| a == "-h" || a == "--help") {
        unsafe { platform::attach_console() };
        println!("{HELP}");
        return;
    }
    if args.iter().any(|a| a == "--version" || a == "-V") {
        unsafe { platform::attach_console() };
        println!("Hox Launcher {}", APP_VERSION);
        return;
    }
    if args.iter().any(|a| a == "--selfcheck") {
        unsafe { platform::attach_console() };
        println!("HoxLauncher selfcheck OK {APP_VERSION}");
        std::process::exit(0);
    }
    let after_update = args.iter().any(|a| a == "--after-update");

    // часовой пояс для отображения времени расписания
    hox_common::LOCAL_UTC_OFFSET_SECS.store(
        unsafe { platform::local_utc_offset_secs() },
        std::sync::atomic::Ordering::Relaxed,
    );

    // одиночный экземпляр: повторный запуск разворачивает открытое окно
    #[cfg(windows)]
    let _mutex_guard = unsafe { platform::try_lock_mutex() };
    #[cfg(windows)]
    {
        if _mutex_guard.is_none() {
            unsafe { platform::send_to_instance("show") };
            return;
        }
    }
    #[cfg(not(windows))]
    {
        let _ = after_update;
    }

    // канал «show» от вторых копий
    let (show_tx, show_rx) = mpsc::channel::<String>();
    let _instance_server = platform::spawn_instance_server(show_tx);

    // иконка для трея
    let ico_path = platform::write_ico_temp().unwrap_or_default();

    // движок обновлений
    let (engine_tx, msg_rx, quit) = updater::Engine::spawn();

    // трей
    let tray_shared = Arc::new(Mutex::new(TrayShared {
        auto_update: settings::Settings::open().get_bool("autoUpdate", true),
        delay_hours: settings::Settings::open().get_i64("updateDelayHours", 0) as u32,
        has_notes: false,
        notes_label: "Что нового".into(),
    }));
    let (tray_tx, tray_rx) = mpsc::channel::<TrayCmd>();
    let (balloon_tx, balloon_rx) = mpsc::channel::<Balloon>();
    let _tray = platform::spawn_tray(tray_shared.clone(), tray_tx, balloon_rx, ico_path.clone());

    let icon = window_icon();
    let mut viewport = egui::ViewportBuilder::default()
        .with_title(format!("Hox — лаунчер v{APP_VERSION}"))
        .with_inner_size([560.0, 430.0])
        .with_resizable(false);
    if let Some(icon) = icon {
        viewport = viewport.with_icon(std::sync::Arc::new(icon));
    }
    let options = eframe::NativeOptions {
        viewport,
        renderer: eframe::Renderer::Glow,
        ..Default::default()
    };

    let quit2 = quit.clone();
    let r = eframe::run_native(
        &format!("Hox — лаунчер v{APP_VERSION}"),
        options,
        Box::new(move |cc| {
            Ok(Box::new(app::LauncherApp {
                ctx: cc.egui_ctx.clone(),
                visible: !after_update,
                status: String::new(),
                log: Vec::new(),
                progress: None,
                busy: false,
                check_busy: false,
                remote_tag: String::new(),
                remote_notes: String::new(),
                player_version: String::new(),
                player_exists: false,
                show_whatsnew: false,
                notes_label: None,
                update_btn: None,
                cmd_tx: engine_tx,
                msg_rx,
                tray_rx,
                tray_shared,
                balloon_tx,
                tray_hint_shown: false,
                quit: quit2,
            }) as Box<dyn eframe::App>)
        }),
    );
    quit.store(true, std::sync::atomic::Ordering::Relaxed);
    if let Err(e) = r {
        eprintln!("HoxLauncher: {e:?}");
        std::process::exit(1);
    }
}

fn window_icon() -> Option<egui::IconData> {
    const ICO: &[u8] = include_bytes!("../assets/hox.ico");
    let img = image::load_from_memory_with_format(ICO, image::ImageFormat::Ico).ok()?;
    let img = img.to_rgba8();
    let (w, h) = (img.width(), img.height());
    Some(egui::IconData { width: w, height: h, rgba: img.into_raw() })
}
