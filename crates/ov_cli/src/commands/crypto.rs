//! Crypto commands for OpenViking CLI.
//!
//! This module provides commands for managing cryptographic keys,
//! including generating and initializing root keys.

use crate::error::{Error, Result};
use clap::Subcommand;
use dirs::home_dir;
use std::fs::OpenOptions;
use std::io::Write;
use std::path::PathBuf;

/// Crypto subcommands.
#[derive(Subcommand, Debug)]
pub enum CryptoCommands {
    /// Initialize a new root key.
    ///
    /// Generates a 32-byte random key and saves it to the specified file.
    /// If the file already exists, this command will fail.
    InitKey {
        /// Output file path for the root key.
        ///
        /// Defaults to `~/.openviking/master.key`.
        #[arg(long = "output-file", short = 'f')]
        output_file: Option<PathBuf>,
    },
}

/// Handle crypto commands.
pub async fn handle_crypto(cmd: CryptoCommands) -> Result<()> {
    match cmd {
        CryptoCommands::InitKey { output_file } => handle_init_key(output_file).await,
    }
}

/// Handle the init-key command.
async fn handle_init_key(output_file: Option<PathBuf>) -> Result<()> {
    let key_path = get_key_path(output_file)?;

    // Check if file already exists
    if key_path.exists() {
        return Err(Error::Client(format!(
            "Key file already exists at: {}\n\
             Use a different path or delete the existing file first.",
            key_path.display()
        )));
    }

    // Create parent directory if it doesn't exist
    if let Some(parent) = key_path.parent() {
        if !parent.exists() {
            std::fs::create_dir_all(parent)
                .map_err(|e| Error::Client(format!("Failed to create directory: {}", e)))?;
        }
    }

    // Generate 32-byte random key
    let mut key = vec![0u8; 32];
    getrandom::getrandom(&mut key)
        .map_err(|e| Error::Client(format!("Failed to generate random key: {}", e)))?;

    // Convert to hex string
    let hex_key = hex::encode(&key);

    // Write to file
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&key_path)
        .map_err(|e| Error::Client(format!("Failed to create key file: {}", e)))?;

    file.write_all(hex_key.as_bytes())
        .map_err(|e| Error::Client(format!("Failed to write to key file: {}", e)))?;

    // Set permissions to 0600 (owner read/write only) on Unix platforms
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut perms = file
            .metadata()
            .map_err(|e| Error::Client(format!("Failed to get file metadata: {}", e)))?
            .permissions();
        perms.set_mode(0o600);
        file.set_permissions(perms)
            .map_err(|e| Error::Client(format!("Failed to set file permissions: {}", e)))?;
    }

    // On Windows, permissions are handled differently
    #[cfg(windows)]
    {
        // Windows doesn't support Unix-style permissions, skip setting mode
    }

    println!("Successfully generated root key at: {}", key_path.display());
    println!("Key permissions set to 0600 (owner read/write only)");

    Ok(())
}

/// Get the key path, using the default if not provided.
fn get_key_path(output_file: Option<PathBuf>) -> Result<PathBuf> {
    match output_file {
        Some(path) => Ok(path),
        None => {
            let home = home_dir()
                .ok_or_else(|| Error::Client("Failed to determine home directory".to_string()))?;
            Ok(home.join(".openviking").join("master.key"))
        }
    }
}
