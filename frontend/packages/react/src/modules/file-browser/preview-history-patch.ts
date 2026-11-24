import type { VersionDiffLine } from './preview-history-patch-types.js';
const linePrefixes = { added: '+', removed: '-', context: ' ' };
const NO_NEWLINE_MARKER = '\\ No newline at end of file';
const previewContent = (content: string) => {
  const heading = content.match(/^#{1,6}\s+(.*)$/);
  return heading
    ? heading[1]!
    : content
        .replace(/^[-*]\s+/, '')
        .replace(/\*\*(.*?)\*\*/g, '$1')
        .replace(/`([^`]+)`/g, '$1');
};

interface Hunk {
  filePath: string;
  fileHeaders?: VersionDiffLine['fileHeaders'];
  isNewFile?: boolean;
  isDeletedFile?: boolean;
  oldStart: number;
  newStart: number;
  oldCount: number;
  newCount: number;
  body: string[];
}

/** Serialize the business line model; FileViewer only receives a complete unified patch. */
export const toFileViewerDiff = (
  lines: readonly VersionDiffLine[],
  viewMode: 'preview' | 'source',
): string => {
  const hunks: Hunk[] = [];
  let hunk: Hunk | undefined;
  let oldCursor = 1;
  let newCursor = 1;
  lines.forEach((line) => {
    const oldPosition = line.oldPosition ?? line.oldLineNumber ?? oldCursor;
    const newPosition = line.newPosition ?? line.newLineNumber ?? newCursor;
    const filePath = line.filePath ?? 'version.md';
    if (
      !hunk ||
      hunk.filePath !== filePath ||
      hunk.fileHeaders !== line.fileHeaders ||
      oldPosition !== oldCursor ||
      newPosition !== newCursor
    ) {
      hunk = {
        filePath,
        fileHeaders: line.fileHeaders,
        isNewFile: line.isNewFile,
        isDeletedFile: line.isDeletedFile,
        oldStart: oldPosition,
        newStart: newPosition,
        oldCount: 0,
        newCount: 0,
        body: [],
      };
      hunks.push(hunk);
    }
    const content =
      viewMode === 'preview' ? previewContent(line.content) : line.content;
    hunk.body.push(`${linePrefixes[line.kind]}${content}`);
    if (line.noNewlineAtEnd) {
      hunk.body.push(NO_NEWLINE_MARKER);
    }
    const oldStep = line.kind === 'added' ? 0 : 1;
    const newStep = line.kind === 'removed' ? 0 : 1;
    hunk.oldCount += oldStep;
    hunk.newCount += newStep;
    oldCursor = oldPosition + oldStep;
    newCursor = newPosition + newStep;
  });

  const patch: string[] = [];
  let previousPath: string | undefined,
    previousHeaders: VersionDiffLine['fileHeaders'];
  hunks.forEach((item) => {
    if (
      item.filePath !== previousPath ||
      item.fileHeaders !== previousHeaders
    ) {
      patch.push(
        ...(item.fileHeaders ?? [
          `--- ${item.isNewFile ? '/dev/null' : item.filePath}`,
          `+++ ${item.isDeletedFile ? '/dev/null' : item.filePath}`,
        ]),
      );
      previousPath = item.filePath;
      previousHeaders = item.fileHeaders;
    }
    const oldStart = item.oldStart - (item.oldCount === 0 ? 1 : 0);
    const newStart = item.newStart - (item.newCount === 0 ? 1 : 0);
    patch.push(
      `@@ -${oldStart},${item.oldCount} +${newStart},${item.newCount} @@`,
      ...item.body,
    );
  });
  return patch.length ? `${patch.join('\n')}\n` : '';
};
