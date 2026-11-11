import { useLang } from '@rspress/core/runtime';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { Console } from '@openviking/react/modules/console';
import '@openviking/react/modules/style.css';

export default function ModulesBasic() {
  const locale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  return (
    <div className="ov-demo-modules">
      <FileBrowser locale={locale} />
      <Console locale={locale} />
    </div>
  );
}
