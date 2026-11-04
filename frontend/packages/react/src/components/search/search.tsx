import styles from './search.module.css';

/** 占位组件：仅渲染固定尺寸的空壳，真实实现按 props 契约重写。 */
export function Search() {
  return <div className={styles.root}>Search</div>;
}
