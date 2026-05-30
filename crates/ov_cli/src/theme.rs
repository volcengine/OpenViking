use colored::{ColoredString, Colorize};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct Rgb(pub(crate) u8, pub(crate) u8, pub(crate) u8);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ThemeColor {
    TrueColor(Rgb),
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
    use super::{CliTheme, Rgb, ThemeColor, active_theme, palette, relative_luminance};

    const PALE_PEARL: Rgb = Rgb(234, 253, 247);
    const WHITE: Rgb = Rgb(255, 255, 255);
    const BLACK: Rgb = Rgb(0, 0, 0);

    #[test]
    fn active_theme_uses_the_single_palette() {
        assert_eq!(active_theme(), palette());
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
