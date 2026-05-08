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

    // Initialize image previewer BEFORE entering alternate screen
    let mut image_previewer = ImagePreviewer::new();
    let previewer_available = ImagePreviewer::is_available();
    let mut previewer_initialized = false;

    if previewer_available {
        if let Err(e) = image_previewer.init() {
            eprintln!("Warning: Failed to initialize image previewer: {}", e);
        } else {
            previewer_initialized = true;
            eprintln!("Image previewer initialized successfully");
        }
    }

    enable_raw_mode()?;
    if let Err(e) = io::stdout().execute(EnterAlternateScreen) {
        let _ = disable_raw_mode();
        return Err(crate::error::Error::Io(e));
    }

    let result = run_loop(client, uri, image_previewer, previewer_available, previewer_initialized).await;

    // Always restore terminal
    let _ = disable_raw_mode();
    let _ = io::stdout().execute(LeaveAlternateScreen);

    result
}

async fn run_loop(
    client: HttpClient,
    uri: &str,
    mut image_previewer: ImagePreviewer,
    previewer_available: bool,
    previewer_initialized: bool,
) -> Result<()> {
    let backend = CrosstermBackend::new(io::stdout());
    let mut terminal = Terminal::new(backend)?;

    let mut app = App::new(client);
    let mut show_debug_logs = false;

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

        // Render TUI
        terminal.draw(|frame| {
            if show_debug_logs {
                // Show debug logs
                let debug_logs = image_previewer.get_debug_logs();
                let text: Vec<Line> = debug_logs
                    .iter()
                    .rev()
                    .take(usize::from(frame.size().height.saturating_sub(2)))
                    .map(|s| Line::from(s.as_str()))
                    .collect();
                let paragraph = Paragraph::new(text)
                    .block(Block::default().borders(Borders::ALL).title(" Debug Logs (Press L to hide) "));
                frame.render_widget(paragraph, frame.size());
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

            // Update image display based on current file only if initialized
            if previewer_available && previewer_initialized {
                if let Some(image_path) = &app.current_preview_image {
                    if let Err(e) = image_previewer.display_image(image_path) {
                        // Don't spam errors, just try to clear and show in status bar
                        let _ = image_previewer.clear_image();
                        // Only update status if not locked
                        if !app.status_message_locked {
                            app.status_message = format!("Image preview error: {}", e);
                            app.status_message_time = Some(std::time::Instant::now());
                        }
                    }
                } else {
                    let _ = image_previewer.clear_image();
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
