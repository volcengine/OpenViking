import { useCallback, useRef, useState } from 'react';

/** 提交阶段切断过期结果；每个复制按钮拥有独立的反馈和请求状态。 */
export function useAgentConnectCopy(
  text: string,
  onCopy?: (text: string) => void,
) {
  const [status, setStatus] = useState<
    'idle' | 'pending' | 'copied' | 'failed'
  >('idle');
  const pending = useRef(false);
  const mounted = useRef(false);
  const setRoot = useCallback((node: HTMLElement | null) => {
    mounted.current = node !== null;
  }, []);

  async function copy() {
    if (pending.current) return;
    pending.current = true;
    setStatus('pending');
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      if (mounted.current) setStatus('failed');
      return;
    } finally {
      pending.current = false;
    }
    if (!mounted.current) return;
    setStatus('copied');
    onCopy?.(text);
  }

  return { status, setRoot, copy };
}
