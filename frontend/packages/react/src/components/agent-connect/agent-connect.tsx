import styles from './agent-connect.module.css';
import type { AgentConnectProps } from './types.js';

export type {
  AgentConnectDetail,
  AgentConnectItem,
  AgentConnectLabels,
  AgentConnectProps,
  AgentConnectTab,
} from './types.js';

/** 占位组件：仅渲染固定尺寸的空壳，真实实现按 props 契约重写。 */
export function AgentConnect(_props: AgentConnectProps) {
  return <div className={styles.root}>AgentConnect</div>;
}
