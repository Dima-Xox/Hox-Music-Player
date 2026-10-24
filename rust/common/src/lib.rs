//! Общие константы и утилиты Hox Music Player (общие для плеера и лаунчера).

use std::sync::atomic::AtomicI64;

/// Смещение локального часового пояса (сек). Платформенная часть
/// (launcher/platform.rs) выставляет его при старте; по умолчанию UTC.
pub static LOCAL_UTC_OFFSET_SECS: AtomicI64 = AtomicI64::new(0);

pub const APP_NAME: &str = "Hox Music Player";
pub const APP_VERSION: &str = "0.6.0";
pub const GITHUB_REPO: &str = "Dima-Xox/Hox-Music-Player";
pub const GITHUB_HOME: &str = "https://github.com/Dima-Xox/Hox-Music-Player";

/// «0.5.0» -> 500, «0.4.10» -> 410 — числовое сравнение по старшинству.
/// Порт C++ LauncherWindow::parseVersion.
pub fn parse_version(v: &str) -> i64 {
    let mut res: i64 = 0;
    for part in v.trim().trim_start_matches('v').split('.') {
        let n: i64 = part.trim().parse().unwrap_or(0);
        res = res * 100 + n;
    }
    res
}

/// «v0.6.0» -> «0.6.0»
pub fn rel_ver_of(tag: &str) -> &str {
    tag.strip_prefix('v').unwrap_or(tag)
}

/// «0.6.0» -> «v0.6.0»
pub fn tag_of(rel_ver: &str) -> String {
    format!("v{rel_ver}")
}

/// мс -> «M:SS»
pub fn fmt_time(ms: i64) -> String {
    let ms = if ms < 0 { 0 } else { ms };
    let s = ms / 1000;
    format!("{}:{:02}", s / 60, s % 60)
}

/// «dd.MM HH:mm» из epoch-миллисекунд (локальное время).
pub fn fmt_when(epoch_ms: i64) -> String {
    // Простая конвертация UTC->локальное время: на Windows используем
    // GetLocalTime-подобный подход через std::time + смещение недоступно
    // без лишних зависимостей — считаем по местному часовому поясу среды.
    // Практично: chrono не тянем, используем секунды с эпохи -> дата UTC+local offset.
    let secs = epoch_ms.div_euclid(1000) as i64;
    let (y, mo, d, h, mi) = civil_from_unix(secs + local_utc_offset_secs());
    format!("{d:02}.{mo:02} {h:02}:{mi:02} ({y})")
}

fn local_utc_offset_secs() -> i64 {
    // Смещение локального часового пояса: сравниваем mktime-подобные расчёты
    // нельзя без libc-тз; на практике лаунчер работает на Windows, где
    // смещение получаем через GetTimeZoneInformation (см. launcher/platform.rs).
    // Здесь нейтральный fallback: 0 (UTC), переопределяется платформенной частью.
    crate::LOCAL_UTC_OFFSET_SECS.load(std::sync::atomic::Ordering::Relaxed)
}

/// Гражданская дата из unix-секунд (UTC), без внешних крейтов.
fn civil_from_unix(secs: i64) -> (i64, u32, u32, u32, u32) {
    let days = secs.div_euclid(86400);
    let rem = secs.rem_euclid(86400);
    let h = (rem / 3600) as u32;
    let mi = ((rem % 3600) / 60) as u32;
    let s = (rem % 60) as u32;
    // алгоритм Говарда Хиннанта (civil_from_days)
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let y = if m <= 2 { y + 1 } else { y };
    let _ = s;
    (y, m, d, h, mi)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn versions() {
        assert_eq!(parse_version("0.5.0"), 500);
        assert_eq!(parse_version("0.4.10"), 410);
        assert_eq!(parse_version("v0.6.0"), 600);
        assert!(parse_version("0.6.0") > parse_version("0.5.4"));
    }

    #[test]
    fn time() {
        assert_eq!(fmt_time(0), "0:00");
        assert_eq!(fmt_time(65_000), "1:05");
    }
}
