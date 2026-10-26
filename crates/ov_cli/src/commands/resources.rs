use crate::client::{HttpClient, ResourceWaitProgress};
use crate::error::{Error, Result};
use crate::i18n::{Language, copy};
use crate::output::{OutputFormat, output_success};
use serde_json::{Map, Value};

fn report_resource_wait(event: ResourceWaitProgress<'_>) {
    let language = Language::current();
    match event {
        ResourceWaitProgress::Waiting(task_id) => eprintln!(
            "{} {task_id}. {}",
            copy(language, "Waiting for task", "正在等待任务"),
            copy(
                language,
                "Ctrl+C stops waiting; the task continues on the server.",
                "Ctrl+C 仅停止等待，任务继续在服务端运行。"
            )
        ),
        ResourceWaitProgress::Retrying {
            task_id,
            error,
            failures,
            delay,
        } => eprintln!(
            "{} {task_id}: {error}; {} {failures}/10, {} {}s.",
            copy(language, "Task", "任务"),
            copy(
                language,
                "consecutive status query failures",
                "连续状态查询失败"
            ),
            copy(language, "retrying in", "重试间隔"),
            delay.as_secs()
        ),
        ResourceWaitProgress::Status { task_id, progress } => {
            eprintln!("{} {task_id}: {progress}", copy(language, "Task", "任务"))
        }
    }
}

pub(crate) fn report_resource_wait_interrupted(task_id: &str) {
    eprintln!(
        "{} ov task status {task_id}",
        copy(
            Language::current(),
            "Stopped waiting. The server task was not cancelled. Check progress with:",
            "已停止等待，服务端任务未取消。查询进度："
        )
    );
}

pub async fn add_resource(
    client: &HttpClient,
    path: &str,
    add_type: Option<String>,
    to: Option<String>,
    parent: Option<String>,
    parent_auto_create: Option<String>,
    reason: String,
    instruction: String,
    wait: bool,
    strict: bool,
    ignore_dirs: Option<String>,
    include: Option<String>,
    exclude: Option<String>,
    directly_upload_media: bool,
    watch_interval: f64,
    processing_mode: String,
    resource_args: Option<Map<String, Value>>,
    tags: Vec<String>,
    tag_mode: String,
    acl: Option<serde_json::Value>,
    format: OutputFormat,
    compact: bool,
    show_progress: bool,
    verbose: bool,
) -> Result<()> {
    let client = client
        .clone()
        .with_resource_wait_reporter(report_resource_wait);
    let result = client
        .add_resource(
            path,
            add_type,
            to,
            parent,
            parent_auto_create,
            &reason,
            &instruction,
            wait,
            strict,
            ignore_dirs,
            include,
            exclude,
            directly_upload_media,
            watch_interval,
            processing_mode,
            resource_args,
            tags,
            tag_mode,
            acl,
            show_progress,
            verbose,
        )
        .await;
    if let Err(Error::Interrupted { task_id }) = &result {
        report_resource_wait_interrupted(task_id);
    }
    let result = result?;

    if !wait && matches!(format, OutputFormat::Table) {
        eprintln!("Note: Resource is being processed in the background.");
        eprintln!(
            "Use 'ov task status <task_id>' to check progress, or 'ov task list' to see all tasks."
        );
    }

    output_success(&result, format, compact);
    Ok(())
}
