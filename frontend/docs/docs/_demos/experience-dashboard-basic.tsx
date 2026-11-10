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
  const [uri, setUri] = useState(DEMO_EXPERIENCE);
  const [gateway, setGateway] = useState(false);
  const [failure, setFailure] = useState(false);
  const [opened, setOpened] = useState('');
  return (
    <div lang="zh-CN">
      <p>
        纯 UI 示例，固定数据由调用方传入；未提供的指标显示“—”，组件不发送请求。
      </p>
      <p className="ov-demo-controls">
        <button
          type="button"
          onClick={() => {
            setUri(DEMO_EXPERIENCES);
            setGateway(false);
          }}
        >
          经验目录
        </button>{' '}
        <button type="button" onClick={() => setUri(DEMO_EXPERIENCE)}>
          经验文件
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
          宿主数据
        </button>{' '}
        <button
          type="button"
          aria-pressed={failure}
          onClick={() => {
            setFailure(!failure);
            setGateway(false);
          }}
        >
          失败状态
        </button>
      </p>
      <ExperienceDashboard
        key={`${gateway}:${failure}`}
        uri={uri}
        kind={uri === DEMO_EXPERIENCES ? 'directory' : 'file'}
        states={
          failure
            ? {
                outcomes: {
                  error: new Error('示例加载失败'),
                  onRetry: () => setFailure(false),
                },
                trajectories: {
                  error: new Error('示例加载失败'),
                  onRetry: () => setFailure(false),
                },
              }
            : undefined
        }
        locale="zh-CN"
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
                      title: '测试 Message 1',
                      uri: DEMO_TRAJECTORY,
                      content:
                        '# 发布检查\nUser: 验证审批规则和回滚条件。\nAssistant: 检查通过，已保存验证证据。',
                    },
                  ],
                }
              : experienceViewData
        }
      />
      {opened ? <p role="status">打开意图：{opened}</p> : null}
    </div>
  );
}
