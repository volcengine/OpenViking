import { useLang } from '@rspress/core/runtime';
import {
  ScopeSwitcher,
  ScopeSelector,
} from '@openviking/react/components/scope-switcher';
import { LibraryIcon, SpaceIcon, UserIcon } from '@openviking/react/icons';
import {
  useScopeCascade,
  useScopeCascadeLevel,
  type ScopeCascadeLevel,
} from '@openviking/react/modules/scope-cascade';
import '@openviking/react/modules/scope-cascade/style.css';

// Fixed mock APIs, adapted to the same public option shape.
const api = {
  loadLibraries: () => Promise.resolve([{ id: 'demo', label: 'Demo library' }]),
  loadSpaces: (_libraryId: string) =>
    Promise.resolve([{ id: 'public', label: 'Public space' }]),
  loadUsers: (_libraryId: string, _spaceId: string) =>
    Promise.resolve([{ id: 'default', label: 'Default user' }]),
};

// The custom view supplies its own icons; `ScopeCascade` would fill in the same
// three defaults, but a hand-composed row has to pass them explicitly.
const icons = [LibraryIcon, SpaceIcon, UserIcon];

/** The level's default icon; the library swatch is keyed by id. */
function DefaultIcon({ index, id }: { index: 0 | 1 | 2; id?: string }) {
  const Icon = icons[index]!;
  return index === 0 ? <LibraryIcon name={id ?? ''} /> : <Icon />;
}

function CustomLevel({
  level,
  name,
  index,
}: {
  level: ScopeCascadeLevel;
  name: string;
  index: 0 | 1 | 2;
}) {
  const componentLocale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  const state = useScopeCascadeLevel(level);
  const options = state.options.map((option) =>
    option.icon
      ? option
      : { ...option, icon: <DefaultIcon index={index} id={option.id} /> },
  );
  return (
    <ScopeSelector
      locale={componentLocale}
      key={state.scopeKey}
      kind="selectable"
      identity={{ key: name, ariaLabel: name }}
      trigger={{
        label: state.selected?.label ?? name,
        icon: <DefaultIcon index={index} id={state.selected?.id} />,
      }}
      data={{ selectedId: state.selected?.id ?? '', options }}
      state={{
        disabled: !state.enabled,
        loading: state.loading,
        error: !!state.error,
      }}
      actions={{ onSelect: level.select }}
      slots={{
        errorFallback: (
          <button type="button" onClick={level.refresh}>
            Retry
          </button>
        ),
        renderPanel: ({ list }) => (
          <>
            <strong>{name}</strong>
            {list}
            <button type="button" onClick={level.refresh}>
              Refresh
            </button>
          </>
        ),
      }}
    />
  );
}
export default function ScopeCascadeCustom() {
  const componentLocale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  const controller = useScopeCascade(api);
  return (
    <div style={{ minHeight: 320 }}>
      <ScopeSwitcher locale={componentLocale}>
        <CustomLevel index={0} level={controller.levels[0]} name="Library" />
        <CustomLevel index={1} level={controller.levels[1]} name="Space" />
        <CustomLevel index={2} level={controller.levels[2]} name="User" />
      </ScopeSwitcher>
    </div>
  );
}
