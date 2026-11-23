import { useCallback, useState } from 'react';
import { resolveResourceNavigation } from './resource-navigation.js';

/** Keep link requests and manual summary selection in the same instance-local state. */
export function useDirectoryPreviewLevels(uri: string | null) {
  const [levels, setLevels] = useState<Record<string, string>>({});
  const selectLevel = useCallback((target: string, key: string) => {
    setLevels((previous) => ({ ...previous, [target]: key }));
  }, []);
  const requestUri = useCallback(
    (resource: string) => {
      const { uri: target, level } = resolveResourceNavigation(resource);
      if (level) selectLevel(target, level);
      return target;
    },
    [selectLevel],
  );
  return {
    requestUri,
    previewLevel: {
      activeLevelKey: uri ? levels[uri] : undefined,
      onActiveLevelChange: (key: string) => {
        if (uri) selectLevel(uri, key);
      },
    },
  };
}
