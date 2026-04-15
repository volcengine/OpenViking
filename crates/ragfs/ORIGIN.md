# RAGFS Origin

This crate (RAGFS) is a Rust reimplementation of the AGFS project originally authored by [c44pt0r](https://github.com/c44pt0r).

## Source

RAGFS is based on the Go implementation of AGFS located at `third_party/agfs/` in this repository.

## License

The original AGFS project is open source. This Rust implementation maintains compatibility with and references the original AGFS license.

## Switch
export RAGFS_IMPL=auto (default to rust, with fallback to go)
export RAGFS_IMPL=rust
export RAGFS_IMPL=go