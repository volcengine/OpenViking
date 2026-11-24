import { useLang } from '@rspress/core/runtime';
import {
  ExperienceDashboard,
  type ExperienceTrajectory,
} from '@openviking/react/components/experience-dashboard';
import '@openviking/react/components/experience-dashboard/style.css';
import { DEMO_EXPERIENCE, DEMO_TRAJECTORY } from '#dashboard-demo-data';
import { RenderFailureDemo, useFailing } from '#render-failure';

function Dashboard() {
  const zh = useLang() === 'zh';
  const failing = useFailing();
  const trajectory = {
    uri: DEMO_TRAJECTORY,
    name: 'Release verification',
    created_at: '2026-11-09T12:03:00Z',
  };
  // 模拟宿主传入不完整的数据：轨迹名称为空，且缺少必填的 uri。
  const broken = { name: '' } as ExperienceTrajectory;
  return (
    <ExperienceDashboard
      locale={zh ? 'zh-CN' : 'en-US'}
      uri={DEMO_EXPERIENCE}
      kind="file"
      data={{
        trajectories: [failing ? broken : trajectory],
        trajectoryTotal: 1,
      }}
    />
  );
}

export default function ExperienceDashboardCrash() {
  const zh = useLang() === 'zh';
  return (
    <RenderFailureDemo
      hint={
        zh
          ? '宿主传入的轨迹名称为空、又缺少必填的 uri 时，看板整体显示兜底。'
          : 'When a host-supplied trajectory has an empty name and no required uri, the board shows the fallback.'
      }
    >
      <Dashboard />
    </RenderFailureDemo>
  );
}
