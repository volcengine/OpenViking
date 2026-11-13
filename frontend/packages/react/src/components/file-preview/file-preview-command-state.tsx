import { createContext, useContext, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { FilePreviewAction } from './action-types.js';
import type { FilePreviewTab } from './types.js';

interface CommandState {
  pending?: string;
  failure?: { reason: unknown };
}
const CommandContext = createContext<{
  pending: boolean;
  failure?: CommandState['failure'];
  execute: (action: FilePreviewAction, disabled: boolean) => Promise<void>;
} | null>(null);

/** Own execution for the preview lifetime, outside tab bodies and toolbar slots. */
export function FilePreviewCommandProvider({
  tab,
  children,
}: {
  tab: FilePreviewTab;
  children: ReactNode;
}) {
  const source =
    tab.kind === 'file'
      ? tab.source
      : tab.kind === 'directory'
        ? tab.levels[0].source
        : undefined;
  const scope = JSON.stringify(
    source?.path ? ['path', source.path] : ['tab', tab.key, source?.fileName],
  );
  const running = useRef(new Set<string>());
  const [states, setStates] = useState(() => new Map<string, CommandState>());
  const execute = async (action: FilePreviewAction, disabled: boolean) => {
    if (running.current.has(scope) || disabled || action.disabled) return;
    running.current.add(scope);
    setStates((current) =>
      new Map(current).set(scope, { pending: action.key }),
    );
    let failure: CommandState['failure'];
    try {
      await action.onClick();
    } catch (reason) {
      failure = { reason };
    } finally {
      running.current.delete(scope);
      setStates((current) => {
        const next = new Map(current);
        if (failure) next.set(scope, { failure });
        else next.delete(scope);
        return next;
      });
    }
  };
  const state = states.get(scope);
  return (
    <CommandContext.Provider
      value={{
        pending: state?.pending !== undefined,
        failure: state?.failure,
        execute,
      }}
    >
      {children}
    </CommandContext.Provider>
  );
}

export function useFilePreviewCommands() {
  return useContext(CommandContext)!;
}
