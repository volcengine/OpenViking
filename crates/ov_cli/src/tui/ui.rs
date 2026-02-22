use ratatui::{
    layout::{Constraint, Direction, Layout},
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Borders, List, ListItem, ListState, Paragraph, Wrap},
    Frame,
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
                if row.expanded {
                    "▾ "
                } else {
                    "▸ "
                }
            } else {
                "  "
            };

            let style = if row.is_dir {
                Style::default().fg(Color::Blue).add_modifier(Modifier::BOLD)
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
            .bg(if focused { Color::DarkGray } else { Color::Reset })
            .fg(Color::White)
            .add_modifier(Modifier::BOLD),
    );

    frame.render_stateful_widget(list, inner, &mut list_state);
}

fn render_content(frame: &mut Frame, app: &App, area: ratatui::layout::Rect) {
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

fn render_status_bar(frame: &mut Frame, _app: &App, area: ratatui::layout::Rect) {
    let hints = Line::from(vec![
        Span::styled(" q", Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD)),
        Span::raw(":quit  "),
        Span::styled("TAB", Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD)),
        Span::raw(":switch  "),
        Span::styled("j/k", Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD)),
        Span::raw(":navigate  "),
        Span::styled(".", Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD)),
        Span::raw(":toggle folder  "),
        Span::styled("g/G", Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD)),
        Span::raw(":top/bottom"),
    ]);

    let bar = Paragraph::new(hints).style(Style::default().bg(Color::DarkGray).fg(Color::White));
    frame.render_widget(bar, area);
}
