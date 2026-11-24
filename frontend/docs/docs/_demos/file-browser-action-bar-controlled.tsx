import { useLang } from '@rspress/core/runtime';
import { useRef, useState } from 'react';
import { Button } from '#demo-controls';
import { FileBrowserActionBar } from '@openviking/react/components/file-browser-action-bar';
import '@openviking/react/components/file-browser-action-bar/style.css';

export default function FileBrowserActionBarControlled() {
  const zh = useLang() === 'zh';
  const [open, setOpen] = useState(false);
  const [viewed, setViewed] = useState(false);
  const openedFromQuickStart = useRef(false);

  return (
    <div style={{ width: '100%', maxWidth: 352, display: 'grid', gap: 12 }}>
      <Button
        onClick={() => {
          openedFromQuickStart.current = true;
          setOpen(true);
        }}
      >
        {zh ? '快速开始：连接 Agent' : 'Quick start: Connect agent'}
      </Button>
      <FileBrowserActionBar
        locale={zh ? 'zh-CN' : 'en-US'}
        agentConnect={{ apiKey: 'demo-key' }}
        agentConnectOpen={open}
        onAgentConnectOpenChange={(next) => {
          setOpen(next);
          if (!next && openedFromQuickStart.current) {
            openedFromQuickStart.current = false;
            setViewed(true);
          }
        }}
      />
      <span role="status">
        {viewed ? (zh ? '已查看接入指引' : 'Connection guide viewed') : null}
      </span>
    </div>
  );
}
