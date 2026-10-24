//! Библиотека: трек, сканирование папок, теги (lofty), обложки.
//! Порт C++ MainWindow::rescanAll/probeStatus (метаданные) — всё в одном
//! фоновом потоке.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::Sender;
use std::time::Duration;

use lofty::file::{AudioFile, TaggedFileExt};
use lofty::tag::Accessor;

#[derive(Debug, Clone, Default)]
pub struct Track {
    pub path: String,
    pub title: String,
    pub artist: String,
    pub album: String,
    pub num: i64, // -1 = нет; «0» — валидный номер (v0.4.8-фикс)
    pub dur_ms: i64,
    pub cover: Vec<u8>,
    pub has_lrc: bool,
}

pub const AUDIO_SUFFIXES: &[&str] = &["mp3", "wav", "ogg", "flac", "m4a", "aac", "opus"];

pub fn is_audio(p: &Path) -> bool {
    p.extension()
        .and_then(|e| e.to_str())
        .map(|e| AUDIO_SUFFIXES.contains(&e.to_ascii_lowercase().as_str()))
        .unwrap_or(false)
}

/// «01 - Artist - Title.mp3» -> 1; нет ведущего числа -> -1.
/// Порт C++ leadingNumber.
pub fn leading_number(file_name: &str) -> i64 {
    let re = regex::Regex::new(r"^\s*(\d{1,4})[\s.\-_–—)]+").unwrap();
    re.captures(file_name)
        .and_then(|c| c.get(1))
        .and_then(|m| m.as_str().parse().ok())
        .unwrap_or(-1)
}

pub fn lrc_path_of(path: &str) -> PathBuf {
    let p = Path::new(path);
    match p.file_stem() {
        Some(stem) => p.with_file_name(format!(
            "{}.lrc",
            stem.to_string_lossy()
        )),
        None => p.with_extension("lrc"),
    }
}

pub fn has_lrc(path: &str) -> bool {
    lrc_path_of(path).exists()
}

/// «Исполнитель - Название» из имени файла, если теги пусты (порт probeStatus).
fn split_filename(base: &str) -> (String, String) {
    // отрезаем ведущий номер
    let re = regex::Regex::new(r"^\s*\d{1,4}[\s.\-_–—)]+").unwrap();
    let base = re.replace(base, "").to_string();
    if let Some(dash) = base.find(" - ") {
        let artist = base[..dash].trim().to_string();
        let title = base[dash + 3..].trim().to_string();
        (artist, title)
    } else {
        (String::new(), base.trim().to_string())
    }
}

/// Теги + длительность + обложка одного файла (lofty).
pub fn probe_track(path: &str) -> Track {
    let mut t = Track {
        path: path.to_string(),
        num: leading_number(&Path::new(path)
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default()),
        has_lrc: has_lrc(path),
        ..Default::default()
    };
    let file_stem = Path::new(path)
        .file_stem()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_default();

    if let Ok(probed) = lofty::probe::Probe::open(path) {
        if let Ok(tagged) = probed.read() {
            t.dur_ms = tagged.properties().duration().as_millis().max(1) as i64;
            let tag = tagged.primary_tag().or_else(|| tagged.first_tag());
            if let Some(tag) = tag {
                t.title = tag.title().map(|s| s.to_string()).unwrap_or_default();
                // Qt6 не имел ключа «Artist»: AlbumArtist / LeadPerformer /
                // ContributingArtist. В lofty: artist() и album_artist().
                t.artist = tag
                    .artist()
                    .map(|s| s.to_string())
                    .filter(|s| !s.trim().is_empty())
                    .or_else(|| {
                        tag.get_string(&lofty::tag::ItemKey::AlbumArtist)
                            .map(|s| s.to_string())
                    })
                    .unwrap_or_default();
                t.album = tag.album().map(|s| s.to_string()).unwrap_or_default();
                if let Some(num) = tag.track() {
                    t.num = num as i64;
                }
                let pic = tag
                    .pictures()
                    .iter()
                    .find(|p| p.pic_type() == lofty::picture::PictureType::CoverFront)
                    .or_else(|| tag.pictures().first());
                if let Some(p) = pic {
                    t.cover = p.data().to_vec();
                }
            }
        }
    }

    if t.title.is_empty() {
        let (artist, title) = split_filename(&file_stem);
        if t.artist.is_empty() {
            t.artist = artist;
        }
        t.title = title;
    }
    t
}

pub enum ScanMsg {
    Progress { done: usize, total: usize },
    Done,
}

/// Сканирование папок (рекурсивно) в фоновом потоке.
/// Порядок: в каждой папке файлы сортируются по (номер, имя) — как «по номеру
/// в папке». Результат отправляется чанками через `tx_tracks`.
pub fn scan_folders(
    folders: Vec<String>,
    tx_tracks: Sender<Vec<Track>>,
    tx_progress: Sender<ScanMsg>,
    cancel: std::sync::Arc<AtomicBool>,
) {
    std::thread::Builder::new()
        .name("hox-scan".into())
        .spawn(move || {
            // путь -> номер в папке (для файлов без ведущего числа)
            let mut files: Vec<(PathBuf, i64)> = Vec::new();
            for f in &folders {
                if cancel.load(Ordering::Relaxed) {
                    return;
                }
                let mut per_dir: Vec<PathBuf> = Vec::new();
                collect_recursive(Path::new(f), &mut per_dir, 0, &cancel);
                per_dir.sort_by_key(|p| {
                    let name = p
                        .file_name()
                        .map(|n| n.to_string_lossy().to_string())
                        .unwrap_or_default();
                    (leading_number(&name), name.to_lowercase())
                });
                for (idx, p) in per_dir.iter().enumerate() {
                    let ln = leading_number(&p
                        .file_name()
                        .map(|n| n.to_string_lossy().to_string())
                        .unwrap_or_default());
                    files.push((p.clone(), if ln >= 0 { ln } else { idx as i64 }));
                }
            }

            let total = files.len();
            let mut done = 0usize;
            let mut chunk: Vec<Track> = Vec::new();
            for (path, num) in files {
                if cancel.load(Ordering::Relaxed) {
                    return;
                }
                let p = path.to_string_lossy().to_string();
                let mut t = probe_track(&p);
                if t.num < 0 {
                    t.num = num;
                }
                chunk.push(t);
                done += 1;
                if chunk.len() >= 20 {
                    let _ = tx_tracks.send(std::mem::take(&mut chunk));
                }
                if done % 5 == 0 {
                    let _ = tx_progress.send(ScanMsg::Progress { done, total });
                }
            }
            if !chunk.is_empty() {
                let _ = tx_tracks.send(chunk);
            }
            let _ = tx_progress.send(ScanMsg::Done);
        })
        .ok();
}

fn collect_recursive(dir: &Path, out: &mut Vec<PathBuf>, depth: usize, cancel: &AtomicBool) {
    if depth > 8 || cancel.load(Ordering::Relaxed) {
        return;
    }
    if let Ok(rd) = std::fs::read_dir(dir) {
        for e in rd.flatten() {
            let p = e.path();
            if p.is_dir() {
                collect_recursive(&p, out, depth + 1, cancel);
            } else if p.is_file() && is_audio(&p) {
                out.push(p);
            }
        }
    }
}

/// Обложка-заглушка не нужна: рисуем вектором. Здесь только декодирование
/// байтов обложки в RGBA (для текстуры egui).
pub fn decode_cover(bytes: &[u8], side: u32) -> Option<(Vec<u8>, u32, u32)> {
    let img = image::load_from_memory(bytes).ok()?;
    let img = if img.width() > side * 2 || img.height() > side * 2 {
        img.resize(side * 2, side * 2, image::imageops::FilterType::Triangle)
    } else {
        img
    };
    let (w, h) = (img.width(), img.height());
    Some((img.to_rgba8().into_raw(), w, h))
}

pub fn _dur_unused(_d: Duration) {}
