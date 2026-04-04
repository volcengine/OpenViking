use crate::client::HttpClient;

use super::tree::TreeState;

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Panel {
    Tree,
    Content,
}

#[derive(Debug, Clone)]
pub struct VectorRecordsState {
    pub records: Vec<serde_json::Value>,
    pub cursor: usize,
    pub scroll_offset: usize,
    pub next_page_cursor: Option<String>,
    pub has_more: bool,
    pub total_count: Option<u64>,
}

impl VectorRecordsState {
    pub fn new() -> Self {
        Self {
            records: Vec::new(),
            cursor: 0,
            scroll_offset: 0,
            next_page_cursor: None,
            has_more: false,
            total_count: None,
        }
    }

    /// Adjust scroll_offset so cursor is visible in the given viewport height
    pub fn adjust_scroll(&mut self, viewport_height: usize) {
        if viewport_height == 0 {
            return;
        }
        if self.cursor < self.scroll_offset {
            self.scroll_offset = self.cursor;
        } else if self.cursor >= self.scroll_offset + viewport_height {
            self.scroll_offset = self.cursor - viewport_height + 1;
        }
    }
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
    pub vector_state: VectorRecordsState,
    pub showing_vector_records: bool,
    pub current_uri: String,
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
            vector_state: VectorRecordsState::new(),
            showing_vector_records: false,
            current_uri: "/".to_string(),
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

        self.current_uri = uri.clone();
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

        // If in vector mode, reload records with new current_uri
        if self.showing_vector_records {
            self.load_vector_records(Some(self.current_uri.clone()))
                .await;
        }
    }

    async fn load_directory_content(&mut self, uri: &str) {
        let (abstract_result, overview_result) =
            tokio::join!(self.client.abstract_content(uri), self.client.overview(uri),);

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

    pub async fn load_vector_records(&mut self, uri_prefix: Option<String>) {
        self.status_message = "Loading vector records...".to_string();
        match self
            .client
            .debug_vector_scroll(Some(100), None, uri_prefix.clone())
            .await
        {
            Ok((records, next_cursor)) => {
                self.vector_state.records = records;
                self.vector_state.has_more = next_cursor.is_some();
                self.vector_state.next_page_cursor = next_cursor;
                self.vector_state.cursor = 0;
                self.vector_state.scroll_offset = 0;
                self.status_message =
                    format!("Loaded {} vector records", self.vector_state.records.len());
            }
            Err(e) => {
                self.status_message = format!("Failed to load vector records: {}", e);
            }
        }
    }

    pub async fn load_next_vector_page(&mut self) {
        if !self.vector_state.has_more {
            self.status_message = "No more pages".to_string();
            return;
        }

        self.status_message = "Loading next page...".to_string();
        match self
            .client
            .debug_vector_scroll(
                Some(100),
                self.vector_state.next_page_cursor.clone(),
                Some(self.current_uri.clone()),
            )
            .await
        {
            Ok((mut new_records, next_cursor)) => {
                self.vector_state.records.append(&mut new_records);
                self.vector_state.has_more = next_cursor.is_some();
                self.vector_state.next_page_cursor = next_cursor;
                self.status_message = format!(
                    "Loaded {} total vector records",
                    self.vector_state.records.len()
                );
            }
            Err(e) => {
                self.status_message = format!("Failed to load next page: {}", e);
            }
        }
    }

    pub async fn toggle_vector_records_mode(&mut self) {
        self.showing_vector_records = !self.showing_vector_records;
        if self.showing_vector_records && self.vector_state.records.is_empty() {
            self.load_vector_records(Some(self.current_uri.clone()))
                .await;
        }
    }

    pub async fn load_vector_count(&mut self) {
        self.status_message = "Loading vector count...".to_string();
        match self
            .client
            .debug_vector_count(None, Some(self.current_uri.clone()))
            .await
        {
            Ok(count) => {
                self.vector_state.total_count = Some(count);
                self.status_message = format!("Total vector records: {}", count);
            }
            Err(e) => {
                self.status_message = format!("Failed to load count: {}", e);
            }
        }
    }

    pub fn move_vector_cursor_up(&mut self) {
        if self.vector_state.cursor > 0 {
            self.vector_state.cursor -= 1;
        }
    }

    pub fn move_vector_cursor_down(&mut self) {
        if !self.vector_state.records.is_empty()
            && self.vector_state.cursor < self.vector_state.records.len() - 1
        {
            self.vector_state.cursor += 1;
        }
    }

    pub fn scroll_vector_top(&mut self) {
        self.vector_state.cursor = 0;
    }

    pub fn scroll_vector_bottom(&mut self) {
        if !self.vector_state.records.is_empty() {
            self.vector_state.cursor = self.vector_state.records.len() - 1;
        }
    }
}
