//! Local profile naming, selection, saving, switching, and deletion.

use super::credentials::root_key_normal_command_notice_lines;
use super::prompts::{
    InputValueLabel, LiveRegion, PromptResult, confirm, print_cancelled, prompt_select,
    prompt_text, status_live_lines,
};
use super::status::compact_kind_label;
use crate::config::{Config, DEFAULT_CUSTOM_URL};
use crate::config_wizard::store::{
    ConfigEntry, ConfigKind, ConfigStore, validate_config, validate_config_name,
    validation_error_copy, validation_error_copy_zh,
};
use crate::error::{Error, Result};
use crate::i18n::{Language, copy};
use crate::theme;
use colored::Colorize;
use uuid::Uuid;

pub(super) fn section_add() -> &'static str {
    copy(
        Language::current(),
        "Create a new OpenViking config.",
        "创建新的 OpenViking 配置。",
    )
}

pub(super) fn section_edit() -> &'static str {
    copy(
        Language::current(),
        "Update a saved config.",
        "更新已保存的配置。",
    )
}

fn section_switch() -> &'static str {
    copy(
        Language::current(),
        "Switch to a saved config.",
        "切换到已保存的配置。",
    )
}

fn section_delete() -> &'static str {
    copy(
        Language::current(),
        "Delete a saved config.",
        "删除已保存的配置。",
    )
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum SaveAction {
    SaveAndActivate,
    SaveOnly,
    SaveActive,
    Cancel,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum SaveOutcome {
    Activated,
    SavedOnly,
    UpdatedActive,
}

pub(super) fn run_delete_config(store: &ConfigStore, ui: &mut LiveRegion) -> Result<bool> {
    enum Stage {
        Select,
        Confirm,
    }

    let configs = store.list_configs()?;
    if configs.is_empty() {
        let helper_lines = vec![
            theme::warning(copy(
                Language::current(),
                "No saved configs to delete.",
                "没有可删除的配置。",
            ))
            .to_string(),
        ];
        let _ = prompt_select(
            ui,
            section_delete(),
            copy(Language::current(), "Nothing to delete.", "没有可删除项。"),
            &[copy(Language::current(), "Back", "返回")],
            0,
            &helper_lines,
        )?;
        return Ok(false);
    }

    let mut stage = Stage::Select;
    let mut selected = 0usize;

    loop {
        match stage {
            Stage::Select => match prompt_config_select(
                ui,
                section_delete(),
                copy(Language::current(), "Config to delete", "要删除的配置"),
                &configs,
            )? {
                PromptResult::Value(index) => {
                    selected = index;
                    if configs[index].is_active {
                        let helper_lines = active_delete_block_helper_lines();
                        let _ = prompt_select(
                            ui,
                            section_delete(),
                            copy(
                                Language::current(),
                                "Active config cannot be deleted.",
                                "不能删除当前配置。",
                            ),
                            &[copy(Language::current(), "Back", "返回")],
                            0,
                            &helper_lines,
                        )?;
                        return Ok(false);
                    }
                    stage = Stage::Confirm;
                }
                PromptResult::Back => return Ok(false),
                PromptResult::Quit => {
                    print_cancelled(ui)?;
                    return Ok(true);
                }
            },
            Stage::Confirm => {
                let name = &configs[selected].name;
                match confirm(ui, section_delete(), &delete_confirm_prompt(name), false)? {
                    PromptResult::Value(true) => {
                        ui.clear()?;
                        store.delete_config(name)?;
                        println!();
                        println!(
                            "{} {}",
                            theme::success("✓"),
                            theme::success(deleted_config_message(name))
                        );
                        println!(
                            "{} {}",
                            theme::muted(copy(Language::current(), "Removed:", "已删除：")),
                            store
                                .saved_config_path(name)?
                                .display()
                                .to_string()
                                .magenta()
                        );
                        println!(
                            "{} {}",
                            theme::muted(copy(Language::current(), "Next:", "下一步：")),
                            next_step_copy()
                        );
                        return Ok(true);
                    }
                    PromptResult::Value(false) => {
                        print_cancelled(ui)?;
                        return Ok(true);
                    }
                    PromptResult::Back => stage = Stage::Select,
                    PromptResult::Quit => {
                        print_cancelled(ui)?;
                        return Ok(true);
                    }
                }
            }
        }
    }
}

pub(super) async fn run_switch_config(store: &ConfigStore, ui: &mut LiveRegion) -> Result<bool> {
    enum Stage {
        Select,
        Confirm,
    }

    let configs = store.list_configs()?;
    if configs.is_empty() {
        let helper_lines = vec![
            theme::warning(copy(
                Language::current(),
                "No saved configs to switch.",
                "没有可切换的配置。",
            ))
            .to_string(),
        ];
        let _ = prompt_select(
            ui,
            section_switch(),
            copy(Language::current(), "Nothing to switch.", "没有可切换项。"),
            &[copy(Language::current(), "Back", "返回")],
            0,
            &helper_lines,
        )?;
        return Ok(false);
    }

    let mut stage = Stage::Select;
    let mut selected = 0usize;

    loop {
        match stage {
            Stage::Select => match prompt_config_select(
                ui,
                section_switch(),
                copy(Language::current(), "Config to switch to", "要切换到的配置"),
                &configs,
            )? {
                PromptResult::Value(index) => {
                    selected = index;
                    if configs[index].is_active {
                        let helper_lines = vec![
                            theme::muted(config_already_active_message(&configs[index].name))
                                .to_string(),
                        ];
                        let _ = prompt_select(
                            ui,
                            section_switch(),
                            copy(
                                Language::current(),
                                "Config already active.",
                                "配置已是当前配置。",
                            ),
                            &[copy(Language::current(), "Back", "返回")],
                            0,
                            &helper_lines,
                        )?;
                        return Ok(false);
                    }
                    stage = Stage::Confirm;
                }
                PromptResult::Back => return Ok(false),
                PromptResult::Quit => {
                    print_cancelled(ui)?;
                    return Ok(true);
                }
            },
            Stage::Confirm => {
                let selected_config = &configs[selected];
                match confirm(
                    ui,
                    section_switch(),
                    &switch_confirm_prompt(&selected_config.name),
                    true,
                )? {
                    PromptResult::Value(true) => {
                        ui.render(&status_live_lines(
                            section_switch(),
                            copy(
                                Language::current(),
                                "Validating target config...",
                                "正在验证目标配置...",
                            ),
                        ))?;
                        if let Err(error) = validate_config(&selected_config.config).await {
                            ui.clear()?;
                            print_switch_validation_error(
                                &selected_config.name,
                                selected_config.kind,
                                &error,
                            );
                            return Ok(true);
                        }

                        ui.clear()?;
                        store.activate_config(&selected_config.name)?;
                        println!();
                        println!(
                            "{} {}",
                            theme::success("✓"),
                            theme::success(switched_config_message(&selected_config.name))
                        );
                        println!(
                            "{} {}",
                            theme::muted(copy(Language::current(), "Next:", "下一步：")),
                            theme::body(copy(
                                Language::current(),
                                "Run ov status to inspect it.",
                                "运行 ov status 查看状态。",
                            ))
                        );
                        return Ok(true);
                    }
                    PromptResult::Value(false) => stage = Stage::Select,
                    PromptResult::Back => stage = Stage::Select,
                    PromptResult::Quit => {
                        print_cancelled(ui)?;
                        return Ok(true);
                    }
                }
            }
        }
    }
}

pub(super) fn ensure_add_config_save_name(
    store: &ConfigStore,
    ui: &mut LiveRegion,
    name: &mut Option<String>,
    generated_name: &str,
) -> Result<PromptResult<String>> {
    if let Some(name) = name.as_deref() {
        return Ok(PromptResult::Value(name.to_string()));
    }

    match prompt_custom_or_generated_config_name(
        store,
        ui,
        section_add(),
        copy(
            Language::current(),
            "Save local config name",
            "保存本地配置名称",
        ),
        generated_name,
        &add_generated_config_name_helper_lines(),
    )? {
        PromptResult::Value(value) => {
            *name = Some(value.clone());
            Ok(PromptResult::Value(value))
        }
        PromptResult::Back => Ok(PromptResult::Back),
        PromptResult::Quit => Ok(PromptResult::Quit),
    }
}

fn prompt_custom_or_generated_config_name(
    store: &ConfigStore,
    ui: &mut LiveRegion,
    section: &str,
    prompt: &str,
    generated_name: &str,
    helper_lines: &[String],
) -> Result<PromptResult<String>> {
    let labels = local_config_name_choice_labels(generated_name);
    match prompt_select(ui, section, prompt, &labels, 0, helper_lines)? {
        PromptResult::Value(0) => {
            prompt_unique_config_name(ui, section, store, generated_name, None)
        }
        PromptResult::Value(1) => Ok(PromptResult::Value(generated_name.to_string())),
        PromptResult::Value(_) | PromptResult::Back => Ok(PromptResult::Back),
        PromptResult::Quit => Ok(PromptResult::Quit),
    }
}

pub(super) fn prompt_unique_config_name(
    ui: &mut LiveRegion,
    section: &str,
    store: &ConfigStore,
    default: &str,
    current_name: Option<&str>,
) -> Result<PromptResult<String>> {
    let mut error: Option<String> = None;
    loop {
        let mut helper_lines = unique_config_name_helper_lines();
        if let Some(value) = error.as_ref() {
            helper_lines.push(theme::error(value).to_string());
        }
        match prompt_text(
            ui,
            section,
            copy(Language::current(), "Saved config name", "保存配置名称"),
            Some(default),
            Some(InputValueLabel::Default),
            false,
            false,
            &helper_lines,
        )? {
            PromptResult::Value(value) => match match current_name {
                Some(current_name) => validate_config_name_change(store, current_name, &value),
                None => validate_new_config_name(store, &value),
            } {
                Ok(()) => return Ok(PromptResult::Value(value)),
                Err(next_error) => error = Some(prompt_validation_error(next_error)),
            },
            PromptResult::Back => return Ok(PromptResult::Back),
            PromptResult::Quit => return Ok(PromptResult::Quit),
        }
    }
}

pub(crate) fn validate_new_config_name(store: &ConfigStore, name: &str) -> Result<()> {
    validate_config_name(name)?;
    if is_legacy_user_management_config_name(name) {
        return Err(Error::Config(reserved_user_management_config_name_error(
            name,
        )));
    }
    if config_name_exists(store, name)? {
        return Err(Error::Config(config_name_exists_error(name)));
    }
    Ok(())
}

pub(super) fn validate_config_name_change(
    store: &ConfigStore,
    original_name: &str,
    candidate_name: &str,
) -> Result<()> {
    validate_config_name(candidate_name)?;
    if candidate_name == original_name {
        return Ok(());
    }
    validate_new_config_name(store, candidate_name)
}

fn config_name_exists(store: &ConfigStore, name: &str) -> Result<bool> {
    let path = store.saved_config_path(name)?;
    path.try_exists()
        .map_err(|error| Error::Config(format!("Failed to inspect config '{name}': {error}")))
}

pub(super) fn prompt_validation_error(error: Error) -> String {
    match error {
        Error::Config(message) => message,
        other => other.to_string(),
    }
}

pub(super) fn local_config_name_choice_labels(generated_name: &str) -> Vec<String> {
    match Language::current() {
        Language::En => vec![
            "Set custom local name".to_string(),
            format!("Use generated name ({generated_name})"),
            "Back".to_string(),
        ],
        Language::ZhCn => vec![
            "设置自定义本地名称".to_string(),
            format!("使用生成名称（{generated_name}）"),
            "返回".to_string(),
        ],
    }
}

fn unique_config_name_helper_lines() -> Vec<String> {
    vec![
        theme::muted(copy(
            Language::current(),
            "Press Enter to use the default name, or type a custom one.",
            "按 Enter 使用默认名称，或输入自定义名称。",
        ))
        .to_string(),
    ]
}

fn add_generated_config_name_helper_lines() -> Vec<String> {
    vec![
        theme::muted(copy(
            Language::current(),
            "Choose how to name this local CLI config.",
            "选择这个本地 CLI 配置的名称。",
        ))
        .to_string(),
        theme::muted(copy(
            Language::current(),
            "Saved as ovcli.conf.<name>; the active config is ovcli.conf.",
            "保存为 ovcli.conf.<name>；当前配置为 ovcli.conf。",
        ))
        .to_string(),
    ]
}

fn config_name_exists_error(name: &str) -> String {
    match Language::current() {
        Language::En => format!("Config '{name}' already exists. Enter another name."),
        Language::ZhCn => format!("配置 '{name}' 已存在。请输入另一个名称。"),
    }
}

fn reserved_user_management_config_name_error(name: &str) -> String {
    match Language::current() {
        Language::En => format!("Config name '{name}' is reserved. Enter another name."),
        Language::ZhCn => format!("配置名称 '{name}' 已保留。请输入另一个名称。"),
    }
}

pub(super) fn is_legacy_user_management_config_name(name: &str) -> bool {
    name == "active"
}

pub(super) fn save_config(
    store: &ConfigStore,
    name: &str,
    config: &Config,
    activate: bool,
) -> Result<()> {
    validate_new_config_name(store, name)?;
    if activate {
        store.save_and_activate(name, config)?;
        print_saved(store, name, SaveOutcome::Activated, config)
    } else {
        store.save_named_config(name, config)?;
        print_saved(store, name, SaveOutcome::SavedOnly, config)
    }
}

pub(super) fn print_saved(
    store: &ConfigStore,
    name: &str,
    outcome: SaveOutcome,
    config: &Config,
) -> Result<()> {
    println!();
    let message = match outcome {
        SaveOutcome::Activated => saved_message_activated(name),
        SaveOutcome::SavedOnly => saved_message_only(name),
        SaveOutcome::UpdatedActive => saved_message_updated_active(name),
    };
    println!("{} {}", theme::success("✓"), theme::success(message));
    println!(
        "{} {}",
        theme::muted(copy(Language::current(), "Saved to:", "保存到：")),
        store
            .saved_config_path(name)?
            .display()
            .to_string()
            .magenta()
    );
    match outcome {
        SaveOutcome::Activated | SaveOutcome::UpdatedActive => {
            println!(
                "{} {}",
                theme::muted(copy(Language::current(), "Active config:", "当前配置：")),
                store.active_path().display().to_string().magenta()
            );
        }
        SaveOutcome::SavedOnly => {
            println!(
                "{} {}",
                theme::muted(copy(Language::current(), "Activate later:", "稍后启用：")),
                theme::command("ov config switch")
            );
        }
    }
    for line in root_key_normal_command_notice_lines(config) {
        println!("{line}");
    }
    println!(
        "{} {}",
        theme::muted(copy(Language::current(), "Next:", "下一步：")),
        next_step_copy()
    );
    Ok(())
}

fn next_step_copy() -> String {
    match Language::current() {
        Language::En => format!(
            "{}{}{}",
            theme::body("Run "),
            theme::command("ov --help").bold(),
            theme::body(" to get started.")
        ),
        Language::ZhCn => format!(
            "{}{}{}",
            theme::body("运行 "),
            theme::command("ov --help").bold(),
            theme::body(" 查看可用命令。")
        ),
    }
}

fn saved_message_activated(name: &str) -> String {
    match Language::current() {
        Language::En => format!("Saved config '{name}' and made it active."),
        Language::ZhCn => format!("已保存配置 '{name}'，并设为当前配置。"),
    }
}

fn saved_message_only(name: &str) -> String {
    match Language::current() {
        Language::En => format!("Saved config '{name}'."),
        Language::ZhCn => format!("已保存配置 '{name}'。"),
    }
}

fn saved_message_updated_active(name: &str) -> String {
    match Language::current() {
        Language::En => format!("Saved active config '{name}'."),
        Language::ZhCn => format!("已保存当前配置 '{name}'。"),
    }
}

pub(crate) fn add_config_name_label() -> &'static str {
    copy(
        Language::current(),
        "Config name (optional)",
        "配置名称（可选）",
    )
}

fn add_config_name_helper_lines() -> Vec<String> {
    vec![
        theme::muted(copy(
            Language::current(),
            "Leave empty to generate one.",
            "留空将自动生成名称。",
        ))
        .to_string(),
    ]
}

pub(super) fn prompt_add_config_name(
    ui: &mut LiveRegion,
    section: &str,
    prompt: &str,
    store: &ConfigStore,
) -> Result<PromptResult<Option<String>>> {
    let mut error: Option<String> = None;
    loop {
        let mut helper_lines = add_config_name_helper_lines();
        if let Some(value) = error.as_ref() {
            helper_lines.push(theme::error(value).to_string());
        }

        match prompt_text(ui, section, prompt, None, None, true, false, &helper_lines)? {
            PromptResult::Value(value) => {
                let value = value.trim();
                if value.is_empty() {
                    return Ok(PromptResult::Value(None));
                }
                match validate_new_config_name(store, value) {
                    Ok(()) => return Ok(PromptResult::Value(Some(value.to_string()))),
                    Err(next_error) => error = Some(prompt_validation_error(next_error)),
                }
            }
            PromptResult::Back => return Ok(PromptResult::Back),
            PromptResult::Quit => return Ok(PromptResult::Quit),
        }
    }
}

pub(crate) fn allocate_config_name(store: &ConfigStore, kind: ConfigKind) -> Result<String> {
    let prefix = match kind {
        ConfigKind::OpenVikingService => "ov-service",
        ConfigKind::Custom => "custom",
    };

    for _ in 0..32 {
        let suffix = Uuid::new_v4().simple().to_string();
        let candidate = format!("{prefix}-{}", &suffix[..6]);
        if !config_name_exists(store, &candidate)? {
            return Ok(candidate);
        }
    }

    Err(Error::Config(
        "Could not generate a unique config name. Please enter one manually.".to_string(),
    ))
}

pub(super) fn prompt_config_name(
    ui: &mut LiveRegion,
    section: &str,
    prompt: &str,
    default: Option<&str>,
    validate: impl Fn(&str) -> Result<()>,
) -> Result<PromptResult<String>> {
    let mut error: Option<String> = None;
    loop {
        let helper_lines: Vec<String> = error
            .as_ref()
            .map(|value| vec![theme::error(value).to_string()])
            .unwrap_or_default();
        match prompt_text(
            ui,
            section,
            prompt,
            default,
            Some(InputValueLabel::Current),
            false,
            false,
            &helper_lines,
        )? {
            PromptResult::Value(value) => match validate(&value) {
                Ok(()) => return Ok(PromptResult::Value(value)),
                Err(next_error) => error = Some(prompt_validation_error(next_error)),
            },
            PromptResult::Back => return Ok(PromptResult::Back),
            PromptResult::Quit => return Ok(PromptResult::Quit),
        }
    }
}

pub(super) fn prompt_config_select(
    ui: &mut LiveRegion,
    section: &str,
    prompt: &str,
    configs: &[ConfigEntry],
) -> Result<PromptResult<usize>> {
    let items: Vec<String> = configs.iter().map(config_select_label).collect();
    prompt_select(ui, section, prompt, &items, 0, &[])
}

fn config_select_label(entry: &ConfigEntry) -> String {
    let label = format!("{} - {}", entry.name, compact_kind_label(entry.kind));
    if entry.is_active {
        format!("{} {}", label, theme::error(active_badge()).bold())
    } else {
        label
    }
}

pub(super) fn active_config_index(configs: &[ConfigEntry]) -> Option<usize> {
    configs.iter().position(|entry| entry.is_active)
}

pub(super) fn current_server_url_default(store: &ConfigStore) -> Result<String> {
    Ok(store
        .load_active()?
        .map(|config| config.url)
        .filter(|url| !url.trim().is_empty())
        .unwrap_or_else(|| DEFAULT_CUSTOM_URL.to_string()))
}

fn active_badge() -> &'static str {
    copy(Language::current(), "[Active]", "[当前]")
}

fn active_delete_block_helper_lines() -> Vec<String> {
    match Language::current() {
        Language::En => vec![
            theme::error("Deleting the active config is blocked.").to_string(),
            format!(
                "{} {} {}",
                theme::muted("Run"),
                theme::command("ov config switch").bold(),
                theme::muted("to choose another config, then delete this one.")
            ),
        ],
        Language::ZhCn => vec![
            theme::error("不能删除当前配置。").to_string(),
            format!(
                "{} {} {}",
                theme::muted("请先运行"),
                theme::command("ov config switch").bold(),
                theme::muted("切换到其他配置，然后再删除。")
            ),
        ],
    }
}

fn delete_confirm_prompt(name: &str) -> String {
    match Language::current() {
        Language::En => format!("Delete config '{name}'?"),
        Language::ZhCn => format!("删除配置 '{name}'？"),
    }
}

fn switch_confirm_prompt(name: &str) -> String {
    match Language::current() {
        Language::En => format!("Switch active config to '{name}'?"),
        Language::ZhCn => format!("切换当前配置为 '{name}'？"),
    }
}

pub(super) fn localized_validation_error(kind: ConfigKind, error: &Error) -> String {
    match Language::current() {
        Language::ZhCn => validation_error_copy_zh(kind, error),
        _ => validation_error_copy(kind, error),
    }
}

fn deleted_config_message(name: &str) -> String {
    match Language::current() {
        Language::En => format!("Deleted config '{name}'."),
        Language::ZhCn => format!("已删除配置 '{name}'。"),
    }
}

fn switched_config_message(name: &str) -> String {
    match Language::current() {
        Language::En => format!("Switched active config to '{name}'."),
        Language::ZhCn => format!("已切换当前配置为 '{name}'。"),
    }
}

fn config_already_active_message(name: &str) -> String {
    match Language::current() {
        Language::En => format!("Config '{name}' is already active."),
        Language::ZhCn => format!("配置 '{name}' 已是当前配置。"),
    }
}

fn switch_validation_error_lines(name: &str, kind: ConfigKind, error: &Error) -> Vec<String> {
    vec![
        String::new(),
        format!(
            "{} {}",
            theme::error("✗"),
            theme::error(match Language::current() {
                Language::En => format!("Target config '{name}' failed validation."),
                Language::ZhCn => format!("目标配置 '{name}' 验证失败。"),
            })
        ),
        format!(
            "  {}",
            theme::muted(localized_validation_error(kind, error))
        ),
        format!(
            "{} {}",
            theme::muted(copy(Language::current(), "Next:", "下一步：")),
            theme::body(copy(
                Language::current(),
                "Run ov config and edit this config before switching.",
                "请运行 ov config 编辑该配置后再切换。",
            ))
        ),
    ]
}

fn print_switch_validation_error(name: &str, kind: ConfigKind, error: &Error) {
    for line in switch_validation_error_lines(name, kind, error) {
        println!("{line}");
    }
}

pub(crate) fn add_save_action_labels() -> Vec<&'static str> {
    vec!["Save and activate", "Save only", "Cancel"]
}

pub(crate) fn edit_save_action_labels(is_active: bool) -> Vec<&'static str> {
    if is_active {
        vec!["Save changes", "Cancel"]
    } else {
        vec!["Save only", "Save and activate", "Cancel"]
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum SaveActionSet {
    Add,
    EditActive,
    EditInactive,
}

impl SaveActionSet {
    fn labels(self, language: Language) -> Vec<&'static str> {
        match (self, language) {
            (Self::Add, Language::En) => add_save_action_labels(),
            (Self::Add, Language::ZhCn) => vec!["保存并设为当前配置", "仅保存", "取消"],
            (Self::EditActive, Language::En) => edit_save_action_labels(true),
            (Self::EditActive, Language::ZhCn) => vec!["保存更改", "取消"],
            (Self::EditInactive, Language::En) => edit_save_action_labels(false),
            (Self::EditInactive, Language::ZhCn) => {
                vec!["仅保存", "保存并设为当前配置", "取消"]
            }
        }
    }

    fn action(self, index: usize) -> SaveAction {
        match (self, index) {
            (Self::Add, 0) => SaveAction::SaveAndActivate,
            (Self::Add, 1) => SaveAction::SaveOnly,
            (Self::Add, _) => SaveAction::Cancel,
            (Self::EditActive, 0) => SaveAction::SaveActive,
            (Self::EditActive, _) => SaveAction::Cancel,
            (Self::EditInactive, 0) => SaveAction::SaveOnly,
            (Self::EditInactive, 1) => SaveAction::SaveAndActivate,
            (Self::EditInactive, _) => SaveAction::Cancel,
        }
    }
}

pub(super) fn prompt_save_action(
    ui: &mut LiveRegion,
    section: &str,
    prompt: &str,
    action_set: SaveActionSet,
    default: usize,
) -> Result<PromptResult<SaveAction>> {
    let items = action_set.labels(Language::current());
    match prompt_select(ui, section, prompt, &items, default, &[])? {
        PromptResult::Value(index) => Ok(PromptResult::Value(action_set.action(index))),
        PromptResult::Back => Ok(PromptResult::Back),
        PromptResult::Quit => Ok(PromptResult::Quit),
    }
}

#[cfg(test)]
#[path = "tests/profiles.rs"]
mod tests;
