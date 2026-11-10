import { useCallback, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { useLang } from '@rspress/core/runtime';
import { FileTree } from '@openviking/react/components/file-tree';
import type {
  FileTreeHandle,
  FileTreeRow,
} from '@openviking/react/components/file-tree';
import {
  DirectoryTour,
  openVikingDirectoryTourSteps,
} from '@openviking/react/components/directory-tour';
import '@openviking/react/components/file-tree/style.css';
import '@openviking/react/components/directory-tour/style.css';
import '#directory-tour-basic.css';

const TARGETS: Record<string, string> = {
  userMemories: 'user/memories',
  userSessions: 'user/sessions',
  userResources: 'user/resources',
  sharedResources: 'resources',
  userExperiences: 'user/memories/experiences',
  userPeers: 'user/peers',
};
const TITLES = [
  '用户记忆（memories）',
  '对话记录（sessions）',
  '个人资料（resources）',
  '团队公共资料（resources）',
  'Agent 经验（experiences）',
  '对象记忆（peers）',
];
const DESCRIPTIONS = [
  'Agent 从对话中自动提炼的长期记忆 —— 你的身份、偏好和做过的决定。',
  '即长期记忆，你和 Agent 的每一次对话、跑过的任务都完整保存在这里。追溯 Agent 的判断，可随时回来查看原文。',
  '你主动导入的仅你可见的资料 —— 文档、笔记、知识库都放在这里。',
  '成员均可见的公共知识库 —— 文档、规范、历史项目都放在这里。你的 Agent 回答时站在全团队的经验之上，不用每次从零讲起。',
  'Agent 把成功完成的任务自动沉淀为经验。下次遇到类似问题，直接复用已有方案，少走弯路。',
  '只属于某个对象的记忆（可以是某个仓库/Agent/同事），会单独存进 peers 子目录，互不干扰；通用的偏好和习惯，则放在 memories。',
];
const entry = (
  id: string,
  name: string,
  depth: number,
  expanded = false,
): FileTreeRow => ({
  kind: 'entry',
  id,
  uri: `viking://${id}`,
  name,
  type: 'directory',
  depth,
  expanded,
});

export default function DirectoryTourBasic() {
  const zh = useLang() === 'zh';
  const tree = useRef<FileTreeHandle>(null);
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState(new Set(['user']));
  const [selectedId, select] = useState<string>();
  const [missing, setMissing] = useState(false);
  const [complete, setComplete] = useState(false);
  const [skipped, setSkipped] = useState(0);
  const close = useCallback(() => setOpen(false), []);
  // The demo has static data. A real host awaits its own loader here, then commits
  // the expanded path before resolving the DOM element. Never fetch inside Tour.
  const locateTarget = useCallback(
    (target: string, signal: AbortSignal) => {
      const id = TARGETS[target];
      if (signal.aborted || !id || (missing && target === 'userSessions'))
        return Promise.resolve(null);
      flushSync(() =>
        setExpanded(
          (old) =>
            new Set([
              ...old,
              'user',
              ...(target === 'userExperiences' ? ['user/memories'] : []),
            ]),
        ),
      );
      return Promise.resolve(tree.current?.getRowElement(id) ?? null);
    },
    [missing],
  );
  const steps = openVikingDirectoryTourSteps.map((step, index) => ({
    ...step,
    nextLabel: zh && index === 4 ? '下一个' : undefined,
    title: zh ? TITLES[index] : step.title,
    description: zh ? DESCRIPTIONS[index] : step.description,
    illustration: (
      <img
        src={`/ui/directory-tour/step-${index + 1}.png`}
        alt=""
        width={212}
        height={70}
      />
    ),
  }));
  const rows: FileTreeRow[] = [entry('user', 'Alice', 0, expanded.has('user'))];
  if (expanded.has('user')) {
    rows.push(
      entry('user/memories', 'memories', 1, expanded.has('user/memories')),
    );
    if (expanded.has('user/memories'))
      rows.push(entry('user/memories/experiences', 'experiences', 2));
    rows.push(
      entry('user/peers', 'peers', 1),
      entry('user/resources', 'resources', 1),
    );
    if (!missing) rows.push(entry('user/sessions', 'sessions', 1));
  }
  rows.push(entry('resources', 'Resources', 0));
  return (
    <div className="ov-directory-tour-demo">
      <div className="ov-directory-tour-demo__controls">
        <button type="button" onClick={() => setOpen(true)}>
          {complete
            ? zh
              ? '重新查看目录引导'
              : 'Replay directory guide'
            : zh
              ? '了解 OV 目录树'
              : 'Explore OV directories'}
        </button>
        <label>
          <input
            type="checkbox"
            checked={missing}
            onChange={(event) => {
              close();
              setMissing(event.target.checked);
            }}
          />
          {zh ? '模拟 sessions 不存在' : 'Simulate missing sessions'}
        </label>
        <output>
          {complete ? (zh ? '引导已完成' : 'Guide completed') : ''}
          {skipped ? ` (${skipped} ${zh ? '步已跳过' : 'skipped'})` : ''}
        </output>
      </div>
      <div className="ov-directory-tour-demo__layout">
        <FileTree
          ref={tree}
          rows={rows}
          selectedId={selectedId}
          operations={{
            select: (row) => {
              select(row.id);
              return Promise.resolve();
            },
            toggleExpand: (row) => {
              setExpanded((old) => {
                const next = new Set(old);
                if (next.has(row.id)) next.delete(row.id);
                else next.add(row.id);
                return next;
              });
              return Promise.resolve();
            },
          }}
        />
        <p>
          {zh ? '当前选中：' : 'Selected: '}
          {selectedId ?? '—'}
          <br />
          {zh
            ? '引导不会改变文件选中状态。'
            : 'Guidance does not change the selected file.'}
        </p>
      </div>
      <DirectoryTour
        open={open}
        steps={steps}
        locateTarget={locateTarget}
        onClose={close}
        onComplete={({ skippedStepIds }) => {
          close();
          setSkipped(skippedStepIds.length);
          setComplete(skippedStepIds.length === 0);
        }}
        labels={
          zh
            ? {
                close: '关闭指引',
                next: '下一步',
                done: '知道了',
                locating: '正在定位目录…',
                unavailable: '目录不存在或暂时无法加载，请重试或跳过。',
                retry: '重试',
                skip: '跳过',
              }
            : undefined
        }
      />
    </div>
  );
}
