//! Караоке: разбор .lrc (обычные и enhanced A2 строки) + состояние подсветки.
//! Порт C++ parseLrc + KaraokeWidget (логика без отрисовки).

use encoding_rs::WINDOWS_1251;

#[derive(Debug, Clone)]
pub struct LrcLine {
    pub t: i64,                          // мс от начала трека
    pub text: String,                    // текст строки без тегов
    pub words: Vec<(i64, String)>,       // (время мс, слово) — enhanced A2
}

const TAG_RE: &str = r"\[(\d+):(\d+(?:\.\d+)?)\]";
const WORD_RE: &str = r"<(\d+):(\d+(?:\.\d+)?)>";

/// Чтение файла с определением кодировки: UTF-8/UTF-16 с BOM, иначе UTF-8,
/// при замене символов — CP1251 (порт C++ parseLrc).
fn read_text(path: &std::path::Path) -> String {
    let raw = match std::fs::read(path) {
        Ok(r) => r,
        Err(_) => return String::new(),
    };
    if raw.len() >= 2 && raw[0] == 0xFF && raw[1] == 0xFE {
        let u16s: Vec<u16> = raw[2..].chunks_exact(2).map(|c| u16::from_le_bytes([c[0], c[1]])).collect();
        return String::from_utf16_lossy(&u16s);
    }
    if raw.len() >= 2 && raw[0] == 0xFE && raw[1] == 0xFF {
        let u16s: Vec<u16> = raw[2..].chunks_exact(2).map(|c| u16::from_be_bytes([c[0], c[1]])).collect();
        return String::from_utf16_lossy(&u16s);
    }
    let (content, _, had_errors) = WINDOWS_1251.decode(&raw);
    if had_errors && std::str::from_utf8(&raw).is_ok() {
        // реально валидный UTF-8 (CP1251 дал ошибки — например, кириллица в UTF-8)
        return String::from_utf8_lossy(&raw).to_string();
    }
    // Порт C++: сначала UTF-8; если появились U+FFFD — CP1251
    match std::str::from_utf8(&raw) {
        Ok(s) if !s.contains('\u{FFFD}') => s.to_string(),
        _ => content.to_string(),
    }
}

/// Разбор .lrc. Возвращает строки, отсортированные по времени.
pub fn parse_lrc(path: &std::path::Path) -> Vec<LrcLine> {
    let content = read_text(path);
    let mut out: Vec<LrcLine> = Vec::new();

    let tag_re = regex::Regex::new(TAG_RE).unwrap();
    let word_re = regex::Regex::new(WORD_RE).unwrap();

    for raw_line in content.split('\n') {
        let line = raw_line.trim();
        if line.is_empty() {
            continue;
        }
        // временные теги в начале строки
        let mut times: Vec<i64> = Vec::new();
        let mut pos = 0usize;
        let bytes = line;
        loop {
            match tag_re.captures_at(bytes, pos) {
                Some(c) if c.get(0).unwrap().start() == pos => {
                    let m = c.get(1).unwrap().as_str().parse::<i64>().unwrap_or(0);
                    let s = c.get(2).unwrap().as_str().parse::<f64>().unwrap_or(0.0);
                    times.push(m * 60_000 + (s * 1000.0) as i64);
                    pos = c.get(0).unwrap().end();
                }
                _ => break,
            }
        }
        if times.is_empty() {
            continue; // метаданные [ar:] и прочее — пропускаем
        }
        let text_part = line[pos..].trim();

        // пословные метки <mm:ss.xx>
        let mut words: Vec<(i64, String)> = Vec::new();
        let mut segs: Vec<(usize, usize, i64)> = Vec::new(); // (start, end, t)
        for c in word_re.captures_iter(text_part) {
            let g0 = c.get(0).unwrap();
            let m = c.get(1).unwrap().as_str().parse::<i64>().unwrap_or(0);
            let s = c.get(2).unwrap().as_str().parse::<f64>().unwrap_or(0.0);
            segs.push((g0.start(), g0.end(), m * 60_000 + (s * 1000.0) as i64));
        }
        if !segs.is_empty() {
            for i in 0..segs.len() {
                let wstart = segs[i].1;
                let wend = if i + 1 < segs.len() { segs[i + 1].0 } else { text_part.len() };
                if wend >= wstart {
                    words.push((segs[i].2, text_part[wstart..wend].to_string()));
                }
            }
        }
        let plain = word_re.replace_all(text_part, "").trim().to_string();

        for t in times {
            out.push(LrcLine { t, text: plain.clone(), words: words.clone() });
        }
    }
    out.sort_by_key(|l| l.t);
    out
}

/// Состояние караоке (логическая часть KaraokeWidget).
#[derive(Debug, Clone, Default)]
pub struct Karaoke {
    pub lines: Vec<LrcLine>,
    pub pos_ms: i64,
    pub offset_ms: i64,
    pub current: isize, // -1 = нет активной
    pub fill: f64,
}

impl Karaoke {
    pub fn set_lyrics(&mut self, lines: Vec<LrcLine>) {
        self.lines = lines;
        self.current = -1;
        self.fill = 0.0;
        self.pos_ms = 0;
    }

    pub fn clear(&mut self) {
        self.lines.clear();
        self.current = -1;
        self.fill = 0.0;
    }

    pub fn is_empty(&self) -> bool {
        self.lines.is_empty()
    }

    pub fn set_position(&mut self, pos_ms: i64) {
        self.pos_ms = pos_ms;
        self.update_current();
    }

    pub fn set_offset(&mut self, off: i64) {
        self.offset_ms = off;
        self.update_current();
    }

    /// Порт KaraokeWidget::updateCurrent: активная строка + заливка.
    pub fn update_current(&mut self) {
        let t = self.pos_ms + self.offset_ms;
        let mut cur: isize = -1;
        for (i, l) in self.lines.iter().enumerate() {
            if l.t <= t {
                cur = i as isize;
            } else {
                break;
            }
        }
        let mut fill = 0.0;
        if cur >= 0 {
            let i = cur as usize;
            let l = &self.lines[i];
            if !l.words.is_empty() {
                let mut widx: isize = -1;
                for (j, (wt, _)) in l.words.iter().enumerate() {
                    if *wt <= t {
                        widx = j as isize;
                    } else {
                        break;
                    }
                }
                if widx < 0 {
                    fill = 0.0;
                } else if (widx as usize) + 1 < l.words.len() {
                    let t0 = l.words[widx as usize].0;
                    let t1 = l.words[widx as usize + 1].0;
                    let frac = if t1 > t0 { (t - t0) as f64 / (t1 - t0) as f64 } else { 1.0 };
                    fill = (widx as f64 + frac) / l.words.len() as f64;
                } else {
                    let next = if i + 1 < self.lines.len() { self.lines[i + 1].t } else { l.t + 6000 };
                    let t0 = l.words[widx as usize].0;
                    let frac = if next > t0 { (t - t0) as f64 / (next - t0) as f64 } else { 1.0 };
                    fill = (widx as f64 + frac) / l.words.len() as f64;
                }
            } else {
                let next = if i + 1 < self.lines.len() { self.lines[i + 1].t } else { l.t + 6000 };
                fill = if next > l.t { (t - l.t) as f64 / (next - l.t) as f64 } else { 0.0 };
            }
        }
        self.fill = fill.clamp(0.0, 1.0);
        self.current = cur;
    }

    /// Текст активной строки (для миниплеера).
    pub fn current_line_text(&self) -> String {
        if self.current < 0 || self.current as usize >= self.lines.len() {
            return String::new();
        }
        self.lines[self.current as usize].text.clone()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_basic() {
        let dir = std::env::temp_dir().join("hox_lrc_test");
        std::fs::create_dir_all(&dir).unwrap();
        let p = dir.join("t.lrc");
        std::fs::write(
            &p,
            "[ti:x]\n[00:01.00]Первая строка\n[00:05.5]Вторая <00:05.50>стр<00:06.00>ока\n",
        )
        .unwrap();
        let lines = parse_lrc(&p);
        assert_eq!(lines.len(), 2);
        assert_eq!(lines[0].t, 1000);
        assert_eq!(lines[0].text, "Первая строка");
        assert_eq!(lines[1].t, 5500);
        assert_eq!(lines[1].text, "Вторая строка");
        assert_eq!(lines[1].words.len(), 2);
    }

    #[test]
    fn fill_moves() {
        let mut k = Karaoke::default();
        k.set_lyrics(vec![
            LrcLine { t: 0, text: "a".into(), words: vec![] },
            LrcLine { t: 1000, text: "b".into(), words: vec![] },
        ]);
        k.set_position(500);
        assert_eq!(k.current, 0);
        assert!(k.fill > 0.4 && k.fill < 0.6);
        k.set_position(1500);
        assert_eq!(k.current, 1);
    }
}
