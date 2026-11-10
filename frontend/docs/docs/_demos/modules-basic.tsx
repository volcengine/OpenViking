import { FileBrowser } from '@openviking/react/modules/file-browser';
import { Console } from '@openviking/react/modules/console';
import '@openviking/react/modules/style.css';

export default function ModulesBasic() {
  return (
    <div className="ov-demo-modules" lang="en">
      <FileBrowser />
      <Console />
    </div>
  );
}
