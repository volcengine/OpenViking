use std::process::{Child, Stdio};
use std::io::Write;
use std::sync::{Arc, Mutex};
use serde_json;

/// Image previewer using ueberzugpp
pub struct ImagePreviewer {
    ueberzugpp_process: Option<Child>,
    socket_path: Option<String>,
    current_image: Arc<Mutex<Option<String>>>,
    preview_area: Arc<Mutex<Option<PreviewArea>>>,
}

/// Preview area coordinates (in terminal cells)
#[derive(Debug, Clone, Copy)]
pub struct PreviewArea {
    pub x: u16,
    pub y: u16,
    pub width: u16,
    pub height: u16,
}

impl ImagePreviewer {
    /// Create a new image previewer
    pub fn new() -> Self {
        Self {
            ueberzugpp_process: None,
            socket_path: None,
            current_image: Arc::new(Mutex::new(None)),
            preview_area: Arc::new(Mutex::new(None)),
        }
    }

    /// Check if ueberzugpp is available
    pub fn is_available() -> bool {
        #[cfg(target_os = "macos")]
        {
            std::process::Command::new("brew")
                .args(["list", "ueberzugpp"])
                .output()
                .map(|o| o.status.success())
                .unwrap_or(false)
        }
        #[cfg(target_os = "linux")]
        {
            std::process::Command::new("which")
                .arg("ueberzugpp")
                .output()
                .map(|o| o.status.success())
                .unwrap_or(false)
        }
        #[cfg(not(any(target_os = "macos", target_os = "linux")))]
        {
            false
        }
    }

    /// Get installation instructions
    pub fn installation_instructions() -> &'static str {
        #[cfg(target_os = "macos")]
        {
            "To enable image preview, install ueberzugpp:\n  brew install jstkdng/programs/ueberzugpp"
        }
        #[cfg(target_os = "linux")]
        {
            "To enable image preview, install ueberzugpp:\n  See https://github.com/jstkdng/ueberzugpp for installation instructions"
        }
        #[cfg(not(any(target_os = "macos", target_os = "linux")))]
        {
            "Image preview is only available on macOS and Linux"
        }
    }

    /// Initialize the image previewer
    pub fn init(&mut self) -> Result<(), String> {
        if !Self::is_available() {
            return Err("ueberzugpp is not installed".to_string());
        }

        // Start ueberzugpp layer process
        let child = std::process::Command::new("ueberzugpp")
            .args(["layer", "--silent"])
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| format!("Failed to start ueberzugpp: {}", e))?;

        self.ueberzugpp_process = Some(child);

        // Wait a bit for the process to create the socket
        std::thread::sleep(std::time::Duration::from_millis(100));

        Ok(())
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
        let child = match &mut self.ueberzugpp_process {
            Some(child) => child,
            None => return Err("ueberzugpp not initialized".to_string()),
        };

        let stdin = match child.stdin.as_mut() {
            Some(stdin) => stdin,
            None => return Err("Failed to get ueberzugpp stdin".to_string()),
        };

        // Check if file exists and is readable
        let path = std::path::Path::new(image_path);
        if !path.exists() {
            return Err(format!("Image file not found: {}", image_path));
        }
        if !path.is_file() {
            return Err(format!("Path is not a file: {}", image_path));
        }

        // Build the ueberzugpp command
        let cmd = serde_json::json!({
            "action": "add",
            "identifier": "ov_preview",
            "x": area.x,
            "y": area.y,
            "width": area.width,
            "height": area.height,
            "path": image_path,
            "scaler": "fit_contain",
        });

        let cmd_str = serde_json::to_string(&cmd).map_err(|e| format!("Failed to serialize command: {}", e))?;

        writeln!(stdin, "{}", cmd_str).map_err(|e| format!("Failed to send command to ueberzugpp: {}", e))?;
        stdin.flush().map_err(|e| format!("Failed to flush ueberzugpp stdin: {}", e))?;

        // Check if process is still alive
        match child.try_wait() {
            Ok(None) => {
                // Process is still running
            }
            Ok(Some(status)) => {
                return Err(format!("ueberzugpp exited unexpectedly with status: {}", status));
            }
            Err(e) => {
                return Err(format!("Failed to check ueberzugpp process: {}", e));
            }
        }

        *self.current_image.lock().unwrap() = Some(image_path.to_string());

        Ok(())
    }

    /// Clear the currently displayed image
    pub fn clear_image(&mut self) -> Result<(), String> {
        let child = match &mut self.ueberzugpp_process {
            Some(child) => child,
            None => return Ok(()),
        };

        let stdin = match child.stdin.as_mut() {
            Some(stdin) => stdin,
            None => return Ok(()),
        };

        let cmd = serde_json::json!({
            "action": "remove",
            "identifier": "ov_preview",
        });

        let cmd_str = serde_json::to_string(&cmd).unwrap_or_default();
        let _ = writeln!(stdin, "{}", cmd_str);
        let _ = stdin.flush();

        *self.current_image.lock().unwrap() = None;

        Ok(())
    }

    /// Set the preview area
    pub fn set_preview_area(&mut self, area: PreviewArea) {
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

    /// Cleanup resources
    pub fn cleanup(&mut self) {
        let _ = self.clear_image();
        if let Some(mut child) = self.ueberzugpp_process.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

impl Drop for ImagePreviewer {
    fn drop(&mut self) {
        self.cleanup();
    }
}

/// Check if a file is an image based on extension
pub fn is_image_file(filename: &str) -> bool {
    let lower = filename.to_lowercase();
    lower.ends_with(".png")
        || lower.ends_with(".jpg")
        || lower.ends_with(".jpeg")
        || lower.ends_with(".gif")
        || lower.ends_with(".webp")
        || lower.ends_with(".svg")
        || lower.ends_with(".bmp")
        || lower.ends_with(".ico")
}
