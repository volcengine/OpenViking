import styles from './tui.module.css';

export type {
  TuiCommandSuggestion,
  TuiCustomContent,
  TuiErrorContent,
  TuiHistoryEntry,
  TuiJsonContent,
  TuiLabels,
  TuiMarkdownContent,
  TuiProps,
  TuiQuickAction,
  TuiRecord,
  TuiRecordContent,
  TuiRecordStatus,
  TuiResourceTreeContent,
  TuiResourceTreeDetailRow,
  TuiResourceTreeItem,
  TuiSearchListContent,
  TuiSearchResultItem,
  TuiStatusContent,
  TuiStatusRow,
  TuiTextContent,
  TuiWriteResultContent,
} from './types.js';

/** 占位组件：仅渲染固定尺寸的空壳，真实实现按 props 契约重写。 */
export function Tui() {
  return <div className={styles.root}>Tui</div>;
}
