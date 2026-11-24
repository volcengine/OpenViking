import { useLang } from '@rspress/core/runtime';
import { OpenVikingProvider } from '@openviking/react/client';
import { Console } from '@openviking/react/modules/console';
import '@openviking/react/modules/console/style.css';
import { consoleTransport } from '#console-transport';
import { Fault, RenderFailureDemo } from '#render-failure';

const transport = consoleTransport();

export default function ConsoleCrash() {
  const zh = useLang() === 'zh';
  return (
    <RenderFailureDemo
      hint={
        zh
          ? '宿主追加到工具栏的内容出错时，只有工具栏显示兜底，命令面板照常可用。'
          : 'When host content added to the toolbar fails, only the toolbar shows the fallback; the command panel stays usable.'
      }
    >
      <OpenVikingProvider fetch={transport}>
        <div style={{ display: 'flex', flexDirection: 'column', height: 480 }}>
          <Console
            locale={zh ? 'zh-CN' : 'en-US'}
            defaultMode="command"
            slots={{ toolbar: <Fault /> }}
          />
        </div>
      </OpenVikingProvider>
    </RenderFailureDemo>
  );
}
