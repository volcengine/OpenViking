import styles from './agent-connect.module.css';

export type {
  AgentConnectCatalogProps,
  AgentConnectDetail,
  AgentConnectDetailProps,
  AgentConnectItem,
  AgentConnectLabels,
  AgentConnectProps,
  AgentConnectSnippet,
  AgentConnectStatus,
  AgentConnectStep,
  AgentConnectTab,
} from './types.js';

/** 占位组件：仅渲染固定尺寸的空壳，真实实现按 props 契约重写。 */
export function AgentConnect() {
  return <div className={styles.root}>AgentConnect</div>;
}
