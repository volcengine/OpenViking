//! Interactive configuration entry point and main menu.

mod add;
mod admin;
mod credentials;
mod edit;
mod profiles;
mod prompts;
mod status;
mod users;

#[cfg(test)]
mod test_support;

use self::add::run_add_config;
use self::edit::run_edit_config;
use self::profiles::{run_delete_config, run_switch_config};
use self::prompts::{LiveRegion, PromptResult, print_cancelled, prompt_select};
use self::status::{live_status_box_frame, print_full_header, print_status_box};
use self::users::run_user_management;
use crate::config_wizard::store::ConfigStore;
use crate::error::Result;
use crate::i18n::{self, Language, copy};
use crate::theme;
use colored::Colorize;

pub async fn run_config_wizard() -> Result<()> {
    let store = ConfigStore::new()?;
    run_config_wizard_with_store(store).await
}

async fn run_config_wizard_with_store(store: ConfigStore) -> Result<()> {
    let mut ui = LiveRegion::default();
    if !ensure_language_selected(&mut ui)? {
        return Ok(());
    }
    print_full_header(live_status_box_frame().mode);
    print_status_box(&store).await?;

    loop {
        let language = Language::current();
        match prompt_select(
            &mut ui,
            copy(
                language,
                "What would you like to configure?",
                "你想配置什么？",
            ),
            copy(language, "Choose action", "选择操作"),
            &main_action_labels_for_language(language),
            0,
            &[],
        )? {
            PromptResult::Value(0) => {
                if run_add_config(&store, &mut ui).await? {
                    return Ok(());
                }
            }
            PromptResult::Value(1) => {
                if run_switch_config(&store, &mut ui).await? {
                    return Ok(());
                }
            }
            PromptResult::Value(2) => {
                if run_edit_config(&store, &mut ui).await? {
                    return Ok(());
                }
            }
            PromptResult::Value(3) => {
                if run_delete_config(&store, &mut ui)? {
                    return Ok(());
                }
            }
            PromptResult::Value(4) => {
                if run_user_management(&store, &mut ui).await? {
                    return Ok(());
                }
            }
            PromptResult::Back | PromptResult::Quit => {
                print_cancelled(&mut ui)?;
                return Ok(());
            }
            PromptResult::Value(_) => unreachable!("selection is constrained by action list"),
        }
    }
}

fn ensure_language_selected(ui: &mut LiveRegion) -> Result<bool> {
    if i18n::has_saved_language() {
        return Ok(true);
    }

    let choices = ["English", "简体中文"];
    match prompt_select(
        ui,
        "Language / 语言",
        "Choose display language / 选择显示语言",
        &choices,
        0,
        &[format!(
            "{} {}",
            theme::muted("Change later:"),
            theme::command("ov language").bold()
        )],
    )? {
        PromptResult::Value(0) => i18n::save_language(Language::En)?,
        PromptResult::Value(1) => i18n::save_language(Language::ZhCn)?,
        PromptResult::Back | PromptResult::Quit => {
            print_cancelled(ui)?;
            return Ok(false);
        }
        PromptResult::Value(_) => unreachable!("selection is constrained by language list"),
    }

    ui.clear()?;
    Ok(true)
}

pub(crate) fn main_action_labels() -> [&'static str; 5] {
    [
        "Add Config",
        "Switch Config",
        "Edit Config",
        "Delete Config",
        "User Management",
    ]
}

fn main_action_labels_for_language(language: Language) -> [&'static str; 5] {
    match language {
        Language::En => main_action_labels(),
        Language::ZhCn => ["添加配置", "切换配置", "编辑配置", "删除配置", "用户管理"],
    }
}

#[cfg(test)]
mod tests {
    use super::main_action_labels;

    #[test]
    fn wizard_main_actions_include_switch_config() {
        assert_eq!(
            main_action_labels().as_slice(),
            [
                "Add Config",
                "Switch Config",
                "Edit Config",
                "Delete Config",
                "User Management",
            ]
            .as_slice()
        );
    }
}
