import {
  ScopeSwitcher,
  ScopeSelector,
} from '@openviking/react/components/scope-switcher';
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

function CustomLevel({
  level,
  name,
}: {
  level: ScopeCascadeLevel;
  name: string;
}) {
  const state = useScopeCascadeLevel(level);
  return (
    <ScopeSelector
      key={state.scopeKey}
      level={{
        kind: 'selectable',
        key: name,
        ariaLabel: name,
        selectedId: state.selected?.id ?? '',
        selectedLabel: state.selected?.label ?? name,
        options: state.options,
        disabled: !state.enabled,
        loading: state.loading,
        error: !!state.error,
        onSelect: level.select,
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
  const controller = useScopeCascade(api);
  return (
    <div style={{ minHeight: 320 }}>
      <ScopeSwitcher>
        <CustomLevel level={controller.levels[0]} name="Library" />
        <CustomLevel level={controller.levels[1]} name="Space" />
        <CustomLevel level={controller.levels[2]} name="User" />
      </ScopeSwitcher>
    </div>
  );
}
