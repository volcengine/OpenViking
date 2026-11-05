mod config;
mod parsing;
mod preprocess;
mod privacy;
mod skills;
mod upload;

use std::ffi::OsString;

pub(crate) fn os_args(args: &[&str]) -> Vec<OsString> {
    args.iter().map(OsString::from).collect()
}
