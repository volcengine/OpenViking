export type {
  TuiCommandHelpContent,
  TuiCommandHelpItem,
  TuiCommandHelpSection,
  TuiLabels,
  TuiProps,
  TuiRecord,
  TuiRecordContent,
  TuiRecordStatus,
  TuiResourcePreviewContent,
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
  return <div className="ov-tui">Tui</div>;
}
