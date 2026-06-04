use std::env;

use colored::{ColoredString, Colorize};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct Rgb(pub(crate) u8, pub(crate) u8, pub(crate) u8);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ThemeColor {
    TrueColor(Rgb),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ColorLevel {
    NoColor,
    Ansi16,
    Ansi256,
    TrueColor,
}

impl ThemeColor {
    pub(crate) fn rgb_fallback(&self) -> Rgb {
        match self {
            ThemeColor::TrueColor(rgb) => *rgb,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct CliTheme {
    pub(crate) wordmark_start: Rgb,
    pub(crate) wordmark_mid: Rgb,
    pub(crate) wordmark_end: Rgb,
    pub(crate) logo_end: Rgb,
    pub(crate) tagline_start: Rgb,
    pub(crate) tagline_mid: Rgb,
    pub(crate) tagline_end: Rgb,
    pub(crate) border: ThemeColor,
    pub(crate) version: ThemeColor,
    pub(crate) brand_title: ThemeColor,
    pub(crate) body: ThemeColor,
    pub(crate) muted: ThemeColor,
    pub(crate) command: ThemeColor,
    pub(crate) heading: ThemeColor,
    pub(crate) value: ThemeColor,
    pub(crate) sky_value: ThemeColor,
    pub(crate) success: ThemeColor,
    pub(crate) warning: ThemeColor,
    pub(crate) error: ThemeColor,
    pub(crate) config_name: ThemeColor,
    pub(crate) section_marker: ThemeColor,
    pub(crate) prompt: ThemeColor,
    pub(crate) selection: ThemeColor,
}

pub(crate) fn active_theme() -> CliTheme {
    palette()
}

pub(crate) fn palette() -> CliTheme {
    CliTheme {
        wordmark_start: Rgb(22, 181, 166),
        wordmark_mid: Rgb(0, 140, 132),
        wordmark_end: Rgb(5, 86, 80),
        logo_end: Rgb(5, 86, 80),
        tagline_start: Rgb(0, 128, 128),
        tagline_mid: Rgb(0, 112, 190),
        tagline_end: Rgb(0, 128, 128),
        border: ThemeColor::TrueColor(Rgb(0, 128, 128)),
        version: ThemeColor::TrueColor(Rgb(0, 128, 128)),
        brand_title: ThemeColor::TrueColor(Rgb(0, 128, 128)),
        body: ThemeColor::TrueColor(Rgb(96, 111, 126)),
        muted: ThemeColor::TrueColor(Rgb(104, 112, 120)),
        command: ThemeColor::TrueColor(Rgb(0, 128, 128)),
        heading: ThemeColor::TrueColor(Rgb(0, 128, 128)),
        value: ThemeColor::TrueColor(Rgb(0, 112, 190)),
        sky_value: ThemeColor::TrueColor(Rgb(0, 112, 190)),
        success: ThemeColor::TrueColor(Rgb(0, 133, 90)),
        warning: ThemeColor::TrueColor(Rgb(185, 90, 0)),
        error: ThemeColor::TrueColor(Rgb(212, 60, 55)),
        config_name: ThemeColor::TrueColor(Rgb(199, 80, 0)),
        section_marker: ThemeColor::TrueColor(Rgb(139, 92, 246)),
        prompt: ThemeColor::TrueColor(Rgb(150, 109, 27)),
        selection: ThemeColor::TrueColor(Rgb(0, 133, 90)),
    }
}

pub(crate) fn colorize(text: impl Into<String>, color: ThemeColor) -> ColoredString {
    let text = text.into();
    match color {
        ThemeColor::TrueColor(Rgb(red, green, blue)) => text.truecolor(red, green, blue),
    }
}

pub(crate) fn terminal_color_level() -> ColorLevel {
    if !colored::control::SHOULD_COLORIZE.should_colorize() {
        return ColorLevel::NoColor;
    }

    terminal_color_level_from_env(
        env::var("COLORTERM").ok().as_deref(),
        env::var("TERM").ok().as_deref(),
        env::var("TERM_PROGRAM").ok().as_deref(),
    )
}

pub(crate) fn terminal_color_level_from_env(
    colorterm: Option<&str>,
    term: Option<&str>,
    term_program: Option<&str>,
) -> ColorLevel {
    if colorterm
        .map(|value| value.eq_ignore_ascii_case("truecolor") || value.eq_ignore_ascii_case("24bit"))
        .unwrap_or(false)
        || term
            .map(|value| {
                let value = value.to_ascii_lowercase();
                value.contains("truecolor") || value.contains("24bit") || value.contains("direct")
            })
            .unwrap_or(false)
        || term_program
            .map(|value| {
                matches!(
                    value.to_ascii_lowercase().as_str(),
                    "iterm.app" | "wezterm" | "vscode" | "windows_terminal"
                )
            })
            .unwrap_or(false)
    {
        return ColorLevel::TrueColor;
    }

    if term
        .map(|value| value.to_ascii_lowercase().contains("256color"))
        .unwrap_or(false)
        || term_program
            .map(|value| value.eq_ignore_ascii_case("Apple_Terminal"))
            .unwrap_or(false)
    {
        return ColorLevel::Ansi256;
    }

    ColorLevel::Ansi16
}

pub(crate) fn style_rgb(text: impl AsRef<str>, rgb: Rgb, bold: bool) -> String {
    style_rgb_for_level(text, rgb, bold, terminal_color_level())
}

pub(crate) fn style_rgb_for_level(
    text: impl AsRef<str>,
    rgb: Rgb,
    bold: bool,
    level: ColorLevel,
) -> String {
    let text = text.as_ref();
    match level {
        ColorLevel::NoColor => text.to_string(),
        ColorLevel::TrueColor => {
            let Rgb(red, green, blue) = rgb;
            ansi_style(text, &format!("38;2;{red};{green};{blue}"), bold)
        }
        ColorLevel::Ansi256 => {
            ansi_style(text, &format!("38;5;{}", ansi256_index_for_rgb(rgb)), bold)
        }
        ColorLevel::Ansi16 => ansi_style(text, &ansi16_fg_code_for_rgb(rgb).to_string(), bold),
    }
}

fn ansi_style(text: &str, fg_code: &str, bold: bool) -> String {
    if bold {
        format!("\u{1b}[1;{fg_code}m{text}\u{1b}[0m")
    } else {
        format!("\u{1b}[{fg_code}m{text}\u{1b}[0m")
    }
}

pub(crate) fn ansi256_index_for_rgb(rgb: Rgb) -> u8 {
    let Rgb(red, green, blue) = rgb;

    if red.abs_diff(green) <= 10 && green.abs_diff(blue) <= 10 {
        let average = (u16::from(red) + u16::from(green) + u16::from(blue)) / 3;
        if average < 8 {
            return 16;
        }
        if average > 238 {
            return 231;
        }
        return 232 + ((average - 8) / 10) as u8;
    }

    let red = ansi256_cube_component(red);
    let green = ansi256_cube_component(green);
    let blue = ansi256_cube_component(blue);

    16 + 36 * red + 6 * green + blue
}

fn ansi256_cube_component(value: u8) -> u8 {
    if value < 48 {
        0
    } else if value < 115 {
        1
    } else {
        ((value - 35) / 40).min(5)
    }
}

fn ansi16_fg_code_for_rgb(rgb: Rgb) -> u8 {
    const ANSI16: [(u8, Rgb); 16] = [
        (30, Rgb(0, 0, 0)),
        (31, Rgb(128, 0, 0)),
        (32, Rgb(0, 128, 0)),
        (33, Rgb(128, 128, 0)),
        (34, Rgb(0, 0, 128)),
        (35, Rgb(128, 0, 128)),
        (36, Rgb(0, 128, 128)),
        (37, Rgb(192, 192, 192)),
        (90, Rgb(128, 128, 128)),
        (91, Rgb(255, 0, 0)),
        (92, Rgb(0, 255, 0)),
        (93, Rgb(255, 255, 0)),
        (94, Rgb(0, 0, 255)),
        (95, Rgb(255, 0, 255)),
        (96, Rgb(0, 255, 255)),
        (97, Rgb(255, 255, 255)),
    ];

    ANSI16
        .iter()
        .min_by_key(|(_, candidate)| rgb_distance_squared(rgb, *candidate))
        .map(|(code, _)| *code)
        .unwrap_or(37)
}

fn rgb_distance_squared(left: Rgb, right: Rgb) -> u32 {
    let red = i32::from(left.0) - i32::from(right.0);
    let green = i32::from(left.1) - i32::from(right.1);
    let blue = i32::from(left.2) - i32::from(right.2);

    (red * red + green * green + blue * blue) as u32
}

pub(crate) fn brand_title(text: impl Into<String>) -> ColoredString {
    let theme = active_theme();
    colorize(text, theme.brand_title)
}

pub(crate) fn border(text: impl Into<String>) -> ColoredString {
    colorize(text, active_theme().border)
}

pub(crate) fn version(text: impl Into<String>) -> ColoredString {
    colorize(text, active_theme().version)
}

pub(crate) fn command(text: impl Into<String>) -> ColoredString {
    let theme = active_theme();
    colorize(text, theme.command)
}

pub(crate) fn body(text: impl Into<String>) -> ColoredString {
    let theme = active_theme();
    colorize(text, theme.body)
}

pub(crate) fn muted(text: impl Into<String>) -> ColoredString {
    let theme = active_theme();
    colorize(text, theme.muted)
}

pub(crate) fn heading(text: impl Into<String>) -> ColoredString {
    let theme = active_theme();
    colorize(text, theme.heading)
}

pub(crate) fn value(text: impl Into<String>) -> ColoredString {
    let theme = active_theme();
    colorize(text, theme.value)
}

pub(crate) fn sky_value(text: impl Into<String>) -> ColoredString {
    let theme = active_theme();
    colorize(text, theme.sky_value)
}

pub(crate) fn success(text: impl Into<String>) -> ColoredString {
    let theme = active_theme();
    colorize(text, theme.success)
}

pub(crate) fn warning(text: impl Into<String>) -> ColoredString {
    let theme = active_theme();
    colorize(text, theme.warning)
}

pub(crate) fn error(text: impl Into<String>) -> ColoredString {
    let theme = active_theme();
    colorize(text, theme.error)
}

pub(crate) fn config_name(text: impl Into<String>) -> ColoredString {
    let theme = active_theme();
    colorize(text, theme.config_name)
}

pub(crate) fn section_marker(text: impl Into<String>) -> ColoredString {
    let theme = active_theme();
    colorize(text, theme.section_marker)
}

pub(crate) fn prompt(text: impl Into<String>) -> ColoredString {
    let theme = active_theme();
    colorize(text, theme.prompt)
}

pub(crate) fn selection(text: impl Into<String>) -> ColoredString {
    let theme = active_theme();
    colorize(text, theme.selection)
}

pub(crate) fn strong(text: impl Into<String>) -> ColoredString {
    body(text).bold()
}

#[cfg(test)]
fn relative_luminance(color: Rgb) -> f32 {
    fn channel(value: u8) -> f32 {
        let value = value as f32 / 255.0;
        if value <= 0.03928 {
            value / 12.92
        } else {
            ((value + 0.055) / 1.055).powf(2.4)
        }
    }
    0.2126 * channel(color.0) + 0.7152 * channel(color.1) + 0.0722 * channel(color.2)
}

#[cfg(test)]
mod tests {
    use super::{
        CliTheme, ColorLevel, Rgb, ThemeColor, active_theme, ansi256_index_for_rgb, palette,
        relative_luminance, style_rgb_for_level, terminal_color_level_from_env,
    };

    const PALE_PEARL: Rgb = Rgb(234, 253, 247);
    const WHITE: Rgb = Rgb(255, 255, 255);
    const BLACK: Rgb = Rgb(0, 0, 0);

    #[test]
    fn active_theme_uses_the_single_palette() {
        assert_eq!(active_theme(), palette());
    }

    #[test]
    fn apple_terminal_uses_ansi256_instead_of_truecolor() {
        assert_eq!(
            terminal_color_level_from_env(None, Some("xterm-256color"), Some("Apple_Terminal")),
            ColorLevel::Ansi256
        );
        assert_eq!(
            terminal_color_level_from_env(
                Some("truecolor"),
                Some("xterm-256color"),
                Some("Apple_Terminal")
            ),
            ColorLevel::TrueColor
        );
    }

    #[test]
    fn ansi256_mapping_keeps_dark_teal_out_of_black_and_gray() {
        let index = ansi256_index_for_rgb(Rgb(5, 86, 80));

        assert_eq!(index, 23);
        assert!(!matches!(index, 0 | 8 | 232..=255));
    }

    #[test]
    fn rgb_styling_uses_fixed_ansi256_when_truecolor_is_unavailable() {
        let styled = style_rgb_for_level("X", Rgb(5, 86, 80), true, ColorLevel::Ansi256);

        assert_eq!(styled, "\u{1b}[1;38;5;23mX\u{1b}[0m");
        assert!(!styled.contains("38;2"));
    }

    fn functional_colors(palette: CliTheme) -> [(&'static str, ThemeColor); 13] {
        [
            ("brand_title", palette.brand_title),
            ("body", palette.body),
            ("muted", palette.muted),
            ("command", palette.command),
            ("heading", palette.heading),
            ("value", palette.value),
            ("sky_value", palette.sky_value),
            ("success", palette.success),
            ("warning", palette.warning),
            ("error", palette.error),
            ("config_name", palette.config_name),
            ("section_marker", palette.section_marker),
            ("prompt", palette.prompt),
        ]
    }

    fn contrast_ratio(foreground: Rgb, background: Rgb) -> f32 {
        let foreground = relative_luminance(foreground);
        let background = relative_luminance(background);
        let (lighter, darker) = if foreground > background {
            (foreground, background)
        } else {
            (background, foreground)
        };
        (lighter + 0.05) / (darker + 0.05)
    }

    fn assert_min_contrast(name: &str, color: ThemeColor, background: Rgb, minimum: f32) {
        let ratio = contrast_ratio(color.rgb_fallback(), background);
        assert!(
            ratio >= minimum,
            "{name} contrast {ratio:.2} is below {minimum:.2} against {background:?}"
        );
    }

    #[test]
    fn single_palette_uses_explicit_balanced_functional_colors() {
        let palette = palette();
        for (name, color) in functional_colors(palette) {
            assert_ne!(
                color.rgb_fallback(),
                PALE_PEARL,
                "{name} must not use pale Pearl"
            );
            assert_min_contrast(name, color, WHITE, 4.0);
            assert_min_contrast(name, color, BLACK, 4.0);
        }
    }

    #[test]
    fn single_palette_separates_neutral_text_from_teal_structure() {
        let palette = palette();

        for (name, color) in [("body", palette.body), ("muted", palette.muted)] {
            let Rgb(red, green, blue) = color.rgb_fallback();
            assert!(
                red >= 80,
                "{name} should be a readable neutral, not another teal accent"
            );
            assert!(
                green.abs_diff(blue) <= 35,
                "{name} should stay neutral enough for descriptions"
            );
        }

        let Rgb(command_red, command_green, _) = palette.command.rgb_fallback();
        assert!(
            command_red < 40 && command_green >= 115,
            "commands should remain green/teal structure text"
        );

        let Rgb(sky_red, sky_green, sky_blue) = palette.sky_value.rgb_fallback();
        assert!(
            sky_blue > sky_green && sky_blue > sky_red,
            "model names, paths, and URLs should use a clearer sky-blue value color"
        );
    }
}
