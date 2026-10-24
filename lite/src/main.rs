use std::cmp::Ordering;
use std::fs::File;
use std::io::BufReader;
use std::iter::Peekable;
use std::path::{Path, PathBuf};
use std::str::Chars;
use std::time::Duration;

use eframe::egui;
use lofty::prelude::*;
use rodio::{Decoder, OutputStream, OutputStreamHandle, Sink};
use walkdir::WalkDir;

// ---------------------------------------------------------------------------
// Библиотека: поиск файлов, метаданные, натуральная сортировка
// ---------------------------------------------------------------------------

const EXTENSIONS: &[&str] = &[
    "flac", "wav", "wave", "mp3", "ogg", "m4a", "aac", "opus", "wma", "aiff",
];

#[derive(Clone, Debug)]
struct Track {
    path: PathBuf,
    title: String,
    artist: String,
    album: String,
    track_no: Option<u32>,
    duration: Duration,
}

/// Натуральная сортировка: «2» < «10», регистр не важен.
fn natural_cmp(a: &str, b: &str) -> Ordering {
    let mut ai: Peekable<Chars> = a.chars().peekable();
    let mut bi: Peekable<Chars> = b.chars().peekable();
    loop {
        match (ai.peek().copied(), bi.peek().copied()) {
            (None, None) => return Ordering::Equal,
            (None, Some(_)) => return Ordering::Less,
            (Some(_), None) => return Ordering::Greater,
            (Some(x), Some(y)) if x.is_ascii_digit() && y.is_ascii_digit() => {
                let na = take_digits(&mut ai);
                let nb = take_digits(&mut bi);
                let ta = na.trim_start_matches('0');
                let tb = nb.trim_start_matches('0');
                let o = ta.len().cmp(&tb.len()).then_with(|| ta.cmp(tb));
                if o != Ordering::Equal {
                    return o;
                }
            }
            (Some(x), Some(y)) => {
                let o = x.to_lowercase().cmp(y.to_lowercase());
                if o != Ordering::Equal {
                    return o;
                }
                ai.next();
                bi.next();
            }
        }
    }
}

fn take_digits(it: &mut Peekable<Chars>) -> String {
    let mut s = String::new();
    while let Some(c) = it.peek().copied() {
        if c.is_ascii_digit() {
            s.push(c);
            it.next();
        } else {
            break;
        }
    }
    s
}

fn is_supported(path: &Path) -> bool {
    path.extension()
        .and_then(|e| e.to_str())
        .map(|e| EXTENSIONS.contains(&e.to_lowercase().as_str()))
        .unwrap_or(false)
}

/// Метаданные одного файла. Без тегов — имя файла и папка как запасной вариант.
fn read_track(path: &Path) -> Track {
    let title = path
        .file_stem()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default();
    let album = path
        .parent()
        .and_then(|p| p.file_name())
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_else(|| "Без альбома".to_string());

    let mut t = Track {
        path: path.to_path_buf(),
        title,
        artist: "Неизвестный исполнитель".to_string(),
        album,
        track_no: None,
        duration: Duration::ZERO,
    };

    if let Ok(tagged) = lofty::read_from_path(path) {
        t.duration = tagged.properties().duration();
        if let Some(tag) = tagged.primary_tag().or_else(|| tagged.first_tag()) {
            if let Some(v) = tag.title().map(|v| v.trim().to_string()).filter(|v| !v.is_empty()) {
                t.title = v;
            }
            if let Some(v) = tag.artist().map(|v| v.trim().to_string()).filter(|v| !v.is_empty()) {
                t.artist = v;
            }
            if let Some(v) = tag.album().map(|v| v.trim().to_string()).filter(|v| !v.is_empty()) {
                t.album = v;
            }
            t.track_no = tag.track();
        }
    }
    t
}

/// Рекурсивный обход папки; треки в натуральном порядке путей.
/// Синхронный: на больших библиотеках окно подвиснет.
fn scan(folder: &Path) -> Vec<Track> {
    let mut paths: Vec<PathBuf> = WalkDir::new(folder)
        .into_iter()
        .filter_map(|e| e.ok())
        .filter(|e| e.file_type().is_file())
        .map(|e| e.into_path())
        .filter(|p| is_supported(p))
        .collect();

    paths.sort_by(|a, b| natural_cmp(&a.to_string_lossy(), &b.to_string_lossy()));
    paths.iter().map(|p| read_track(p)).collect()
}

// ---------------------------------------------------------------------------
// Воспроизведение (rodio)
// ---------------------------------------------------------------------------

struct Player {
    // Поток вывода должен жить, пока жив плеер, иначе звука не будет
    _stream: OutputStream,
    handle: OutputStreamHandle,
    sink: Option<Sink>,
    volume: f32,
}

impl Player {
    fn new(volume: f32) -> Result<Self, String> {
        let (stream, handle) = OutputStream::try_default().map_err(|e| e.to_string())?;
        Ok(Self {
            _stream: stream,
            handle,
            sink: None,
            volume,
        })
    }

    fn play_file(&mut self, path: &Path) -> Result<(), String> {
        let file = File::open(path).map_err(|e| e.to_string())?;
        let source = Decoder::new(BufReader::new(file)).map_err(|e| e.to_string())?;
        let sink = Sink::try_new(&self.handle).map_err(|e| e.to_string())?;
        sink.set_volume(self.volume);
        sink.append(source);
        // Старый sink удаляется при замене — предыдущий трек останавливается
        self.sink = Some(sink);
        Ok(())
    }

    fn toggle_pause(&mut self) {
        if let Some(s) = &self.sink {
            if s.is_paused() {
                s.play();
            } else {
                s.pause();
            }
        }
    }

    fn stop(&mut self) {
        self.sink = None;
    }

    /// Трек доигран до конца (sink есть, но пуст).
    fn is_finished(&self) -> bool {
        self.sink.as_ref().map_or(false, |s| s.empty())
    }

    fn is_paused(&self) -> bool {
        self.sink.as_ref().map_or(false, |s| s.is_paused())
    }

    fn set_volume(&mut self, v: f32) {
        self.volume = v;
        if let Some(s) = &self.sink {
            s.set_volume(v);
        }
    }
}

// ---------------------------------------------------------------------------
// Интерфейс (egui / eframe)
// ---------------------------------------------------------------------------

struct App {
    tracks: Vec<Track>,
    current: Option<usize>,
    player: Option<Player>,
    volume: f32,
    error: Option<String>,
}

impl App {
    fn new() -> Self {
        let volume = 0.7;
        let (player, error) = match Player::new(volume) {
            Ok(p) => (Some(p), None),
            Err(e) => (None, Some(format!("Нет аудиовыхода: {e}"))),
        };
        Self {
            tracks: Vec::new(),
            current: None,
            player,
            volume,
            error,
        }
    }

    fn load_folder(&mut self, folder: PathBuf) {
        self.tracks = scan(&folder);
        self.current = None;
        self.error = None;
        if let Some(p) = self.player.as_mut() {
            p.stop();
        }
    }

    fn play(&mut self, idx: usize) {
        let Some(player) = self.player.as_mut() else {
            self.error = Some("Аудиовыход недоступен".to_string());
            return;
        };
        match player.play_file(&self.tracks[idx].path) {
            Ok(()) => {
                self.current = Some(idx);
                self.error = None;
            }
            Err(e) => {
                self.error = Some(format!(
                    "Не удалось открыть «{}»: {e}",
                    self.tracks[idx].title
                ));
            }
        }
    }

    /// Следующий трек. Ручное нажатие идёт по кругу, автопереход — нет.
    fn next(&mut self, wrap: bool) {
        let len = self.tracks.len();
        if len == 0 {
            return;
        }
        match self.current {
            None => self.play(0),
            Some(c) if c + 1 < len => self.play(c + 1),
            Some(_) if wrap => self.play(0),
            Some(_) => {
                // Конец списка при автопереходе: останавливаемся
                if let Some(p) = self.player.as_mut() {
                    p.stop();
                }
                self.current = None;
            }
        }
    }

    fn prev(&mut self) {
        let len = self.tracks.len();
        if len == 0 {
            return;
        }
        let idx = self.current.map_or(len - 1, |c| (c + len - 1) % len);
        self.play(idx);
    }

    fn toggle(&mut self) {
        match self.current {
            Some(_) => {
                if let Some(p) = self.player.as_mut() {
                    p.toggle_pause();
                }
            }
            None => {
                if !self.tracks.is_empty() {
                    self.play(0);
                }
            }
        }
    }
}

fn fmt_dur(d: Duration) -> String {
    let s = d.as_secs();
    format!("{:02}:{:02}", s / 60, s % 60)
}

impl eframe::App for App {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        if self.player.as_ref().map_or(false, |p| p.is_finished()) {
            self.next(false);
        }
        ctx.request_repaint_after(Duration::from_millis(250));

        let mut to_play: Option<usize> = None;
        let mut action_open = false;
        let mut action_prev = false;
        let mut action_toggle = false;
        let mut action_stop = false;
        let mut action_next = false;

        egui::TopBottomPanel::top("controls").show(ctx, |ui| {
            ui.horizontal(|ui| {
                if ui.button("Открыть папку").clicked() {
                    action_open = true;
                }
                ui.separator();
                if ui.button("⏮").clicked() {
                    action_prev = true;
                }
                let is_playing = self.current.is_some()
                    && self.player.as_ref().map_or(false, |p| !p.is_paused());
                let label = if is_playing { "⏸" } else { "▶" };
                if ui.button(label).clicked() {
                    action_toggle = true;
                }
                if ui.button("⏹").clicked() {
                    action_stop = true;
                }
                if ui.button("⏭").clicked() {
                    action_next = true;
                }
                ui.separator();
                ui.label("Громкость");
                let slider =
                    ui.add(egui::Slider::new(&mut self.volume, 0.0..=1.0).show_value(false));
                if slider.changed() {
                    if let Some(p) = self.player.as_mut() {
                        p.set_volume(self.volume);
                    }
                }
            });
        });

        egui::TopBottomPanel::bottom("status").show(ctx, |ui| {
            let now = match self.current {
                Some(i) => format!("▶ {} — {}", self.tracks[i].title, self.tracks[i].artist),
                None => "Трек не выбран".to_string(),
            };
            ui.label(now);
            if let Some(err) = &self.error {
                ui.colored_label(egui::Color32::LIGHT_RED, err);
            }
            ui.label(format!("Треков: {}", self.tracks.len()));
        });

        egui::CentralPanel::default().show(ctx, |ui| {
            if self.tracks.is_empty() {
                ui.label("Откройте папку с музыкой.");
                return;
            }
            egui::ScrollArea::vertical().show(ui, |ui| {
                for (i, t) in self.tracks.iter().enumerate() {
                    let num = t.track_no.map_or(i + 1, |n| n as usize);
                    let label = format!(
                        "{:>4}. {} — {} [{}] {}",
                        num,
                        t.title,
                        t.artist,
                        t.album,
                        fmt_dur(t.duration)
                    );
                    let resp = ui.selectable_label(self.current == Some(i), label);
                    if resp.double_clicked() {
                        to_play = Some(i);
                    }
                }
            });
        });

        // Действия применяются после отрисовки панелей
        if action_open {
            if let Some(folder) = rfd::FileDialog::new().pick_folder() {
                self.load_folder(folder);
            }
        }
        if action_prev {
            self.prev();
        }
        if action_toggle {
            self.toggle();
        }
        if action_stop {
            if let Some(p) = self.player.as_mut() {
                p.stop();
            }
            self.current = None;
        }
        if action_next {
            self.next(true);
        }
        if let Some(i) = to_play {
            self.play(i);
        }
    }
}

fn main() -> eframe::Result<()> {
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default().with_inner_size([1000.0, 640.0]),
        ..Default::default()
    };
    eframe::run_native(
        "Hox Music Player",
        options,
        Box::new(|_cc| Ok(Box::new(App::new()))),
    )
}