import styles from './search.module.css';

/** 占位组件：仅渲染固定尺寸的空壳；props 契约尚未定稿，真实实现迁入时一并补充。 */
export function Search() {
  return <div className={styles.root}>Search</div>;
}
