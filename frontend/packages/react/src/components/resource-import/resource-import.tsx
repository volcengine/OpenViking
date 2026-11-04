import styles from './resource-import.module.css';

export type {
  ResourceImportField,
  ResourceImportFile,
  ResourceImportFilePickerProps,
  ResourceImportFormProps,
  ResourceImportLabels,
  ResourceImportProps,
  ResourceImportSource,
  ResourceImportTask,
  ResourceImportTaskListProps,
} from './types.js';

/** 占位组件：仅渲染固定尺寸的空壳，真实实现按 props 契约重写。 */
export function ResourceImport() {
  return <div className={styles.root}>ResourceImport</div>;
}
