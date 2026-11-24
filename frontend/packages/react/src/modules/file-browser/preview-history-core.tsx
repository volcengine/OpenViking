import { useMemo } from 'react';
import {
  FileViewerCore,
  type FileViewerCoreProps,
} from '@ark-design/ui/file-viewer/core';
import { diff } from '@ark-design/ui/file-viewer/adapters/diff';
import { markdown } from '@ark-design/ui/file-viewer/adapters/markdown';
import { code } from '@ark-design/ui/file-viewer/adapters/code';
import { fenceMarkdownFrontmatter } from '../../shared/markdown/preview-frontmatter.js';
import type { Locale } from '../../shared/locale.js';
import type { DiffLine } from './preview-diff.js';
import { toFileViewerDiff } from './preview-history-patch.js';

const adapters = [
  diff({ defaultViewMode: 'unified' }),
  markdown({
    rendererProps: {
      sourcePlugins: (current) => [
        ...current,
        {
          name: 'ov-yaml-frontmatter',
          config: { priority: -1 },
          modifier: ({ source }) => fenceMarkdownFrontmatter(source),
          before: () => {},
          after: () => {},
          beforeEach: () => {},
          afterEach: () => {},
        },
      ],
    },
  }),
  code({ showLineNumbers: true }),
];
export function PreviewHistoryCore({
  content,
  changes,
  view,
  locale,
  path,
  addedFile,
  deletedFile,
}: {
  content: string;
  changes?: DiffLine[];
  view: 'preview' | 'source';
  locale: Locale;
  path?: string;
  addedFile?: boolean;
  deletedFile?: boolean;
}) {
  const source = useMemo<FileViewerCoreProps['source']>(() => {
    const patch =
      changes &&
      toFileViewerDiff(
        changes.map((line) => ({
          isNewFile: addedFile,
          isDeletedFile: deletedFile,
          kind: line.kind === 'same' ? 'context' : line.kind,
          content: line.text.replace(/\r?\n$/, ''),
          filePath: path ?? 'version.md',
          noNewlineAtEnd: !line.text.endsWith('\n'),
        })),
        view,
      );
    return {
      kind: 'file',
      file: new File(
        [patch ?? content],
        changes ? 'version.diff' : (path?.split('/').pop() ?? 'version.md'),
      ),
      fileType: changes ? 'diff' : 'markdown',
    };
  }, [content, changes, view, path, addedFile, deletedFile]);
  return (
    <FileViewerCore
      className="ov-file-history__core"
      adapters={adapters}
      capabilities={false}
      locale={locale}
      source={source}
      toolbar={false}
      viewMode={changes ? undefined : view}
    />
  );
}
