//! UI плеера на egui: левая панель (обложка/транспорт/визуализатор),
//! таблица библиотеки с сортировкой и контекстным меню, караоке,
//! миниплеер (второй viewport), «Что нового», drag&drop.

use std::sync::{Arc, Mutex};

use egui::{Color32, RichText, Sense, Vec2};

use crate::app::{HoxApp, MiniCmd, MiniState};
use crate::themes::{self, Theme};

fn c(rgb: [u8; 3]) -> Color32 {
    Color32::from_rgb(rgb[0], rgb[1], rgb[2])
}

fn c_alpha(rgb: [u8; 3], a: u8) -> Color32 {
    Color32::from_rgba_unmultiplied(rgb[0], rgb[1], rgb[2], a)
}

pub fn main_panel_fill(idx: usize) -> Color32 {
    c(themes::theme(idx).bg)
}

pub fn run_ui(app: &mut HoxApp, ctx: &egui::Context) {
    let t: &'static Theme = themes::theme(app.theme_idx);

    // ---------------------------------------------------- drag&drop файлов
    let dropped = ctx.input(|i| i.raw.dropped_files.clone());
    if !dropped.is_empty() {
        let mut files: Vec<String> = Vec::new();
        let mut has_folder = false;
        for f in &dropped {
            if let Some(p) = &f.path {
                if p.is_dir() {
                    has_folder = true;
                } else if p.is_file() {
                    files.push(p.to_string_lossy().to_string());
                }
            }
        }
        if has_folder {
            app.add_folder();
        } else if !files.is_empty() {
            app.start_playback_from_paths(&files);
        }
    }

    // ---------------------------------------------------- статус-бар
    egui::TopBottomPanel::bottom("hox-status")
        .frame(egui::Frame::NONE.fill(c(t.panel)).inner_margin(egui::Margin::symmetric(10, 4)))
        .show(ctx, |ui| {
            let scan = if app.scan_status.is_empty() { String::new() } else { format!("   |   {}", app.scan_status) };
            ui.label(RichText::new(format!("{}{}", app.status, scan)).size(11.0).color(c(t.sub)));
        });

    // ---------------------------------------------------- левая панель
    egui::SidePanel::left("hox-left")
        .exact_width(300.0)
        .frame(egui::Frame::NONE.fill(c(t.bg)).inner_margin(egui::Margin::same(14)))
        .show(ctx, |ui| {
            left_panel(app, ui, t);
        });

    // ---------------------------------------------------- правая часть
    egui::CentralPanel::default()
        .frame(egui::Frame::NONE.fill(c(t.bg)))
        .show(ctx, |ui| {
            toolbar(app, ui, t);
            table_panel(app, ui, t);
            if !app.kara.is_empty() {
                karaoke_panel(app, ui, ctx, t);
            }
        });

    // ---------------------------------------------------- «Что нового»
    if app.show_whatsnew {
        let mut open = app.show_whatsnew;
        egui::Window::new("Что нового — список изменений по версиям")
            .open(&mut open)
            .default_width(560.0)
            .default_height(520.0)
            .show(ctx, |ui| {
                egui::ScrollArea::vertical().show(ui, |ui| {
                    for e in crate::whatsnew::ENTRIES {
                        ui.add_space(4.0);
                        ui.heading(RichText::new(format!("Версия {}", e.version)).size(15.0));
                        for it in e.items {
                            ui.label(RichText::new(format!("• {it}")).size(12.5));
                        }
                    }
                });
            });
        app.show_whatsnew = open;
    }

    // ---------------------------------------------------- удаление папки
    if let Some(folders) = app.open_folder_menu.clone() {
        let mut open = true;
        egui::Window::new("Удалить папку из библиотеки")
            .open(&mut open)
            .default_width(460.0)
            .show(ctx, |ui| {
                if folders.is_empty() {
                    ui.label("Библиотека пуста — сначала добавьте папку.");
                }
                for (i, f) in folders.iter().enumerate() {
                    ui.horizontal(|ui| {
                        ui.label(RichText::new(f).size(11.5));
                        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                            if ui.button("Удалить").clicked() {
                                app.remove_folder(i);
                                app.open_folder_menu = None;
                            }
                        });
                    });
                }
            });
        if !open {
            app.open_folder_menu = None;
        }
    }
}

// ==================================================================== левая панель
fn left_panel(app: &mut HoxApp, ui: &mut egui::Ui, t: &'static Theme) {
    ui.vertical_centered(|ui| {
        ui.add_space(2.0);
        ui.heading(
            RichText::new("Hox")
                .size(30.0)
                .strong()
                .color(Color32::WHITE),
        );
        ui.label(
            RichText::new(format!("MUSIC PLAYER  v{}", hox_common::APP_VERSION))
                .size(9.0)
                .color(c_alpha([160, 175, 205], 255)),
        );
        ui.add_space(10.0);

        // обложка 200x200
        let cover_side = 200.0f32;
        let (cover_rect, _) = ui.allocate_exact_size(Vec2::splat(cover_side), Sense::hover());
        let tex = (app.current >= 0)
            .then(|| {
                let ctx = app.ctx.clone();
                app.cover_texture(&ctx, app.current as usize, 256)
            })
            .flatten();
        match tex {
            Some(tex) => {
                let painter = ui.painter_at(cover_rect);
                // квадрат с заполнением (KeepAspectRatioByExpanding)
                let (w, h) = (tex.size()[0] as f32, tex.size()[1] as f32);
                let scale = (cover_side / w).max(cover_side / h);
                let dw = w * scale;
                let dh = h * scale;
                let dst = egui::Rect::from_center_size(
                    cover_rect.center(),
                    Vec2::new(dw.min(cover_side * 4.0), dh.min(cover_side * 4.0)),
                );
                painter.image(tex.id(), dst, egui::Rect::from_min_max(egui::pos2(0.0, 0.0), egui::pos2(1.0, 1.0)), Color32::WHITE);
                // обрезка до квадрата делается клипом
                let _ = dst;
            }
            None => {
                ui.painter().rect_filled(cover_rect, 10.0, c_alpha([255, 255, 255], 16));
                draw_note(ui.painter(), cover_rect.center(), c_alpha([255, 255, 255], 90), 30.0);
            }
        }

        ui.add_space(8.0);
        // название / исполнитель
        let (title, artist) = if app.current >= 0 && (app.current as usize) < app.tracks.len() {
            let tr = &app.tracks[app.current as usize];
            (tr.title.clone(), tr.artist.clone())
        } else {
            ("<нет трека>".to_string(), String::new())
        };
        ui.label(RichText::new(title).size(15.0).strong().color(c(t.text)));
        if !artist.is_empty() {
            ui.label(RichText::new(artist).size(12.0).color(c(t.sub)));
        }
    });

    ui.add_space(6.0);

    // ползунок позиции
    let dur = if app.current >= 0 && (app.current as usize) < app.tracks.len() {
        app.tracks[app.current as usize].dur_ms.max(1)
    } else {
        1
    };
    let pos = app.audio.position_ms();
    let mut slider_val = app.seek_drag.unwrap_or(pos as f64) as f64;
    let slider = egui::Slider::new(&mut slider_val, 0.0..=(dur as f64))
        .show_value(false);
    let resp = ui.add(slider);
    if resp.dragged() {
        app.seek_drag = Some(slider_val);
    }
    if resp.drag_stopped() {
        let v = app.seek_drag.unwrap_or(0.0);
        app.audio.seek_ms(v as i64);
        app.kara.set_position(v as i64);
        app.seek_drag = None;
    }

    ui.vertical_centered(|ui| {
        ui.label(
            RichText::new(format!("{} / {}", hox_common::fmt_time(pos), hox_common::fmt_time(dur)))
                .size(11.0)
                .color(c(t.sub)),
        );
    });
    ui.add_space(4.0);

    // транспорт
    let playing = app.audio.is_playing();
    ui.horizontal(|ui| {
        let bw = Vec2::new(52.0, 40.0);
        let pw = Vec2::new(64.0, 44.0);
        let sp = ui.available_width() / 2.0 - (bw.x + pw.x + bw.x / 2.0 + 16.0);
        ui.add_space(sp.max(0.0));
        if vector_button(ui, bw, IconKind::Prev, c(t.panel), c(t.text), c(t.accent)).clicked() {
            app.prev_track();
        }
        if vector_button(
            ui,
            pw,
            if playing { IconKind::Pause } else { IconKind::Play },
            c(t.accent),
            c(t.bg),
            c(t.accent),
        )
        .clicked()
        {
            app.toggle_play_pause();
        }
        if vector_button(ui, bw, IconKind::Next, c(t.panel), c(t.text), c(t.accent)).clicked() {
            app.next_track();
        }
    });
    ui.add_space(8.0);

    // громкость
    ui.horizontal(|ui| {
        draw_speaker(ui.painter(), ui.cursor().left_top() + Vec2::new(6.0, 12.0), c(t.sub));
        ui.add_space(18.0);
        let mut vol = (app.volume * 100.0).round() as i64;
        let r = ui.add(
            egui::Slider::new(&mut vol, 0..=100)
                .show_value(false)
                .text(""),
        );
        if r.changed() {
            app.set_volume(vol as f32 / 100.0);
        }
    });
    ui.add_space(4.0);

    // визуализатор внизу
    ui.with_layout(egui::Layout::bottom_up(egui::Align::Center), |ui| {
        let avail = ui.available_size();
        let viz_h = 64.0f32;
        let (rect, _) = ui.allocate_exact_size(Vec2::new(ui.available_width(), viz_h), Sense::hover());
        let _ = avail;
        draw_viz(ui, rect, t, &app.viz_levels.clone());
    });
}

// ==================================================================== тулбар
fn toolbar(app: &mut HoxApp, ui: &mut egui::Ui, t: &'static Theme) {
    ui.horizontal_wrapped(|ui| {
        ui.add_space(2.0);
        let btn = |ui: &mut egui::Ui, label: &str| -> egui::Response {
            ui.add(
                egui::Button::new(RichText::new(label).size(12.0).color(c(t.text)))
                    .fill(c(t.panel))
                    .stroke(egui::Stroke::new(1.0, c_alpha(t.text, 40)))
                    .min_size(Vec2::new(0.0, 28.0))
                    .corner_radius(8.0),
            )
        };

        if btn(ui, "＋ Папка").clicked() {
            app.add_folder();
        }
        if btn(ui, "－").clicked() {
            // открываем меню папок
            let folders = app.folders.clone();
            app.open_folder_menu = Some(folders);
        }
        if btn(ui, "Скан").clicked() {
            app.rescan_all();
        }
        ui.add_space(6.0);
        if btn(ui, "Всё").clicked() && !app.tracks.is_empty() {
            let n = app.tracks.len();
            app.sort_col = usize::MAX; // сброс сортировки — «как есть»
            let mut order: Vec<usize> = (0..n).collect();
            order.sort_by_key(|&i| (app.tracks[i].num, app.tracks[i].path.to_lowercase()));
            app.tracks = order.into_iter().map(|i| app.tracks[i].clone()).collect();
            app.play_index(0);
        }
        let rep_label = match app.repeat {
            0 => "Повтор: выкл",
            1 => "Повтор: один",
            _ => "Повтор: все",
        };
        if btn(ui, rep_label).clicked() {
            app.cycle_repeat();
        }
        let shuf_label = if app.shuffle { "Случайно: вкл" } else { "Случайно: выкл" };
        if btn(ui, shuf_label).clicked() {
            app.toggle_shuffle();
        }
        ui.add_space(6.0);
        let mini_open = app.mini.lock().map(|m| m.open).unwrap_or(false);
        if btn(ui, if mini_open { "Мини-плеер ✕" } else { "Мини-плеер" }).clicked() {
            app.toggle_mini();
        }
        if btn(ui, "Что нового").clicked() {
            app.show_whatsnew = true;
        }
        ui.add_space(6.0);
        ui.label(RichText::new("Тема:").size(12.0).color(c(t.sub)));
        egui::ComboBox::from_id_salt("theme-cb")
            .selected_text(themes::theme(app.theme_idx).name)
            .width(110.0)
            .show_ui(ui, |ui| {
                for (i, th) in themes::THEMES.iter().enumerate() {
                    if ui.selectable_label(i == app.theme_idx, th.name).clicked() {
                        app.theme_idx = i;
                        crate::settings::Settings::open().set_i64("theme", i as i64);
                    }
                }
            });
    });
    ui.add_space(4.0);
}

// ==================================================================== таблица
fn table_panel(app: &mut HoxApp, ui: &mut egui::Ui, t: &'static Theme) {
    use egui_extras::{Column, TableBuilder};

    let headers: [&str; 5] = ["№", "Название", "Исполнитель", "Альбом", "Длительность"];

    let mut play_row: Option<usize> = None;

    TableBuilder::new(ui)
        .id_salt("hox-table")
        .striped(true)
        .vscroll(true)
        .auto_shrink([false, false])
        .resizable(true)
        .column(Column::exact(48.0).resizable(false))
        .column(Column::remainder())
        .column(Column::initial(190.0))
        .column(Column::initial(190.0))
        .column(Column::exact(90.0).resizable(false))
        .header(26.0, |mut header| {
            for (ci, h) in headers.iter().enumerate() {
                header.col(|ui| {
                    let marker = if app.sort_col == ci {
                        if app.sort_asc { " ▲" } else { " ▼" }
                    } else {
                        ""
                    };
                    if ui
                        .add(
                            egui::Button::new(
                                RichText::new(format!("{h}{marker}")).size(11.5).strong().color(c(t.sub)),
                            )
                            .fill(Color32::TRANSPARENT)
                            .stroke(egui::Stroke::NONE),
                        )
                        .clicked()
                    {
                        if app.sort_col == ci {
                            app.sort_asc = !app.sort_asc;
                        } else {
                            app.sort_col = ci;
                            app.sort_asc = true;
                        }
                        sort_tracks(app);
                    }
                });
            }
        })
        .body(|mut body| {
            let n = app.tracks.len();
            for r in 0..n {
                body.row(30.0, |mut row| {
                    let is_cur = app.current == r as isize;
                    let cells: [String; 5] = [
                        if app.tracks[r].num >= 0 { app.tracks[r].num.to_string() } else { "–".into() },
                        if app.tracks[r].title.is_empty() {
                            crate::app::file_name(&app.tracks[r].path)
                        } else {
                            app.tracks[r].title.clone()
                        },
                        app.tracks[r].artist.clone(),
                        app.tracks[r].album.clone(),
                        hox_common::fmt_time(app.tracks[r].dur_ms),
                    ];
                    for ci in 0..5 {
                        row.col(|ui| {
                            let align_right = ci == 0 || ci == 4;
                            let resp = {
                                let (_, resp) = ui.allocate_exact_size(
                                    Vec2::new(ui.available_width(), ui.available_height()),
                                    Sense::click(),
                                );
                                // фон строки: играющей — акцент, полосной чередование делает striped
                                if is_cur {
                                    ui.painter().rect_filled(resp.rect, 0.0, c_alpha(t.accent, 45));
                                }
                                ui.painter().text(
                                    if align_right { resp.rect.right_center() - Vec2::new(8.0, 0.0) } else { resp.rect.left_center() + Vec2::new(8.0, 0.0) },
                                    if align_right { egui::Align2::RIGHT_CENTER } else { egui::Align2::LEFT_CENTER },
                                    &cells[ci],
                                    egui::FontId::proportional(12.0),
                                    if is_cur { c(t.accent) } else { c(t.text) },
                                );
                                if resp.double_clicked() {
                                    play_row = Some(r);
                                }
                                if resp.secondary_clicked() {
                                    // контекстное меню откроется через resp.context_menu ниже
                                }
                                resp
                            };
                            // контекстное меню строки
                            resp.context_menu(|ui| {
                                if ui.button("Играть").clicked() {
                                    play_row = Some(r);
                                    ui.close_menu();
                                }
                                if ui.button("Воспроизвести альбом").clicked() {
                                    play_album(app, r);
                                    ui.close_menu();
                                }
                                if ui.button("Играть все треки исполнителя").clicked() {
                                    play_artist(app, r);
                                    ui.close_menu();
                                }
                                ui.separator();
                                if ui.button("Показать в папке").clicked() {
                                    if let Some(dir) = std::path::Path::new(&app.tracks[r].path).parent() {
                                        let _ = open_in_explorer(dir);
                                    }
                                    ui.close_menu();
                                }
                                if ui.button("Убрать из библиотеки").clicked() {
                                    if app.current as usize == r {
                                        app.audio.stop();
                                        app.current = -1;
                                    }
                                    app.tracks.remove(r);
                                    app.refresh_status();
                                    ui.close_menu();
                                }
                            });
                        });
                    }
                });
            }
        });

    if let Some(r) = play_row {
        app.play_index(r);
    }
}

fn play_album(app: &mut HoxApp, r: usize) {
    // v0.4.8-фикс: альбом в порядке его треков, затем остальная библиотека
    let alb = app.tracks[r].album.clone();
    let mut order: Vec<usize> = Vec::new();
    for (i, tr) in app.tracks.iter().enumerate() {
        if tr.album == alb {
            order.push(i);
        }
    }
    for (i, tr) in app.tracks.iter().enumerate() {
        if tr.album != alb {
            order.push(i);
        }
    }
    app.tracks = order.into_iter().map(|i| app.tracks[i].clone()).collect();
    app.play_index(0);
}

fn play_artist(app: &mut HoxApp, r: usize) {
    let art = app.tracks[r].artist.clone();
    let mut order: Vec<usize> = Vec::new();
    for (i, tr) in app.tracks.iter().enumerate() {
        if tr.artist == art {
            order.push(i);
        }
    }
    for (i, tr) in app.tracks.iter().enumerate() {
        if tr.artist != art {
            order.push(i);
        }
    }
    app.tracks = order.into_iter().map(|i| app.tracks[i].clone()).collect();
    app.play_index(0);
}

fn sort_tracks(app: &mut HoxApp) {
    if app.sort_col == usize::MAX {
        return;
    }
    let col = app.sort_col;
    let asc = app.sort_asc;
    let cur_path = if app.current >= 0 && (app.current as usize) < app.tracks.len() {
        Some(app.tracks[app.current as usize].path.clone())
    } else {
        None
    };
    app.tracks.sort_by(|a, b| {
        let ord = match col {
            0 => a.num.cmp(&b.num).then_with(|| a.path.cmp(&b.path)),
            1 => a.title.to_lowercase().cmp(&b.title.to_lowercase()),
            2 => a.artist.to_lowercase().cmp(&b.artist.to_lowercase()),
            3 => a.album.to_lowercase().cmp(&b.album.to_lowercase()),
            _ => a.dur_ms.cmp(&b.dur_ms),
        };
        if asc { ord } else { ord.reverse() }
    });
    if let Some(p) = cur_path {
        if let Some(i) = app.tracks.iter().position(|t| t.path == p) {
            app.current = i as isize;
        }
    }
}

#[cfg(windows)]
fn open_in_explorer(dir: &std::path::Path) -> bool {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    std::process::Command::new("explorer")
        .arg(dir)
        .creation_flags(CREATE_NO_WINDOW)
        .spawn()
        .is_ok()
}

#[cfg(not(windows))]
fn open_in_explorer(_dir: &std::path::Path) -> bool {
    false
}

// ==================================================================== караоке
fn karaoke_panel(app: &mut HoxApp, ui: &mut egui::Ui, ctx: &egui::Context, t: &'static Theme) {
    let height = 150.0f32;
    let (rect, resp) = ui.allocate_exact_size(
        Vec2::new(ui.available_width(), height),
        Sense::click_and_drag(),
    );
    ui.painter().rect_filled(rect, 0.0, c(t.bg2));

    let lh = 32.0f32;
    let center_y = rect.center().y;
    let cur = app.kara.current;
    let painter = ui.painter_at(rect);

    let line_range = (cur - 3).max(0)..=(cur + 3).min(app.kara.lines.len() as isize - 1);
    if cur >= 0 {
        for i in line_range.clone() {
            let idx = i as usize;
            let dy = (i - cur) as f32 * lh;
            let y = center_y + dy - lh / 2.0 + 8.0;
            let text_rect = egui::Rect::from_min_size(
                egui::pos2(rect.left() + 24.0, y),
                Vec2::new(rect.width() - 48.0, lh),
            );
            let text = elide(&painter, &app.kara.lines[idx].text, text_rect.width(), 11.0);
            if i == cur {
                // база + заливка слева направо
                painter.text(
                    text_rect.center(),
                    egui::Align2::CENTER_CENTER,
                    &text,
                    egui::FontId::proportional(11.0),
                    c_alpha(t.text, 120),
                );
                let fw = text_rect.width() * app.kara.fill as f32;
                if fw > 1.0 {
                    let clip = egui::Rect::from_min_size(text_rect.left_top(), Vec2::new(fw, lh));
                    let pr = painter.with_clip_rect(clip);
                    pr.text(
                        text_rect.center(),
                        egui::Align2::CENTER_CENTER,
                        &text,
                        egui::FontId::proportional(11.0),
                        c(t.accent),
                    );
                }
            } else {
                let alpha = if i < cur { 70u8 } else { 120u8 };
                painter.text(
                    text_rect.center(),
                    egui::Align2::CENTER_CENTER,
                    &text,
                    egui::FontId::proportional(11.0),
                    c_alpha(t.text, alpha),
                );
            }
        }
    }

    // подсказка сдвига
    if app.kara.offset_ms != 0 {
        painter.text(
            egui::pos2(rect.right() - 10.0, rect.top() + 10.0),
            egui::Align2::RIGHT_TOP,
            format!("сдвиг: {:+} мс", app.kara.offset_ms),
            egui::FontId::proportional(10.0),
            c_alpha(t.sub, 180),
        );
    }

    // клик — перемотка (порт lineAtY: отрицательные — выше активной)
    if resp.clicked() {
        if let Some(pos) = resp.interact_pointer_pos() {
            let rrel = ((pos.y - center_y) / lh).floor() as isize;
            let idx = cur + rrel;
            if idx >= 0 && (idx as usize) < app.kara.lines.len() {
                app.karaoke_seek(app.kara.lines[idx as usize].t);
            }
        }
    }
    // колесо — ручной сдвиг синхронизации ±100 мс
    if resp.hovered() {
        let dy = ctx.input(|i| i.raw_scroll_delta.y);
        if dy.abs() > 0.5 {
            let steps = (dy / 53.0).round() as i64;
            if steps != 0 {
                let off = app.kara.offset_ms + steps * 100;
                app.kara.set_offset(off);
                app.karaoke_offset_changed(off);
            }
        }
    }
}

fn elide(painter: &egui::Painter, text: &str, max_w: f32, size: f32) -> String {
    let galley = painter.layout_no_wrap(
        text.to_string(),
        egui::FontId::proportional(size),
        Color32::WHITE,
    );
    if galley.size().x <= max_w {
        return text.to_string();
    }
    let mut s: String = text.to_string();
    while s.chars().count() > 4 {
        let cut: String = s.chars().take(s.chars().count() - 2).collect();
        s = cut + "…";
        let g = painter.layout_no_wrap(s.clone(), egui::FontId::proportional(size), Color32::WHITE);
        if g.size().x <= max_w {
            return s;
        }
    }
    "…".to_string()
}

// ==================================================================== иконки/визуализатор
#[derive(Clone, Copy)]
enum IconKind {
    Play,
    Pause,
    Prev,
    Next,
}

fn vector_button(
    ui: &mut egui::Ui,
    size: Vec2,
    kind: IconKind,
    fill: Color32,
    glyph: Color32,
    _hover: Color32,
) -> egui::Response {
    let (rect, resp) = ui.allocate_exact_size(size, Sense::click());
    let hovered = resp.hovered();
    let bg = if hovered {
        let f = fill;
        Color32::from_rgba_unmultiplied(f.r() + 18, f.g() + 18, f.b() + 18, 255)
    } else {
        fill
    };
    ui.painter().rect_filled(rect, 8.0, bg);
    let p = ui.painter();
    let ctr = rect.center();
    let s = size.y * 0.34;
    let _ = glyph;
    match kind {
        IconKind::Play => {
            let a = egui::pos2(ctr.x - s * 0.6, ctr.y - s);
            let b = egui::pos2(ctr.x - s * 0.6, ctr.y + s);
            let cc = egui::pos2(ctr.x + s, ctr.y);
            p.add(egui::Shape::convex_polygon(
                vec![a, b, cc],
                glyph_color(fill),
                egui::Stroke::NONE,
            ));
        }
        IconKind::Pause => {
            let w = s * 0.55;
            p.rect_filled(
                egui::Rect::from_min_size(egui::pos2(ctr.x - s * 0.8, ctr.y - s), Vec2::new(w, 2.0 * s)),
                2.0,
                glyph_color(fill),
            );
            p.rect_filled(
                egui::Rect::from_min_size(egui::pos2(ctr.x + s * 0.25, ctr.y - s), Vec2::new(w, 2.0 * s)),
                2.0,
                glyph_color(fill),
            );
        }
        IconKind::Prev => {
            for dx in [0.0, s * 1.1] {
                let cx = ctr.x - s * 0.55 - dx;
                let a = egui::pos2(cx + s * 0.6, ctr.y - s);
                let b = egui::pos2(cx + s * 0.6, ctr.y + s);
                let cc = egui::pos2(cx - s * 0.6, ctr.y);
                p.add(egui::Shape::convex_polygon(
                    vec![a, b, cc],
                    glyph_color(fill),
                    egui::Stroke::NONE,
                ));
            }
        }
        IconKind::Next => {
            for dx in [0.0, s * 1.1] {
                let cx = ctr.x + s * 0.55 + dx;
                let a = egui::pos2(cx - s * 0.6, ctr.y - s);
                let b = egui::pos2(cx - s * 0.6, ctr.y + s);
                let cc = egui::pos2(cx + s * 0.6, ctr.y);
                p.add(egui::Shape::convex_polygon(
                    vec![a, b, cc],
                    glyph_color(fill),
                    egui::Stroke::NONE,
                ));
            }
        }
    }
    resp
}

fn glyph_color(fill: Color32) -> Color32 {
    // glyph задаётся вызывающей стороной через fill для Play; для Prev/Next
    // рисуем цветом текста темы — упрощение: если фон светлый, глиф тёмный
    let lum = 0.299 * fill.r() as f32 + 0.587 * fill.g() as f32 + 0.114 * fill.b() as f32;
    if lum > 140.0 {
        Color32::from_rgb(30, 30, 30)
    } else {
        Color32::from_rgb(240, 240, 245)
    }
}

fn draw_note(p: &egui::Painter, ctr: egui::Pos2, color: Color32, size: f32) {
    // векторная нота: штиль + флажок + головка
    let stem_x = ctr.x + size * 0.12;
    let top = egui::pos2(stem_x, ctr.y - size * 0.5);
    let bottom = egui::pos2(stem_x, ctr.y + size * 0.35);
    p.line_segment([top, bottom], egui::Stroke::new(size * 0.07, color));
    p.circle_filled(egui::pos2(stem_x - size * 0.18, ctr.y + size * 0.38), size * 0.17, color);
    let flag_start = egui::pos2(stem_x, ctr.y - size * 0.5);
    let flag_mid = egui::pos2(stem_x + size * 0.22, ctr.y - size * 0.28);
    let flag_end = egui::pos2(stem_x, ctr.y - size * 0.1);
    p.add(egui::Shape::line(
        vec![flag_start, flag_mid, flag_end],
        egui::Stroke::new(size * 0.09, color),
    ));
}

fn draw_speaker(p: &egui::Painter, at: egui::Pos2, color: Color32) {
    let s = 5.5f32;
    let a = egui::pos2(at.x, at.y - s * 0.5);
    let b = egui::pos2(at.x, at.y + s * 0.5);
    let cc = egui::pos2(at.x + s * 0.8, at.y);
    p.add(egui::Shape::convex_polygon(vec![a, b, cc], color, egui::Stroke::NONE));
    p.rect_filled(
        egui::Rect::from_min_size(egui::pos2(at.x - s * 0.9, at.y - s * 0.25), Vec2::new(s * 0.6, s * 0.5)),
        1.0,
        color,
    );
}

fn draw_viz(ui: &egui::Ui, rect: egui::Rect, t: &'static Theme, levels: &[f64]) {
    let p = ui.painter_at(rect);
    let n = levels.len();
    if n == 0 {
        return;
    }
    let gap = 3.0f32;
    let w = (rect.width() - gap * (n as f32 - 1.0)) / n as f32;
    let base_y = rect.bottom() - 4.0;
    for (i, lvl) in levels.iter().enumerate() {
        let lvl = *lvl;
        if lvl <= 0.01 {
            continue;
        }
        let bh = (3.0f32).max(lvl as f32 * (rect.height() - 10.0));
        let x = rect.left() + 4.0 + i as f32 * (w + gap);
        let bar = egui::Rect::from_min_size(egui::pos2(x, base_y - bh), Vec2::new(w, bh));
        // градиентная имитация: три полосы от accent2 к accent
        let steps = 3;
        for k in 0..steps {
            let f0 = k as f32 / steps as f32;
            let f1 = (k + 1) as f32 / steps as f32;
            let y0 = base_y - bh + bh * f0;
            let y1 = base_y - bh + bh * f1;
            let col = lerp_color(c(t.accent2), c(t.accent), (f0 + f1) / 2.0);
            p.rect_filled(
                egui::Rect::from_min_max(egui::pos2(bar.left(), y0), egui::pos2(bar.right(), y1)),
                if k == 0 { 2.0 } else { 0.0 },
                col,
            );
        }
    }
}

fn lerp_color(a: Color32, b: Color32, t: f32) -> Color32 {
    let t = t.clamp(0.0, 1.0);
    Color32::from_rgb(
        (a.r() as f32 + (b.r() as f32 - a.r() as f32) * t) as u8,
        (a.g() as f32 + (b.g() as f32 - a.g() as f32) * t) as u8,
        (a.b() as f32 + (b.b() as f32 - a.b() as f32) * t) as u8,
    )
}

// ==================================================================== миниплеер
pub fn show_mini_viewport(
    ctx: &egui::Context,
    mini: Arc<Mutex<MiniState>>,
    mini_cover: Arc<Mutex<Option<egui::TextureHandle>>>,
    cmd_tx: std::sync::mpsc::Sender<MiniCmd>,
    theme_idx: usize,
) {
    let t = themes::theme(theme_idx);
    let open = mini.lock().map(|m| m.open).unwrap_or(false);
    if !open {
        return;
    }
    ctx.show_viewport_immediate(
        egui::ViewportId(egui::Id::new("hox-mini")),
        egui::ViewportBuilder::default()
            .with_title("Hox — мини-плеер")
            .with_inner_size([460.0, 116.0])
            .with_always_on_top()
            .with_decorations(false),
        move |ctx, _class| {
            egui::CentralPanel::default()
                .frame(egui::Frame::NONE.fill(c(t.bg2)).inner_margin(egui::Margin::same(12)))
                .show(ctx, |ui| {
                    // перетаскивание окна за верхнюю полосу
                    let (drag_rect, drag_resp) =
                        ui.allocate_exact_size(Vec2::new(ui.available_width(), 10.0), Sense::drag());
                    let _ = drag_rect;
                    if drag_resp.dragged() {
                        ctx.send_viewport_cmd(egui::ViewportCommand::StartDrag);
                    }

                    let st = mini.lock().unwrap().clone();
                    ui.horizontal(|ui| {
                        // обложка 84x84
                        let (crect, _) = ui.allocate_exact_size(Vec2::splat(84.0), Sense::hover());
                        let cover = mini_cover.lock().unwrap().clone();
                        let p = ui.painter_at(crect);
                        match cover {
                            Some(tex) => {
                                let (w, h) = (tex.size()[0] as f32, tex.size()[1] as f32);
                                let scale = (84.0 / w).max(84.0 / h);
                                let dst = egui::Rect::from_center_size(
                                    crect.center(),
                                    Vec2::new(w * scale, h * scale),
                                );
                                p.image(tex.id(), dst, egui::Rect::from_min_max(egui::pos2(0.0, 0.0), egui::pos2(1.0, 1.0)), Color32::WHITE);
                            }
                            None => {
                                p.rect_filled(crect, 8.0, c(t.panel));
                                draw_note(&p, crect.center(), c(t.sub), 22.0);
                            }
                        }
                        ui.add_space(10.0);
                        ui.vertical(|ui| {
                            ui.label(
                                RichText::new(if st.title.is_empty() { "—".to_string() } else { st.title.clone() })
                                    .size(13.0)
                                    .strong()
                                    .color(c(t.text)),
                            );
                            ui.label(RichText::new(st.artist.clone()).size(11.0).color(c(t.sub)));
                            ui.add_space(2.0);
                            ui.horizontal(|ui| {
                                let bw = Vec2::new(38.0, 30.0);
                                if vector_button(ui, bw, IconKind::Prev, c(t.panel), c(t.text), c(t.accent)).clicked() {
                                    let _ = cmd_tx.send(MiniCmd::Prev);
                                }
                                if vector_button(
                                    ui,
                                    bw,
                                    if st.playing { IconKind::Pause } else { IconKind::Play },
                                    c(t.accent),
                                    c(t.bg),
                                    c(t.accent),
                                )
                                .clicked()
                                {
                                    let _ = cmd_tx.send(MiniCmd::PlayPause);
                                }
                                if vector_button(ui, bw, IconKind::Next, c(t.panel), c(t.text), c(t.accent)).clicked() {
                                    let _ = cmd_tx.send(MiniCmd::Next);
                                }
                                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                                    if ui
                                        .add(egui::Button::new(RichText::new("✕").size(11.0).color(c(t.sub))).fill(Color32::TRANSPARENT).stroke(egui::Stroke::NONE))
                                        .clicked()
                                    {
                                        let _ = cmd_tx.send(MiniCmd::Close);
                                    }
                                });
                            });
                            let mut frac = st.pos_frac;
                            let r = ui.add(
                                egui::Slider::new(&mut frac, 0.0..=1000.0).show_value(false),
                            );
                            if r.changed() {
                                let _ = cmd_tx.send(MiniCmd::SeekFrac(frac));
                            }
                            if !st.karaoke_line.is_empty() {
                                ui.label(RichText::new(st.karaoke_line.clone()).size(10.5).color(c(t.accent)));
                            }
                        });
                    });
                });
        },
    );
}
