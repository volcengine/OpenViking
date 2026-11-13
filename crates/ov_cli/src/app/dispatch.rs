//! Dispatch parsed commands to the existing handlers.

use crate::CliContext;
use crate::cli::{
    AttrsCommands, Commands, CompileCommands, McpCommands, SkillCommands, TaskCommands,
    UploadCliOptions, WatchCommands,
};
use crate::commands;
use crate::config;
use crate::error::{self, Result};
use crate::handlers;
use crate::openviking_assets;
use crate::output::OutputFormat;
use crate::theme;
use colored::Colorize;

pub(super) async fn dispatch(
    command: Commands,
    ctx: CliContext,
    legacy_upload_options: UploadCliOptions,
) -> Result<()> {
    match command {
        Commands::AddResource {
            path,
            add_type,
            manifest,
            to,
            parent,
            parent_auto_create,
            reason,
            instruction,
            wait,
            tags,
            tag_mode,
            acl,
            strict_mode,
            ignore_dirs,
            include,
            exclude,
            no_directly_upload_media,
            watch_interval,
            processing_mode,
            resource_args,
            upload_options,
        } => {
            let ctx =
                ctx.with_upload_options(upload_options.merged_with_legacy(legacy_upload_options));
            if let Some(manifest) = manifest {
                match handlers::parse_add_resource_args(resource_args.as_deref())
                    .and_then(|args| openviking_assets::parse_manifest_run_args(args.as_ref()))
                {
                    Err(e) => Err(e),
                    Ok(run) => {
                        openviking_assets::handle_manifest_apply(
                            manifest,
                            run.catalog,
                            openviking_assets::ManifestRunOptions {
                                dry_run: run.dry_run,
                                skip_failed: run.skip_failed,
                                wait,
                                watch_interval,
                                processing_mode,
                                external_connector: run.external_connector,
                            },
                            ctx,
                        )
                        .await
                    }
                }
            } else if let Some(path) = path {
                handlers::handle_add_resource(
                    path,
                    add_type,
                    to,
                    parent,
                    parent_auto_create,
                    reason,
                    instruction,
                    wait,
                    strict_mode,
                    ignore_dirs,
                    include,
                    exclude,
                    no_directly_upload_media,
                    watch_interval.unwrap_or(0.0),
                    processing_mode,
                    resource_args,
                    tags,
                    tag_mode,
                    acl,
                    ctx,
                )
                .await
            } else {
                Err(error::Error::Client(
                    "a path/URL or --manifest is required".to_string(),
                ))
            }
        }
        Commands::AddSkill(args) => {
            handlers::handle_add_skill(args, legacy_upload_options, ctx).await
        }
        Commands::Skills { action } => match action {
            SkillCommands::Add(args) => {
                handlers::handle_add_skill(args, legacy_upload_options, ctx).await
            }
            SkillCommands::List { node_limit, parent } => {
                let client = ctx.get_client();
                commands::skills::list(
                    &client,
                    node_limit,
                    ctx.output_format,
                    ctx.compact,
                    parent.as_deref(),
                )
                .await
            }
            SkillCommands::Find {
                query,
                node_limit,
                threshold,
                level,
                parent,
            } => {
                let client = ctx.get_client();
                commands::skills::find(
                    &client,
                    &query,
                    node_limit,
                    threshold,
                    level,
                    ctx.output_format,
                    ctx.compact,
                    parent.as_deref(),
                )
                .await
            }
            SkillCommands::Show {
                name,
                level,
                files,
                source,
                format,
                content,
                parent,
            } => {
                let client = ctx.get_client();
                let output_format = format
                    .as_deref()
                    .map(OutputFormat::from)
                    .unwrap_or(ctx.output_format);
                let level = if content { Some(2) } else { level };
                commands::skills::show(
                    &client,
                    &name,
                    level,
                    files,
                    source,
                    output_format,
                    ctx.compact,
                    parent.as_deref(),
                )
                .await
            }
            SkillCommands::Update {
                skills,
                wait,
                yes,
                parent,
            } => {
                let client = ctx.get_client();
                commands::skills::update(
                    &client,
                    skills,
                    wait,
                    yes,
                    ctx.output_format,
                    ctx.compact,
                    parent.as_deref(),
                )
                .await
            }
            SkillCommands::Remove {
                skills,
                all,
                yes,
                parent,
            } => {
                let client = ctx.get_client();
                commands::skills::remove(
                    &client,
                    skills,
                    all,
                    yes,
                    ctx.output_format,
                    ctx.compact,
                    parent.as_deref(),
                )
                .await
            }
            SkillCommands::Validate { path, strict } => {
                let client = ctx.get_client();
                commands::skills::validate(&client, &path, strict, ctx.output_format, ctx.compact)
                    .await
            }
        },
        Commands::Mcps { action } => {
            let client = ctx.get_client();
            match action {
                McpCommands::Add {
                    config,
                    wait,
                    target_uri,
                } => {
                    commands::mcps::add(
                        &client,
                        &config,
                        wait,
                        target_uri.as_deref(),
                        ctx.output_format,
                        ctx.compact,
                    )
                    .await
                }
                McpCommands::Remove { name, target_uri } => {
                    commands::mcps::remove(
                        &client,
                        &name,
                        target_uri.as_deref(),
                        ctx.output_format,
                        ctx.compact,
                    )
                    .await
                }
                McpCommands::List { target_uri } => {
                    commands::mcps::list(
                        &client,
                        target_uri.as_deref(),
                        ctx.output_format,
                        ctx.compact,
                    )
                    .await
                }
                McpCommands::Find {
                    query,
                    limit,
                    target_uri,
                } => {
                    commands::mcps::find(
                        &client,
                        &query,
                        limit,
                        target_uri.as_deref(),
                        ctx.output_format,
                        ctx.compact,
                    )
                    .await
                }
                McpCommands::Validate { config } => {
                    commands::mcps::validate(&client, &config, ctx.output_format, ctx.compact).await
                }
                McpCommands::Show { name, target_uri } => {
                    commands::mcps::show(
                        &client,
                        &name,
                        target_uri.as_deref(),
                        ctx.output_format,
                        ctx.compact,
                    )
                    .await
                }
                McpCommands::Sync {
                    name,
                    wait,
                    target_uri,
                } => {
                    commands::mcps::sync(
                        &client,
                        &name,
                        wait,
                        target_uri.as_deref(),
                        ctx.output_format,
                        ctx.compact,
                    )
                    .await
                }
                McpCommands::Status { name, target_uri } => {
                    commands::mcps::status(
                        &client,
                        &name,
                        target_uri.as_deref(),
                        ctx.output_format,
                        ctx.compact,
                    )
                    .await
                }
                McpCommands::Mount {
                    tool_uri,
                    node_uri,
                    title,
                    description,
                    timeout_seconds,
                    arguments,
                    wait,
                } => {
                    commands::mcps::mount(
                        &client,
                        &tool_uri,
                        &node_uri,
                        &title,
                        description.as_deref(),
                        timeout_seconds,
                        &arguments,
                        wait,
                        ctx.output_format,
                        ctx.compact,
                    )
                    .await
                }
                McpCommands::Unmount { node_uri, wait } => {
                    commands::mcps::unmount(
                        &client,
                        &node_uri,
                        wait,
                        ctx.output_format,
                        ctx.compact,
                    )
                    .await
                }
                McpCommands::Mounts {
                    mcp,
                    tool,
                    target_uri,
                } => {
                    commands::mcps::mounts(
                        &client,
                        &mcp,
                        tool.as_deref(),
                        target_uri.as_deref(),
                        ctx.output_format,
                        ctx.compact,
                    )
                    .await
                }
                McpCommands::Invoke { uri, arguments } => {
                    commands::mcps::invoke(
                        &client,
                        &uri,
                        &arguments,
                        ctx.output_format,
                        ctx.compact,
                    )
                    .await
                }
            }
        }
        Commands::Export {
            uri,
            to,
            include_vectors,
        } => handlers::handle_export(uri, to, include_vectors, ctx).await,
        Commands::Backup {
            to,
            include_vectors,
        } => handlers::handle_backup(to, include_vectors, ctx).await,
        Commands::Import {
            file_path,
            target_uri,
            on_conflict,
            vector_mode,
        } => handlers::handle_import(file_path, target_uri, on_conflict, vector_mode, ctx).await,
        Commands::Restore {
            file_path,
            on_conflict,
            vector_mode,
        } => handlers::handle_restore(file_path, on_conflict, vector_mode, ctx).await,
        Commands::Wait => {
            let client = ctx.get_client();
            commands::system::wait(&client, ctx.output_format, ctx.compact).await
        }
        Commands::Task { action } => match action {
            TaskCommands::Status { task_id } => {
                let client = ctx.get_client();
                commands::task::status(&client, &task_id, ctx.output_format, ctx.compact).await
            }
            TaskCommands::Cancel { task_id } => {
                let client = ctx.get_client();
                commands::task::cancel(&client, &task_id, ctx.output_format, ctx.compact).await
            }
            TaskCommands::List { task_type, status } => {
                let client = ctx.get_client();
                commands::task::list(
                    &client,
                    task_type.as_deref(),
                    status.as_deref(),
                    ctx.output_format,
                    ctx.compact,
                )
                .await
            }
            TaskCommands::Watch { action } => {
                let client = ctx.get_client();
                match action {
                    WatchCommands::Ls { active_only } => {
                        commands::watch::ls(&client, active_only, ctx.output_format, ctx.compact)
                            .await
                    }
                    WatchCommands::Show { key } => {
                        commands::watch::show(&client, &key, ctx.output_format, ctx.compact).await
                    }
                    WatchCommands::Rm { key } => {
                        commands::watch::rm(&client, &key, ctx.output_format, ctx.compact).await
                    }
                    WatchCommands::Pause { key } => {
                        commands::watch::pause(&client, &key, ctx.output_format, ctx.compact).await
                    }
                    WatchCommands::Resume { key } => {
                        commands::watch::resume(&client, &key, ctx.output_format, ctx.compact).await
                    }
                    WatchCommands::Update {
                        key,
                        interval,
                        active,
                        reason,
                        instruction,
                    } => {
                        commands::watch::update(
                            &client,
                            &key,
                            interval,
                            active,
                            reason,
                            instruction,
                            ctx.output_format,
                            ctx.compact,
                        )
                        .await
                    }
                    WatchCommands::Trigger { key } => {
                        commands::watch::trigger(&client, &key, ctx.output_format, ctx.compact)
                            .await
                    }
                }
            }
        },
        Commands::Snapshot { cmd } => {
            let client = ctx.get_client();
            commands::snapshot::dispatch(&client, cmd, ctx.output_format, ctx.compact).await
        }
        Commands::Status { verbose } => {
            let client = ctx.get_client();
            commands::system::diagnostic_status(
                &client,
                &ctx.config,
                ctx.output_format,
                ctx.compact,
                verbose,
            )
            .await
        }
        Commands::Health => handlers::handle_health(ctx).await,
        Commands::System { action } => handlers::handle_system(action, ctx).await,
        Commands::Observer { action } => handlers::handle_observer(action, ctx).await,
        Commands::Session { action } => handlers::handle_session(action, ctx).await,
        Commands::Admin { action } => handlers::handle_admin(action, ctx).await,
        Commands::Privacy { action } => handlers::handle_privacy(*action, ctx).await,
        Commands::Ls {
            uri,
            simple,
            recursive,
            abs_limit,
            include_abstract,
            include_overview,
            overview_limit,
            all,
            node_limit,
            offset,
            limit,
            sort_by,
            sort_order,
            fields,
            tags,
        } => {
            handlers::handle_ls(
                uri,
                simple,
                recursive,
                abs_limit,
                include_abstract,
                include_overview,
                overview_limit,
                all,
                node_limit,
                offset,
                limit,
                sort_by,
                sort_order,
                fields,
                tags,
                ctx,
            )
            .await
        }
        Commands::Tree {
            uri,
            abs_limit,
            include_abstract,
            include_overview,
            overview_limit,
            all,
            directories_only,
            node_limit,
            offset,
            limit,
            level_limit,
            simple,
            fields,
            tags,
        } => {
            handlers::handle_tree(
                uri,
                abs_limit,
                include_abstract,
                include_overview,
                overview_limit,
                all,
                directories_only,
                node_limit,
                offset,
                limit,
                level_limit,
                simple,
                fields,
                tags,
                ctx,
            )
            .await
        }
        Commands::Mkdir {
            uri,
            description,
            acl,
        } => handlers::handle_mkdir(uri, description, acl, ctx).await,
        Commands::Rm {
            uri,
            recursive,
            wait,
        } => handlers::handle_rm(uri, recursive, wait, ctx).await,
        Commands::Cp {
            from_uri,
            to_uri,
            recursive,
        } => handlers::handle_cp(from_uri, to_uri, recursive, ctx).await,
        Commands::Mv { from_uri, to_uri } => handlers::handle_mv(from_uri, to_uri, ctx).await,
        Commands::Stat { uri } => handlers::handle_stat(uri, ctx).await,
        Commands::Attrs { action } => match action {
            AttrsCommands::Get { uri, key } => handlers::handle_attrs(uri, key, ctx).await,
            AttrsCommands::SetTags {
                uri,
                tags,
                mode,
                recursive,
            } => handlers::handle_set_tags(uri, tags, mode, recursive, ctx).await,
            AttrsCommands::BatchSetTags {
                uris,
                tags,
                mode,
                recursive,
            } => handlers::handle_batch_set_tags(uris, tags, mode, recursive, ctx).await,
        },
        Commands::Acl { action } => handlers::handle_acl(action, ctx).await,
        Commands::AddMemory { content } => handlers::handle_add_memory(content, ctx).await,
        Commands::Tui { uri } => handlers::handle_tui(uri, ctx).await,
        Commands::Chat {
            message,
            session,
            sender,
            stream,
            no_format,
            no_history,
        } => {
            let session_id = session.or_else(|| config::get_or_create_machine_id().ok());
            let endpoint = if let Ok(env_endpoint) = std::env::var("VIKINGBOT_ENDPOINT") {
                Some(env_endpoint)
            } else if let Ok(config_url) = std::env::var("OPENVIKING_URL") {
                Some(format!("{}/bot/v1", config_url))
            } else {
                None
            };
            let api_key = std::env::var("VIKINGBOT_API_KEY").ok();
            let cmd = commands::chat::ChatCommand {
                endpoint,
                api_key,
                account: ctx.config.account.clone(),
                user: ctx.config.user.clone(),
                actor_peer_id: ctx.config.effective_actor_peer_id(),
                session: session_id,
                sender,
                message,
                stream,
                no_format,
                no_history,
            };
            cmd.run().await
        }
        Commands::Compile {
            command,
            compile_name,
            from_uris,
            to,
            skill,
            instruction,
            args,
        } => {
            let client = ctx.get_client();
            if let Some(compile_name) = compile_name {
                return commands::compile::run_saved(
                    &client,
                    &compile_name,
                    args,
                    ctx.output_format,
                    ctx.compact,
                )
                .await;
            }
            let (from_uris, to, skill, instruction, args, compile_name) = match command {
                Some(CompileCommands::Create {
                    compile_name,
                    from_uris,
                    to,
                    skill,
                    instruction,
                    args,
                }) => (
                    from_uris,
                    Some(to),
                    skill,
                    instruction,
                    args,
                    Some(compile_name),
                ),
                Some(CompileCommands::Memory {
                    to,
                    instruction,
                    args,
                }) => (
                    Vec::new(),
                    Some(to),
                    "memory".to_string(),
                    instruction,
                    args,
                    None,
                ),
                None => (
                    from_uris,
                    to,
                    skill.expect("--skill is required without --compile-name"),
                    instruction,
                    args,
                    None,
                ),
            };
            commands::compile::run(
                &client,
                from_uris,
                to,
                skill,
                instruction,
                args,
                compile_name,
                ctx.output_format,
                ctx.compact,
            )
            .await
        }
        Commands::Config { action } => handlers::handle_config(action, ctx).await,
        Commands::Language { .. } => unreachable!("language command is handled before config load"),
        Commands::Version => {
            println!(
                "{}     {}",
                theme::muted("CLI:"),
                theme::version(env!("OPENVIKING_CLI_VERSION")).bold()
            );

            // Try to get server version from /health endpoint with a short timeout (3 seconds)
            let client = ctx.get_client_with_timeout(Some(3.0));
            match client.get::<serde_json::Value>("/health", &[]).await {
                Ok(health) => {
                    if let Some(version) = health.get("version").and_then(|v| v.as_str()) {
                        println!(
                            "{}  {}",
                            theme::muted("Server:"),
                            theme::sky_value(version).bold()
                        );
                    }
                }
                Err(_) => {
                    // If can't connect to server, just don't print server version
                }
            }
            Ok(())
        }
        Commands::Read { uri, offset, limit } => {
            handlers::handle_read(uri, offset, limit, ctx).await
        }
        Commands::Abstract { uri } => handlers::handle_abstract(uri, ctx).await,
        Commands::Overview { uri } => handlers::handle_overview(uri, ctx).await,
        Commands::Write {
            uri,
            content,
            from_file,
            append,
            mode,
            wait,
            processing_mode,
            tags,
            tag_mode,
            acl,
        } => {
            let effective_mode = if let Some(m) = mode {
                m
            } else if append {
                "append".to_string()
            } else {
                "replace".to_string()
            };
            handlers::handle_write(
                uri,
                content,
                from_file,
                effective_mode,
                wait,
                processing_mode,
                tags,
                tag_mode,
                acl,
                ctx,
            )
            .await
        }
        Commands::SetTags {
            uri,
            tags,
            mode,
            recursive,
        } => handlers::handle_set_tags(uri, tags, mode, recursive, ctx).await,
        Commands::Reindex {
            uri,
            mode,
            wait,
            force,
            tags,
            tag_mode,
            recursive,
        } => handlers::handle_reindex(uri, mode, wait, force, tags, tag_mode, recursive, ctx).await,
        Commands::Get { uri, local_path } => handlers::handle_get(uri, local_path, ctx).await,
        Commands::Find {
            query,
            image,
            uri,
            node_limit,
            threshold,
            after,
            before,
            level,
            context_type,
            tags,
            read_content,
            events_time_decay_protection,
        } => {
            handlers::handle_find(
                query,
                uri,
                image,
                node_limit,
                threshold,
                after,
                before,
                level,
                context_type,
                tags,
                read_content,
                events_time_decay_protection,
                ctx,
            )
            .await
        }
        Commands::Search {
            query,
            image,
            search_type,
            mode,
            uri,
            session_id,
            node_limit,
            threshold,
            after,
            before,
            level,
            context_type,
            tags,
            read_content,
            events_time_decay_protection,
        } => {
            handlers::handle_search(
                query,
                uri,
                image,
                search_type,
                mode,
                session_id,
                node_limit,
                threshold,
                after,
                before,
                level,
                context_type,
                tags,
                read_content,
                events_time_decay_protection,
                ctx,
            )
            .await
        }
        Commands::Grep {
            uri,
            exclude_uri,
            pattern,
            ignore_case,
            after_context,
            before_context,
            node_limit,
            level_limit,
            tags,
            fields,
        } => {
            handlers::handle_grep(
                uri,
                exclude_uri,
                pattern,
                ignore_case,
                after_context,
                before_context,
                node_limit,
                level_limit,
                tags,
                fields,
                ctx,
            )
            .await
        }

        Commands::Glob {
            pattern,
            uri,
            node_limit,
            simple,
            fields,
            tags,
        } => handlers::handle_glob(pattern, uri, node_limit, simple, fields, tags, ctx).await,
    }
}
