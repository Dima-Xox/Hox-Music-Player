//! Встраивание иконки hox.ico в exe (Windows). Ошибки не роняют сборку:
//! иконка — косметика, окно задаст её в рантайме из встроенных байтов.

use std::env;
use std::path::PathBuf;

fn main() {
    println!("cargo:rerun-if-changed=assets/hox.ico");
    if env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") {
        return;
    }
    let ico = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap()).join("assets/hox.ico");
    if !ico.exists() {
        println!("cargo:warning=hox.ico не найден — иконка не встроена");
        return;
    }
    match winresource::WindowsResource::new().set_icon(ico.to_str().unwrap()).compile() {
        Ok(()) => {}
        Err(e) => println!("cargo:warning=иконка не встроена (winresource): {e}"),
    }
}
