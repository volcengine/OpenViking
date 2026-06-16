use std::io;

use crossterm::{
    cursor, execute,
    terminal::{Clear, ClearType},
};
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

use crate::error::Result;

pub(crate) fn display_width(value: &str) -> usize {
    UnicodeWidthStr::width(value)
}

pub(crate) fn visible_display_width(value: &str) -> usize {
    let mut width = 0usize;
    let mut chars = value.chars().peekable();

    while let Some(ch) = chars.next() {
        if ch == '\u{1b}' && chars.peek() == Some(&'[') {
            chars.next();
            for next in chars.by_ref() {
                if next.is_ascii_alphabetic() {
                    break;
                }
            }
            continue;
        }

        width += UnicodeWidthChar::width(ch).unwrap_or(0);
    }

    width
}

pub(crate) fn truncate_to_display_width(value: &str, width: usize) -> String {
    if display_width(value) <= width {
        return value.to_string();
    }

    if width <= 3 {
        return ".".repeat(width);
    }

    let target = width - 3;
    let mut truncated = String::new();
    let mut used = 0usize;

    for ch in value.chars() {
        let ch_width = UnicodeWidthChar::width(ch).unwrap_or(0);
        if used + ch_width > target {
            break;
        }
        truncated.push(ch);
        used += ch_width;
    }

    truncated.push_str("...");
    truncated
}

pub(crate) fn pad_to_display_width(value: &str, width: usize) -> String {
    format!(
        "{}{}",
        value,
        " ".repeat(width.saturating_sub(display_width(value)))
    )
}

pub(crate) fn fit_to_display_width(value: &str, width: usize) -> String {
    pad_to_display_width(&truncate_to_display_width(value, width), width)
}

pub(crate) fn terminal_width(columns: usize, default_width: usize) -> usize {
    let available = columns.saturating_sub(1).max(4);
    available.min(default_width)
}

pub(crate) fn rendered_line_rows(text: &str, columns: usize) -> usize {
    let columns = columns.max(1);
    let width = visible_display_width(text);
    width.max(1).div_ceil(columns)
}

pub(crate) fn rendered_row_count(lines: &[String], columns: usize) -> usize {
    lines
        .iter()
        .map(|line| rendered_line_rows(line, columns))
        .sum()
}

pub(crate) fn live_select_block(lines: &[String]) -> String {
    if lines.is_empty() {
        return String::new();
    }

    let mut rendered = lines.join("\r\n");
    rendered.push_str("\r\n");
    rendered
}

pub(crate) fn clear_rendered_lines(lines: usize) -> Result<()> {
    if lines == 0 {
        return Ok(());
    }
    let mut stdout = io::stdout();
    execute!(
        stdout,
        cursor::MoveUp(lines as u16),
        cursor::MoveToColumn(0)
    )?;
    for line in 0..lines {
        execute!(
            stdout,
            cursor::MoveToColumn(0),
            Clear(ClearType::CurrentLine)
        )?;
        if line + 1 < lines {
            execute!(stdout, cursor::MoveDown(1))?;
        }
    }
    execute!(
        stdout,
        cursor::MoveUp(lines.saturating_sub(1) as u16),
        cursor::MoveToColumn(0)
    )?;
    Ok(())
}

#[derive(Default)]
pub(crate) struct RenderedRegion {
    lines: Vec<String>,
    rows_drawn: usize,
}

impl RenderedRegion {
    pub(crate) fn from_lines(lines: &[String], columns: usize) -> Self {
        Self {
            lines: lines.to_vec(),
            rows_drawn: rendered_row_count(lines, columns),
        }
    }

    pub(crate) fn rows_to_clear(&self, columns: usize) -> usize {
        self.rows_drawn
            .max(rendered_row_count(&self.lines, columns))
    }
}

#[cfg(test)]
mod tests {
    use super::{
        RenderedRegion, display_width, fit_to_display_width, live_select_block, rendered_row_count,
        terminal_width, truncate_to_display_width, visible_display_width,
    };

    #[test]
    fn visible_display_width_ignores_ansi_and_counts_cjk_columns() {
        assert_eq!(visible_display_width("\u{1b}[32mOpen\u{1b}[0m"), 4);
        assert_eq!(visible_display_width("配置"), 4);
    }

    #[test]
    fn truncate_and_fit_respect_display_width() {
        assert_eq!(truncate_to_display_width("abcdef", 4), "a...");
        assert_eq!(truncate_to_display_width("abcdef", 2), "..");
        let fitted = fit_to_display_width("配置abc", 6);

        assert_eq!(display_width(&fitted), 6);
        assert_eq!(fitted, "配... ");
    }

    #[test]
    fn rendered_row_count_accounts_for_wrapping_and_empty_lines() {
        let lines = vec!["\u{1b}[31m12345678901\u{1b}[0m".to_string(), String::new()];

        assert_eq!(rendered_row_count(&lines, 10), 3);
    }

    #[test]
    fn terminal_width_leaves_room_for_prompt_edge() {
        assert_eq!(terminal_width(3, 72), 4);
        assert_eq!(terminal_width(80, 72), 72);
        assert_eq!(terminal_width(40, 72), 39);
    }

    #[test]
    fn rendered_region_clears_original_or_current_wrapped_rows() {
        let lines = vec!["x".repeat(90)];
        let region = RenderedRegion::from_lines(&lines, 90);

        assert_eq!(region.rows_to_clear(30), 3);
        assert_eq!(region.rows_to_clear(120), 1);
    }

    #[test]
    fn live_select_block_uses_crlf_for_raw_mode_rows() {
        let lines = vec!["Choose config".to_string(), "  > local".to_string()];

        assert_eq!(live_select_block(&lines), "Choose config\r\n  > local\r\n");
    }
}
