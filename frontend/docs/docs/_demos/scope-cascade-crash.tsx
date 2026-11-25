import { useLang } from '@rspress/core/runtime';
import { ScopeCascade } from '@openviking/react/modules/scope-cascade';
import '@openviking/react/modules/scope-cascade/style.css';
import { LibraryIcon } from '@openviking/react/icons';
import { Fault, RenderFailureDemo } from '#render-failure';

// Mock interfaces only: the library icon is the host-rendered node that fails.
const api = {
  loadLibraries: () =>
    Promise.resolve(
      ['research', 'support'].map((id) => ({
        id,
        label: id === 'research' ? 'Research' : 'Support',
        icon: (
          <Fault>
            <LibraryIcon name={id} />
          </Fault>
        ),
      })),
    ),
  loadSpaces: (libraryId: string) =>
    Promise.resolve([{ id: `${libraryId}-public`, label: 'Public space' }]),
  loadUsers: (_libraryId: string, spaceId: string) =>
    Promise.resolve([{ id: `${spaceId}-alice`, label: 'Alice' }]),
};

export default function ScopeCascadeCrash() {
  const zh = useLang() === 'zh';
  return (
    <RenderFailureDemo
      hint={
        zh
          ? '宿主提供的库图标渲染出错时，级联选择器整体显示兜底。'
          : 'When a host-provided library icon fails to render, the cascade shows the fallback.'
      }
    >
      <div style={{ minHeight: 200 }}>
        <ScopeCascade locale={zh ? 'zh-CN' : 'en-US'} {...api} />
      </div>
    </RenderFailureDemo>
  );
}
