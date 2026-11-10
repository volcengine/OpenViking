import { Button } from '#demo-controls';
import { useLang } from '@rspress/core/runtime';
import { useState, type ReactNode } from 'react';
import { LibraryIcon, SpaceIcon, UserIcon } from '@openviking/react/icons';
import {
  ScopeSwitcher,
  ScopeSelector,
  type ScopeSwitcherOption,
} from '@openviking/react/components/scope-switcher';
import '@openviking/react/components/scope-switcher/style.css';

/**
 * 宿主自己排布面板内容：标题行带操作、底部入口与说明都由宿主渲染，组件只提供
 * 搜索框和列表。这里刻意只做展示，点击底部入口不触发路由。
 *
 * The host lays out the panel itself: the heading with its action and the footer
 * entries are host-rendered, and the component supplies only the search box and
 * the list. The footer here is presentational; a real host routes on click.
 */
function Shell({
  title,
  action,
  footer,
  children,
}: {
  title: string;
  action?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="demo-panel">
      <div className="demo-panel__header">
        <span className="demo-panel__title">{title}</span>
        {action}
      </div>
      {children}
      {footer && (
        <>
          <div className="demo-panel__divider" />
          <div className="demo-panel__footer">{footer}</div>
        </>
      )}
    </div>
  );
}

const libraries: ScopeSwitcherOption[] = [
  {
    id: 'research',
    label: '星河电商研发库',
    icon: <LibraryIcon name="research" />,
    description: '企业版',
  },
  {
    id: 'support',
    label: 'Support library',
    icon: <LibraryIcon name="support" />,
    description: '个人版',
  },
];
const spaces: ScopeSwitcherOption[] = [
  { id: 'default', label: 'default', icon: <SpaceIcon /> },
  { id: 'analytics', label: 'analytics', icon: <SpaceIcon /> },
];
const users: ScopeSwitcherOption[] = [
  { id: 'default', label: 'default', icon: <UserIcon /> },
  { id: 'liyanong', label: 'liyanong', icon: <UserIcon /> },
];

export default function ScopeSwitcherPanel() {
  const componentLocale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  const [library, setLibrary] = useState('research');
  const [space, setSpace] = useState('default');
  const [user, setUser] = useState('default');
  const [keyword, setKeyword] = useState('');

  const matches = (option: ScopeSwitcherOption) =>
    option.label.toLocaleLowerCase().includes(keyword.toLocaleLowerCase());

  return (
    <div style={{ minHeight: 380 }}>
      <ScopeSwitcher locale={componentLocale}>
        <ScopeSelector
          locale={componentLocale}
          kind="selectable"
          identity={{ key: 'library', ariaLabel: 'Switch library' }}
          data={{
            selectedId: library,
            options: libraries.filter(matches),
            total: libraries.length,
          }}
          state={{ searchKeyword: keyword }}
          actions={{
            onSearchKeywordChange: setKeyword,
            onSelect: (option) => setLibrary(option.id),
          }}
          slots={{
            renderPanel: ({ search, list }) => (
              <Shell
                title="全部库"
                action={
                  <Button
                    size="mini"
                    type="text"
                    htmlType="button"
                    className="demo-panel__action"
                  >
                    创建
                  </Button>
                }
                footer={
                  <>
                    <button type="button" className="demo-panel__footer-item">
                      库设置
                    </button>
                    <button type="button" className="demo-panel__footer-item">
                      查看账单
                    </button>
                  </>
                }
              >
                {search}
                {list}
              </Shell>
            ),
          }}
        />
        <ScopeSelector
          locale={componentLocale}
          kind="selectable"
          identity={{ key: 'space', ariaLabel: 'Switch data space' }}
          data={{ selectedId: space, options: spaces }}
          actions={{ onSelect: (option) => setSpace(option.id) }}
          slots={{
            renderPanel: ({ list }) => (
              <Shell
                title="选择数据空间"
                footer={
                  <button type="button" className="demo-panel__footer-item">
                    管理空间
                  </button>
                }
              >
                {list}
              </Shell>
            ),
          }}
        />
        <ScopeSelector
          locale={componentLocale}
          kind="selectable"
          identity={{ key: 'user', ariaLabel: 'Switch user' }}
          data={{ selectedId: user, options: users }}
          actions={{ onSelect: (option) => setUser(option.id) }}
          slots={{
            renderPanel: ({ list }) => (
              <Shell
                title="选择 User"
                footer={
                  <button type="button" className="demo-panel__footer-item">
                    管理 User
                  </button>
                }
              >
                {list}
              </Shell>
            ),
          }}
        />
      </ScopeSwitcher>
      <style>{`
        .demo-panel { display: flex; flex-direction: column; gap: 4px; width: 100%; }
        .demo-panel__header { display: flex; align-items: center; justify-content: space-between; padding: 0 8px; min-height: 22px; }
        .demo-panel__title { font-size: 12px; font-weight: 500; line-height: 20px; color: var(--ov-ui-text-muted, #6e718c); }

        .demo-panel__divider { height: 1px; margin: 4px 0; background: var(--ov-ui-border, #f0f2fa); }
        .demo-panel__footer { display: flex; flex-direction: column; }


        .demo-panel__footer-item { padding: 6px 8px; border: 0; border-radius: 6px; background: transparent; color: var(--ov-ui-text-muted, #3f3f51); font: inherit; font-size: 13px; line-height: 22px; text-align: start; cursor: pointer; }
        .demo-panel__footer-item:hover { background: var(--ov-ui-scope-switcher-row-hover, #f6f7fa); }
      `}</style>
    </div>
  );
}
