import { resolveScopeCascadeLabels } from './labels.js';
import { useState } from 'react';
import { useLocale } from '../../shared/locale-context.js';
import type { Locale } from '../../shared/locale.js';
import {
  ScopeSelector,
  ScopeSwitcher,
} from '../../components/scope-switcher/index.js';
import { useScopeCascade, useScopeCascadeLevel } from './use-scope-cascade.js';
import type {
  ScopeCascadeLevel,
  ScopeCascadeProps,
  ScopeCascadeSnapshot,
} from './types.js';

type LevelProps = Pick<ScopeCascadeProps, 'labels' | 'retryLabel'> & {
  locale: Locale;
  level: ScopeCascadeLevel;
  name: string;
};
function ConnectedLevel(props: LevelProps) {
  const state = useScopeCascadeLevel(props.level);
  return <LevelView key={state.scopeKey} {...props} state={state} />;
}
function LevelView({
  level,
  locale,
  state,
  name,
  labels,
  retryLabel,
}: LevelProps & { state: ScopeCascadeSnapshot }) {
  const [keyword, setKeyword] = useState('');
  const text = resolveScopeCascadeLabels(locale);
  return (
    <ScopeSelector
      locale={locale}
      labels={labels}
      kind="selectable"
      identity={{ key: name, ariaLabel: name }}
      trigger={{
        label: state.selected?.label ?? name,
        icon: state.selected?.icon,
      }}
      data={{
        selectedId: state.selected?.id ?? '',
        total: state.options.length,
        // Keep the full list authoritative so a search that hides the current
        // selection does not look like an invalid scope; filter for display only.
        options: state.options,
        visibleOptions: state.options.filter((option) =>
          option.label
            .toLocaleLowerCase()
            .includes(keyword.toLocaleLowerCase()),
        ),
      }}
      state={{
        disabled: !state.enabled,
        loading: state.loading,
        error: state.error !== null,
        searchKeyword: keyword,
      }}
      actions={{
        onSearchKeywordChange: setKeyword,
        onSelect: (option) => {
          level.select(option);
          setKeyword('');
        },
      }}
      slots={{
        errorFallback: (
          <>
            {labels?.error ?? text.error}{' '}
            <button type="button" onClick={level.refresh}>
              {retryLabel ?? text.retry}
            </button>
          </>
        ),
      }}
    />
  );
}

/** Only leaf selectors subscribe to request state; this wrapper resolves language. */
export function ScopeCascade({
  locale: localeProp,
  loadLibraries,
  loadSpaces,
  loadUsers,
  onChange,
  labels,
  levelNames: names,
  retryLabel,
  ...props
}: ScopeCascadeProps) {
  const locale = useLocale(localeProp);
  const levelNames = names ?? resolveScopeCascadeLabels(locale).levelNames;
  const controller = useScopeCascade(
    { loadLibraries, loadSpaces, loadUsers },
    onChange,
  );
  return (
    <ScopeSwitcher {...props} locale={locale}>
      <ConnectedLevel
        locale={locale}
        level={controller.levels[0]}
        name={levelNames[0]}
        labels={labels}
        retryLabel={retryLabel}
      />
      <ConnectedLevel
        locale={locale}
        level={controller.levels[1]}
        name={levelNames[1]}
        labels={labels}
        retryLabel={retryLabel}
      />
      <ConnectedLevel
        locale={locale}
        level={controller.levels[2]}
        name={levelNames[2]}
        labels={labels}
        retryLabel={retryLabel}
      />
    </ScopeSwitcher>
  );
}
