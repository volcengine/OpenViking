use crate::theme;
use serde_json::Value;
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

pub(crate) fn append_profile_lines(profile: Option<&Value>, lines: &mut Vec<String>) {
    let Some(profile) = profile else {
        return;
    };

    lines.push(String::new());
    lines.push(theme::heading("profile").to_string());

    if let Some(items) = profile.as_array() {
        for item in items {
            if let Some(text) = item.as_str() {
                lines.push(theme::body(text).to_string());
            } else {
                lines.push(theme::body(item.to_string()).to_string());
            }
        }
        return;
    }

    if let Some(text) = profile.as_str() {
        lines.push(theme::body(text).to_string());
    } else {
        lines.push(theme::body(profile.to_string()).to_string());
    }
}

pub(crate) fn wrap_display_text(input: &str, width: usize, max_lines: usize) -> Vec<String> {
    if width == 0 || max_lines == 0 {
        return Vec::new();
    }

    let normalized = input.split_whitespace().collect::<Vec<_>>().join(" ");
    if normalized.is_empty() {
        return Vec::new();
    }

    let mut lines = Vec::new();
    let mut current = String::new();
    let mut truncated = false;

    for word in normalized.split(' ') {
        if word.is_empty() {
            continue;
        }

        if append_word_to_line(&mut current, word, width) {
            continue;
        }

        if !current.is_empty() {
            lines.push(std::mem::take(&mut current));

            if lines.len() == max_lines {
                truncated = true;
                break;
            }

            if append_word_to_line(&mut current, word, width) {
                continue;
            }
        }

        let mut segment = String::new();
        let mut segment_width = 0;
        for ch in word.chars() {
            let char_width = UnicodeWidthChar::width(ch).unwrap_or(0);
            if !segment.is_empty() && segment_width + char_width > width {
                lines.push(segment);
                segment = String::new();
                segment_width = 0;

                if lines.len() == max_lines {
                    truncated = true;
                    break;
                }
            }

            segment.push(ch);
            segment_width += char_width;
        }

        if truncated {
            break;
        }

        current = segment;
    }

    if !truncated && !current.is_empty() {
        lines.push(current);
    }

    if lines.len() > max_lines {
        lines.truncate(max_lines);
        truncated = true;
    }

    if truncated {
        if let Some(last) = lines.last_mut() {
            *last = with_ascii_ellipsis(last, width);
        }
    }

    lines
}

fn append_word_to_line(line: &mut String, word: &str, width: usize) -> bool {
    let separator_width = usize::from(!line.is_empty());
    if line.width() + separator_width + word.width() > width {
        return false;
    }

    if !line.is_empty() {
        line.push(' ');
    }
    line.push_str(word);
    true
}

pub(crate) fn with_ascii_ellipsis(line: &str, width: usize) -> String {
    const ELLIPSIS: &str = "...";
    let ellipsis_width = ELLIPSIS.width();
    if line.width() + ellipsis_width <= width {
        return format!("{line}{ELLIPSIS}");
    }

    let target_width = width.saturating_sub(ellipsis_width);
    let mut output = String::new();
    let mut output_width = 0;

    for ch in line.chars() {
        let char_width = UnicodeWidthChar::width(ch).unwrap_or(0);
        if output_width + char_width > target_width {
            break;
        }
        output.push(ch);
        output_width += char_width;
    }

    format!("{}{ELLIPSIS}", output.trim_end())
}
