import styles from './scope-switcher.module.css';

export type {
  ScopeLevelAction,
  ScopeOption,
  ScopeSwitcherLabels,
  ScopeSwitcherLevel,
  ScopeSwitcherProps,
} from './types.js';

/** 占位组件：仅渲染固定尺寸的空壳，真实实现按 props 契约重写。 */
export function ScopeSwitcher() {
  return <div className={styles.root}>ScopeSwitcher</div>;
}
