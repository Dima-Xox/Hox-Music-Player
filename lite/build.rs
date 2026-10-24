//! Иконка и метаданные Windows-exe.
//! Сам код плеера (src/main.rs) не затрагивается — только ресурсы бинарника.

fn main() {
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        let mut res = winresource::WindowsResource::new();
        res.set_icon("assets/hox.ico");
        if let Err(e) = res.compile() {
            // Иконка — украшение: не роняем сборку, если тулчейн ресурсов недоступен.
            eprintln!("cargo:warning=winresource: {e}");
        }
        // GUI-подсистема, чтобы у плеера не открывалось консольное окно.
        // (в src/main.rs код не трогаем — подсистему задаём флагом линкера)
        match std::env::var("CARGO_CFG_TARGET_ENV").as_deref() {
            Ok("gnu") => println!("cargo:rustc-link-arg-bins=-mwindows"),
            Ok("msvc") => {
                println!("cargo:rustc-link-arg-bins=/SUBSYSTEM:WINDOWS");
                // main() вместо WinMain: без этого libcmt ищет WinMain (LNK2019)
                println!("cargo:rustc-link-arg-bins=/ENTRY:mainCRTStartup");
            }
            _ => {}
        }
    }
}
