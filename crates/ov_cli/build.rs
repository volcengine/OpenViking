use std::env;

fn main() {
    println!("cargo:rerun-if-env-changed=OPENVIKING_VERSION");
    println!("cargo:rerun-if-env-changed=SETUPTOOLS_SCM_PRETEND_VERSION_FOR_OPENVIKING");

    let version = env::var("OPENVIKING_VERSION")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .or_else(|| {
            env::var("SETUPTOOLS_SCM_PRETEND_VERSION_FOR_OPENVIKING")
                .ok()
                .filter(|value| !value.trim().is_empty())
        })
        .unwrap_or_else(|| env::var("CARGO_PKG_VERSION").expect("CARGO_PKG_VERSION must be set"));

    println!("cargo:rustc-env=OPENVIKING_CLI_VERSION={version}");
}
