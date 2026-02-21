use serde::Deserialize;

use crate::client::HttpClient;

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsEntry {
    pub uri: String,
    #[serde(default)]
    pub size: Option<u64>,
    #[serde(default)]
    pub is_dir: bool,
    #[serde(default)]
    pub mod_time: Option<String>,
}

impl FsEntry {
    pub fn name(&self) -> &str {
        let path = self.uri.trim_end_matches('/');
        path.rsplit('/').next().unwrap_or(&self.uri)
    }
}

#[derive(Debug, Clone)]
pub struct TreeNode {
    pub entry: FsEntry,
    pub depth: usize,
    pub expanded: bool,
    pub children_loaded: bool,
    pub children: Vec<TreeNode>,
}

#[derive(Debug, Clone)]
pub struct VisibleRow {
    pub depth: usize,
    pub name: String,
    pub uri: String,
    pub is_dir: bool,
    pub expanded: bool,
    /// Index path into the tree for identifying this node
    pub node_index: Vec<usize>,
}

pub struct TreeState {
    pub nodes: Vec<TreeNode>,
    pub visible: Vec<VisibleRow>,
    pub cursor: usize,
    pub scroll_offset: usize,
}

impl TreeState {
    pub fn new() -> Self {
        Self {
            nodes: Vec::new(),
            visible: Vec::new(),
            cursor: 0,
            scroll_offset: 0,
        }
    }

    /// Known root-level scopes in OpenViking
    const ROOT_SCOPES: &'static [&'static str] =
        &["resources", "memories", "skills", "temp", "agent", "queue", "user", "session"];

    pub async fn load_root(&mut self, client: &HttpClient, uri: &str) {
        let is_root = uri == "viking://" || uri == "viking:///";

        if is_root {
            // Synthesize root scope folders and eagerly load their children
            let mut root_nodes = Vec::new();
            for scope in Self::ROOT_SCOPES {
                let scope_uri = format!("viking://{}", scope);
                let mut node = TreeNode {
                    entry: FsEntry {
                        uri: scope_uri.clone(),
                        size: None,
                        is_dir: true,
                        mod_time: None,
                    },
                    depth: 0,
                    expanded: false,
                    children_loaded: false,
                    children: Vec::new(),
                };

                // Try to load children eagerly to show first level
                if let Ok(mut children) = Self::fetch_children(client, &scope_uri).await {
                    for child in &mut children {
                        child.depth = 1;
                    }
                    node.children = children;
                    node.children_loaded = true;
                    if !node.children.is_empty() {
                        node.expanded = true;
                    }
                }
                // Always show all scopes
                root_nodes.push(node);
            }

            self.nodes = root_nodes;
            self.rebuild_visible();
        } else {
            match Self::fetch_children(client, uri).await {
                Ok(nodes) => {
                    self.nodes = nodes;
                    self.rebuild_visible();
                }
                Err(e) => {
                    self.nodes = vec![TreeNode {
                        entry: FsEntry {
                            uri: format!("(error: {})", e),
                            size: None,
                            is_dir: false,
                            mod_time: None,
                        },
                        depth: 0,
                        expanded: false,
                        children_loaded: false,
                        children: Vec::new(),
                    }];
                    self.rebuild_visible();
                }
            }
        }
    }

    async fn fetch_children(
        client: &HttpClient,
        uri: &str,
    ) -> Result<Vec<TreeNode>, String> {
        let result = client
            .ls(uri, false, false, "original", 256, false, 1000)
            .await
            .map_err(|e| e.to_string())?;

        let entries: Vec<FsEntry> = if let Some(arr) = result.as_array() {
            arr.iter()
                .filter_map(|v| serde_json::from_value(v.clone()).ok())
                .collect()
        } else {
            serde_json::from_value(result).unwrap_or_default()
        };

        let mut nodes: Vec<TreeNode> = entries
            .into_iter()
            .map(|entry| TreeNode {
                depth: 0,
                expanded: false,
                children_loaded: !entry.is_dir,
                children: Vec::new(),
                entry,
            })
            .collect();

        // Sort: directories first, then alphabetical
        nodes.sort_by(|a, b| {
            b.entry
                .is_dir
                .cmp(&a.entry.is_dir)
                .then_with(|| a.entry.name().to_lowercase().cmp(&b.entry.name().to_lowercase()))
        });

        Ok(nodes)
    }

    pub fn rebuild_visible(&mut self) {
        self.visible.clear();
        let mut path = Vec::new();
        for (i, node) in self.nodes.iter().enumerate() {
            path.push(i);
            Self::flatten_node(node, 0, &mut self.visible, &mut path);
            path.pop();
        }
    }

    fn flatten_node(
        node: &TreeNode,
        depth: usize,
        visible: &mut Vec<VisibleRow>,
        path: &mut Vec<usize>,
    ) {
        visible.push(VisibleRow {
            depth,
            name: node.entry.name().to_string(),
            uri: node.entry.uri.clone(),
            is_dir: node.entry.is_dir,
            expanded: node.expanded,
            node_index: path.clone(),
        });

        if node.expanded {
            for (i, child) in node.children.iter().enumerate() {
                path.push(i);
                Self::flatten_node(child, depth + 1, visible, path);
                path.pop();
            }
        }
    }

    pub async fn toggle_expand(&mut self, client: &HttpClient) {
        if self.visible.is_empty() {
            return;
        }
        let row = &self.visible[self.cursor];
        if !row.is_dir {
            return;
        }

        let index_path = row.node_index.clone();
        let node = Self::get_node_mut(&mut self.nodes, &index_path);

        if let Some(node) = node {
            if !node.children_loaded {
                // Lazy load children
                match Self::fetch_children(client, &node.entry.uri).await {
                    Ok(mut children) => {
                        let child_depth = node.depth + 1;
                        for child in &mut children {
                            child.depth = child_depth;
                        }
                        node.children = children;
                        node.children_loaded = true;
                    }
                    Err(_) => {
                        node.children_loaded = true;
                        // Leave children empty on error
                    }
                }
            }
            node.expanded = !node.expanded;
        }

        self.rebuild_visible();
    }

    fn get_node_mut<'a>(
        nodes: &'a mut Vec<TreeNode>,
        index_path: &[usize],
    ) -> Option<&'a mut TreeNode> {
        if index_path.is_empty() {
            return None;
        }
        let mut current = nodes.get_mut(index_path[0])?;
        for &idx in &index_path[1..] {
            current = current.children.get_mut(idx)?;
        }
        Some(current)
    }

    pub fn move_cursor_up(&mut self) {
        if self.cursor > 0 {
            self.cursor -= 1;
        }
    }

    pub fn move_cursor_down(&mut self) {
        if !self.visible.is_empty() && self.cursor < self.visible.len() - 1 {
            self.cursor += 1;
        }
    }

    pub fn selected_uri(&self) -> Option<&str> {
        self.visible.get(self.cursor).map(|r| r.uri.as_str())
    }

    pub fn selected_is_dir(&self) -> Option<bool> {
        self.visible.get(self.cursor).map(|r| r.is_dir)
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
