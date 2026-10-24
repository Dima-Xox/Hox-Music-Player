//! Встраивание иконки в HoxLauncher.exe (Windows, best-effort).

use std::env;
use std::path::PathBuf;

fn main() {
    println!("cargo:rerun-if-changed=assets/hox.ico");
    if env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") {
        return;
    }
    let ico = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap()).join("assets/hox.ico");
    if !ico.exists() {
        return;
    }
    match winresource::WindowsResource::new().set_icon(ico.to_str().unwrap()).compile() {
        Ok(()) => {}
        Err(e) => println!("cargo:warning=иконка не встроена (winresource): {e}"),
    }
}
