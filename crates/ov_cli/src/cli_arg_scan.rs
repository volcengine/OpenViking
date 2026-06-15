use std::collections::BTreeSet;

use clap::{Arg, ArgAction, Command};

#[derive(Debug, Clone, Default)]
pub(crate) struct ValueOptions {
    tokens: BTreeSet<String>,
}

impl ValueOptions {
    pub(crate) fn from_command(command: &Command) -> Self {
        let mut options = Self::default();
        options.collect_from_command(command);
        options
    }

    pub(crate) fn from_command_arguments(command: &Command) -> Self {
        let mut options = Self::default();
        options.collect_from_arguments(command);
        options
    }

    pub(crate) fn consumes_value(&self, token: &str) -> bool {
        let option_token = token.split_once('=').map(|(name, _)| name).unwrap_or(token);
        self.tokens.contains(option_token)
    }

    fn collect_from_command(&mut self, command: &Command) {
        self.collect_from_arguments(command);
        for subcommand in command.get_subcommands() {
            self.collect_from_command(subcommand);
        }
    }

    fn collect_from_arguments(&mut self, command: &Command) {
        for arg in command
            .get_arguments()
            .filter(|arg| arg_consumes_value(arg))
        {
            self.insert_arg(arg);
        }
    }

    fn insert_arg(&mut self, arg: &Arg) {
        if let Some(short) = arg.get_short() {
            self.tokens.insert(format!("-{short}"));
        }
        if let Some(short_aliases) = arg.get_all_short_aliases() {
            for short_alias in short_aliases {
                self.tokens.insert(format!("-{short_alias}"));
            }
        }
        if let Some(long) = arg.get_long() {
            self.tokens.insert(format!("--{long}"));
        }
        if let Some(aliases) = arg.get_all_aliases() {
            for alias in aliases {
                self.tokens.insert(format!("--{alias}"));
            }
        }
    }
}

fn arg_consumes_value(arg: &Arg) -> bool {
    arg.get_num_args().unwrap_or_default().takes_values()
        && !matches!(
            arg.get_action(),
            ArgAction::SetTrue
                | ArgAction::SetFalse
                | ArgAction::Help
                | ArgAction::HelpShort
                | ArgAction::HelpLong
                | ArgAction::Version
        )
}

#[cfg(test)]
mod tests {
    use clap::{Arg, ArgAction, Command};

    use super::ValueOptions;

    #[test]
    fn value_options_are_discovered_from_clap_metadata() {
        let mut command = Command::new("ov")
            .arg(
                Arg::new("owner")
                    .long("owner")
                    .short('O')
                    .short_alias('P')
                    .alias("assignee")
                    .num_args(1),
            )
            .arg(Arg::new("flag").long("flag").action(ArgAction::SetTrue))
            .subcommand(
                Command::new("task")
                    .arg(Arg::new("status").long("status").num_args(1))
                    .arg(
                        Arg::new("active-only")
                            .long("active-only")
                            .action(ArgAction::SetTrue),
                    ),
            );
        command.build();

        let value_options = ValueOptions::from_command(&command);

        assert!(value_options.consumes_value("--owner"));
        assert!(value_options.consumes_value("--owner=help"));
        assert!(value_options.consumes_value("-O"));
        assert!(value_options.consumes_value("-P"));
        assert!(value_options.consumes_value("--assignee"));
        assert!(value_options.consumes_value("--assignee=help"));
        assert!(value_options.consumes_value("--status"));
        assert!(value_options.consumes_value("--status=help"));

        assert!(!value_options.consumes_value("--flag"));
        assert!(!value_options.consumes_value("--active-only"));
        assert!(!value_options.consumes_value("--help"));
    }

    #[test]
    fn root_value_options_ignore_subcommand_options() {
        let mut command = Command::new("ov")
            .arg(Arg::new("output").long("output").short('o').num_args(1))
            .subcommand(Command::new("task").arg(Arg::new("status").long("status").num_args(1)));
        command.build();

        let value_options = ValueOptions::from_command_arguments(&command);

        assert!(value_options.consumes_value("--output"));
        assert!(value_options.consumes_value("--output=json"));
        assert!(value_options.consumes_value("-o"));
        assert!(!value_options.consumes_value("--status"));
    }
}
