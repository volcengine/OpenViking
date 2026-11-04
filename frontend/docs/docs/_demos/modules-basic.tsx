import { FileBrowser } from '@openviking/react/modules/file-browser';
import { Console } from '@openviking/react/modules/console';
import { AgentConnect } from '@openviking/react/modules/agent-connect';
import { TaskCenter } from '@openviking/react/modules/task-center';
import { UserUsage } from '@openviking/react/modules/user-usage';
import '@openviking/react/modules/style.css';

export default function ModulesBasic() {
  return (
    <div className="ov-demo-modules" lang="en">
      <FileBrowser />
      <Console />
      <AgentConnect />
      <TaskCenter />
      <UserUsage />
    </div>
  );
}
