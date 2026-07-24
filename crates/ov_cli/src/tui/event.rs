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
        KeyCode::Char('g') => {
            app.tree.move_cursor_top();
            app.load_content_for_selected().await;
        }
        KeyCode::Char('G') => {
            app.tree.move_cursor_bottom();
            app.load_content_for_selected().await;
        }
        KeyCode::Char('.') => {
            // First try to load pending image if any
            if !app.load_pending_image().await {
                // No pending image, toggle directory expand
                let client = app.client.clone();
                app.tree.toggle_expand(&client).await;
                app.load_content_for_selected().await;
            }
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
                let message = format!(
                    "Delete {}? (y/n): {}",
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

#[cfg(test)]
mod tests {
    use crossterm::event::KeyModifiers;

    use super::*;
    use crate::{client::HttpClient, tui::tree::VisibleRow};

    fn app_with_tree_rows() -> App {
        let client = HttpClient::new(
            "http://127.0.0.1:9",
            None,
            None,
            None,
            None,
            0.1,
            false,
            None,
        );
        let mut app = App::new(client);
        app.tree.visible = (0..3)
            .map(|index| VisibleRow {
                depth: 0,
                name: format!("file-{index}.md"),
                uri: format!("viking://resources/file-{index}.md"),
                is_dir: false,
                expanded: false,
                node_index: vec![index],
            })
            .collect();
        app
    }

    #[tokio::test]
    async fn tree_g_moves_cursor_to_first_row() {
        let mut app = app_with_tree_rows();
        app.tree.cursor = 2;

        handle_tree_key(
            &mut app,
            KeyEvent::new(KeyCode::Char('g'), KeyModifiers::NONE),
        )
        .await;

        assert_eq!(app.tree.cursor, 0);
    }

    #[tokio::test]
    async fn tree_capital_g_moves_cursor_to_last_row() {
        let mut app = app_with_tree_rows();

        handle_tree_key(
            &mut app,
            KeyEvent::new(KeyCode::Char('G'), KeyModifiers::SHIFT),
        )
        .await;

        assert_eq!(app.tree.cursor, 2);
    }
}
