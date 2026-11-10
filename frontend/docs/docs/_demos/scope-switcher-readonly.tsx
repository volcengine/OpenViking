import { useLang } from '@rspress/core/runtime';
import { useState } from 'react';
import { LibraryIcon, SpaceIcon, UserIcon } from '#scope-switcher-icons';
import {
  ScopeSwitcher,
  ScopeSelector,
  type ScopeSwitcherOption,
} from '@openviking/react/components/scope-switcher';
import '@openviking/react/components/scope-switcher/style.css';

/**
 * 个人终端里的作用域：库与数据空间跟随登录态固定，只有 User 可切换。固定的
 * 层级用 `kind: 'fixed'`，它不渲染按钮、不展开面板，几何尺寸与可切换层级一致，
 * 所以整行看起来仍是一条完整的面包屑。
 *
 * The scope in a personal terminal: the library and data space are fixed by the
 * sign-in and only the user can be switched. A fixed level uses `kind: 'fixed'`,
 * which renders no button and opens no panel, yet keeps the same geometry as a
 * selectable level, so the row still reads as one breadcrumb.
 */
const users: ScopeSwitcherOption[] = [
  { id: 'default', label: 'default', icon: <UserIcon /> },
  { id: 'liyanong', label: 'liyanong', icon: <UserIcon /> },
];

export default function ScopeSwitcherReadonly() {
  const componentLocale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  const [user, setUser] = useState('default');
  return (
    <div style={{ minHeight: 240 }}>
      <ScopeSwitcher locale={componentLocale}>
        <ScopeSelector
          locale={componentLocale}
          kind="fixed"
          identity={{ key: 'library', ariaLabel: 'Current library' }}
          trigger={{
            label: '星河电商研发库',
            icon: <LibraryIcon name="personal" />,
          }}
        />
        <ScopeSelector
          locale={componentLocale}
          kind="fixed"
          identity={{ key: 'space', ariaLabel: 'Current data space' }}
          trigger={{ label: 'default', icon: <SpaceIcon /> }}
        />
        <ScopeSelector
          locale={componentLocale}
          kind="selectable"
          identity={{ key: 'user', ariaLabel: 'Switch user' }}
          trigger={{ label: user, icon: <UserIcon /> }}
          data={{ selectedId: user, options: users }}
          actions={{ onSelect: (option) => setUser(option.id) }}
        />
      </ScopeSwitcher>
    </div>
  );
}
