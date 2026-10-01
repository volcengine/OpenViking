use std::env;

use pyo3_stub_gen::Result;

fn main() -> Result<()> {
    let manifest_dir = env!("CARGO_MANIFEST_DIR");
    env::set_var("CARGO_MANIFEST_DIR", manifest_dir);
    env::set_current_dir(manifest_dir)?;
    ragfs_python::stub_info()?.generate()
}
