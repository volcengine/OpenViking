import type { TuiRecord } from '@openviking/react/components/tui';
import { createTuiCompileRecord } from './tui-result-fixtures';

export function createTuiRecords(
  isChinese: boolean,
  now = Date.now(),
): readonly TuiRecord[] {
  const t = (en: string, zh: string) => (isChinese ? zh : en);
  const records: readonly TuiRecord[] = [
    {
      id: 'find',
      command: '/find "release checklist"',
      status: 'success',
      createdAt: '10:24',
      content: {
        variant: 'search-list',
        summary: t(
          'Search scope: viking://resources/acme · 2 hits',
          '检索范围：viking://resources/acme · 2 条结果',
        ),
        items: [
          {
            id: 'release',
            title: 'viking://resources/acme/release.md',
            uri: 'viking://resources/acme/release.md',
            levelLabel: 'L2',
            typeLabel: t('file', '文件'),
            scoreLabel: '0.92',
            scoreDetails: (
              <span>
                {t(
                  'Semantic 0.92 · Keyword 0.71 · Recency 0.88 (demo data)',
                  '语义 0.92 · 关键词 0.71 · 时效性 0.88（模拟数据）',
                )}
              </span>
            ),
            preview: t(
              'Release checklist: freeze, verify, publish, announce.',
              '发布检查清单：冻结分支、验证、发布与通知。',
            ),
          },
          {
            id: 'overview',
            title: 'viking://resources/acme',
            uri: 'viking://resources/acme',
            levelLabel: 'L1',
            typeLabel: t('dir', '目录'),
            scoreLabel: '0.74',
            preview: t(
              'Project overview covering release and onboarding.',
              '包含发布流程与新人指南的项目概览。',
            ),
          },
        ],
      },
    },
    {
      id: 'stat',
      command: '/stat viking://resources/acme/release.md',
      status: 'success',
      createdAt: '10:25',
      content: {
        variant: 'status',
        title: 'release.md',
        rows: [
          { label: 'URI', value: 'viking://resources/acme/release.md' },
          { label: t('Type', '类型'), value: t('file', '文件') },
          { label: t('Size', '大小'), value: '2.4 KB' },
          {
            label: t('Tags', '标签'),
            value: 'team=release',
            variant: 'long-text',
          },
        ],
      },
    },
    {
      id: 'read',
      command: '/read viking://resources/acme/release.md',
      status: 'success',
      createdAt: '10:26',
      content: {
        variant: 'resource-preview',
        title: 'release.md',
        meta: ['Markdown', '2.4 KB'],
        uri: 'viking://resources/acme/release.md',
        content: t(
          '# Release checklist\n\n1. Freeze the branch.\n2. Verify the smoke suite.\n3. Publish and announce.\n',
          '# 发布检查清单\n\n1. 冻结分支。\n2. 验证冒烟测试。\n3. 发布与通知。\n',
        ),
      },
    },
    {
      id: 'ls',
      command: '/ls viking://resources/acme',
      status: 'success',
      createdAt: '10:26',
      content: {
        variant: 'resource-tree',
        title: 'viking://resources/acme',
        items: [
          {
            id: 'release',
            title: 'release.md',
            uri: 'viking://resources/acme/release.md',
            detailRows: [
              { label: t('Type', '类型'), value: t('file', '文件') },
              { label: t('Size', '大小'), value: '2.4 KB' },
            ],
          },
          {
            id: 'onboarding',
            title: 'onboarding/',
            uri: 'viking://resources/acme/onboarding',
            detailRows: [
              { label: t('Type', '类型'), value: t('directory', '目录') },
              { label: t('Size', '大小'), value: '—' },
            ],
          },
        ],
      },
    },
    {
      id: 'mkdir',
      command: '/mkdir viking://resources/acme/archive',
      status: 'success',
      createdAt: '10:27',
      content: {
        variant: 'write-result',
        title: t('Created', '已创建'),
        lines: [
          t(
            'Directory: viking://resources/acme/archive',
            '目录：viking://resources/acme/archive',
          ),
          t('Mode: 0755', '权限：0755'),
        ],
      },
    },
    {
      id: 'write',
      command: '/write viking://resources/acme/notes.md --content "Hello"',
      status: 'error',
      createdAt: '10:27',
      content: {
        variant: 'text',
        text: t(
          'Permission denied: viking://resources/acme/notes.md is read-only for this account.',
          '权限不足：当前账号只能读取 viking://resources/acme/notes.md。',
        ),
      },
    },
    createTuiCompileRecord(),
  ];
  return records.map((record, index) => ({
    ...record,
    createdAtMs: now - (records.length - index - 1) * 60_000,
  }));
}
