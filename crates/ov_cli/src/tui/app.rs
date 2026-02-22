use crate::client::HttpClient;

use super::tree::TreeState;

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Panel {
    Tree,
    Content,
}

pub struct App {
    pub client: HttpClient,
    pub tree: TreeState,
    pub focus: Panel,
    pub content: String,
    pub content_title: String,
    pub content_scroll: u16,
    pub content_line_count: u16,
    pub should_quit: bool,
    pub status_message: String,
}

impl App {
    pub fn new(client: HttpClient) -> Self {
        Self {
            client,
            tree: TreeState::new(),
            focus: Panel::Tree,
            content: String::new(),
            content_title: String::new(),
            content_scroll: 0,
            content_line_count: 0,
            should_quit: false,
            status_message: String::new(),
        }
    }

    pub async fn init(&mut self, uri: &str) {
        self.tree.load_root(&self.client, uri).await;
        self.load_content_for_selected().await;
    }

    pub async fn load_content_for_selected(&mut self) {
        let (uri, is_dir) = match (
            self.tree.selected_uri().map(|s| s.to_string()),
            self.tree.selected_is_dir(),
        ) {
            (Some(uri), Some(is_dir)) => (uri, is_dir),
            _ => {
                self.content = "(nothing selected)".to_string();
                self.content_title = String::new();
                self.content_scroll = 0;
                return;
            }
        };

        self.content_title = uri.clone();
        self.content_scroll = 0;

        if is_dir {
            // For root-level scope URIs (e.g. viking://resources), show a
            // simple placeholder instead of calling abstract/overview which
            // don't work at this level.
            if Self::is_root_scope_uri(&uri) {
                let scope = uri.trim_start_matches("viking://").trim_end_matches('/');
                self.content = format!(
                    "Scope: {}\n\nPress '.' to expand/collapse.\nUse j/k to navigate.",
                    scope
                );
            } else {
                self.load_directory_content(&uri).await;
            }
        } else {
            self.load_file_content(&uri).await;
        }

        self.content_line_count = self.content.lines().count() as u16;
    }

    async fn load_directory_content(&mut self, uri: &str) {
        let (abstract_result, overview_result) = tokio::join!(
            self.client.abstract_content(uri),
            self.client.overview(uri),
        );

        let mut parts = Vec::new();

        match abstract_result {
            Ok(text) if !text.is_empty() => {
                parts.push(format!("=== Abstract ===\n\n{}", text));
            }
            Ok(_) => {
                parts.push("=== Abstract ===\n\n(empty)".to_string());
            }
            Err(_) => {
                parts.push("=== Abstract ===\n\n(not available)".to_string());
            }
        }

        match overview_result {
            Ok(text) if !text.is_empty() => {
                parts.push(format!("=== Overview ===\n\n{}", text));
            }
            Ok(_) => {
                parts.push("=== Overview ===\n\n(empty)".to_string());
            }
            Err(_) => {
                parts.push("=== Overview ===\n\n(not available)".to_string());
            }
        }

        self.content = parts.join("\n\n---\n\n");
    }

    async fn load_file_content(&mut self, uri: &str) {
        match self.client.read(uri).await {
            Ok(text) if !text.is_empty() => {
                self.content = text;
            }
            Ok(_) => {
                self.content = "(empty file)".to_string();
            }
            Err(e) => {
                self.content = format!("(error reading file: {})", e);
            }
        }
    }

    pub fn scroll_content_up(&mut self) {
        self.content_scroll = self.content_scroll.saturating_sub(1);
    }

    pub fn scroll_content_down(&mut self) {
        if self.content_scroll < self.content_line_count.saturating_sub(1) {
            self.content_scroll += 1;
        }
    }

    pub fn scroll_content_top(&mut self) {
        self.content_scroll = 0;
    }

    pub fn scroll_content_bottom(&mut self) {
        self.content_scroll = self.content_line_count.saturating_sub(1);
    }

    /// Returns true if the URI is a root-level scope (e.g. "viking://resources")
    fn is_root_scope_uri(uri: &str) -> bool {
        let stripped = uri.trim_start_matches("viking://").trim_end_matches('/');
        // Root scope = no slashes after the scheme (just the scope name)
        !stripped.is_empty() && !stripped.contains('/')
    }

    pub fn toggle_focus(&mut self) {
        self.focus = match self.focus {
            Panel::Tree => Panel::Content,
            Panel::Content => Panel::Tree,
        };
    }
}
