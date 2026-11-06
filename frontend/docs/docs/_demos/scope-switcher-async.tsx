import { useEffect, useState } from 'react';
import {
  ScopeSwitcher,
  type ScopeSwitcherOption,
} from '@openviking/react/components/scope-switcher';
import '@openviking/react/components/scope-switcher/style.css';

// Demo-only API adapters. Replace these three functions with your own APIs,
// mapping the response to { id, label } and forwarding AbortSignal.
async function mockOptions(prefix: string, signal: AbortSignal) {
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(resolve, 450);
    signal.addEventListener(
      'abort',
      () => {
        clearTimeout(timer);
        reject(new DOMException('Aborted', 'AbortError'));
      },
      { once: true },
    );
  });
  return Array.from({ length: 12 }, (_, index) => ({
    id: `${prefix}-${index + 1}`,
    label: `${prefix} ${index + 1}`,
  }));
}
const listLibraries = (_parent: string, signal: AbortSignal) =>
  mockOptions('Library', signal);
const listSpaces = (libraryId: string, signal: AbortSignal) =>
  mockOptions(`${libraryId} / Space`, signal);
const listUsers = (scope: string, signal: AbortSignal) =>
  mockOptions(`${scope} / User`, signal);
type Loader = (
  parent: string,
  signal: AbortSignal,
) => Promise<ScopeSwitcherOption[]>;

// This hook belongs to the host demo, never to the view component.
function useOptions(parent: string | undefined, loader: Loader, fail: boolean) {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{
    parent?: string;
    attempt: number;
    fail: boolean;
    options: ScopeSwitcherOption[];
    error: boolean;
  }>();
  // Synchronize the host's selected parent with a remote option list, including
  // programmatic parent changes. Abort and ignore results on dependency changes
  // or unmount so a previous scope can never overwrite the current one.
  useEffect(() => {
    if (parent === undefined) return;
    const controller = new AbortController();
    loader(parent, controller.signal)
      .then((options) => {
        if (!controller.signal.aborted)
          setResult({
            parent,
            attempt,
            fail,
            options: fail ? [] : options,
            error: fail,
          });
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setResult({ parent, attempt, fail, options: [], error: true });
      });
    return () => controller.abort();
  }, [parent, loader, fail, attempt]);
  const current =
    result?.parent === parent &&
    result?.attempt === attempt &&
    result?.fail === fail
      ? result
      : undefined;
  return {
    options: current?.options ?? [],
    loading: parent !== undefined && !current,
    error: current?.error,
    retry: () => setAttempt(attempt + 1),
  };
}

export default function ScopeSwitcherAsync() {
  const [selected, setSelected] = useState<ScopeSwitcherOption[]>([]);
  const [panel, setPanel] = useState<string>();
  const [keywords, setKeywords] = useState<string[]>([]);
  const [fail, setFail] = useState(false);
  const library = useOptions('', listLibraries, fail);
  const space = useOptions(selected[0]?.id, listSpaces, fail);
  const user = useOptions(
    selected[0] && selected[1]
      ? `${selected[0].id}/${selected[1].id}`
      : undefined,
    listUsers,
    fail,
  );
  const data = [library, space, user];
  return (
    <div style={{ minHeight: 380 }}>
      <p>Mock APIs · 450 ms each</p>
      <label>
        <input
          type="checkbox"
          checked={fail}
          onChange={(event) => setFail(event.target.checked)}
        />{' '}
        Simulate API failure
      </label>
      <div style={{ marginTop: 16 }}>
        <ScopeSwitcher
          levels={['Library', 'Space', 'User'].map((key, index) => ({
            kind: 'selectable',
            key,
            ariaLabel: `Async ${key.toLowerCase()}`,
            selectedId: selected[index]?.id ?? '',
            selectedLabel:
              selected[index]?.label ?? `Select ${key.toLowerCase()}`,
            disabled: index > 0 && !selected[index - 1],
            options: data[index]!.options.filter((option) =>
              option.label
                .toLowerCase()
                .includes((keywords[index] ?? '').toLowerCase()),
            ),
            total: data[index]!.options.length,
            loading: data[index]!.loading,
            error: data[index]!.error,
            visible: panel === key,
            onVisibleChange: (open) => setPanel(open ? key : undefined),
            searchKeyword: keywords[index] ?? '',
            onSearchKeywordChange: (keyword) =>
              setKeywords((previous) => {
                const next = [...previous];
                next[index] = keyword;
                return next;
              }),
            onSelect: (option) => {
              setSelected([...selected.slice(0, index), option]);
              setKeywords([]);
              setPanel(undefined);
            },
            errorFallback: (
              <>
                <span>Unable to load options. </span>
                <button type="button" onClick={data[index]!.retry}>
                  Retry
                </button>
              </>
            ),
            renderPanel: ({ search, list }) => (
              <>
                <strong>{key}</strong>
                {search}
                {list}
              </>
            ),
          }))}
        />
      </div>
    </div>
  );
}
