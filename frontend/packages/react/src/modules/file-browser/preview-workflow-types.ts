import type { OvRequestOptions } from '../../client/types.js';
import type { Locale } from '../../shared/locale.js';
export interface FileBrowserWorkflowLabels {
  configure: string;
  historyEmpty: string;
  historyLimit: string;
  compareChanges: string;
  noChanges: string;
  addedLines: string;
  removedLines: string;
  restoreVersion: string;
  confirmRestore: string;
  copyOriginal: string;
  copied: string;
  historyPreview: string;
  historyMarkdown: string;
  version: string;
  importFile: string;
  importHint: string;
  importSubmit: string;
  imported: string;
  saved: string;
  cancel: string;
  save: string;
  failed: string;
  sources: string;
  skill: string;
  instruction: string;
  automatic: string;
  schedule: string;
  noSchedule: string;
  daily: string;
  weekly: string;
  time: string;
  weekday: string;
}
export interface PreviewWorkflowProps {
  uri: string;
  locale: Locale;
  text: FileBrowserWorkflowLabels;
  requestOptions?: OvRequestOptions;
  onUpdated: () => void;
  onClose: () => void;
}
export interface CompileConfig {
  to: string;
  from: string[];
  skill: string;
  instruction: string | null;
  auto_update: boolean;
  update_schedule: {
    frequency: 'daily' | 'weekly';
    hour: number;
    minute: number;
    weekday?: number;
  } | null;
}
export interface PreviewHistoryEntry {
  id: string;
  snapshot: string;
  path: string;
  title: string;
  numbered?: boolean;
  note?: string;
  timestamp?: number;
}
