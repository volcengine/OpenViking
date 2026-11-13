import { useState } from 'react';
import { useLang } from '@rspress/core/runtime';
import { Button, useNotification } from '#demo-controls';
import '#notification-style.css';

export default function NotificationTasks() {
  const zh = useLang() === 'zh';
  const [notification, holder] = useNotification({
    locale: zh ? 'zh-CN' : 'en-US',
  });
  const [opened, setOpened] = useState('');
  const content = (
    <>
      <div>viking://resources/example</div>
      <div>
        {zh
          ? '模拟任务状态，可继续操作。'
          : 'Simulated task status. You can keep working.'}
      </div>
    </>
  );
  const btn = (
    <Button
      type="primary"
      onClick={() =>
        setOpened(
          zh ? '已打开任务详情（模拟）' : 'Task details opened (simulated)',
        )
      }
    >
      {zh ? '任务管理' : 'Task management'}
    </Button>
  );
  const running = () =>
    notification.info({
      id: 'compile-demo',
      title: zh ? '上下文编译执行中' : 'Compiling context',
      content,
      duration: 0,
      btn,
      icon: <span className="ov-button__spinner" />,
    });
  const complete = () =>
    notification.success({
      id: 'compile-demo',
      title: zh ? '上下文编译已完成' : 'Context compiled',
      content,
      duration: 0,
      btn,
    });
  const fail = () =>
    notification.error({
      id: 'compile-demo',
      title: zh ? '上下文编译失败' : 'Context compilation failed',
      content: zh
        ? '示例错误：服务暂时不可用。'
        : 'Example error: service temporarily unavailable.',
      duration: 0,
      btn,
    });
  return (
    <div>
      <p>
        {zh
          ? '内部基础件演示：手动模拟状态更新，不访问后端；关闭通知不取消任务。'
          : 'Internal control preview: manually simulated updates, no backend calls. Dismissal does not cancel tasks.'}
      </p>
      {holder}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        <Button onClick={running}>
          {zh ? '模拟运行中' : 'Simulate running'}
        </Button>
        <Button onClick={complete}>
          {zh ? '模拟完成' : 'Simulate completion'}
        </Button>
        <Button onClick={fail}>{zh ? '模拟失败' : 'Simulate failure'}</Button>
        <Button
          onClick={() =>
            notification.info({
              title: zh ? '另一个任务' : 'Another task',
              content,
              duration: 0,
              btn,
            })
          }
        >
          {zh ? '新增并发任务' : 'Add concurrent task'}
        </Button>
        <Button
          onClick={() =>
            notification.normal({
              content: zh ? '三秒后关闭' : 'Closes after three seconds',
            })
          }
        >
          {zh ? '自动关闭' : 'Auto dismiss'}
        </Button>
        <Button onClick={notification.clear}>
          {zh ? '清空通知' : 'Clear notices'}
        </Button>
      </div>
      <p role="status">{opened}</p>
    </div>
  );
}
