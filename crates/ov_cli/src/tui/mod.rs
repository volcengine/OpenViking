mod app;
mod event;
mod tree;
mod ui;
mod image_preview;

use std::io;

use crossterm::{
    ExecutableCommand,
    event::{self as ct_event, Event, KeyCode},
    terminal::{EnterAlternateScreen, LeaveAlternateScreen, disable_raw_mode, enable_raw_mode},
};
use ratatui::prelude::*;
use ratatui::text::Line;
use ratatui::widgets::{Paragraph, Block, Borders};

use crate::client::HttpClient;
use crate::error::Result;
use app::App;
use image_preview::ImagePreviewer;

pub async fn run_tui(client: HttpClient, uri: &str) -> Result<()> {
    // Set up panic hook to restore terminal
    let original_hook = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |panic_info| {
        let _ = disable_raw_mode();
        let _ = io::stdout().execute(LeaveAlternateScreen);
        original_hook(panic_info);
    }));

    enable_raw_mode()?;
    if let Err(e) = io::stdout().execute(EnterAlternateScreen) {
        let _ = disable_raw_mode();
        return Err(crate::error::Error::Io(e));
    }

    let result = run_loop(client, uri).await;

    // Always restore terminal
    let _ = disable_raw_mode();
    let _ = io::stdout().execute(LeaveAlternateScreen);

    result
}

async fn run_loop(client: HttpClient, uri: &str) -> Result<()> {
    let backend = CrosstermBackend::new(io::stdout());
    let mut terminal = Terminal::new(backend)?;

    let mut app = App::new(client);
    let mut image_previewer = ImagePreviewer::new();
    let mut show_debug_logs = false;

    // Initialize viuer (always available)
    let _ = image_previewer.init();

    app.init(uri).await;

    loop {
        // Adjust tree scroll before rendering
        let tree_height = {
            let area = terminal.size()?;
            // main area height minus borders (2) minus status bar (1)
            area.height.saturating_sub(3) as usize
        };
        app.tree.adjust_scroll(tree_height);
        // Adjust vector scroll before rendering
        if app.showing_vector_records {
            app.vector_state.adjust_scroll(tree_height);
        }

        // Update status message (clear after 3 seconds)
        app.update_messages();

        // Store content area
        let mut captured_content_area = None;

        // Track if we had an image in the previous iteration
        static PREV_HAD_IMAGE: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
        let had_image = PREV_HAD_IMAGE.load(std::sync::atomic::Ordering::Relaxed);
        let has_image = app.current_preview_image.is_some();

        // Clear image if we just transitioned away from an image
        if !show_debug_logs && had_image && !has_image {
            // Use ratatui terminal clear to properly reset
            let _ = terminal.clear();
            let _ = image_previewer.clear_image();
        }
        PREV_HAD_IMAGE.store(has_image, std::sync::atomic::Ordering::Relaxed);

        // Render TUI first
        terminal.draw(|frame| {
            if show_debug_logs {
                // Show debug logs
                let debug_logs = image_previewer.get_debug_logs();
                let text: Vec<Line> = debug_logs
                    .iter()
                    .rev()
                    .take(usize::from(frame.area().height.saturating_sub(2)))
                    .map(|s| Line::from(s.as_str()))
                    .collect();
                let paragraph = Paragraph::new(text)
                    .block(Block::default().borders(Borders::ALL).title(" Debug Logs (Press L to hide) "));
                frame.render_widget(paragraph, frame.area());
            } else {
                // Normal UI
                let areas = ui::render_with_content_area(frame, &app);
                // Save content area
                captured_content_area = Some(areas.1);
            }
        })?;

        // Update the preview area coordinates only if not showing debug logs
        if !show_debug_logs {
            if let Some(content_area) = captured_content_area {
                image_previewer.set_preview_area(image_preview::PreviewArea {
                    x: content_area.x,
                    y: content_area.y,
                    width: content_area.width,
                    height: content_area.height,
                });
            }

            // Update image display based on current file
            if let Some(image_path) = &app.current_preview_image {
                if let Err(e) = image_previewer.display_image(image_path) {
                    // Show error in status bar
                    if !app.status_message_locked {
                        app.status_message = format!("Image preview error: {}", e);
                        app.status_message_time = Some(std::time::Instant::now());
                    }
                }
            }
        }

        if ct_event::poll(std::time::Duration::from_millis(100))? {
            if let Event::Key(key) = ct_event::read()? {
                if key.kind == crossterm::event::KeyEventKind::Press {
                    match key.code {
                        KeyCode::Char('L') | KeyCode::Char('l') => {
                            show_debug_logs = !show_debug_logs;
                            if !show_debug_logs {
                                let _ = image_previewer.clear_image();
                            }
                        }
                        _ => {
                            if !show_debug_logs {
                                event::handle_key(&mut app, key).await;
                            }
                        }
                    }
                }
            }
        }

        if app.should_quit {
            break;
        }
    }

    // Cleanup image previewer
    image_previewer.cleanup();

    Ok(())
}
