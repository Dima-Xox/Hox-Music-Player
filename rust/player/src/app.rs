//! Состояние и логика плеера (порт C++ MainWindow + MainWindow_play).

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use egui::ColorImage;

use crate::audio::AudioEngine;
use crate::karaoke::Karaoke;
use crate::library::{self, ScanMsg, Track};
use crate::settings::Settings;
use crate::themes;
use crate::ui;

/// Состояние, видимое миниплееру (второй viewport).
#[derive(Clone, Default)]
pub struct MiniState {
    pub open: bool,
    pub playing: bool,
    pub title: String,
    pub artist: String,
    pub pos_frac: f64, // 0..1000
    pub karaoke_line: String,
}

/// Команды миниплеера главному окну.
#[derive(Debug, Clone, Copy)]
pub enum MiniCmd {
    PlayPause,
    Next,
    Prev,
    SeekFrac(f64),
    Close,
}

pub struct HoxApp {
    pub ctx: egui::Context,
    pub audio: AudioEngine,
    pub tracks: Vec<Track>,
    pub folders: Vec<String>,
    pub current: isize,
    pub repeat: usize,
    pub shuffle: bool,
    pub volume: f32,
    pub theme_idx: usize,
    pub kara: Karaoke,
    pub status: String,
    pub scan_status: String,
    pub show_whatsnew: bool,
    pub seek_drag: Option<f64>,
    pub open_folder_menu: Option<Vec<String>>,
    // скан
    track_rx: Option<Receiver<Vec<Track>>>,
    scan_rx: Option<Receiver<ScanMsg>>,
    scan_cancel: Arc<AtomicBool>,
    scan_active: bool,
    pending_restore: Option<(String, i64)>,
    // одиночный экземпляр
    pub incoming_rx: Option<Receiver<Vec<String>>>,
    // миниплеер
    pub mini: Arc<Mutex<MiniState>>,
    pub mini_cover: Arc<Mutex<Option<egui::TextureHandle>>>,
    pub mini_cmd_tx: Sender<MiniCmd>,
    mini_cmd_rx: Receiver<MiniCmd>,
    mini_track_key: String,
    // кэш обложек
    cover_cache: HashMap<String, egui::TextureHandle>,
    // визуализатор
    pub viz_levels: Vec<f64>,
    pub viz_targets: Vec<f64>,
    pub viz_playing: bool,
    // сортировка таблицы (usize::MAX = без сортировки)
    pub sort_col: usize,
    pub sort_asc: bool,
}

impl HoxApp {
    pub fn new(cc: &eframe::CreationContext<'_>, incoming_rx: Option<Receiver<Vec<String>>>) -> Self {
        let ctx = cc.egui_ctx.clone();
        let st = Settings::open();
        let volume = (st.get_i64("volume", 80) as f32 / 100.0).clamp(0.0, 1.0);
        let theme_idx = (st.get_i64("theme", 0) as usize).min(themes::THEMES.len() - 1);
        let repeat = (st.get_i64("repeat", 0) as usize) % 3;
        let shuffle = st.get_bool("shuffle", false);
        let folders = st.get_list("folders");

        let audio = AudioEngine::new().unwrap_or_else(|e| {
            eprintln!("HoxPlayer: {e}");
            AudioEngine::dummy()
        });

        let (mini_cmd_tx, mini_cmd_rx) = std::sync::mpsc::channel();

        let mut app = HoxApp {
            ctx: ctx.clone(),
            audio,
            tracks: Vec::new(),
            folders,
            current: -1,
            repeat,
            shuffle,
            volume,
            theme_idx,
            kara: Karaoke::default(),
            status: String::new(),
            scan_status: String::new(),
            show_whatsnew: false,
            seek_drag: None,
            open_folder_menu: None,
            track_rx: None,
            scan_rx: None,
            scan_cancel: Arc::new(AtomicBool::new(false)),
            scan_active: false,
            pending_restore: None,
            incoming_rx,
            mini: Arc::new(Mutex::new(MiniState::default())),
            mini_cover: Arc::new(Mutex::new(None)),
            mini_cmd_tx,
            mini_cmd_rx,
            mini_track_key: String::new(),
            cover_cache: HashMap::new(),
            viz_levels: vec![0.0; 28],
            viz_targets: vec![0.0; 28],
            viz_playing: false,
            sort_col: usize::MAX,
            sort_asc: true,
        };

        {
            let mut m = app.mini.lock().unwrap();
            m.open = st.get_bool("mini_visible", false);
        }
        // «Что нового» — один раз на версию
        if st.get_str("whatsnew_seen", "") != hox_common::APP_VERSION {
            app.show_whatsnew = true;
            st.set_str("whatsnew_seen", hox_common::APP_VERSION);
        }
        app.audio.set_volume(volume);

        // восстановление библиотеки и последнего трека (порт restoreLast)
        if !app.folders.is_empty() {
            let last = st.get_str("last_track", "");
            let pos = st.get_i64("last_pos", 0);
            if !last.is_empty() {
                app.pending_restore = Some((last, pos));
            }
            app.rescan_all();
        }
        app.refresh_status();
        app
    }

    // ------------------------------------------------------------ статус
    pub fn refresh_status(&mut self) {
        self.status = format!(
            "Треков: {}   |   Папок: {}",
            self.tracks.len(),
            self.folders.len()
        );
    }

    // ------------------------------------------------------------ сканирование
    pub fn add_folder(&mut self) {
        if let Some(dir) = rfd::FileDialog::new()
            .set_title("Добавить папку с музыкой")
            .pick_folder()
        {
            let dir = dir.to_string_lossy().to_string();
            if !self.folders.contains(&dir) {
                self.folders.push(dir);
                let st = Settings::open();
                st.set_list("folders", &self.folders);
            }
            self.rescan_all();
        }
    }

    pub fn remove_folder(&mut self, idx: usize) {
        if idx < self.folders.len() {
            self.folders.remove(idx);
            let st = Settings::open();
            st.set_list("folders", &self.folders);
            self.rescan_all();
        }
    }

    pub fn rescan_all(&mut self) {
        self.tracks.clear();
        self.current = -1;
        self.scan_cancel.store(true, Ordering::Relaxed);
        let cancel = Arc::new(AtomicBool::new(false));
        self.scan_cancel = cancel.clone();

        let (ttx, trx) = std::sync::mpsc::channel();
        let (ptx, prx) = std::sync::mpsc::channel();
        self.track_rx = Some(trx);
        self.scan_rx = Some(prx);
        self.scan_active = true;
        self.scan_status = String::new();

        let folders = self.folders.clone();
        library::scan_folders(folders, ttx, ptx, cancel);
        self.refresh_status();
    }

    pub fn poll_scan(&mut self) {
        if let Some(rx) = &self.track_rx {
            while let Ok(chunk) = rx.try_recv() {
                self.tracks.extend(chunk);
            }
        }
        if let Some(rx) = &self.scan_rx {
            let mut done = false;
            while let Ok(m) = rx.try_recv() {
                match m {
                    ScanMsg::Progress { done, total } => {
                        self.scan_status = format!("Сканирование: {done}/{total}");
                    }
                    ScanMsg::Done => done = true,
                }
            }
            if done {
                self.scan_active = false;
                self.scan_status.clear();
                self.track_rx = None;
                self.scan_rx = None;
                self.refresh_status();
                self.finish_restore();
            }
        }
    }

    /// После сканирования — восстановить последний трек (порт restoreLast).
    fn finish_restore(&mut self) {
        let Some((last, pos)) = self.pending_restore.take() else { return };
        let r = self.tracks.iter().position(|t| t.path == last);
        if let Some(r) = r {
            self.current = r as isize;
            let dur = self.tracks[r].dur_ms;
            if self.audio.play_file(&last).is_ok() {
                self.audio.pause();
                if pos > 0 && pos < dur - 1000 {
                    self.audio.seek_ms(pos);
                }
            }
            self.load_karaoke(r);
            self.sync_mini_track();
        }
    }

    // ------------------------------------------------------------ воспроизведение
    pub fn play_index(&mut self, row: usize) {
        if row >= self.tracks.len() {
            return;
        }
        self.current = row as isize;
        let t = self.tracks[row].clone();
        if self.audio.play_file(&t.path).is_err() {
            self.scan_status = format!("Ошибка воспроизведения: {}", file_name(&t.path));
        }
        self.kara = Karaoke::default();
        self.load_karaoke(row);
        self.sync_mini_track();
        let st = Settings::open();
        st.set_str("last_track", &t.path);
        st.set_i64("last_pos", 0);
    }

    pub fn toggle_play_pause(&mut self) {
        if self.current < 0 {
            if !self.tracks.is_empty() {
                self.play_index(0);
            }
            return;
        }
        if self.audio.is_playing() {
            self.audio.pause();
        } else if self.audio.finished() {
            self.play_index(self.current as usize);
        } else {
            self.audio.resume();
        }
    }

    pub fn next_track(&mut self) {
        if self.tracks.is_empty() {
            return;
        }
        if self.repeat == 1 && self.current >= 0 {
            self.audio.seek_ms(0);
            self.audio.resume();
            return;
        }
        let n = self.tracks.len();
        let idx = if self.shuffle {
            if n > 1 {
                (self.current.max(0) as usize + 1 + rand_below(n as u32 - 1) as usize) % n
            } else {
                0
            }
        } else {
            let cur = if self.current < 0 { -1 } else { self.current };
            let idx = cur + 1;
            if idx as usize >= n {
                if self.repeat == 2 {
                    0
                } else {
                    n - 1
                }
            } else {
                idx as usize
            }
        };
        self.play_index(idx);
    }

    pub fn prev_track(&mut self) {
        if self.tracks.is_empty() {
            return;
        }
        // v0.4.8-фикс: от нуля при невыбранном треке
        let start = if self.current >= 0 { self.current as usize } else { 0 };
        let idx = (start + self.tracks.len() - 1) % self.tracks.len();
        self.play_index(idx);
    }

    /// Конец трека -> следующий (учитывая repeat).
    pub fn on_track_finished(&mut self) {
        if self.tracks.is_empty() {
            return;
        }
        match self.repeat {
            1 => {
                let cur = self.current.max(0) as usize;
                self.play_index(cur);
            }
            2 => {
                let idx = (self.current.max(0) as usize + 1) % self.tracks.len();
                self.play_index(idx);
            }
            _ => {
                let idx = self.current.max(0) as usize + 1;
                if idx < self.tracks.len() {
                    self.play_index(idx);
                } else {
                    self.audio.stop();
                    self.current = -1;
                }
            }
        }
    }

    pub fn set_volume(&mut self, v: f32) {
        self.volume = v.clamp(0.0, 1.0);
        self.audio.set_volume(self.volume);
        Settings::open().set_i64("volume", (self.volume * 100.0).round() as i64);
    }

    pub fn cycle_repeat(&mut self) {
        self.repeat = (self.repeat + 1) % 3;
        Settings::open().set_i64("repeat", self.repeat as i64);
    }

    pub fn toggle_shuffle(&mut self) {
        self.shuffle = !self.shuffle;
        Settings::open().set_bool("shuffle", self.shuffle);
    }

    // ------------------------------------------------------------ караоке
    pub fn load_karaoke(&mut self, row: usize) {
        let off = Settings::open().get_i64("lyric_offset_ms", 0);
        self.kara.set_offset(off);
        let t = &self.tracks[row];
        let lp = library::lrc_path_of(&t.path);
        if !t.has_lrc || !lp.exists() {
            self.kara.clear();
            return;
        }
        let lines = crate::karaoke::parse_lrc(&lp);
        if lines.is_empty() {
            self.kara.clear();
            return;
        }
        self.kara.set_lyrics(lines);
        self.kara.set_position(self.audio.position_ms());
    }

    pub fn karaoke_seek(&mut self, raw_ms: i64) {
        self.audio.seek_ms(raw_ms.max(0));
        self.kara.set_position(raw_ms.max(0));
    }

    pub fn karaoke_offset_changed(&mut self, off: i64) {
        Settings::open().set_i64("lyric_offset_ms", off);
    }

    // ------------------------------------------------------------ файлы из аргументов/второго экземпляра
    pub fn start_playback_from_paths(&mut self, paths: &[String]) {
        let mut first: Option<usize> = None;
        for p in paths {
            let pb = std::path::Path::new(p);
            if !pb.is_file() || !library::is_audio(pb) {
                continue;
            }
            let name = pb
                .file_name()
                .map(|n| n.to_string_lossy().to_string())
                .unwrap_or_default();
            let probed = library::probe_track(p);
            let mut t = Track {
                path: pb.to_string_lossy().to_string(),
                num: if probed.num >= 0 { probed.num } else { library::leading_number(&name) },
                title: probed.title,
                artist: probed.artist,
                album: probed.album,
                dur_ms: probed.dur_ms,
                cover: probed.cover,
                has_lrc: probed.has_lrc,
            };
            if t.title.is_empty() {
                t.title = pb
                    .file_stem()
                    .map(|s| s.to_string_lossy().to_string())
                    .unwrap_or(name);
            }
            if first.is_none() {
                first = Some(self.tracks.len());
            }
            self.tracks.push(t);
        }
        if let Some(f) = first {
            self.refresh_status();
            self.play_index(f);
        }
    }

    // ------------------------------------------------------------ миниплеер
    pub fn toggle_mini(&mut self) {
        let mut m = self.mini.lock().unwrap();
        m.open = !m.open;
        Settings::open().set_bool("mini_visible", m.open);
    }

    pub fn sync_mini_track(&mut self) {
        let key = if self.current >= 0 && (self.current as usize) < self.tracks.len() {
            self.tracks[self.current as usize].path.clone()
        } else {
            String::new()
        };
        if key == self.mini_track_key {
            return;
        }
        self.mini_track_key = key;
        let (title, artist, cover_tex) = {
            if self.current >= 0 && (self.current as usize) < self.tracks.len() {
                let idx = self.current as usize;
                let t = &self.tracks[idx];
                let title = t.title.clone();
                let artist = t.artist.clone();
                let ctx = self.ctx.clone();
                let tex = self.cover_texture(&ctx, idx, 168);
                (title, artist, tex)
            } else {
                (String::new(), String::new(), None)
            }
        };
        let mut m = self.mini.lock().unwrap();
        m.title = title;
        m.artist = artist;
        *self.mini_cover.lock().unwrap() = cover_tex;
    }

    pub fn poll_mini_cmds(&mut self) {
        while let Ok(c) = self.mini_cmd_rx.try_recv() {
            match c {
                MiniCmd::PlayPause => self.toggle_play_pause(),
                MiniCmd::Next => self.next_track(),
                MiniCmd::Prev => self.prev_track(),
                MiniCmd::SeekFrac(f) => {
                    if self.current >= 0 {
                        let dur = self.tracks[self.current as usize].dur_ms.max(1);
                        let pos = (f * dur as f64) as i64;
                        self.audio.seek_ms(pos);
                        self.kara.set_position(pos);
                    }
                }
                MiniCmd::Close => {
                    let mut m = self.mini.lock().unwrap();
                    m.open = false;
                    Settings::open().set_bool("mini_visible", false);
                }
            }
        }
    }

    // ------------------------------------------------------------ настройки/выход
    pub fn save_settings(&mut self) {
        let st = Settings::open();
        st.set_i64("volume", (self.volume * 100.0).round() as i64);
        st.set_i64("theme", self.theme_idx as i64);
        st.set_i64("repeat", self.repeat as i64);
        st.set_bool("shuffle", self.shuffle);
        st.set_i64("lyric_offset_ms", self.kara.offset_ms);
        let cur = self.current;
        if cur >= 0 && (cur as usize) < self.tracks.len() {
            let t = &self.tracks[cur as usize];
            st.set_str("last_track", &t.path);
            st.set_i64("last_pos", self.audio.position_ms());
        }
        let mini_open = self.mini.lock().unwrap().open;
        st.set_bool("mini_visible", mini_open);
    }

    // ------------------------------------------------------------ обложка
    pub fn cover_texture(
        &mut self,
        ctx: &egui::Context,
        idx: usize,
        side: u32,
    ) -> Option<egui::TextureHandle> {
        let t = self.tracks.get(idx)?;
        if t.cover.is_empty() {
            return None;
        }
        let key = format!("{}|{}|{}", t.path, t.cover.len(), side);
        if let Some(tex) = self.cover_cache.get(&key) {
            return Some(tex.clone());
        }
        let (rgba, w, h) = library::decode_cover(&t.cover, side)?;
        let img = ColorImage::from_rgba_unmultiplied([w as usize, h as usize], &rgba);
        let tex = ctx.load_texture(format!("cover-{key}"), img, egui::TextureOptions::LINEAR);
        if self.cover_cache.len() > 24 {
            self.cover_cache.clear();
        }
        self.cover_cache.insert(key, tex.clone());
        Some(tex)
    }

    // ------------------------------------------------------------ визуализатор
    pub fn tick_viz(&mut self) {
        let n = self.viz_levels.len();
        if self.viz_playing {
            let t = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs_f64())
                .unwrap_or(0.0);
            for i in 0..n {
                let base = 0.35 + 0.65 * (i as f64 * 0.55 + t).sin().abs();
                let rnd = pseudo_rand(i, t);
                self.viz_targets[i] = (base * (0.35 + 0.65 * rnd)).clamp(0.05, 1.0);
            }
            for i in 0..n {
                self.viz_levels[i] += (self.viz_targets[i] - self.viz_levels[i]) * 0.35;
            }
        } else {
            for i in 0..n {
                self.viz_levels[i] += (0.0 - self.viz_levels[i]) * 0.12;
            }
        }
    }
}

impl eframe::App for HoxApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        // приём файлов от второго экземпляра
        if let Some(rx) = &self.incoming_rx {
            let mut got: Vec<Vec<String>> = Vec::new();
            while let Ok(paths) = rx.try_recv() {
                got.push(paths);
            }
            for paths in got {
                self.start_playback_from_paths(&paths);
            }
        }

        self.poll_scan();
        self.poll_mini_cmds();

        // конец трека
        if self.audio.finished() && !self.tracks.is_empty() && self.current >= 0 {
            self.on_track_finished();
        }

        // синхронизация состояния
        self.viz_playing = self.audio.is_playing();
        let playing = self.audio.is_playing();
        self.kara.set_position(self.audio.position_ms());
        self.sync_mini_track();
        {
            let mut m = self.mini.lock().unwrap();
            m.playing = playing;
            let dur = if self.current >= 0 && (self.current as usize) < self.tracks.len() {
                self.tracks[self.current as usize].dur_ms.max(1)
            } else {
                1
            };
            m.pos_frac = (self.audio.position_ms() as f64 / dur as f64 * 1000.0).clamp(0.0, 1000.0);
            m.karaoke_line = if self.kara.is_empty() {
                String::new()
            } else {
                self.kara.current_line_text()
            };
        }
        self.tick_viz();

        // отрисовка
        ui::run_ui(self, ctx);
        ui::show_mini_viewport(
            ctx,
            self.mini.clone(),
            self.mini_cover.clone(),
            self.mini_cmd_tx.clone(),
            self.theme_idx,
        );

        // сохранение настроек при закрытии
        if ctx.input(|i| i.viewport().close_requested()) {
            self.save_settings();
        }

        // частота перерисовки: играет/сканируется — 30 fps
        if self.audio.is_playing() || self.scan_active {
            ctx.request_repaint_after(Duration::from_millis(33));
        }
    }

    fn on_exit(&mut self, _gl: Option<&eframe::glow::Context>) {
        self.save_settings();
    }
}

fn rand_below(n: u32) -> u32 {
    if n == 0 {
        return 0;
    }
    let t = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.subsec_nanos() as u64 ^ (d.as_secs() << 17))
        .unwrap_or(0);
    let mut x = t ^ (std::process::id() as u64) ^ ((n as u64) << 13);
    x ^= x << 13;
    x ^= x >> 7;
    x ^= x << 17;
    (x % n as u64) as u32
}

fn pseudo_rand(i: usize, t: f64) -> f64 {
    let x = (i as f64 * 12.9898 + t * 78.233).sin() * 43758.5453;
    x - x.floor()
}

pub fn file_name(p: &str) -> String {
    std::path::Path::new(p)
        .file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_else(|| p.to_string())
}
