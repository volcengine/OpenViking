use super::{
    COMPACT_STATUS_DETAIL_WIDTH, FULL_STATUS_BOX_MIN_ROWS, OV_LOGO_LINES, StatusBoxFrame,
    StatusBoxMode, StatusBoxRuntime, active_summary_lines, active_summary_render_parts,
    box_content_line, box_footer_line, box_title_line, compact_status_box_width,
    display_config_home, extract_models_from_status_payload, logo_glass_color_for_theme,
    ov_logo_width, saved_summary_render_parts, status_box_frame_for_size, status_box_lines,
    status_box_lines_with_runtime, status_box_lines_with_runtime_width, status_box_width,
    status_payload_is_healthy, styled_logo_to_width_for_color_level,
    styled_wordmark_line_for_color_level, tagline_color_for_theme, wizard_header_lines,
    wordmark_lines, wordmark_width,
};
use crate::config::Config;
use crate::config_wizard::store::{ConfigEntry, ConfigKind, ConfigStore, OPENVIKING_SERVICE_URL};
use crate::terminal_ui::display_width;
use crate::theme::{self, ThemeColor};
use serde_json::json;
use std::path::PathBuf;

#[test]
fn wizard_header_contains_standalone_wordmark() {
    let header = wizard_header_lines().join("\n");

    assert!(header.contains("██████╗"));
    assert!(!header.contains("Context Database for AI Agents"));
    assert!(!header.contains(".openviking ~ ov config"));
    assert!(!header.contains("CLI config"));
    assert!(!header.contains("profile manager"));
    assert!(!header.contains("↑/↓ choose"));
    assert!(!header.contains("OpenViking CLI v"));
}

#[test]
fn wizard_header_has_no_standalone_spaced_bands() {
    let lines = wizard_header_lines();

    assert_eq!(lines.len(), wordmark_lines().len() + 1);
    assert_eq!(lines.last().map(String::as_str), Some(""));
}

#[test]
fn header_omits_tagline_and_version() {
    let lines = wizard_header_lines();
    let header = lines.join("\n");

    assert!(!header.contains("Context Database for AI Agents"));
    assert!(!header.contains("OpenViking CLI v"));
}

#[test]
fn full_status_box_puts_motto_in_border_without_wordmark() {
    let config = Config {
        url: "http://127.0.0.1:1933".to_string(),
        ..Config::default()
    };
    let lines = status_box_lines(
        Some(&config),
        &[ConfigEntry {
            name: "test".to_string(),
            config: config.clone(),
            is_active: true,
            kind: ConfigKind::Custom,
        }],
        "~/.openviking",
    );
    let version = format!("v{}", env!("OPENVIKING_CLI_VERSION"));

    assert!(lines.len() >= 6);
    assert!(lines[0].contains("Context Database for AI Agents"));
    assert!(!lines.iter().any(|line| line.contains("██████╗")));
    assert!(lines.iter().any(|line| line.contains('⣿')));
    assert!(!lines[0].contains(&version));
    assert!(
        lines
            .last()
            .expect("footer should render")
            .contains(&version)
    );
    assert!(!lines[0].contains("OpenViking CLI"));
    assert!(lines[0].starts_with('╭'));
    assert!(lines[0].ends_with('╮'));
    assert!(lines.last().expect("footer should render").starts_with('╰'));
    assert!(lines.last().expect("footer should render").ends_with('╯'));
    assert_eq!(status_box_width(), wordmark_width());
    assert_eq!(display_width(&lines[0]), status_box_width());
    assert!(
        lines
            .last()
            .expect("footer should render")
            .find(&version)
            .expect("version should render")
            > status_box_width() / 2
    );
    for line in &lines {
        assert_eq!(display_width(line), status_box_width(), "{line:?}");
    }
}

#[test]
fn status_box_empty_title_uses_continuous_top_border() {
    let width = 48;
    let title = box_title_line("", width);

    assert_eq!(title, format!("╭{}╮", "─".repeat(width - 2)));
    assert_eq!(display_width(&title), width);
}

#[test]
fn compact_status_box_omits_logo_art_and_fits_status_details() {
    let config = Config {
        url: "http://127.0.0.1:1933".to_string(),
        ..Config::default()
    };
    let width = compact_status_box_width();
    let lines = status_box_lines_with_runtime_width(
        Some(&config),
        &[ConfigEntry {
            name: "compact".to_string(),
            config: config.clone(),
            is_active: true,
            kind: ConfigKind::Custom,
        }],
        "~/.openviking",
        &StatusBoxRuntime::unknown(),
        StatusBoxFrame {
            width,
            mode: StatusBoxMode::Compact,
        },
    );
    let text = lines.join("\n");

    assert!(width >= COMPACT_STATUS_DETAIL_WIDTH + 4);
    assert!(width <= status_box_width());
    assert!(lines[0].contains("OpenViking | Context Database for AI Agents"));
    assert!(text.contains("Active:"));
    assert!(!text.contains('⣿'));
    assert!(!text.contains('⣶'));
    assert!(!text.contains("██████╗"));
    assert!(!text.contains("╚═════╝"));
    assert!(!text.contains("OPENVIKING"));
    assert!(!text.contains("⠠⣶⣾⣿⣿⣿⣶⣤⣀ ⠾⠟⠛⠉⠉   ⣀⣤⣾⡿⠃"));
    for line in &lines {
        assert_eq!(display_width(line), width, "{line:?}");
    }
}

#[test]
fn status_box_frame_uses_compact_mode_when_full_art_would_not_fit() {
    let compact_width = compact_status_box_width();

    assert_eq!(
        status_box_frame_for_size(compact_width as u16 + 1, FULL_STATUS_BOX_MIN_ROWS - 1),
        StatusBoxFrame {
            width: compact_width,
            mode: StatusBoxMode::Compact,
        }
    );
    assert_eq!(
        status_box_frame_for_size(status_box_width() as u16 - 1, FULL_STATUS_BOX_MIN_ROWS).mode,
        StatusBoxMode::Compact
    );
    assert_eq!(
        status_box_frame_for_size(status_box_width() as u16 + 1, FULL_STATUS_BOX_MIN_ROWS).mode,
        StatusBoxMode::Full
    );
}

#[test]
fn status_box_cjk_lines_align_to_display_width() {
    let width = status_box_width();
    let title = box_title_line("AI Agent 上下文数据库", width);
    let content = box_content_line(
        "",
        "当前配置： VPS_ROOT (自定义)",
        width,
        StatusBoxMode::Full,
    );
    let footer = box_footer_line("v0.0.0", width);

    for line in [title, content, footer] {
        assert_eq!(display_width(&line), width, "{line:?}");
    }
}

#[test]
fn status_box_content_line_respects_narrow_terminal_width() {
    let width = 40;
    let content = box_content_line(
        OV_LOGO_LINES[8],
        "Context Database for AI Agents",
        width,
        StatusBoxMode::Full,
    );

    assert_eq!(display_width(&content), width, "{content:?}");
}

#[test]
fn status_box_contains_config_status_without_global_controls() {
    let config = Config {
        url: "http://127.0.0.1:1933".to_string(),
        ..Config::default()
    };
    let lines = status_box_lines(
        Some(&config),
        &[ConfigEntry {
            name: "orange".to_string(),
            config: config.clone(),
            is_active: true,
            kind: ConfigKind::Custom,
        }],
        "~/.openviking",
    );
    let text = lines.join("\n");

    assert!(text.contains("Active:"));
    assert!(text.contains("orange (Custom)"));
    assert!(text.contains("Status:"));
    assert!(text.contains("VLM:"));
    assert!(text.contains("Embedding:"));
    assert!(text.contains("Saved configs:"));
    assert!(text.contains("1"));
    assert!(text.contains("Config home:"));
    assert!(text.contains("~/.openviking"));
    assert!(text.contains("Context Database for AI Agents"));
    assert!(!text.contains("Save policy:"));
    assert!(!text.contains("validation + explicit save"));
    assert!(!text.contains("↑/↓ choose"));
    assert!(!text.contains("Enter select"));
    assert!(!text.contains("Esc back"));
}

#[test]
fn status_box_checking_runtime_renders_immediate_placeholders() {
    let lines =
        status_box_lines_with_runtime(None, &[], "~/.openviking", &StatusBoxRuntime::checking());
    let text = lines.join("\n");

    assert!(text.contains("Status: Checking..."));
    assert!(text.contains("VLM: Checking..."));
    assert!(text.contains("Embedding: Checking..."));
}

#[test]
fn status_box_can_render_healthy_status_and_runtime_models() {
    let config = Config {
        url: "http://127.0.0.1:1933".to_string(),
        ..Config::default()
    };
    let runtime = StatusBoxRuntime::connected(
        true,
        Some("doubao-seed-2-0-lite-260428".to_string()),
        Some("doubao-embedding-vision-251215".to_string()),
    );
    let lines = status_box_lines_with_runtime(
        Some(&config),
        &[ConfigEntry {
            name: "vps".to_string(),
            config: config.clone(),
            is_active: true,
            kind: ConfigKind::Custom,
        }],
        "~/.openviking",
        &runtime,
    );
    let text = lines.join("\n");

    assert!(text.contains("Status: Connected (Healthy)"));
    assert!(text.contains("VLM: doubao-seed-2-0-lite-260428"));
    assert!(text.contains("Embedding: doubao-embedding-vision-251215"));
}

#[test]
fn status_payload_parser_reuses_ov_status_model_tables() {
    let payload = json!({
        "is_healthy": true,
        "components": {
            "models": {
                "name": "models",
                "is_healthy": true,
                "status": "\nVLM Models:\n+-------+\n| Model | Provider |\n+-------+\n| doubao-seed-2-0-lite-260428 | volcengine |\n+-------+\n\nEmbedding Models:\n+-------+\n| Model | Provider |\n+-------+\n| doubao-embedding-vision-251215 | volcengine |\n+-------+\n"
            }
        }
    });

    assert_eq!(
        extract_models_from_status_payload(&payload),
        (
            Some("doubao-seed-2-0-lite-260428".to_string()),
            Some("doubao-embedding-vision-251215".to_string())
        )
    );
}

#[test]
fn status_payload_health_falls_back_to_component_health() {
    let payload = json!({
        "components": {
            "queue": { "is_healthy": true, "has_errors": false },
            "models": { "is_healthy": true, "has_errors": false }
        }
    });

    assert!(status_payload_is_healthy(&payload));
}

#[test]
fn status_box_details_are_vertically_centered_without_label_justification() {
    let config = Config {
        url: "http://127.0.0.1:1933".to_string(),
        ..Config::default()
    };
    let lines = status_box_lines(
        Some(&config),
        &[ConfigEntry {
            name: "orange".to_string(),
            config: config.clone(),
            is_active: true,
            kind: ConfigKind::Custom,
        }],
        "~/.openviking",
    );

    let active_index = lines
        .iter()
        .position(|line| line.contains("Active:"))
        .expect("active detail should render");

    assert!(active_index > 2, "details should not start at the top");
    assert!(
        active_index + 6 < lines.len() - 1,
        "details should not end at the bottom"
    );
    assert!(lines[active_index].contains("Active: orange (Custom)"));
    assert!(lines[active_index + 1].contains("Status: Unknown"));
    assert!(lines[active_index + 2].contains("VLM: Unknown"));
    assert!(lines[active_index + 3].contains("Embedding: Unknown"));
    assert!(lines[active_index + 4].contains("Saved configs: 1"));
    assert!(lines[active_index + 5].contains("Config home: ~/.openviking"));
    assert!(!lines[active_index + 6].contains("Save policy:"));
}

#[test]
fn status_box_renders_the_sail_and_slash_mark() {
    let lines = status_box_lines(None, &[], "~/.openviking");
    let text = lines.join("\n");

    assert!(
        text.contains(OV_LOGO_LINES[1].trim()),
        "sail tip should render"
    );
    assert!(
        text.contains(OV_LOGO_LINES[12].trim()),
        "flat base should render"
    );
    assert!(
        !text.contains("⠾⠟⠛⠉⠉"),
        "the retired two-sail boat and wave art should be gone"
    );
    assert!(!text.contains("/\\"));
}

#[test]
fn status_box_logo_reads_as_tapered_slash_then_upright_sail() {
    let ink_rows = &OV_LOGO_LINES[1..OV_LOGO_LINES.len() - 1];

    // Same row budget and width as the art it replaces, so the full
    // status box layout does not move.
    assert_eq!(OV_LOGO_LINES.len(), 14);
    assert!(ov_logo_width() <= 28);
    assert_eq!(OV_LOGO_LINES[0], "", "top row is breathing room");
    assert_eq!(OV_LOGO_LINES[13], "", "bottom row is breathing room");

    let mast = display_width(ink_rows[0]);
    let mut previous_start = usize::MAX;
    let mut previous_sail = 0;
    let mut previous_slash = 0;
    for line in ink_rows {
        // Every ink row is two strokes, the slash and the sail, separated
        // by the brand's slash-to-sail gap: that pair is what reads as `//`.
        assert_eq!(
            visible_group_count(line),
            2,
            "{line:?} should be `/` + sail"
        );
        let (slash, sail) = line
            .trim_start()
            .split_once(' ')
            .expect("gap between strokes");
        let sail = sail.trim_start();
        assert!(
            line.contains("  "),
            "{line:?} should keep open space between slash and sail"
        );
        assert!(
            slash
                .chars()
                .map(|ch| (ch as u32 - 0x2800).count_ones())
                .sum::<u32>()
                >= previous_slash,
            "{line:?} E slash should widen from its fine tip toward the base"
        );

        // Upright mast: the sail's vertical edge sits in one column.
        assert_eq!(
            display_width(line),
            mast,
            "{line:?} mast should be vertical"
        );
        // Both strokes lean forward: each row starts left of the one above
        // and the sail widens toward its flat base.
        let start = display_width(line) - display_width(line.trim_start());
        assert!(start < previous_start, "{line:?} should step left");
        assert!(
            display_width(sail) >= previous_sail,
            "{line:?} sail should widen"
        );
        previous_start = start;
        previous_sail = display_width(sail);
        previous_slash = slash
            .chars()
            .map(|ch| (ch as u32 - 0x2800).count_ones())
            .sum::<u32>();
    }
    assert!(
        ink_rows[ink_rows.len() - 1].contains("⣿⣿⣿⣿"),
        "the base should be a flat, fully inked edge"
    );
}

fn visible_group_count(line: &str) -> usize {
    let mut groups = 0;
    let mut in_group = false;
    for ch in line.chars() {
        if ch.is_whitespace() {
            in_group = false;
        } else if !in_group {
            groups += 1;
            in_group = true;
        }
    }
    groups
}

#[test]
fn display_config_home_uses_tilde_for_current_home() {
    let home = std::env::var_os("HOME").expect("HOME should be set for CLI tests");
    let dir = PathBuf::from(home).join(".openviking");
    let store = ConfigStore::for_config_dir(dir.clone());

    assert_eq!(display_config_home(&store), "~/.openviking");
}

#[test]
fn wordmark_lines_have_identical_width() {
    let wordmark = wordmark_lines();
    let width = display_width(wordmark[0]);

    for line in wordmark {
        assert_eq!(display_width(line), width, "{line:?} should match");
    }
}

#[test]
fn wordmark_keeps_all_caps_block_art_at_the_status_box_width() {
    // The brand wordmark is mixed-case "OpenViking" in Geist; block glyphs
    // have no lowercase, so the terminal keeps the all-caps block letters.
    // Their width also sets the full status box width.
    let width = wordmark_width();

    assert_eq!(width, 81, "wordmark width drives the status box layout");
    assert_eq!(status_box_width(), width);
    assert!(
        wordmark_lines().iter().any(|line| line.contains("████")),
        "wordmark should keep block-art styling"
    );
}

#[test]
fn wordmark_visible_edges_are_consistent() {
    let wordmark = wordmark_lines();
    let width = wordmark_width();

    for line in wordmark {
        let first_visible = line
            .chars()
            .position(|ch| !ch.is_whitespace())
            .expect("wordmark line should have visible text");
        assert!(
            first_visible <= 1,
            "{line:?} should not protrude or drift horizontally"
        );
        assert!(
            display_width(line.trim_end()) >= width - 1,
            "{line:?} should reach the right edge"
        );
    }
}

#[test]
fn wordmark_does_not_use_protruding_corner_glyphs() {
    let wordmark = wordmark_lines();

    assert!(!wordmark[0].starts_with('◢'));
    assert!(!wordmark[0].ends_with('◣'));
    assert!(!wordmark[wordmark.len() - 1].starts_with('◥'));
    assert!(!wordmark[wordmark.len() - 1].ends_with('◤'));
}

#[test]
fn wordmark_follows_the_terminal_foreground() {
    // Brand rule: light artwork on dark backgrounds, dark artwork on
    // light ones. The default foreground gives Paper-on-Ink or
    // Ink-on-Paper without guessing the terminal background.
    assert_eq!(theme::active_theme().wordmark, ThemeColor::DefaultFg);

    let line = wordmark_lines()[0];
    let rendered = styled_wordmark_line_for_color_level(line, theme::ColorLevel::TrueColor);
    assert_eq!(rendered, format!("\u{1b}[1;39m{line}\u{1b}[0m"));
    assert!(
        !rendered.contains("38;2;"),
        "wordmark must not hardcode a color"
    );
}

#[test]
fn wordmark_needs_no_palette_fallback_without_truecolor() {
    let line = wordmark_lines()[0];

    for level in [theme::ColorLevel::Ansi256, theme::ColorLevel::Ansi16] {
        let rendered = styled_wordmark_line_for_color_level(line, level);
        assert_eq!(rendered, format!("\u{1b}[1;39m{line}\u{1b}[0m"));
        assert!(ansi256_indexes(&rendered).is_empty());
    }
    assert_eq!(
        styled_wordmark_line_for_color_level(line, theme::ColorLevel::NoColor),
        line,
        "NO_COLOR should print the plain block letters"
    );
}

#[test]
fn logo_uses_stable_deep_teal_ansi256_fallback_when_truecolor_is_unavailable() {
    let rendered = styled_logo_to_width_for_color_level(
        OV_LOGO_LINES[8],
        ov_logo_width(),
        8,
        theme::ColorLevel::Ansi256,
    );

    assert!(rendered.contains("\u{1b}[1;38;5;"));
    assert!(!rendered.contains("38;2;"));
    assert_eq!(ansi256_indexes(&rendered), vec![30]);
}

#[test]
fn tagline_is_solid_deep_teal() {
    let width = display_width("Context Database for AI Agents");
    let palette = theme::active_theme();

    assert_eq!(
        tagline_color_for_theme(palette, 0, width),
        theme::BRAND_DEEP_TEAL
    );
    assert_eq!(
        tagline_color_for_theme(palette, width - 1, width),
        theme::BRAND_DEEP_TEAL
    );
    assert_eq!(
        tagline_color_for_theme(palette, width / 2, width),
        theme::BRAND_DEEP_TEAL
    );
}

fn ansi256_indexes(rendered: &str) -> Vec<u8> {
    let mut indexes = Vec::new();
    let mut rest = rendered;
    while let Some(start) = rest.find("38;5;") {
        let value_start = start + "38;5;".len();
        let digits: String = rest[value_start..]
            .chars()
            .take_while(|ch| ch.is_ascii_digit())
            .collect();
        if let Ok(index) = digits.parse::<u8>()
            && !indexes.contains(&index)
        {
            indexes.push(index);
        }
        rest = &rest[value_start..];
    }
    indexes
}

#[test]
fn status_box_border_uses_brand_deep_teal() {
    assert_eq!(
        theme::active_theme().border,
        ThemeColor::TrueColor(theme::BRAND_DEEP_TEAL)
    );
}

#[test]
fn status_box_footer_version_uses_brand_deep_teal() {
    assert_eq!(
        theme::active_theme().version,
        ThemeColor::TrueColor(theme::BRAND_DEEP_TEAL)
    );
}

#[test]
fn status_box_logo_is_solid_deep_teal() {
    let width = ov_logo_width();
    let palette = theme::active_theme();
    for row in 0..OV_LOGO_LINES.len() {
        for column in 0..width {
            assert_eq!(
                logo_glass_color_for_theme(palette, column, row, width),
                theme::BRAND_DEEP_TEAL
            );
        }
    }
}

#[test]
fn active_summary_hides_url_and_shows_kind() {
    let config = Config {
        url: "http://127.0.0.1:1933".to_string(),
        ..Config::default()
    };
    let lines = active_summary_lines(
        Some(&config),
        &[ConfigEntry {
            name: "local".to_string(),
            config: config.clone(),
            is_active: true,
            kind: ConfigKind::Custom,
        }],
    );

    assert_eq!(lines[0], "Active: local (Custom)");
    assert_eq!(lines[1], "Saved configs: 1");
    assert!(!lines[0].contains("127.0.0.1"));
    assert!(!lines[0].starts_with(' '));
    assert!(!lines[1].starts_with(' '));
}

#[test]
fn active_summary_uses_compact_openviking_service_kind() {
    let config = Config {
        url: OPENVIKING_SERVICE_URL.to_string(),
        ..Config::default()
    };
    let lines = active_summary_lines(
        Some(&config),
        &[ConfigEntry {
            name: "serverless".to_string(),
            config: config.clone(),
            is_active: true,
            kind: ConfigKind::OpenVikingService,
        }],
    );

    assert_eq!(lines[0], "Active: serverless (OpenViking Service)");
    assert!(!lines[0].contains("VolcEngine Cloud"));

    let active = active_summary_render_parts(&lines[0])
        .expect("compact OpenViking Service summary should split");
    assert_eq!(active.name, "serverless");
    assert_eq!(active.kind.as_deref(), Some("(OpenViking Service)"));
}

#[test]
fn summary_render_parts_split_config_name_type_and_count() {
    let active =
        active_summary_render_parts("Active: test (Custom)").expect("active summary should split");
    let saved = saved_summary_render_parts("Saved configs: 2").expect("saved summary should split");

    assert_eq!(active.label, "Active:");
    assert_eq!(active.name, "test");
    assert_eq!(active.kind.as_deref(), Some("(Custom)"));
    assert_eq!(saved.label, "Saved configs:");
    assert_eq!(saved.count, "2");
}
