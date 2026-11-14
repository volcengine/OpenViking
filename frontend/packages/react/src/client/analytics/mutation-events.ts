import { operation, completed, failed } from './fields.js';

export const mutationEvents = {
  file_browser_pack_import_submit: {
    zh: '内置 ovpack 导入：开始执行。',
    en: 'Built-in ovpack import: execution starts.',
    fields: operation,
  },
  file_browser_pack_import_success: {
    zh: '内置 ovpack 导入：执行完成。',
    en: 'Built-in ovpack import: execution completes.',
    fields: completed,
  },
  file_browser_pack_import_fail: {
    zh: '内置 ovpack 导入：执行失败，不包含错误原文。',
    en: 'Built-in ovpack import: execution fails; raw errors are omitted.',
    fields: failed,
  },
  file_browser_pack_export_submit: {
    zh: '内置 ovpack 导出并发起下载：开始执行。',
    en: 'Built-in ovpack export and download initiation: execution starts.',
    fields: operation,
  },
  file_browser_pack_export_success: {
    zh: '内置 ovpack 导出并发起下载：执行完成。',
    en: 'Built-in ovpack export and download initiation: execution completes.',
    fields: completed,
  },
  file_browser_pack_export_fail: {
    zh: '内置 ovpack 导出并发起下载：执行失败，不包含错误原文。',
    en: 'Built-in ovpack export and download initiation: execution fails; raw errors are omitted.',
    fields: failed,
  },
  file_browser_resource_delete_submit: {
    zh: '确认后删除资源：开始执行。',
    en: 'Resource deletion after confirmation: execution starts.',
    fields: operation,
  },
  file_browser_resource_delete_success: {
    zh: '确认后删除资源：执行完成。',
    en: 'Resource deletion after confirmation: execution completes.',
    fields: completed,
  },
  file_browser_resource_delete_fail: {
    zh: '确认后删除资源：执行失败，不包含错误原文。',
    en: 'Resource deletion after confirmation: execution fails; raw errors are omitted.',
    fields: failed,
  },
  file_browser_compile_config_save_submit: {
    zh: '保存编译配置，不代表编译完成：开始执行。',
    en: 'Compile configuration save, not compilation completion: execution starts.',
    fields: operation,
  },
  file_browser_compile_config_save_success: {
    zh: '保存编译配置，不代表编译完成：执行完成。',
    en: 'Compile configuration save, not compilation completion: execution completes.',
    fields: completed,
  },
  file_browser_compile_config_save_fail: {
    zh: '保存编译配置，不代表编译完成：执行失败，不包含错误原文。',
    en: 'Compile configuration save, not compilation completion: execution fails; raw errors are omitted.',
    fields: failed,
  },
  file_browser_acl_change_submit: {
    zh: '授权变更：开始执行。',
    en: 'ACL change: execution starts.',
    fields: operation,
  },
  file_browser_acl_change_success: {
    zh: '授权变更：执行完成。',
    en: 'ACL change: execution completes.',
    fields: completed,
  },
  file_browser_acl_change_fail: {
    zh: '授权变更：执行失败，不包含错误原文。',
    en: 'ACL change: execution fails; raw errors are omitted.',
    fields: failed,
  },
} as const;
