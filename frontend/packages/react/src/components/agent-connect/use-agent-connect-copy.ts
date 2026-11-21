import { useTrack } from '../../shared/analytics/use-track.js';
import { useCallback, useRef, useState } from 'react';

/** 提交阶段切断过期结果；每个复制按钮拥有独立的反馈和请求状态。 */
export function useAgentConnectCopy(
  text: string,
  onCopy?: (text: string) => void,
  target: 'instructions' | 'code_block' = 'code_block',
) {
  const track = useTrack();
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
    track(`agent_connect_${target}_copy_click`);
    pending.current = true;
    setStatus('pending');
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      track(`agent_connect_${target}_copy_fail`);
      if (mounted.current) setStatus('failed');
      return;
    } finally {
      pending.current = false;
    }
    track(`agent_connect_${target}_copy_success`);
    if (!mounted.current) return;
    setStatus('copied');
    onCopy?.(text);
  }

  return { status, setRoot, copy };
}
