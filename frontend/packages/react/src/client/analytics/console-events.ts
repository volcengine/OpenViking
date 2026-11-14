import { operation, completed, failed } from './fields.js';

export const consoleEvents = {
  console_command_panel_show: {
    zh: '命令面板实际可见。',
    en: 'Command panel becomes visible.',
    fields: {},
  },
  console_chat_panel_show: {
    zh: '对话面板实际可见。',
    en: 'Chat panel becomes visible.',
    fields: {},
  },
  console_command_mode_select: {
    zh: '用户选择命令模式。',
    en: 'The user selects command mode.',
    fields: {},
  },
  console_chat_mode_select: {
    zh: '用户选择对话模式。',
    en: 'The user selects conversation mode.',
    fields: {},
  },
  console_command_preset_select: {
    zh: '用户选择命令预设，不代表执行。',
    en: 'The user selects a command preset; this does not imply execution.',
    fields: {},
  },
  console_command_submit: {
    zh: '开始执行非空命令，clear 除外。',
    en: 'A nonempty command starts execution, excluding clear.',
    fields: operation,
  },
  console_command_accepted: {
    zh: '异步任务已受理，不代表执行完成。',
    en: 'An asynchronous task is accepted, not completed.',
    fields: completed,
  },
  console_command_success: {
    zh: '同步命令成功或关联任务全部完成。',
    en: 'A synchronous command succeeds or all associated tasks complete.',
    fields: completed,
  },
  console_command_fail: {
    zh: '命令或关联任务失败。',
    en: 'The command or an associated task fails.',
    fields: failed,
  },
  console_command_cancel: {
    zh: '用户取消命令，或关联任务确认取消。',
    en: 'The user cancels a command or an associated task confirms cancellation.',
    fields: completed,
  },
  command_console_command_submit: {
    zh: '命令视图发出提交意图，不代表执行成功。',
    en: 'The command view requests submission; execution success is not implied.',
    fields: {},
  },
} as const;
