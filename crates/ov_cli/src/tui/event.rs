use crossterm::event::{KeyCode, KeyEvent};

use super::app::{App, Panel};

pub async fn handle_key(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Char('q') => {
            app.should_quit = true;
        }
        KeyCode::Tab => {
            app.toggle_focus();
        }
        KeyCode::Char('v') => {
            app.toggle_vector_records_mode().await;
        }
        KeyCode::Char('n') if app.showing_vector_records => {
            app.load_next_vector_page().await;
        }
        KeyCode::Char('c') if app.showing_vector_records => {
            app.load_vector_count().await;
        }
        _ => match app.focus {
            Panel::Tree => handle_tree_key(app, key).await,
            Panel::Content => handle_content_key(app, key),
        },
    }
}

async fn handle_tree_key(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Char('j') | KeyCode::Down => {
            app.tree.move_cursor_down();
            app.load_content_for_selected().await;
        }
        KeyCode::Char('k') | KeyCode::Up => {
            app.tree.move_cursor_up();
            app.load_content_for_selected().await;
        }
        KeyCode::Char('.') => {
            let client = app.client.clone();
            app.tree.toggle_expand(&client).await;
            app.load_content_for_selected().await;
        }
        _ => {}
    }
}

fn handle_content_key(app: &mut App, key: KeyEvent) {
    if app.showing_vector_records {
        match key.code {
            KeyCode::Char('j') | KeyCode::Down => {
                app.move_vector_cursor_down();
            }
            KeyCode::Char('k') | KeyCode::Up => {
                app.move_vector_cursor_up();
            }
            KeyCode::Char('g') => {
                app.scroll_vector_top();
            }
            KeyCode::Char('G') => {
                app.scroll_vector_bottom();
            }
            _ => {}
        }
    } else {
        match key.code {
            KeyCode::Char('j') | KeyCode::Down => {
                app.scroll_content_down();
            }
            KeyCode::Char('k') | KeyCode::Up => {
                app.scroll_content_up();
            }
            KeyCode::Char('g') => {
                app.scroll_content_top();
            }
            KeyCode::Char('G') => {
                app.scroll_content_bottom();
            }
            _ => {}
        }
    }
}
