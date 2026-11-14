import { useRenderErrors } from './labels.js';
import { ErrorBoundary } from '../../shared/error-boundary/error-boundary.js';
import { Button } from '../../shared/primitives/button/button.js';
import { resolveScopeCascadeLabels } from './labels.js';
import { useState } from 'react';
import { useLocale } from '../../shared/locale-context.js';
import type { Locale } from '../../shared/locale.js';
import {
  ScopeSelector,
  ScopeSwitcher,
} from '../../components/scope-switcher/index.js';
import { useScopeCascade, useScopeCascadeLevel } from './use-scope-cascade.js';
import type { ScopeSwitcherOption } from '../../components/scope-switcher/index.js';
import { LibraryIcon, SpaceIcon, UserIcon } from '../../icons/index.js';
import type {
  ScopeCascadeIndex,
  ScopeCascadeLevel,
  ScopeCascadeProps,
  ScopeCascadeSnapshot,
} from './types.js';

/**
 * 每级的默认图标，按库 / 空间 / 用户顺序。接口没有给 `icon` 时用它，使默认视图
 * 与设计稿一致；宿主给了自己的图标就照用宿主的。
 *
 * The default icon per level, in library / space / user order. It applies when an
 * adapter returns no `icon`, so the default view matches the design; a host icon
 * always wins.
 */
function defaultIcon(index: ScopeCascadeIndex, option?: ScopeSwitcherOption) {
  if (index === 0) return <LibraryIcon name={option?.id ?? ''} />;
  return index === 1 ? <SpaceIcon /> : <UserIcon />;
}

/** 给没有图标的选项补上本级默认图标。Adds the level's default icon to options without one. */
function withDefaultIcons(
  options: readonly ScopeSwitcherOption[],
  index: ScopeCascadeIndex,
) {
  if (options.every((option) => option.icon != null)) return options;
  return options.map((option) =>
    option.icon != null
      ? option
      : { ...option, icon: defaultIcon(index, option) },
  );
}

type LevelProps = Pick<ScopeCascadeProps, 'labels' | 'retryLabel'> & {
  locale: Locale;
  index: ScopeCascadeIndex;
  level: ScopeCascadeLevel;
  name: string;
};
function ConnectedLevel(props: LevelProps) {
  const state = useScopeCascadeLevel(props.level);
  return <LevelView key={state.scopeKey} {...props} state={state} />;
}
function LevelView({
  index,
  level,
  locale,
  state,
  name,
  labels,
  retryLabel,
}: LevelProps & { state: ScopeCascadeSnapshot }) {
  const [keyword, setKeyword] = useState('');
  const text = resolveScopeCascadeLabels(locale);
  const options = withDefaultIcons(state.options, index);
  return (
    <ScopeSelector
      locale={locale}
      labels={labels}
      kind="selectable"
      identity={{ key: name, ariaLabel: name }}
      trigger={{
        label: state.selected?.label ?? name,
        icon: state.selected?.icon ?? defaultIcon(index, state.selected),
      }}
      data={{
        selectedId: state.selected?.id ?? '',
        total: options.length,
        // Keep the full list authoritative so a search that hides the current
        // selection does not look like an invalid scope; filter for display only.
        options,
        visibleOptions: options.filter((option) =>
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
            <Button
              size="small"
              type="default"
              htmlType="button"
              onClick={level.refresh}
            >
              {retryLabel ?? text.retry}
            </Button>
          </>
        ),
      }}
    />
  );
}

/** Only leaf selectors subscribe to request state; this wrapper resolves language. */
function ScopeCascadeContent({
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
        index={0}
        locale={locale}
        level={controller.levels[0]}
        name={levelNames[0]}
        labels={labels}
        retryLabel={retryLabel}
      />
      <ConnectedLevel
        index={1}
        locale={locale}
        level={controller.levels[1]}
        name={levelNames[1]}
        labels={labels}
        retryLabel={retryLabel}
      />
      <ConnectedLevel
        index={2}
        locale={locale}
        level={controller.levels[2]}
        name={levelNames[2]}
        labels={labels}
        retryLabel={retryLabel}
      />
    </ScopeSwitcher>
  );
}

export function ScopeCascade(props: ScopeCascadeProps) {
  const { slots, ...contentProps } = props;
  return (
    <ErrorBoundary
      fallback={slots?.errorFallback}
      message={useRenderErrors(props.locale).selector}
      locale={props.locale}
    >
      <ScopeCascadeContent {...contentProps} />
    </ErrorBoundary>
  );
}
