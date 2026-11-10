import type { Locale } from '../../shared/locale.js';
import type { HTMLAttributes } from 'react';

export type AgentConnectLocale = Locale;
export type AgentConnectTabId = 'mainstream' | 'custom';

/** 固定目录中选中的 Agent。Selected agent from the built-in catalog. */
export interface AgentConnectItem {
  id: string;
  name: string;
}

/** 固定设计视图；文案、图标与正文均内置。Fixed view with built-in copy and icons. */
export interface AgentConnectProps extends Omit<
  HTMLAttributes<HTMLDivElement>,
  'children' | 'onSelect' | 'onCopy'
> {
  /** 调用方提供的密钥；展示脱敏，复制保留原值。Host API key. */
  apiKey: string;
  /** 内置文案语言；必须由调用方指定。Required content locale. */
  locale: AgentConnectLocale;
  /** 服务地址，默认 OpenViking 云服务。Optional service endpoint. */
  baseUrl?: string;
  /** 宿主提供的用户协议链接；未传时不显示协议引用。Host agreement URL. */
  agreementUrl?: string;
  /** 是否展示境外标签，默认 false。Show Overseas tags; defaults to false. */
  showOverseasTag?: boolean;
  /** 受控页签；默认 mainstream。Controlled tab; defaults to mainstream. */
  activeTabId?: AgentConnectTabId;
  /** 受控 Agent；默认 codex。Controlled agent; defaults to codex. */
  selectedId?: string;
  /** 页签切换通知。Tab selection notification. */
  onTabChange?: (tabId: AgentConnectTabId) => void;
  /** 返回内置目录条目的 id 和 name。Reports the built-in item id and name. */
  onSelect?: (item: AgentConnectItem) => void;
  /** 复制成功后返回代码块或完整正文。Reports copied block or full-guide text. */
  onCopy?: (text: string) => void;
}
