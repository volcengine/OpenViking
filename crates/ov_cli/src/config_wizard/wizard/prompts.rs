//! Terminal input, live prompt rendering, and raw-mode lifecycle.

use super::status::status_box_width;
use crate::error::Result;
use crate::i18n::{Language, copy};
use crate::terminal_ui::{rendered_line_rows, rendered_row_count};
use crate::theme;
use colored::Colorize;
use crossterm::ExecutableCommand;
use crossterm::cursor::{Hide, MoveToColumn, MoveUp, Show};
use crossterm::event::{self, Event, KeyCode, KeyEventKind, KeyModifiers};
use crossterm::terminal::{self, Clear, ClearType, disable_raw_mode, enable_raw_mode};
use std::io::{self, Write};
use unicode_width::UnicodeWidthChar;

const TEXT_INPUT_PROMPT: &str = "  > ";

fn nav_hint() -> &'static str {
    copy(
        Language::current(),
        "↑/↓ choose · Enter select · Esc back · Ctrl+C exit",
        "↑/↓ 选择 · Enter 确认 · Esc 返回 · Ctrl+C 退出",
    )
}

fn input_hint() -> &'static str {
    copy(
        Language::current(),
        "Enter continue · Esc back · Ctrl+C exit",
        "Enter 继续 · Esc 返回 · Ctrl+C 退出",
    )
}

pub(super) fn prompt_select<T: ToString>(
    ui: &mut LiveRegion,
    section: &str,
    prompt: &str,
    items: &[T],
    default: usize,
    helper_lines: &[String],
) -> Result<PromptResult<usize>> {
    let items: Vec<String> = items.iter().map(ToString::to_string).collect();
    let mut selected = default.min(items.len().saturating_sub(1));
    let raw = RawPrompt::enter(true)?;

    ui.render(&select_live_lines(
        section,
        prompt,
        &items,
        selected,
        helper_lines,
    ))?;

    loop {
        match event::read()? {
            Event::Key(key) if key.kind == KeyEventKind::Press => {
                match key.code {
                    KeyCode::Up => {
                        selected = if selected == 0 {
                            items.len().saturating_sub(1)
                        } else {
                            selected - 1
                        };
                    }
                    KeyCode::Down => selected = (selected + 1) % items.len().max(1),
                    KeyCode::Enter => {
                        drop(raw);
                        ui.clear()?;
                        return Ok(PromptResult::Value(selected));
                    }
                    KeyCode::Esc => {
                        drop(raw);
                        ui.clear()?;
                        return Ok(PromptResult::Back);
                    }
                    KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                        drop(raw);
                        ui.clear()?;
                        return Ok(PromptResult::Quit);
                    }
                    _ => continue,
                }
                ui.render(&select_live_lines(
                    section,
                    prompt,
                    &items,
                    selected,
                    helper_lines,
                ))?;
            }
            Event::Resize(_, _) => {
                ui.render(&select_live_lines(
                    section,
                    prompt,
                    &items,
                    selected,
                    helper_lines,
                ))?;
            }
            _ => {}
        }
    }
}

pub(super) fn prompt_text(
    ui: &mut LiveRegion,
    section: &str,
    prompt: &str,
    default: Option<&str>,
    value_label: Option<InputValueLabel>,
    allow_empty: bool,
    secret: bool,
    helper_lines: &[String],
) -> Result<PromptResult<String>> {
    let mut error: Option<String> = None;
    'attempt: loop {
        let mut value = String::new();
        let default_copy = default.unwrap_or_default();
        render_text_prompt(
            ui,
            TextPromptView {
                section,
                prompt,
                default,
                value_label,
                secret,
                helper_lines,
                error: error.as_deref(),
                value: &value,
            },
        )?;

        let raw = RawPrompt::enter(false)?;
        loop {
            match event::read()? {
                Event::Key(key) if key.kind == KeyEventKind::Press => match key.code {
                    KeyCode::Enter => {
                        let chosen = if value.trim().is_empty() {
                            default_copy.trim().to_string()
                        } else {
                            value.trim().to_string()
                        };
                        drop(raw);
                        if chosen.is_empty() && !allow_empty {
                            ui.clear()?;
                            error = Some(
                                copy(
                                    Language::current(),
                                    "Value cannot be empty.",
                                    "内容不能为空。",
                                )
                                .to_string(),
                            );
                            continue 'attempt;
                        }
                        ui.clear()?;
                        return Ok(PromptResult::Value(chosen));
                    }
                    KeyCode::Esc => {
                        drop(raw);
                        ui.clear()?;
                        return Ok(PromptResult::Back);
                    }
                    KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                        drop(raw);
                        ui.clear()?;
                        return Ok(PromptResult::Quit);
                    }
                    KeyCode::Backspace => {
                        if let Some(ch) = value.pop() {
                            raw_write(erase_sequence_for_char(ch, secret))?;
                            ui.set_input_prompt(input_prompt_with_value(&value, secret));
                            io::stdout().flush()?;
                        }
                    }
                    KeyCode::Char(ch) => {
                        if !key.modifiers.contains(KeyModifiers::CONTROL) {
                            value.push(ch);
                            if secret {
                                raw_write("*")?;
                            } else {
                                raw_write(ch.to_string())?;
                            }
                            ui.set_input_prompt(input_prompt_with_value(&value, secret));
                            io::stdout().flush()?;
                        }
                    }
                    _ => {}
                },
                Event::Key(_) => {}
                Event::Resize(_, _) => {
                    render_text_prompt(
                        ui,
                        TextPromptView {
                            section,
                            prompt,
                            default,
                            value_label,
                            secret,
                            helper_lines,
                            error: error.as_deref(),
                            value: &value,
                        },
                    )?;
                }
                _ => {}
            }
        }
    }
}

struct TextPromptView<'a> {
    section: &'a str,
    prompt: &'a str,
    default: Option<&'a str>,
    value_label: Option<InputValueLabel>,
    secret: bool,
    helper_lines: &'a [String],
    error: Option<&'a str>,
    value: &'a str,
}

fn render_text_prompt(ui: &mut LiveRegion, view: TextPromptView<'_>) -> Result<()> {
    ui.render_input(
        &input_live_lines(
            view.section,
            view.prompt,
            view.default,
            view.value_label,
            view.secret,
            view.helper_lines,
            view.error,
        ),
        TEXT_INPUT_PROMPT,
    )?;
    let input_prompt = input_prompt_with_value(view.value, view.secret);
    if input_prompt.len() > TEXT_INPUT_PROMPT.len() {
        if view.secret {
            raw_write("*".repeat(view.value.chars().count()))?;
        } else {
            raw_write(view.value)?;
        }
    }
    ui.set_input_prompt(input_prompt);
    io::stdout().flush()?;
    Ok(())
}

fn input_prompt_with_value(value: &str, secret: bool) -> String {
    if value.is_empty() {
        return TEXT_INPUT_PROMPT.to_string();
    }
    let rendered_value = if secret {
        "*".repeat(value.chars().count())
    } else {
        value.to_string()
    };
    format!("{TEXT_INPUT_PROMPT}{rendered_value}")
}

fn erase_sequence_for_char(ch: char, secret: bool) -> String {
    let width = if secret {
        1
    } else {
        UnicodeWidthChar::width(ch).unwrap_or(1).max(1)
    };
    "\x08 \x08".repeat(width)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum InputValueLabel {
    Default,
    Current,
}

impl InputValueLabel {
    fn text(self) -> &'static str {
        match (self, Language::current()) {
            (Self::Default, Language::En) => "Default:",
            (Self::Default, Language::ZhCn) => "默认值：",
            (Self::Current, Language::En) => "Current:",
            (Self::Current, Language::ZhCn) => "当前值：",
        }
    }
}

pub(super) fn confirm(
    ui: &mut LiveRegion,
    section: &str,
    prompt: &str,
    default: bool,
) -> Result<PromptResult<bool>> {
    let items = match Language::current() {
        Language::En => ["Yes", "No"],
        Language::ZhCn => ["是", "否"],
    };
    match prompt_select(ui, section, prompt, &items, usize::from(!default), &[])? {
        PromptResult::Value(0) => Ok(PromptResult::Value(true)),
        PromptResult::Value(1) => Ok(PromptResult::Value(false)),
        PromptResult::Back => Ok(PromptResult::Back),
        PromptResult::Quit => Ok(PromptResult::Quit),
        PromptResult::Value(_) => unreachable!("selection is constrained by confirm list"),
    }
}

#[derive(Default)]
pub(super) struct LiveRegion {
    lines: Vec<String>,
    input_prompt: Option<String>,
    rows_drawn: usize,
    cursor_on_last_line: bool,
}

impl LiveRegion {
    pub(super) fn render(&mut self, lines: &[String]) -> Result<()> {
        self.clear()?;
        for line in lines {
            raw_line(line)?;
        }
        io::stdout().flush()?;
        self.lines = lines.to_vec();
        self.input_prompt = None;
        self.rows_drawn = rendered_live_region_rows(
            &self.lines,
            self.input_prompt.as_deref(),
            live_region_columns(),
        );
        self.cursor_on_last_line = false;
        Ok(())
    }

    fn render_input(&mut self, lines: &[String], prompt: &str) -> Result<()> {
        self.clear()?;
        for line in lines {
            raw_line(line)?;
        }
        raw_write(prompt)?;
        io::stdout().flush()?;
        self.lines = lines.to_vec();
        self.input_prompt = Some(prompt.to_string());
        self.rows_drawn = rendered_live_region_rows(
            &self.lines,
            self.input_prompt.as_deref(),
            live_region_columns(),
        );
        self.cursor_on_last_line = true;
        Ok(())
    }

    pub(super) fn clear(&mut self) -> Result<()> {
        clear_live_region(
            self.rows_to_clear(live_region_columns()),
            self.cursor_on_last_line,
        )?;
        self.lines.clear();
        self.input_prompt = None;
        self.rows_drawn = 0;
        self.cursor_on_last_line = false;
        Ok(())
    }

    fn set_input_prompt(&mut self, prompt: String) {
        self.input_prompt = Some(prompt);
        self.rows_drawn = self.rows_drawn.max(rendered_live_region_rows(
            &self.lines,
            self.input_prompt.as_deref(),
            live_region_columns(),
        ));
    }

    fn rows_to_clear(&self, columns: usize) -> usize {
        self.rows_drawn.max(rendered_live_region_rows(
            &self.lines,
            self.input_prompt.as_deref(),
            columns,
        ))
    }
}

pub(super) fn live_region_columns() -> usize {
    terminal::size()
        .map(|(columns, _)| usize::from(columns).saturating_sub(1).max(1))
        .unwrap_or_else(|_| status_box_width())
}

fn rendered_live_region_rows(
    lines: &[String],
    input_prompt: Option<&str>,
    columns: usize,
) -> usize {
    rendered_row_count(lines, columns)
        + input_prompt
            .map(|prompt| rendered_line_rows(prompt, columns))
            .unwrap_or(0)
}

pub(crate) fn select_live_lines<T: ToString>(
    section: &str,
    prompt: &str,
    items: &[T],
    selected: usize,
    helper_lines: &[String],
) -> Vec<String> {
    let mut lines = vec![
        format!(
            "{} {}",
            theme::section_marker("◆").bold(),
            theme::strong(section)
        ),
        String::new(),
        format!("{} {}", theme::prompt("?").bold(), theme::strong(prompt)),
        format!("  {}", theme::muted(nav_hint())),
    ];

    if !helper_lines.is_empty() {
        lines.push(String::new());
        lines.extend(helper_lines.iter().map(|line| format!("  {line}")));
    }

    lines.push(String::new());
    lines.extend(items.iter().enumerate().map(|(index, item)| {
        let item = item.to_string();
        if index == selected {
            theme::selection(format!("  › {item}")).bold().to_string()
        } else {
            format!("    {}", theme::body(item))
        }
    }));
    lines
}

pub(super) fn input_live_lines(
    section: &str,
    prompt: &str,
    default: Option<&str>,
    value_label: Option<InputValueLabel>,
    secret: bool,
    helper_lines: &[String],
    error: Option<&str>,
) -> Vec<String> {
    let mut lines = vec![
        format!(
            "{} {}",
            theme::section_marker("◆").bold(),
            theme::strong(section)
        ),
        String::new(),
        format!("{} {}", theme::prompt("?").bold(), theme::strong(prompt)),
        format!("  {}", theme::muted(input_hint())),
    ];

    if !helper_lines.is_empty() {
        lines.push(String::new());
        lines.extend(helper_lines.iter().map(|line| format!("  {line}")));
    }

    if let (Some(default_value), Some(value_label)) = (default, value_label) {
        let rendered_default = if secret {
            if default_value.trim().is_empty() {
                theme::muted("(empty)").to_string()
            } else {
                theme::muted("(existing value)").to_string()
            }
        } else {
            theme::muted(default_value).to_string()
        };
        lines.push(format!(
            "  {} {}",
            theme::muted(value_label.text()),
            rendered_default
        ));
    }

    if let Some(error) = error {
        lines.push(format!("  {}", theme::error(error)));
    }

    lines.push(String::new());
    lines
}

pub(super) fn status_live_lines(section: &str, status: &str) -> Vec<String> {
    vec![
        format!(
            "{} {}",
            theme::section_marker("◆").bold(),
            theme::strong(section)
        ),
        String::new(),
        format!("{} {}", theme::command("…").bold(), theme::strong(status)),
    ]
}

pub(super) fn clear_live_region(count: usize, cursor_on_last_line: bool) -> io::Result<()> {
    let mut stdout = io::stdout();

    if count == 0 {
        return Ok(());
    }

    if cursor_on_last_line {
        stdout.execute(MoveToColumn(0))?;
        stdout.execute(Clear(ClearType::CurrentLine))?;
        for _ in 1..count {
            stdout.execute(MoveUp(1))?;
            stdout.execute(MoveToColumn(0))?;
            stdout.execute(Clear(ClearType::CurrentLine))?;
        }
    } else {
        for _ in 0..count {
            stdout.execute(MoveToColumn(0))?;
            stdout.execute(MoveUp(1))?;
            stdout.execute(MoveToColumn(0))?;
            stdout.execute(Clear(ClearType::CurrentLine))?;
        }
    }

    stdout.execute(MoveToColumn(0))?;
    Ok(())
}

fn raw_line(text: impl AsRef<str>) -> io::Result<()> {
    let mut stdout = io::stdout();
    stdout.execute(MoveToColumn(0))?;
    write!(stdout, "{}\r\n", text.as_ref())
}

fn raw_write(text: impl AsRef<str>) -> io::Result<()> {
    write!(io::stdout(), "{}", text.as_ref())
}

pub(super) fn print_cancelled(ui: &mut LiveRegion) -> Result<()> {
    ui.clear()?;
    println!();
    println!(
        "{}",
        theme::warning(copy(
            Language::current(),
            "Cancelled. No partial configuration was written.",
            "已取消。未写入任何未完成配置。",
        ))
    );
    Ok(())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum PromptResult<T> {
    Value(T),
    Back,
    Quit,
}

struct RawPrompt {
    hide_cursor: bool,
}

impl RawPrompt {
    fn enter(hide_cursor: bool) -> Result<Self> {
        enable_raw_mode()?;
        if hide_cursor {
            io::stdout().execute(Hide)?;
        }
        Ok(Self { hide_cursor })
    }
}

impl Drop for RawPrompt {
    fn drop(&mut self) {
        let _ = disable_raw_mode();
        if self.hide_cursor {
            let _ = io::stdout().execute(Show);
        }
    }
}

#[cfg(test)]
#[path = "tests/prompts.rs"]
mod tests;
