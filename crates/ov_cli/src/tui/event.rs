use crossterm::event::{KeyCode, KeyEvent};

use super::app::{App, Panel};

pub async fn handle_key(app: &mut App, key: KeyEvent) {
    // Check for error message first - allow q to quit and Esc to clear
    if !app.error_message.is_empty() {
        match key.code {
            KeyCode::Char('q') => {
                app.should_quit = true;
            }
            KeyCode::Esc => {
                app.clear_error_message();
            }
            _ => {}
        }
        return;
    }

    // Check for confirmation first - allow q to quit, n/Esc to cancel
    if app.confirmation.is_some() {
        match key.code {
            KeyCode::Char('y') => {
                // Confirm action
                if let Some((_, callback)) = app.confirmation.take() {
                    app.status_message_locked = false;
                    callback(app).await;
                }
            }
            KeyCode::Char('n') | KeyCode::Esc => {
                // Cancel action
                app.clear_confirmation();
                app.set_status_message("Action cancelled".to_string());
            }
            KeyCode::Char('q') => {
                // Allow quitting even during confirmation
                app.clear_confirmation();
                app.should_quit = true;
            }
            _ => {
                // Ignore other keys during confirmation
            }
        }
        return;
    }
    
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
        KeyCode::Char('d') => {
            // Delete currently selected URI
            if let Some(selected_uri) = app.tree.selected_uri() {
                let selected_uri = selected_uri.to_string();
                let is_dir = app.tree.selected_is_dir().unwrap_or(false);

                // First check if deletion is allowed
                if !app.tree.allow_deletion(&selected_uri) {
                    app.set_error_message("Cannot delete root and scope directories".to_string());
                    return;
                }

                // Create confirmation
                let message = format!("Delete {}? (y/n): {}",
                    if is_dir { "directory" } else { "file" },
                    selected_uri
                );

                app.create_confirmation(message, move |app| {
                    Box::pin(async move {
                        app.delete_selected_uri().await;
                    })
                });
            } else {
                app.set_status_message("Nothing selected to delete".to_string());
            }
        }
        KeyCode::Char('r') => {
            app.reload_entire_tree().await;
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
