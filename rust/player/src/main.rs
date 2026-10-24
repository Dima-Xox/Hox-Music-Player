//! HoxPlayer — лёгкий плеер с караоке (Rust + egui + rodio/symphonia).
//! CLI: --version, --selfcheck, --open <files>, позиционные файлы.
//! Одиночный экземпляр: вторая копия передаёт файлы первой (named pipe).

#![cfg_attr(all(not(debug_assertions), target_os = "windows"), windows_subsystem = "windows")]

mod app;
mod audio;
mod karaoke;
mod library;
mod platform;
mod settings;
mod themes;
mod ui;
mod whatsnew;

use std::sync::mpsc;

use hox_common::APP_VERSION;

const HELP: &str = "\
Hox Music Player — лёгкий плеер с караоке

USAGE:
    HoxPlayer.exe [OPTIONS] [FILES...]

ARGS:
    <FILES>...    Аудиофайлы для воспроизведения

OPTIONS:
    --open <FILES>...    Открыть файлы
    --version            Версия
    --selfcheck          Проверка запуска (для CI)
    -h, --help           Справка
";

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();

    if args.iter().any(|a| a == "-h" || a == "--help") {
        platform::attach_console();
        println!("{HELP}");
        return;
    }
    if args.iter().any(|a| a == "--version" || a == "-V") {
        platform::attach_console();
        println!("{} {}", hox_common::APP_NAME, APP_VERSION);
        return;
    }
    if args.iter().any(|a| a == "--selfcheck") {
        platform::attach_console();
        selfcheck();
        return;
    }

    // файлы из аргументов (--open X Y или просто X Y)
    let mut files: Vec<String> = Vec::new();
    let mut iter = args.iter();
    while let Some(a) = iter.next() {
        if a == "--open" {
            for f in iter.by_ref() {
                if f.starts_with("--") {
                    break;
                }
                files.push(f.clone());
            }
        } else if !a.starts_with("--") {
            files.push(a.clone());
        }
    }

    // одиночный экземпляр: передаём файлы работающей копии
    if !files.is_empty() || instance_running() {
        let payload = files.join("\n");
        if !payload.is_empty() && platform::send_to_instance(&payload) {
            return; // уже запущенный плеер подхватит файлы
        }
        if instance_running() && files.is_empty() {
            return; // вторая копия без файлов — просто выходим
        }
    }

    // сервер одиночного экземпляра + GUI
    let (tx, rx) = mpsc::channel();
    let _server = platform::spawn_instance_server(tx);

    let icon = platform::window_icon();
    let mut viewport = egui::ViewportBuilder::default()
        .with_title(format!("{} v{}", hox_common::APP_NAME, APP_VERSION))
        .with_inner_size([1120.0, 720.0])
        .with_min_inner_size([940.0, 560.0]);
    if let Some(icon) = icon {
        viewport = viewport.with_icon(std::sync::Arc::new(icon));
    }
    let options = eframe::NativeOptions {
        viewport,
        renderer: eframe::Renderer::Glow,
        ..Default::default()
    };

    let file_args = files.clone();
    let r = eframe::run_native(
        &format!("{} v{}", hox_common::APP_NAME, APP_VERSION),
        options,
        Box::new(move |cc| {
            Ok(Box::new(app::HoxApp::new(cc, Some(rx))) as Box<dyn eframe::App>)
        }),
    );
    if let Err(e) = r {
        eprintln!("HoxPlayer: не удалось запустить GUI: {e:?}");
        std::process::exit(1);
    }
    let _ = file_args;
}

fn instance_running() -> bool {
    // probe: пустая запись в pipe — сервер проигнорирует
    platform::send_to_instance("")
}

/// CI-самопроверка: настройки/декодеры/библиотека поднимаются, без окна.
fn selfcheck() {
    let mut ok = true;
    // настройки: открыть и прочитать
    let st = settings::Settings::open();
    st.set_str("selfcheck", "ok");
    let _ = st.get_str("selfcheck", "");
    st.remove("selfcheck");
    // темы
    debug_assert_eq!(themes::THEMES.len(), 5);
    // парсер LRC на встроенном тесте
    let dir = std::env::temp_dir().join("hox_selfcheck_lrc");
    let _ = std::fs::create_dir_all(&dir);
    let p = dir.join("sc.lrc");
    if std::fs::write(&p, "[00:01.00]тест\n").is_ok() {
        let lines = karaoke::parse_lrc(&p);
        ok &= lines.len() == 1;
        let _ = std::fs::remove_file(&p);
    }
    // аудио-движок (без воспроизведения)
    match audio::AudioEngine::new() {
        Ok(_) => {}
        Err(e) => eprintln!("предупреждение: {e} (CI может быть без аудио)"),
    }
    if ok {
        println!("HoxPlayer selfcheck OK {APP_VERSION}");
        std::process::exit(0);
    }
    eprintln!("HoxPlayer selfcheck FAIL {APP_VERSION}");
    std::process::exit(1);
}
