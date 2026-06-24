use std::env;
use std::path::PathBuf;

fn main() {
    println!("cargo:rerun-if-changed=native/yuanrong_bridge.cpp");
    println!("cargo:rerun-if-changed=native/yuanrong_bridge.h");
    println!("cargo:rerun-if-env-changed=YUANRONG_SDK_INCLUDE");
    println!("cargo:rerun-if-env-changed=YUANRONG_SDK_LIB_DIR");
    println!("cargo:rerun-if-env-changed=YUANRONG_SDK_LIB_NAME");

    if env::var_os("CARGO_FEATURE_NATIVE").is_none() {
        return;
    }

    let include = required_path("YUANRONG_SDK_INCLUDE");
    let lib_dir = required_path("YUANRONG_SDK_LIB_DIR");
    let lib_name = env::var("YUANRONG_SDK_LIB_NAME").unwrap_or_else(|_| "datasystem".into());

    cc::Build::new()
        .cpp(true)
        .std("c++17")
        .warnings(true)
        .include("native")
        .include(include)
        .file("native/yuanrong_bridge.cpp")
        .compile("openviking_yuanrong_bridge");

    println!("cargo:rustc-link-search=native={}", lib_dir.display());
    println!("cargo:rustc-link-lib=dylib={lib_name}");
}

fn required_path(name: &str) -> PathBuf {
    let value = env::var_os(name).unwrap_or_else(|| {
        panic!("{name} must point to the Yuanrong SDK directory when feature `native` is enabled")
    });
    let path = PathBuf::from(value);
    if !path.exists() {
        panic!("{name} does not exist: {}", path.display());
    }
    path
}
