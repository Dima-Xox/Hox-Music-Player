//! Окно лаунчера на egui + маршрутизация событий трея/движка.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{Receiver, Sender};
use std::sync::{Arc, Mutex};

use crate::platform::{self, Balloon, TrayCmd};
use crate::updater::{self, EngineCmd, EngineMsg};

pub struct LauncherApp {
    pub ctx: egui::Context,
    pub visible: bool,
    pub status: String,
    pub log: Vec<String>,
    pub progress: Option<f32>,
    pub busy: bool,
    pub check_busy: bool,
    pub remote_tag: String,
    pub remote_notes: String,
    pub player_version: String,
    pub player_exists: bool,
    pub show_whatsnew: bool,
    pub notes_label: Option<String>,
    pub update_btn: Option<String>,
    pub cmd_tx: Sender<EngineCmd>,
    pub msg_rx: Receiver<EngineMsg>,
    pub tray_rx: Receiver<TrayCmd>,
    pub tray_shared: Arc<Mutex<platform::TrayShared>>,
    pub balloon_tx: Sender<Balloon>,
    pub tray_hint_shown: bool,
    pub quit: Arc<AtomicBool>,
}

impl eframe::App for LauncherApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        // ---------- события движка ----------
        while let Ok(m) = self.msg_rx.try_recv() {
            match m {
                EngineMsg::Log(s) => self.push_log(s),
                EngineMsg::CheckDone { tag, notes, player_ver, player_exists } => {
                    self.remote_tag = tag;
                    self.remote_notes = notes;
                    self.player_version = player_ver;
                    self.player_exists = player_exists;
                    self.refresh_status_and_buttons();
                }
                EngineMsg::DownloadProgress(p) => self.progress = Some(p),
                EngineMsg::Balloon(b) => {
                    let _ = self.balloon_tx.send(b);
                }
                EngineMsg::NotesAvailable(avail, label) => {
                    self.notes_label = if avail { Some(label) } else { None };
                    if let Ok(mut sh) = self.tray_shared.lock() {
                        sh.has_notes = avail;
                        sh.notes_label = self.notes_label.clone().unwrap_or_else(|| "Что нового".into());
                    }
                }
                EngineMsg::Busy(b) => {
                    self.busy = b;
                    if !b {
                        self.progress = None;
                    }
                }
                EngineMsg::UpdateDone => {
                    // установщик обычно сам перезапускает лаунчер; здесь — fallback
                    self.restart_after_update();
                    return;
                }
            }
        }

        // ---------- команды трея ----------
        while let Ok(c) = self.tray_rx.try_recv() {
            match c {
                TrayCmd::ShowWindow => {
                    self.visible = true;
                    self.ctx.send_viewport_cmd(egui::ViewportCommand::Visible(true));
                    self.ctx.send_viewport_cmd(egui::ViewportCommand::Focus);
                }
                TrayCmd::Play => {
                    let _ = self.cmd_tx.send(EngineCmd::Play);
                    self.visible = false;
                }
                TrayCmd::Check => {
                    let _ = self.cmd_tx.send(EngineCmd::Check);
                }
                TrayCmd::WhatsNew => {
                    if !self.remote_notes.is_empty() {
                        self.show_whatsnew = true;
                        self.visible = true;
                    }
                }
                TrayCmd::SetAuto(on) => {
                    let _ = self.cmd_tx.send(EngineCmd::SetAuto(on));
                    if let Ok(mut sh) = self.tray_shared.lock() {
                        sh.auto_update = on;
                    }
                    let label = if on {
                        "Авто-обновление включено: новые релизы ставятся сами"
                    } else {
                        "Авто-обновление выключено: обновление по кнопке"
                    };
                    self.push_log(label.to_string());
                }
                TrayCmd::SetDelay(h) => {
                    let _ = self.cmd_tx.send(EngineCmd::SetDelay(h));
                    if let Ok(mut sh) = self.tray_shared.lock() {
                        sh.delay_hours = h;
                    }
                }
                TrayCmd::Quit => {
                    self.quit.store(true, Ordering::Relaxed);
                    let _ = self.cmd_tx.send(EngineCmd::Quit);
                    self.ctx.send_viewport_cmd(egui::ViewportCommand::Close);
                }
            }
        }

        // крестик — спрятать в трей
        if ctx.input(|i| i.viewport().close_requested()) {
            self.ctx.send_viewport_cmd(egui::ViewportCommand::CancelClose);
            if self.tray_alive() {
                self.visible = false;
                self.ctx.send_viewport_cmd(egui::ViewportCommand::Visible(false));
                if !self.tray_hint_shown {
                    let _ = self.balloon_tx.send(Balloon {
                        title: "Hox".into(),
                        body: "Лаунчер свернут в трей — музыка под рукой".into(),
                    });
                    self.tray_hint_shown = true;
                }
            }
        }

        if self.visible {
            self.draw_window(ctx);
        }
        if self.show_whatsnew {
            self.draw_whatsnew(ctx);
        }

        // трей/движок живы — окно может быть скрыто: перерисовываемся по событиям
        ctx.request_repaint_after(std::time::Duration::from_millis(150));
    }

    fn on_exit(&mut self, _gl: Option<&eframe::glow::Context>) {
        self.quit.store(true, Ordering::Relaxed);
        let _ = self.cmd_tx.send(EngineCmd::Quit);
    }
}

impl LauncherApp {
    fn tray_alive(&self) -> bool {
        // трей есть всегда (кроме падения его потока); считаем живым
        true
    }

    fn push_log(&mut self, s: String) {
        self.log.push(s);
        if self.log.len() > 500 {
            let drop = self.log.len() - 500;
            self.log.drain(0..drop);
        }
    }

    fn refresh_status_and_buttons(&mut self) {
        let tag = self.remote_tag.clone();
        let rel_ver = hox_common::rel_ver_of(&tag).to_string();
        let base = if self.player_exists {
            if self.player_version.is_empty() {
                hox_common::APP_VERSION.to_string()
            } else {
                self.player_version.clone()
            }
        } else {
            "0.0.0".into()
        };
        let newer = hox_common::parse_version(&tag) > hox_common::parse_version(&base);

        if self.player_exists {
            let v = if self.player_version.is_empty() {
                String::new()
            } else {
                format!(" — v{}", self.player_version)
            };
            self.status = format!(
                "Плеер установлен{v}. Обновления лаунчер проверяет сам и ставит автоматически."
            );
            if newer {
                self.update_btn = Some(format!("Обновить плеер до v{rel_ver}"));
            } else {
                self.update_btn = None;
            }
        } else {
            self.status = "Плеер не найден рядом с лаунчером (HoxPlayer.exe).\nНажмите «Скачать плеер с GitHub» — лаунчер скачает последнюю версию с GitHub Releases и тихо установит её.".into();
            self.update_btn = Some(if newer {
                format!("Скачать плеер v{rel_ver} с GitHub")
            } else {
                "Скачать плеер с GitHub".to_string()
            });
        }
    }

    fn restart_after_update(&mut self) {
        // перезапуск себя с --after-update (ветка, если taskkill не нашёл нас)
        let exe = std::env::current_exe().unwrap_or_default();
        #[cfg(windows)]
        unsafe {
            let _ = platform::spawn_detached_after_update(&exe.to_string_lossy());
        }
        self.quit.store(true, Ordering::Relaxed);
        let _ = self.cmd_tx.send(EngineCmd::Quit);
        self.ctx.send_viewport_cmd(egui::ViewportCommand::Close);
    }

    fn draw_window(&mut self, ctx: &egui::Context) {
        egui::CentralPanel::default()
            .frame(egui::Frame::NONE.inner_margin(16.0))
            .show(ctx, |ui| {
                // лого
                ui.horizontal(|ui| {
                    ui.label(
                        egui::RichText::new("Hox")
                            .size(46.0)
                            .strong(),
                    );
                    ui.add_space(6.0);
                    ui.label(
                        egui::RichText::new("MUSIC PLAYER — лаунчер\nРанняя бета")
                            .size(12.0)
                            .weak(),
                    );
                });
                ui.add_space(8.0);
                ui.label(egui::RichText::new(&self.status).size(12.5));

                ui.add_space(6.0);
                ui.horizontal(|ui| {
                    if ui
                        .add_enabled(
                            self.player_exists,
                            egui::Button::new("Запустить плеер").min_size(egui::vec2(130.0, 30.0)),
                        )
                        .clicked()
                    {
                        let _ = self.cmd_tx.send(EngineCmd::Play);
                        self.visible = false;
                        ctx.send_viewport_cmd(egui::ViewportCommand::Visible(false));
                    }
                    if ui
                        .add_enabled(
                            !self.check_busy,
                            egui::Button::new("Проверить обновления").min_size(egui::vec2(160.0, 30.0)),
                        )
                        .clicked()
                    {
                        let _ = self.cmd_tx.send(EngineCmd::Check);
                    }
                    if let Some(label) = &self.notes_label {
                        if ui.button(label).clicked() {
                            self.show_whatsnew = true;
                        }
                    }
                });

                if let Some(label) = &self.update_btn {
                    let label = label.clone();
                    ui.add_space(6.0);
                    if ui
                        .add_enabled(
                            !self.busy,
                            egui::Button::new(egui::RichText::new(label).strong())
                                .min_size(egui::vec2(220.0, 32.0)),
                        )
                        .clicked()
                    {
                        let _ = self.cmd_tx.send(EngineCmd::Download);
                    }
                }

                if let Some(p) = self.progress {
                    ui.add_space(6.0);
                    ui.add(egui::ProgressBar::new(p / 100.0).show_percentage());
                }

                ui.add_space(10.0);
                egui::ScrollArea::vertical().stick_to_bottom(true).show(ui, |ui| {
                    for l in &self.log {
                        ui.label(egui::RichText::new(l).size(11.5).weak());
                    }
                });
            });
    }

    fn draw_whatsnew(&mut self, ctx: &egui::Context) {
        let mut open = self.show_whatsnew;
        egui::Window::new(format!(
            "Hox v{} — что нового",
            hox_common::rel_ver_of(&self.remote_tag)
        ))
        .open(&mut open)
        .default_width(520.0)
        .show(ctx, |ui| {
            let text = crate::updater::notes_to_plain(&self.remote_notes);
            egui::ScrollArea::vertical().max_height(320.0).show(ui, |ui| {
                ui.label(egui::RichText::new(&text).size(12.5));
            });
            ui.add_space(8.0);
            ui.horizontal(|ui| {
                if ui.button("Открыть на GitHub").clicked() {
                    let url = format!(
                        "https://github.com/{}/releases/tag/{}",
                        hox_common::GITHUB_REPO,
                        self.remote_tag
                    );
                    unsafe { platform::open_url(&url) };
                }
                if ui.button("Закрыть").clicked() {
                    self.show_whatsnew = false;
                }
            });
        });
        self.show_whatsnew = open;
    }
}
