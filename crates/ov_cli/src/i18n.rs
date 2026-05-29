use std::{
    fs,
    path::{Path, PathBuf},
};

use serde::{Deserialize, Serialize};

use crate::{
    config::default_config_path,
    error::{Error, Result},
};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub(crate) enum Language {
    #[serde(rename = "en")]
    En,
    #[serde(rename = "zh-CN")]
    ZhCn,
}

impl Language {
    pub(crate) fn label(self) -> &'static str {
        match self {
            Self::En => "English",
            Self::ZhCn => "简体中文",
        }
    }

    pub(crate) fn from_code(value: &str) -> Option<Self> {
        match value.trim().to_ascii_lowercase().as_str() {
            "en" | "en-us" | "en_us" => Some(Self::En),
            "zh" | "zh-cn" | "zh_cn" | "cn" | "chinese" | "中文" | "简体中文" => {
                Some(Self::ZhCn)
            }
            _ => None,
        }
    }

    #[cfg(not(test))]
    pub(crate) fn current() -> Self {
        load_saved_language()
            .ok()
            .flatten()
            .unwrap_or_else(detect_language_from_env)
    }

    #[cfg(test)]
    pub(crate) fn current() -> Self {
        Self::En
    }
}

pub(crate) fn copy<'a>(language: Language, en: &'a str, zh: &'a str) -> &'a str {
    match language {
        Language::En => en,
        Language::ZhCn => zh,
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct CliSettings {
    language: Option<Language>,
}

pub(crate) fn settings_path() -> Result<PathBuf> {
    let active_path = default_config_path()?;
    let config_dir = active_path
        .parent()
        .ok_or_else(|| Error::Config("Could not determine config directory".to_string()))?;
    Ok(config_dir.join("ovcli.settings.conf"))
}

pub(crate) fn load_saved_language() -> Result<Option<Language>> {
    let path = settings_path()?;
    load_saved_language_from_path(&path)
}

pub(crate) fn save_language(language: Language) -> Result<()> {
    let path = settings_path()?;
    save_language_to_path(&path, language)
}

pub(crate) fn has_saved_language() -> bool {
    load_saved_language().ok().flatten().is_some()
}

pub(crate) fn load_saved_language_from_path(path: &Path) -> Result<Option<Language>> {
    if !path.exists() {
        return Ok(None);
    }
    let content = fs::read_to_string(path)?;
    let settings: CliSettings = serde_json::from_str(&content)?;
    Ok(settings.language)
}

pub(crate) fn save_language_to_path(path: &Path, language: Language) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let settings = CliSettings {
        language: Some(language),
    };
    fs::write(path, serde_json::to_string_pretty(&settings)?)?;
    Ok(())
}

#[cfg(not(test))]
pub(crate) fn detect_language_from_env() -> Language {
    for key in ["OPENVIKING_LANG", "LC_ALL", "LC_MESSAGES", "LANG"] {
        if let Ok(value) = std::env::var(key) {
            if let Some(language) = language_from_locale(&value) {
                return language;
            }
        }
    }
    Language::En
}

pub(crate) fn language_from_locale(value: &str) -> Option<Language> {
    let normalized = value.split('.').next().unwrap_or(value);
    if normalized.to_ascii_lowercase().starts_with("zh") {
        Some(Language::ZhCn)
    } else if normalized.to_ascii_lowercase().starts_with("en") {
        Some(Language::En)
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::{
        Language, copy, language_from_locale, load_saved_language_from_path, save_language_to_path,
    };
    use std::time::{SystemTime, UNIX_EPOCH};

    fn settings_path(name: &str) -> std::path::PathBuf {
        let suffix = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock should be valid")
            .as_nanos();
        std::env::temp_dir()
            .join(format!("openviking-i18n-{name}-{suffix}"))
            .join("ovcli.settings.conf")
    }

    #[test]
    fn language_settings_save_and_load() {
        let path = settings_path("save-load");

        assert_eq!(
            load_saved_language_from_path(&path).expect("missing settings should load"),
            None
        );

        save_language_to_path(&path, Language::ZhCn).expect("language should save");

        assert_eq!(
            load_saved_language_from_path(&path).expect("settings should load"),
            Some(Language::ZhCn)
        );
    }

    #[test]
    fn locale_detection_supports_chinese_and_english() {
        assert_eq!(language_from_locale("zh_CN.UTF-8"), Some(Language::ZhCn));
        assert_eq!(language_from_locale("zh-Hans"), Some(Language::ZhCn));
        assert_eq!(language_from_locale("en_US.UTF-8"), Some(Language::En));
        assert_eq!(language_from_locale("fr_FR.UTF-8"), None);
    }

    #[test]
    fn copy_returns_language_specific_text() {
        assert_eq!(copy(Language::En, "Config", "配置"), "Config");
        assert_eq!(copy(Language::ZhCn, "Config", "配置"), "配置");
    }
}
