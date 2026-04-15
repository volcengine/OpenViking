use ratatui::{
    Frame,
    layout::{Constraint, Direction, Layout},
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Borders, List, ListItem, ListState, Paragraph, Wrap},
};

use super::app::{App, Panel};

pub fn render(frame: &mut Frame, app: &App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(1), Constraint::Length(1)])
        .split(frame.area());

    let main_area = chunks[0];
    let status_area = chunks[1];

    let panels = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(35), Constraint::Percentage(65)])
        .split(main_area);

    render_tree(frame, app, panels[0]);
    render_content(frame, app, panels[1]);
    render_status_bar(frame, app, status_area);
}

fn render_tree(frame: &mut Frame, app: &App, area: ratatui::layout::Rect) {
    let focused = app.focus == Panel::Tree;
    let border_color = if focused {
        Color::Cyan
    } else {
        Color::DarkGray
    };

    let block = Block::default()
        .title(" Explorer ")
        .borders(Borders::ALL)
        .border_style(Style::default().fg(border_color));

    let inner = block.inner(area);
    frame.render_widget(block, area);

    if app.tree.visible.is_empty() {
        let empty = Paragraph::new("(empty)").style(Style::default().fg(Color::DarkGray));
        frame.render_widget(empty, inner);
        return;
    }

    let viewport_height = inner.height as usize;

    // Build list items with scroll offset
    let items: Vec<ListItem> = app
        .tree
        .visible
        .iter()
        .skip(app.tree.scroll_offset)
        .take(viewport_height)
        .map(|row| {
            let indent = "  ".repeat(row.depth);
            let icon = if row.is_dir {
                if row.expanded { "▾ " } else { "▸ " }
            } else {
                "  "
            };

            let style = if row.is_dir {
                Style::default()
                    .fg(Color::Blue)
                    .add_modifier(Modifier::BOLD)
            } else {
                Style::default()
            };

            let line = Line::from(vec![
                Span::raw(indent),
                Span::styled(icon, style),
                Span::styled(&row.name, style),
            ]);
            ListItem::new(line)
        })
        .collect();

    // Adjust cursor relative to scroll offset for ListState
    let adjusted_cursor = app.tree.cursor.saturating_sub(app.tree.scroll_offset);
    let mut list_state = ListState::default().with_selected(Some(adjusted_cursor));

    let list = List::new(items).highlight_style(
        Style::default()
            .bg(if focused {
                Color::DarkGray
            } else {
                Color::Reset
            })
            .fg(Color::White)
            .add_modifier(Modifier::BOLD),
    );

    frame.render_stateful_widget(list, inner, &mut list_state);
}

fn render_content(frame: &mut Frame, app: &App, area: ratatui::layout::Rect) {
    if app.showing_vector_records {
        render_vector_records(frame, app, area);
        return;
    }

    let focused = app.focus == Panel::Content;
    let border_color = if focused {
        Color::Cyan
    } else {
        Color::DarkGray
    };

    let title = if app.content_title.is_empty() {
        " Content ".to_string()
    } else {
        format!(" {} ", app.content_title)
    };

    let block = Block::default()
        .title(title)
        .borders(Borders::ALL)
        .border_style(Style::default().fg(border_color));

    let paragraph = Paragraph::new(app.content.as_str())
        .block(block)
        .wrap(Wrap { trim: false })
        .scroll((app.content_scroll, 0));

    frame.render_widget(paragraph, area);
}

fn render_vector_records(frame: &mut Frame, app: &App, area: ratatui::layout::Rect) {
    let focused = app.focus == Panel::Content;
    let border_color = if focused {
        Color::Cyan
    } else {
        Color::DarkGray
    };

    let title = if let Some(total) = app.vector_state.total_count {
        format!(
            " Vector Records for {} ({}/{}, total: {}) ",
            app.current_uri,
            app.vector_state.cursor + 1,
            app.vector_state.records.len(),
            total
        )
    } else {
        format!(
            " Vector Records for {} ({}/{}) ",
            app.current_uri,
            app.vector_state.cursor + 1,
            app.vector_state.records.len()
        )
    };

    let block = Block::default()
        .title(title)
        .borders(Borders::ALL)
        .border_style(Style::default().fg(border_color));

    let inner = block.inner(area);
    frame.render_widget(block, area);

    if app.vector_state.records.is_empty() {
        let empty =
            Paragraph::new("(no vector records)").style(Style::default().fg(Color::DarkGray));
        frame.render_widget(empty, inner);
        return;
    }

    let viewport_height = inner.height as usize;

    let items: Vec<ListItem> = app
        .vector_state
        .records
        .iter()
        .skip(app.vector_state.scroll_offset)
        .take(viewport_height)
        .map(|record| {
            let context_type = record
                .get("context_type")
                .and_then(|v| v.as_str())
                .unwrap_or("(no type)");
            let level_str = record
                .get("level")
                .and_then(|v| v.as_i64())
                .map(|l| l.to_string())
                .unwrap_or("(no level)".to_string());
            let id = record
                .get("id")
                .and_then(|v| v.as_str())
                .unwrap_or("(no id)");
            let uri = record
                .get("uri")
                .and_then(|v| v.as_str())
                .unwrap_or("(no uri)");
            let line = Line::from(vec![
                Span::styled(
                    context_type,
                    Style::default()
                        .fg(Color::Green)
                        .add_modifier(Modifier::BOLD),
                ),
                Span::raw(" "),
                Span::styled(
                    level_str,
                    Style::default()
                        .fg(Color::Magenta)
                        .add_modifier(Modifier::BOLD),
                ),
                Span::raw(" "),
                Span::styled(
                    id,
                    Style::default()
                        .fg(Color::Yellow)
                        .add_modifier(Modifier::BOLD),
                ),
                Span::raw(" "),
                Span::raw(uri),
            ]);
            ListItem::new(line)
        })
        .collect();

    let adjusted_cursor = app
        .vector_state
        .cursor
        .saturating_sub(app.vector_state.scroll_offset);
    let mut list_state = ListState::default().with_selected(Some(adjusted_cursor));

    let list = List::new(items).highlight_style(
        Style::default()
            .bg(if focused {
                Color::DarkGray
            } else {
                Color::Reset
            })
            .fg(Color::White)
            .add_modifier(Modifier::BOLD),
    );

    frame.render_stateful_widget(list, inner, &mut list_state);
}

fn render_status_bar(frame: &mut Frame, app: &App, area: ratatui::layout::Rect) {

    // Show error message only if present
    if !app.error_message.is_empty() {
        let bar = Paragraph::new(app.error_message.clone())
            .style(Style::default().bg(Color::Red).fg(Color::White));
        frame.render_widget(bar, area);
        return;
    }

    // Show confirmation message if present
    if let Some((message, _)) = &app.confirmation {
        let bar = Paragraph::new(message.clone())
            .style(Style::default().bg(Color::Green).fg(Color::White));
        frame.render_widget(bar, area);
        return;
    }

    // Regular status bar with hints
    let mut hints = vec![
        Span::styled(
            " q",
            Style::default()
                .fg(Color::Yellow)
                .add_modifier(Modifier::BOLD),
        ),
        Span::raw(":quit  "),
        Span::styled(
            "TAB",
            Style::default()
                .fg(Color::Yellow)
                .add_modifier(Modifier::BOLD),
        ),
        Span::raw(":switch  "),
    ];

    if app.showing_vector_records {
        hints.extend_from_slice(&[
            Span::styled(
                "v",
                Style::default()
                    .fg(Color::Yellow)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::raw(":files  "),
            Span::styled(
                "j/k",
                Style::default()
                    .fg(Color::Yellow)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::raw(":navigate  "),
            Span::styled(
                "n",
                Style::default()
                    .fg(Color::Yellow)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::raw(":next page  "),
            Span::styled(
                "c",
                Style::default()
                    .fg(Color::Yellow)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::raw(":count  "),
            Span::styled(
                "g/G",
                Style::default()
                    .fg(Color::Yellow)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::raw(":top/bottom"),
        ]);
    } else {
        hints.extend_from_slice(&[
            Span::styled(
                "v",
                Style::default()
                    .fg(Color::Yellow)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::raw(":vectors  "),
            Span::styled(
                "j/k",
                Style::default()
                    .fg(Color::Yellow)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::raw(":navigate  "),
            Span::styled(
                ".",
                Style::default()
                    .fg(Color::Yellow)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::raw(":toggle folder  "),
            Span::styled(
                "g/G",
                Style::default()
                    .fg(Color::Yellow)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::raw(":top/bottom  "),

        ]);
    }

    hints.extend_from_slice(&[
        Span::styled(
            "d", 
            Style::default()
                .fg(Color::Yellow)
                .add_modifier(Modifier::BOLD)), 
        Span::raw(":delete  "),

        Span::styled(
            "r", 
            Style::default()
                .fg(Color::Yellow)
                .add_modifier(Modifier::BOLD)),
        Span::raw(":refresh"),
    ]);

    if !app.status_message.is_empty() {
        hints.push(Span::raw("  |  "));
        hints.push(Span::styled(
            &app.status_message,
            Style::default().fg(Color::Cyan),
        ));
    }

    let bar = Paragraph::new(Line::from(hints))
        .style(Style::default().bg(Color::DarkGray).fg(Color::White));
    frame.render_widget(bar, area);
}
