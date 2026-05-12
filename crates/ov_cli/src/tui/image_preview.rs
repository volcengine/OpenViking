use std::sync::{Arc, Mutex};
use std::path::Path;
use crossterm::{
    ExecutableCommand,
    cursor::MoveTo,
};

/// Preview area coordinates (in terminal cells)
#[derive(Debug, Clone, Copy)]
pub struct PreviewArea {
    pub x: u16,
    pub y: u16,
    pub width: u16,
    pub height: u16,
}

/// Check if a file is an image based on extension
pub fn is_image_file(filename: &str) -> bool {
    let path = Path::new(filename);
    let ext = path.extension().and_then(|e| e.to_str()).map(|e| e.to_lowercase());
    matches!(ext.as_deref(), Some("png" | "jpg" | "jpeg" | "gif" | "bmp" | "webp" | "tiff" | "tif"))
}

/// Image previewer using viuer (simpler, more reliable)
pub struct ImagePreviewer {
    current_image: Arc<Mutex<Option<String>>>,
    preview_area: Arc<Mutex<Option<PreviewArea>>>,
    debug_log: Option<Arc<Mutex<Vec<String>>>>,
}

impl ImagePreviewer {
    /// Create a new image previewer
    pub fn new() -> Self {
        Self {
            current_image: Arc::new(Mutex::new(None)),
            preview_area: Arc::new(Mutex::new(None)),
            debug_log: Some(Arc::new(Mutex::new(Vec::new()))),
        }
    }

    /// Add a debug log entry
    fn log(&self, message: &str) {
        if let Some(log) = &self.debug_log {
            log.lock().unwrap().push(message.to_string());
        }
    }

    /// Get debug logs
    pub fn get_debug_logs(&self) -> Vec<String> {
        if let Some(log) = &self.debug_log {
            log.lock().unwrap().clone()
        } else {
            Vec::new()
        }
    }

    /// Check if viuer is available (it always is, since it's a library)
    pub fn is_available() -> bool {
        true
    }

    /// Get installation instructions (not needed for viuer)
    pub fn installation_instructions() -> &'static str {
        "Image preview is available (using viuer)"
    }

    /// Initialize the image previewer (nothing to do for viuer)
    pub fn init(&mut self) -> Result<(), String> {
        self.log("Initialized viuer image previewer");
        Ok(())
    }

    /// Set the preview area coordinates
    pub fn set_preview_area(&mut self, area: PreviewArea) {
        self.log(&format!("Set preview area: {:?}", area));
        *self.preview_area.lock().unwrap() = Some(area);
    }

    /// Get the preview area
    pub fn preview_area(&self) -> Option<PreviewArea> {
        *self.preview_area.lock().unwrap()
    }

    /// Check if an image is currently displayed
    pub fn has_image_displayed(&self) -> bool {
        self.current_image.lock().unwrap().is_some()
    }

    /// Display an image at the last known preview area
    pub fn display_image(&mut self, image_path: &str) -> Result<(), String> {
        let preview_area = *self.preview_area.lock().unwrap();
        let preview_area = match preview_area {
            Some(area) => area,
            None => return Err("No preview area available".to_string()),
        };

        self.display_image_at(image_path, preview_area)
    }

    /// Display an image at the specified area
    pub fn display_image_at(
        &mut self,
        image_path: &str,
        area: PreviewArea,
    ) -> Result<(), String> {
        // Check if file exists first
        let path = std::path::Path::new(image_path);
        if !path.exists() {
            return Err(format!("Image file not found: {}", image_path));
        }
        if !path.is_file() {
            return Err(format!("Path is not a file: {}", image_path));
        }

        self.log(&format!("Displaying image: {}", image_path));
        self.log(&format!("Preview area: x={}, y={}, width={}, height={}", area.x, area.y, area.width, area.height));

        // Calculate dimensions - viuer uses half-blocks, so height is in characters
        // Each character cell can display 2x1 pixels (approximate)
        let max_width = Some(area.width.saturating_sub(2) as u32);
        let max_height = Some(area.height.saturating_sub(2) as u32);
        self.log(&format!("Target dimensions: width={:?}, height={:?}", max_width, max_height));

        // Try with multiple terminal protocols to find what works
        let configs = [
            // Try with kitty/iterm2 first with sizing
            viuer::Config {
                restore_cursor: true,
                transparent: true,
                truecolor: true,
                use_kitty: true,
                use_iterm: true,
                width: max_width,
                height: max_height,
                ..Default::default()
            },
            // Try with kitty/iterm2 with transparency disabled
            viuer::Config {
                restore_cursor: true,
                transparent: false,
                truecolor: true,
                use_kitty: true,
                use_iterm: true,
                width: max_width,
                height: max_height,
                ..Default::default()
            },
            // Then try without special protocols with sizing
            viuer::Config {
                restore_cursor: true,
                transparent: true,
                truecolor: true,
                use_kitty: false,
                use_iterm: false,
                width: max_width,
                height: max_height,
                ..Default::default()
            },
            // Try without special protocols with transparency disabled
            viuer::Config {
                restore_cursor: true,
                transparent: false,
                truecolor: true,
                use_kitty: false,
                use_iterm: false,
                width: max_width,
                height: max_height,
                ..Default::default()
            },
            // Try with just width specified
            viuer::Config {
                restore_cursor: true,
                transparent: true,
                truecolor: true,
                width: max_width,
                ..Default::default()
            },
            // Try with default settings (no sizing)
            viuer::Config {
                restore_cursor: true,
                transparent: true,
                truecolor: true,
                ..Default::default()
            },
        ];

        let mut displayed = false;
        let mut last_error_str = None;

        // First try using print_from_file (more reliable for different formats)
        self.log("Trying viuer::print_from_file");
        for (i, config) in configs.iter().enumerate() {
            self.log(&format!("Trying viuer config {} (print_from_file)", i + 1));
            self.log(&format!("Config details: width={:?}, height={:?}, use_kitty={}, use_iterm={}",
                config.width, config.height, config.use_kitty, config.use_iterm));

            // Move cursor to the preview area position
            self.log(&format!("Moving cursor to x={}, y={}", area.x, area.y));
            match std::io::stdout().execute(MoveTo(area.x, area.y)) {
                Ok(_) => {
                    match viuer::print_from_file(image_path, config) {
                        Ok(_) => {
                            displayed = true;
                            self.log(&format!("Success with config {} (print_from_file)", i + 1));
                            break;
                        }
                        Err(e) => {
                            self.log(&format!("Config {} failed (print_from_file): {}", i + 1, e));
                            last_error_str = Some(format!("{}", e));
                        }
                    }
                }
                Err(e) => {
                    self.log(&format!("Failed to move cursor: {}", e));
                    last_error_str = Some(format!("Cursor move failed: {}", e));
                }
            }
        }

        // If print_from_file didn't work, try loading with image crate first
        if !displayed {
            self.log("print_from_file failed, trying with image crate");
            match image::open(image_path) {
                Ok(img) => {
                    self.log(&format!("Image loaded: {}x{}", img.width(), img.height()));

                    for (i, config) in configs.iter().enumerate() {
                        self.log(&format!("Trying viuer config {} (image crate)", i + 1));

                        // Move cursor to the preview area position
                        match std::io::stdout().execute(MoveTo(area.x, area.y)) {
                            Ok(_) => {
                                match viuer::print(&img, config) {
                                    Ok(_) => {
                                        displayed = true;
                                        self.log(&format!("Success with config {} (image crate)", i + 1));
                                        break;
                                    }
                                    Err(e) => {
                                        self.log(&format!("Config {} failed (image crate): {}", i + 1, e));
                                        last_error_str = Some(format!("{}", e));
                                    }
                                }
                            }
                            Err(e) => {
                                self.log(&format!("Failed to move cursor: {}", e));
                                last_error_str = Some(format!("Cursor move failed: {}", e));
                            }
                        }
                    }
                }
                Err(e) => {
                    self.log(&format!("Failed to load image: {}", e));
                    last_error_str = Some(format!("Image load failed: {}", e));
                }
            }
        }

        if !displayed {
            return Err(format!("Failed to display image with all configs. Last error: {:?}", last_error_str));
        }

        *self.current_image.lock().unwrap() = Some(image_path.to_string());
        self.log("Image displayed successfully");
        Ok(())
    }

    /// Clear the currently displayed image
    pub fn clear_image(&mut self) -> Result<(), String> {
        // Clear the state - ratatui's next render will cover the image
        *self.current_image.lock().unwrap() = None;
        self.log("Image cleared");
        Ok(())
    }

    /// Cleanup resources
    pub fn cleanup(&mut self) {
        self.log("Cleaning up image previewer");
        let _ = self.clear_image();
    }
}

impl Default for ImagePreviewer {
    fn default() -> Self {
        Self::new()
    }
}
