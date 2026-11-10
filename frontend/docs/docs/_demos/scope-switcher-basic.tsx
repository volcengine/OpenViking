import { useLang } from '@rspress/core/runtime';
import { useState } from 'react';
import {
  createScopeSource,
  useScope,
  type ScopeSource,
  type ScopeIndex,
} from '#scope-switcher-source';
import { LibraryIcon, SpaceIcon, UserIcon } from '#scope-switcher-icons';
import {
  ScopeSwitcher,
  ScopeSelector,
  type ScopeSwitcherOption,
} from '@openviking/react/components/scope-switcher';
import '@openviking/react/components/scope-switcher/style.css';

const libraries = [
  {
    id: 'research',
    label: '星河电商研发库',
    icon: <LibraryIcon name="research" />,
  },
  {
    id: 'support',
    label: 'Support library',
    icon: <LibraryIcon name="support" />,
  },
];
const spaces: Record<string, ScopeSwitcherOption[]> = {
  research: [
    { id: 'papers', label: '算法组', icon: <SpaceIcon /> },
    { id: 'notes', label: 'Notes', icon: <SpaceIcon /> },
  ],
  support: [{ id: 'help', label: 'Help center', icon: <SpaceIcon /> }],
};
const users: Record<string, ScopeSwitcherOption[]> = {
  papers: [
    { id: 'alice', label: 'liyanong', icon: <UserIcon /> },
    { id: 'bob', label: 'default', icon: <UserIcon /> },
  ],
  notes: [{ id: 'team', label: 'Research team', icon: <UserIcon /> }],
  help: [{ id: 'agent', label: 'Support agent', icon: <UserIcon /> }],
};

function StaticSelector({
  source,
  index,
}: {
  source: ScopeSource;
  index: ScopeIndex;
}) {
  const componentLocale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  const scope = useScope(source, index);
  const name = ['Library', 'Space', 'User'][index]!;
  const options =
    index === 0
      ? libraries
      : index === 1
        ? (spaces[scope.ancestors[0]?.id ?? ''] ?? [])
        : (users[scope.ancestors[1]?.id ?? ''] ?? []);
  return (
    <ScopeSelector
      locale={componentLocale}
      key={JSON.stringify(scope.ancestors.map((item) => item.id))}
      kind="selectable"
      identity={{ key: name, ariaLabel: `Switch ${name.toLowerCase()}` }}
      trigger={{
        label: scope.selected?.label ?? `Select ${name.toLowerCase()}`,
        icon: scope.selected?.icon,
      }}
      data={{ selectedId: scope.selected?.id ?? '', options }}
      state={{ disabled: scope.ancestors.length < index }}
      actions={{ onSelect: (option) => source.select(index, option) }}
    />
  );
}

export default function ScopeSwitcherBasic() {
  const componentLocale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  const [source] = useState(() =>
    createScopeSource([libraries[0]!, spaces.research![0]!, users.papers![0]!]),
  );
  return (
    <div style={{ minHeight: 320 }}>
      <ScopeSwitcher locale={componentLocale}>
        <StaticSelector source={source} index={0} />
        <StaticSelector source={source} index={1} />
        <StaticSelector source={source} index={2} />
      </ScopeSwitcher>
    </div>
  );
}
