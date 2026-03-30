//! Utility functions used across the crate.

/// Safely truncate a string at a UTF-8 character boundary
pub fn truncate_utf8(s: &str, max_bytes: usize) -> &str {
    if s.len() <= max_bytes {
        return s;
    }

    let mut boundary = max_bytes;
    while boundary > 0 && !s.is_char_boundary(boundary) {
        boundary -= 1;
    }

    if boundary == 0 { "" } else { &s[..boundary] }
}
