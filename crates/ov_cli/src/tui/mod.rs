mod app;
mod event;
mod tree;
mod ui;

use std::io;

use crossterm::{
    event::{self as ct_event, Event},
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
    ExecutableCommand,
};
use ratatui::prelude::*;

use crate::client::HttpClient;
use crate::error::Result;
use app::App;

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
    app.init(uri).await;

    loop {
        // Adjust tree scroll before rendering
        let tree_height = {
            let area = terminal.size()?;
            // main area height minus borders (2) minus status bar (1)
            area.height.saturating_sub(3) as usize
        };
        app.tree.adjust_scroll(tree_height);

        terminal.draw(|frame| ui::render(frame, &app))?;

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

    Ok(())
}
