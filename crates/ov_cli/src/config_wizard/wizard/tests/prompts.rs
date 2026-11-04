use super::super::credentials::custom_api_key_helper_lines;
use super::super::profiles::add_config_name_label;
use super::{
    InputValueLabel, LiveRegion, erase_sequence_for_char, input_live_lines,
    input_prompt_with_value, rendered_live_region_rows, select_live_lines,
};
use crate::terminal_ui::{RenderedRegion, rendered_row_count};

#[test]
fn erase_sequence_matches_display_width() {
    assert_eq!(erase_sequence_for_char('a', false), "\x08 \x08");
    assert_eq!(erase_sequence_for_char('中', false), "\x08 \x08\x08 \x08");
    assert_eq!(erase_sequence_for_char('中', true), "\x08 \x08");
}

#[test]
fn input_prompt_tracks_visible_or_secret_value() {
    assert_eq!(input_prompt_with_value("", false), "  > ");
    assert_eq!(input_prompt_with_value("abc", false), "  > abc");
    assert_eq!(input_prompt_with_value("secret", true), "  > ******");
}

#[test]
fn rendered_region_clears_render_time_rows_after_width_changes() {
    let lines = vec!["x".repeat(90)];
    let region = RenderedRegion::from_lines(&lines, 30);

    assert_eq!(rendered_row_count(&lines, 30), 3);
    assert_eq!(rendered_row_count(&lines, 90), 1);
    assert_eq!(region.rows_to_clear(90), 3);
}

#[test]
fn live_region_recomputes_clear_rows_after_resize() {
    let lines = vec!["x".repeat(90)];
    let ui = LiveRegion {
        lines: lines.clone(),
        input_prompt: None,
        rows_drawn: rendered_live_region_rows(&lines, None, 90),
        cursor_on_last_line: false,
    };

    assert_eq!(ui.rows_to_clear(30), 3);
}

#[test]
fn live_region_keeps_largest_input_rows_until_clear() {
    let mut ui = LiveRegion {
        lines: vec!["Prompt".to_string()],
        input_prompt: Some(format!("  > {}", "x".repeat(90))),
        rows_drawn: 10,
        cursor_on_last_line: true,
    };

    ui.set_input_prompt("  > x".to_string());

    assert_eq!(ui.rows_to_clear(120), 10);
}

#[test]
fn add_config_name_rendering_has_no_default_or_current_label() {
    let lines = input_live_lines(
        "Create a new OpenViking config.",
        add_config_name_label(),
        None,
        None,
        false,
        &["Leave empty to generate one.".to_string()],
        None,
    );

    let text = lines.join("\n");
    assert!(text.contains("Config name (optional)"));
    assert!(text.contains("Leave empty to generate one."));
    assert!(!text.contains("Default:"));
    assert!(!text.contains("Current:"));
}

#[test]
fn edit_config_name_rendering_uses_current_label() {
    let lines = input_live_lines(
        "Update a saved config.",
        "Config name",
        Some("test"),
        Some(InputValueLabel::Current),
        false,
        &[],
        None,
    );

    let text = lines.join("\n");
    assert!(text.contains("Current:"));
    assert!(text.contains("test"));
    assert!(!text.contains("Default:"));
}

#[test]
fn add_custom_api_key_rendering_has_no_existing_value_placeholder() {
    let lines = input_live_lines(
        "Create a new OpenViking config.",
        "API key (optional)",
        None,
        None,
        true,
        &custom_api_key_helper_lines(true),
        None,
    );
    let text = lines.join("\n");

    assert!(!text.contains("(existing value)"));
    assert!(!text.contains("Default:"));
    assert!(!text.contains("Current:"));
}

#[test]
fn live_select_lines_are_current_step_only() {
    let lines = select_live_lines(
        "What would you like to configure?",
        "Choose action",
        &[
            "Add Config",
            "Switch Config",
            "Edit Config",
            "Delete Config",
        ],
        1,
        &[],
    );

    assert!(lines[0].contains("What would you like to configure?"));
    assert!(lines.iter().any(|line| line.contains("› Switch Config")));
    assert!(!lines.iter().any(|line| line.contains("✓ Choose action")));
    assert!(!lines.iter().any(|line| line.contains("Back")));
    assert_eq!(lines.len(), 9);
}
