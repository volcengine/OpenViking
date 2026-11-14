import { useLang } from '@rspress/core/runtime';
import { WikiDashboard } from '@openviking/react/components/wiki-dashboard';
import '@openviking/react/components/wiki-dashboard/style.css';
import { WikiEditIcon } from '@openviking/react/icons';
import { DEMO_WIKI_FILE } from '#dashboard-demo-data';
import { Fault, RenderFailureDemo } from '#render-failure';

export default function WikiDashboardCrash() {
  const zh = useLang() === 'zh';
  return (
    <RenderFailureDemo
      hint={
        zh
          ? '宿主操作的图标渲染出错时，看板整体显示兜底，页面其他内容不受影响。'
          : 'When a host action icon fails to render, the board shows the fallback while the rest of the page is unaffected.'
      }
    >
      <WikiDashboard
        locale={zh ? 'zh-CN' : 'en-US'}
        kind="directory"
        data={{ files: [DEMO_WIKI_FILE], conflictFileCount: 1 }}
        defineActions={() => [
          {
            key: 'edit',
            tooltip: zh ? '编辑' : 'Edit',
            icon: (
              <Fault>
                <WikiEditIcon />
              </Fault>
            ),
          },
        ]}
      />
    </RenderFailureDemo>
  );
}
