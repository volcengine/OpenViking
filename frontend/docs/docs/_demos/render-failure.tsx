import { Checkbox } from '#demo-controls';
import { useLang } from '@rspress/core/runtime';
import { createContext, useContext, useState } from 'react';
import type { ReactNode } from 'react';

const Failing = createContext(false);

/**
 * 示例里的故障注入点：平时原样渲染子内容，勾选「模拟渲染失败」后抛错，
 * 由所在组件的错误边界接住。
 */
export function Fault({ children }: { children?: ReactNode }) {
  if (useContext(Failing)) throw new Error('Simulated rendering failure');
  return <>{children}</>;
}

/** 当前是否在模拟失败，供需要改造数据而非插入节点的示例使用。 */
export function useFailing() {
  return useContext(Failing);
}

/** 勾选框与说明；子内容中的 `Fault` 随勾选状态抛错。 */
export function RenderFailureDemo({
  children,
  hint,
}: {
  children: ReactNode;
  hint?: string;
}) {
  const zh = useLang() === 'zh';
  const [failing, setFailing] = useState(false);
  return (
    <div>
      {hint ? <p>{hint}</p> : null}
      <p>
        {zh
          ? '勾选后对应区域渲染失败并显示默认兜底；取消勾选后点击「重试显示」恢复。'
          : 'Check the box to make the region fail and show the default fallback; uncheck it, then select Retry display to recover.'}
      </p>
      <p className="ov-demo-controls">
        <Checkbox checked={failing} onChange={setFailing}>
          {zh ? '模拟渲染失败' : 'Simulate a rendering failure'}
        </Checkbox>
      </p>
      <Failing.Provider value={failing}>{children}</Failing.Provider>
    </div>
  );
}
