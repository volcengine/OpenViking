//! Command-local upload options and legacy option compatibility.

use crate::cli::Commands;
use clap::Args;

#[derive(Args, Debug, Clone, Copy, Default)]
pub(crate) struct UploadCliOptions {
    /// Show local file upload progress (overrides config file)
    #[arg(
        long,
        conflicts_with = "no_progress",
        help_heading = "Advanced options"
    )]
    pub(crate) progress: bool,

    /// Disable local file upload progress (overrides config file)
    #[arg(
        long = "no-progress",
        conflicts_with = "progress",
        help_heading = "Advanced options"
    )]
    pub(crate) no_progress: bool,

    /// Print extra diagnostics during local file upload
    #[arg(short, long, help_heading = "Advanced options")]
    pub(crate) verbose: bool,
}

impl UploadCliOptions {
    pub(crate) fn is_set(self) -> bool {
        self.progress || self.no_progress || self.verbose
    }

    pub(crate) fn merged_with_legacy(self, legacy: Self) -> Self {
        Self {
            progress: self.progress || (!self.no_progress && legacy.progress),
            no_progress: self.no_progress || (!self.progress && legacy.no_progress),
            verbose: self.verbose || legacy.verbose,
        }
    }

    pub(crate) fn show_progress_override(self) -> Option<bool> {
        if self.progress {
            Some(true)
        } else if self.no_progress {
            Some(false)
        } else {
            None
        }
    }

    pub(crate) fn verbose_override(self) -> Option<bool> {
        if self.verbose { Some(true) } else { None }
    }
}

pub(crate) fn legacy_upload_option_error(
    options: UploadCliOptions,
    command: &Commands,
) -> Option<&'static str> {
    if options.is_set() && !command.supports_upload_options() {
        Some(
            "--progress, --no-progress, and --verbose are only supported for add-resource, add-skill, and skills add.",
        )
    } else {
        None
    }
}
