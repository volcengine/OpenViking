import { useCallback, useRef } from 'react';

export function useStableCallback<Args extends unknown[], Result>(
  callback: (...args: Args) => Result,
) {
  const current = useRef(callback);
  current.current = callback;
  return useCallback((...args: Args) => current.current(...args), []);
}
