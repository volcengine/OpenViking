import { operation, completed, failed } from './fields.js';

export const editingEvents = {
  file_preview_save_submit: {
    zh: '保存正文：开始执行。',
    en: 'Content save: execution starts.',
    fields: operation,
  },
  file_preview_save_success: {
    zh: '保存正文：执行完成。',
    en: 'Content save: execution completes.',
    fields: { ...completed, version_status: ['not-created', 'unknown'] },
  },
  file_preview_save_fail: {
    zh: '保存正文：执行失败，不包含错误原文。',
    en: 'Content save: execution fails; raw errors are omitted.',
    fields: failed,
  },
  file_preview_download_start: {
    zh: '浏览器下载已发起，不代表文件已写入磁盘。',
    en: 'Browser download is initiated; this does not imply the file was saved to disk.',
    fields: completed,
  },
  file_preview_download_fail: {
    zh: '下载发起失败。',
    en: 'Download initiation fails.',
    fields: failed,
  },
} as const;
