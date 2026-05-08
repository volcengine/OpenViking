mod app;
mod event;
mod tree;
mod ui;
mod image_preview;

use std::io;

use crossterm::{
    ExecutableCommand,
    event::{self as ct_event, Event},
    terminal::{EnterAlternateScreen, LeaveAlternateScreen, disable_raw_mode, enable_raw_mode},
};
use ratatui::prelude::*;

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
    let previewer_available = ImagePreviewer::is_available();

    // Initialize image previewer if available
    if previewer_available {
        if let Err(e) = image_previewer.init() {
            eprintln!("Warning: Failed to initialize image previewer: {}", e);
        }
    }

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
            let areas = ui::render_with_content_area(frame, &app);
            // Save content area
            captured_content_area = Some(areas.1);
        })?;

        // Update the preview area coordinates
        if let Some(content_area) = captured_content_area {
            image_previewer.set_preview_area(image_preview::PreviewArea {
                x: content_area.x,
                y: content_area.y,
                width: content_area.width,
                height: content_area.height,
            });
        }

        // Update image display based on current file
        if previewer_available {
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

        if ct_event::poll(std::time::Duration::from_millis(100))? {
            if let Event::Key(key) = ct_event::read()? {
                if key.kind == crossterm::event::KeyEventKind::Press {
                    event::handle_key(&mut app, key).await;
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
