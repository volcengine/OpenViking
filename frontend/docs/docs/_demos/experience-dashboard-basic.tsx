import { useLang } from '@rspress/core/runtime';
import { useState } from 'react';
import { ExperienceDashboard } from '@openviking/react/components/experience-dashboard';
import '@openviking/react/components/experience-dashboard/style.css';
import {
  experienceViewData,
  DEMO_EXPERIENCES,
  DEMO_EXPERIENCE,
  DEMO_TRAJECTORY,
} from '#dashboard-demo-data';

export default function ExperienceDashboardBasic() {
  const zh = useLang() === 'zh';
  const locale = zh ? 'zh-CN' : 'en-US';
  const text = zh
    ? {
        description:
          '纯 UI 示例，固定数据由调用方传入；未提供的指标显示“—”，组件不发送请求。',
        directory: '经验目录',
        file: '经验文件',
        host: '宿主数据',
        failure: '失败状态',
        error: '示例加载失败',
        opened: '打开意图',
        testTitle: '测试 Message 1',
        testContent:
          '# 发布检查\nUser: 验证审批规则和回滚条件。\nAssistant: 检查通过，已保存验证证据。',
      }
    : {
        description:
          'Pure UI example with fixed host data. Missing metrics display “—”; the component makes no requests.',
        directory: 'Experience directory',
        file: 'Experience file',
        host: 'Host data',
        failure: 'Error state',
        error: 'Example load failed',
        opened: 'Open intent',
        testTitle: 'Test Message 1',
        testContent:
          '# Release checks\nUser: Verify approval rules and rollback conditions.\nAssistant: Checks passed; verification evidence saved.',
      };
  const [uri, setUri] = useState(DEMO_EXPERIENCE);
  const [gateway, setGateway] = useState(false);
  const [failure, setFailure] = useState(false);
  const [opened, setOpened] = useState('');
  return (
    <div lang={locale}>
      <p>{text.description}</p>
      <p className="ov-demo-controls">
        <button
          type="button"
          onClick={() => {
            setUri(DEMO_EXPERIENCES);
            setGateway(false);
          }}
        >
          {text.directory}
        </button>{' '}
        <button type="button" onClick={() => setUri(DEMO_EXPERIENCE)}>
          {text.file}
        </button>{' '}
        <button
          type="button"
          aria-pressed={gateway}
          onClick={() => {
            setGateway(!gateway);
            setFailure(false);
            setUri(DEMO_EXPERIENCE);
          }}
        >
          {text.host}
        </button>{' '}
        <button
          type="button"
          aria-pressed={failure}
          onClick={() => {
            setFailure(!failure);
            setGateway(false);
          }}
        >
          {text.failure}
        </button>
      </p>
      <ExperienceDashboard
        uri={uri}
        kind={uri === DEMO_EXPERIENCES ? 'directory' : 'file'}
        states={
          failure
            ? {
                outcomes: {
                  error: new Error(text.error),
                  onRetry: () => setFailure(false),
                },
                trajectories: {
                  error: new Error(text.error),
                  onRetry: () => setFailure(false),
                },
              }
            : undefined
        }
        locale={locale}
        onOpenUri={(value) =>
          value === DEMO_EXPERIENCE ? setUri(value) : setOpened(value)
        }
        data={
          failure
            ? {}
            : gateway
              ? {
                  effect: {
                    injectionCount: 12,
                    recallCount: 20,
                    injectionRate: 0.6,
                  },
                  outcomes: [
                    { outcome: 'success', count: 8 },
                    { outcome: 'partial', count: 2 },
                  ],
                  trajectories: [
                    {
                      uri: DEMO_TRAJECTORY,
                      name: 'Release verification',
                      created_at: '2026-11-09T12:03:00Z',
                    },
                  ],
                  trajectoryTotal: 1,
                  sources: [
                    { uri: DEMO_TRAJECTORY, name: 'Release verification' },
                  ],
                  testMaterials: [
                    {
                      id: 'test-1',
                      title: text.testTitle,
                      uri: DEMO_TRAJECTORY,
                      content: text.testContent,
                    },
                  ],
                }
              : experienceViewData
        }
      />
      {opened ? (
        <p role="status">
          {text.opened}: {opened}
        </p>
      ) : null}
    </div>
  );
}
