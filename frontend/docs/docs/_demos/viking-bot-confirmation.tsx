import { useState } from 'react';
import { useLang } from '@rspress/core/runtime';
import { Checkbox } from '#demo-controls';
import {
  VikingBotConfirmation,
  VikingBotInteraction,
} from '@openviking/react/components/viking-bot/interaction';
import type { VikingBotScheduleValue } from '@openviking/react/components/viking-bot';
import '@openviking/react/components/viking-bot/interaction/style.css';

export default function VikingBotConfirmationDemo() {
  const zh = useLang() === 'zh';
  const [editing, setEditing] = useState(false);
  const [fail, setFail] = useState(false);
  const [result, setResult] = useState('');
  const [configuration, setConfiguration] = useState({
    taskName: 'team-knowledge',
    instruction: 'Keep source citations',
    source:
      'viking://resources/project/research\nviking://resources/project/meeting-notes',
    skill: 'viking://skills/llm-wiki',
    target: 'viking://resources/team-knowledge-wiki',
    schedule: { mode: 'daily', time: '10:30' } as VikingBotScheduleValue,
  });
  const titles = zh
    ? {
        source: '源目录',
        skill: 'Skill',
        target: '保存目录',
        schedule: '更新时间',
      }
    : {
        source: 'Source directories',
        skill: 'Skill',
        target: 'Output directory',
        schedule: 'Automatic updates',
      };
  const schedule = configuration.schedule;
  const time =
    schedule.mode === 'none'
      ? zh
        ? '不自动更新'
        : 'Do not update'
      : `${
          schedule.mode === 'weekly'
            ? `${zh ? '每周' : 'Every '}${(zh ? ['一', '二', '三', '四', '五', '六', '日'] : ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'])[schedule.weekday - 1]}`
            : zh
              ? '每日'
              : 'Daily'
        } ${schedule.time} (Asia/Shanghai)`;
  return (
    <>
      <p>
        {zh
          ? '固定模拟数据；修改后进入宿主的预填表单，确认后显示回调结果。'
          : 'Fixed demo data. Edit opens the host’s prefilled form; confirmation displays its callback result.'}
      </p>
      <Checkbox checked={fail} onChange={setFail}>
        {zh ? '模拟提交失败' : 'Simulate submission failure'}
      </Checkbox>
      <div style={{ maxWidth: 360 }}>
        {editing ? (
          <VikingBotInteraction
            locale={zh ? 'zh-CN' : 'en-US'}
            requestKey="configuration-editor"
            request={{
              kind: 'steps',
              steps: [
                {
                  kind: 'text',
                  question: titles.source,
                  initialValue: configuration.source,
                  multiline: true,
                },
                {
                  kind: 'text',
                  question: titles.skill,
                  initialValue: configuration.skill,
                },
                {
                  kind: 'text',
                  question: titles.target,
                  initialValue: configuration.target,
                },
                {
                  kind: 'schedule',
                  question: titles.schedule,
                  initialValue: schedule,
                  timezone: 'Asia/Shanghai',
                },
              ],
            }}
            onCancel={() => setEditing(false)}
            onSubmit={(answer) => {
              if (answer.kind !== 'steps') return;
              const [source, skill, target, update] = answer.value;
              if (
                source?.kind !== 'text' ||
                skill?.kind !== 'text' ||
                target?.kind !== 'text' ||
                update?.kind !== 'schedule'
              )
                return;
              setConfiguration({
                ...configuration,
                source: source.value,
                skill: skill.value,
                target: target.value,
                schedule: update.value,
              });
              setEditing(false);
            }}
          />
        ) : (
          <VikingBotConfirmation
            locale={zh ? 'zh-CN' : 'en-US'}
            title={
              zh ? '确认生成 Wiki 的全部信息' : 'Confirm Wiki Creation Details'
            }
            rows={[
              { label: titles.source, value: configuration.source },
              { label: titles.skill, value: configuration.skill },
              { label: titles.target, value: configuration.target },
              { label: titles.schedule, value: time },
              {
                label: zh ? '任务名称' : 'Task name',
                value: configuration.taskName,
              },
              {
                label: zh ? '补充要求' : 'Additional instructions',
                value: configuration.instruction,
              },
            ]}
            onEdit={() => {
              setResult('');
              setEditing(true);
            }}
            onConfirm={async () => {
              await new Promise((resolve) => setTimeout(resolve, 800));
              if (fail)
                throw new Error(
                  zh ? '模拟提交失败' : 'Simulated submission failure',
                );
              setResult(JSON.stringify(configuration, null, 2));
            }}
          />
        )}
      </div>
      {result && <pre>{result}</pre>}
    </>
  );
}
