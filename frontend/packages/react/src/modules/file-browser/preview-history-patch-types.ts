export interface VersionDiffLine {
  content: string;
  kind: 'added' | 'context' | 'removed';
  newLineNumber?: number;
  oldLineNumber?: number;
  /** One-based cursors also retain the unchanged side of an insertion/deletion. */
  oldPosition?: number;
  newPosition?: number;
  filePath?: string;
  /** Keep both source headers, including version identities and /dev/null. */
  fileHeaders?: readonly [string, string];
  noNewlineAtEnd?: boolean;
  isNewFile?: boolean;
  /** The compared version removed the file, so the patch ends at /dev/null. */
  isDeletedFile?: boolean;
}
