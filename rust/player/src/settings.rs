//! Настройки плеера: реестр HKCU\Software\Hox\HoxPlayer — формат QSettings
//! (NativeFormat), которым писала C++-версия. Читаем толерантно (DWORD /
//! QWORD / SZ / MULTI_SZ), пишем в том же духе, чтобы настройки Volume,
//! темы, папок и последнего трека пережили переезд с C++ на Rust.

use winreg::enums::{HKEY_CURRENT_USER, RegType, KEY_READ, KEY_SET_VALUE};
use winreg::{RegKey, RegValue};

pub const SETTINGS_KEY: &str = r"Software\Hox\HoxPlayer";

/// Значение реестра, приведённое к одному виду.
#[derive(Debug, Clone)]
pub enum SVal {
    Str(String),
    List(Vec<String>),
    Int(i64),
}

pub struct Settings {
    key: RegKey,
}

impl Settings {
    pub fn open() -> Settings {
        let hkcu = RegKey::predef(HKEY_CURRENT_USER);
        let key = hkcu
            .create_subkey(SETTINGS_KEY)
            .map(|(k, _)| k)
            .unwrap_or_else(|_| {
                hkcu.open_subkey_with_flags(SETTINGS_KEY, KEY_SET_VALUE)
                    .unwrap_or_else(|_| RegKey::predef(HKEY_CURRENT_USER))
            });
        Settings { key }
    }

    fn raw(&self, name: &str) -> Option<SVal> {
        let v: RegValue = self.key.get_raw_value(name).ok()?;
        Some(match v.vtype {
            RegType::REG_SZ | RegType::REG_EXPAND_SZ => {
                let u16s: Vec<u16> = v
                    .bytes
                    .chunks_exact(2)
                    .map(|c| u16::from_le_bytes([c[0], c[1]]))
                    .collect();
                SVal::Str(String::from_utf16_lossy(&u16s).trim_end_matches('\0').to_string())
            }
            RegType::REG_MULTI_SZ => {
                let u16s: Vec<u16> = v
                    .bytes
                    .chunks_exact(2)
                    .map(|c| u16::from_le_bytes([c[0], c[1]]))
                    .collect();
                SVal::List(
                    String::from_utf16_lossy(&u16s)
                        .split('\0')
                        .filter(|s| !s.is_empty())
                        .map(|s| s.to_string())
                        .collect(),
                )
            }
            RegType::REG_DWORD => {
                let mut a = [0u8; 4];
                a.copy_from_slice(&v.bytes[..v.bytes.len().min(4)]);
                SVal::Int(u32::from_le_bytes(a) as i64)
            }
            RegType::REG_QWORD => {
                let mut a = [0u8; 8];
                a.copy_from_slice(&v.bytes[..v.bytes.len().min(8)]);
                SVal::Int(u64::from_le_bytes(a) as i64)
            }
            _ => return None,
        })
    }

    pub fn get_str(&self, name: &str, def: &str) -> String {
        match self.raw(name) {
            Some(SVal::Str(s)) => s,
            Some(SVal::Int(i)) => i.to_string(),
            _ => def.to_string(),
        }
    }

    pub fn get_i64(&self, name: &str, def: i64) -> i64 {
        match self.raw(name) {
            Some(SVal::Int(i)) => i,
            Some(SVal::Str(s)) => s.trim().parse().unwrap_or(def),
            _ => def,
        }
    }

    pub fn get_bool(&self, name: &str, def: bool) -> bool {
        match self.raw(name) {
            Some(SVal::Int(i)) => i != 0,
            Some(SVal::Str(s)) => {
                let s = s.trim().to_ascii_lowercase();
                if s.is_empty() {
                    def
                } else {
                    s == "true" || s == "1" || s == "yes" || s == "on"
                }
            }
            _ => def,
        }
    }

    pub fn get_list(&self, name: &str) -> Vec<String> {
        match self.raw(name) {
            Some(SVal::List(l)) => l,
            Some(SVal::Str(s)) => s
                .split('\n')
                .filter(|s| !s.trim().is_empty())
                .map(|s| s.to_string())
                .collect(),
            _ => Vec::new(),
        }
    }

    pub fn set_str(&self, name: &str, val: &str) {
        let _ = self.key.set_value(name, &val);
    }

    pub fn set_i64(&self, name: &str, val: i64) {
        // Как QSettings: 32-бит -> REG_DWORD, больше -> REG_SZ-десятичное
        if val >= 0 && val <= u32::MAX as i64 {
            let _ = self.key.set_value(name, &(val as u32));
        } else {
            let _ = self.key.set_value(name, &val.to_string());
        }
    }

    pub fn set_bool(&self, name: &str, val: bool) {
        // QSettings пишет bool строкой "true"/"false"
        let _ = self.key.set_value(name, &if val { "true" } else { "false" });
    }

    pub fn set_list(&self, name: &str, val: &[String]) {
        let _ = self.key.set_value(name, &val.to_vec());
    }

    pub fn remove(&self, name: &str) {
        let _ = self.key.delete_value(name);
    }
}

pub fn read_only() -> Option<RegKey> {
    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    hkcu.open_subkey_with_flags(SETTINGS_KEY, KEY_READ).ok()
}
