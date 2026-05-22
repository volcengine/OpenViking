import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

const CONFIG = {
  serverUrl: (import.meta.env.VITE_ZOUK_SERVER_URL || 'https://zouk.zaynjarvis.com').replace(/\/+$/, ''),
  workspaceId: import.meta.env.VITE_ZOUK_WORKSPACE_ID || 'zayn',
  channel: (import.meta.env.VITE_ZOUK_CHANNEL || 'blog').replace(/^#/, ''),
  guestName: import.meta.env.VITE_ZOUK_GUEST_NAME || 'reader',
};

const BROWSER_ID_KEY = 'openviking.zouk.browserId';
const CLOSE_ANIMATION_MS = 220;

function browserAvailable() {
  return typeof window !== 'undefined' && typeof document !== 'undefined';
}

function compactText(text = '', limit = 900) {
  return String(text).trim().replace(/\s+/g, ' ').slice(0, limit);
}

function escapeContextText(text = '', limit = 900) {
  return compactText(text, limit)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function unescapeContextText(text = '') {
  return String(text)
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&');
}

function currentPostSelectionText() {
  if (!browserAvailable()) return '';
  const selection = window.getSelection?.();
  const text = compactText(selection?.toString() || '');
  if (!text || !selection?.rangeCount) return '';
  const postBody = document.querySelector('.b-post__body');
  if (!postBody || !selection.anchorNode || !selection.focusNode) return '';
  if (!postBody.contains(selection.anchorNode) || !postBody.contains(selection.focusNode)) return '';
  return text;
}

function createBrowserId() {
  if (browserAvailable() && window.crypto?.randomUUID) return window.crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

function getBrowserId() {
  if (!browserAvailable()) return '';
  try {
    const existing = window.localStorage.getItem(BROWSER_ID_KEY);
    if (existing) return existing;
    const next = createBrowserId();
    window.localStorage.setItem(BROWSER_ID_KEY, next);
    return next;
  } catch {
    return createBrowserId();
  }
}

function currentSourceUrl() {
  if (!browserAvailable()) return 'https://blog.openviking.ai/';
  return window.location.href;
}

function currentGuestPictureUrl() {
  if (!browserAvailable()) return 'https://blog.openviking.ai/assets/logo.png';
  try {
    return new URL('/assets/logo.png', window.location.origin).toString();
  } catch {
    return 'https://blog.openviking.ai/assets/logo.png';
  }
}

function wsUrlFor(serverUrl, token, workspaceId) {
  const url = new URL('/ws', serverUrl);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  url.searchParams.set('token', token);
  url.searchParams.set('workspaceId', workspaceId);
  return url.toString();
}

async function parseJsonResponse(res) {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body?.error || `Request failed (${res.status})`);
  return body;
}

function normalizeAvatarUrl(value = '') {
  const src = String(value || '').trim();
  if (!src) return '';
  return /^(https?:\/\/|data:image\/)/i.test(src) ? src : '';
}

function normalizeMessage(message) {
  if (!message) return null;
  const rawReplies = Array.isArray(message.replies) ? message.replies : [];
  const avatarUrl = normalizeAvatarUrl(
    message.senderPicture
      || message.sender_picture
      || message.senderAvatarUrl
      || message.sender_avatar_url
      || message.picture
      || message.avatarUrl
      || message.avatar_url
      || message.senderGravatarUrl
      || message.sender_gravatar_url
      || message.gravatarUrl
      || message.gravatar_url
      || '',
  );
  return {
    id: message.id || message.messageId,
    content: message.content || '',
    senderName: message.senderName || message.sender_name || 'unknown',
    senderType: message.senderType || message.sender_type || 'human',
    createdAt: message.createdAt || message.timestamp || new Date().toISOString(),
    channelName: message.channelName || message.channel_name || '',
    channelType: message.channelType || message.channel_type || 'channel',
    parentChannelName: message.parentChannelName || message.parent_channel_name || '',
    parentMessageId: message.parentMessageId || message.parent_message_id || '',
    threadId: message.threadId || message.thread_id || '',
    replyCount: Number(message.replyCount ?? message.reply_count ?? rawReplies.length) || 0,
    replies: rawReplies.map(normalizeMessage).filter(Boolean),
    avatarUrl,
  };
}

function senderKey(value = '') {
  return String(value || '').replace(/^@/, '').trim().toLowerCase();
}

function normalizeChannelName(value = '') {
  return String(value || '').replace(/^#/, '').trim().toLowerCase();
}

function normalizeAgentChannel(value) {
  if (typeof value === 'string') return normalizeChannelName(value);
  if (value && typeof value === 'object') return normalizeChannelName(value.name || value.channelName || value.channel_name || '');
  return '';
}

function normalizeAgentActivity(value = '') {
  const activity = String(value || '').trim().toLowerCase();
  return ['thinking', 'working', 'online', 'offline', 'error'].includes(activity)
    ? activity
    : '';
}

function normalizeAgent(agent) {
  if (!agent?.id) return null;
  return {
    id: String(agent.id),
    name: String(agent.name || agent.id),
    displayName: String(agent.displayName || agent.display_name || agent.name || agent.id),
    avatarUrl: normalizeAvatarUrl(
      agent.picture
        || agent.avatarUrl
        || agent.avatar_url
        || agent.gravatarUrl
        || agent.gravatar_url
        || '',
    ),
    status: String(agent.status || 'inactive'),
    activity: normalizeAgentActivity(agent.activity),
    activityDetail: String(agent.activityDetail || agent.activity_detail || '').trim(),
    channels: Array.isArray(agent.channels)
      ? agent.channels.map(normalizeAgentChannel).filter(Boolean)
      : [],
  };
}

function agentBelongsToChannel(agent, channelName) {
  const normalizedChannel = normalizeChannelName(channelName);
  if (!normalizedChannel || !Array.isArray(agent?.channels)) return false;
  return agent.channels.includes(normalizedChannel);
}

function agentSortWeight(agent) {
  const activity = agent?.activity;
  if (activity === 'working') return 0;
  if (activity === 'thinking') return 1;
  if (activity === 'error') return 2;
  if (activity === 'online') return 3;
  return 4;
}

function sortAgents(agents) {
  return [...agents].sort((a, b) => {
    const weight = agentSortWeight(a) - agentSortWeight(b);
    if (weight) return weight;
    return (a.displayName || a.name).localeCompare(b.displayName || b.name);
  });
}

function mergeAgents(current, incoming) {
  const next = new Map(current.map((agent) => [agent.id, agent]));
  const list = Array.isArray(incoming) ? incoming : [incoming];
  list.forEach((raw) => {
    const agent = normalizeAgent(raw);
    if (!agent) return;
    const previous = next.get(agent.id);
    next.set(agent.id, {
      ...previous,
      ...agent,
      activity: agent.activity || previous?.activity || '',
      activityDetail: agent.activityDetail || previous?.activityDetail || '',
      channels: agent.channels.length ? agent.channels : previous?.channels || [],
    });
  });
  return sortAgents(Array.from(next.values()));
}

function updateAgentStatus(agents, packet) {
  if (!packet?.agentId) return agents;
  if (packet.status === 'deleted') return agents.filter((agent) => agent.id !== packet.agentId);
  return sortAgents(agents.map((agent) => (
    agent.id === packet.agentId ? { ...agent, status: String(packet.status || agent.status) } : agent
  )));
}

function updateAgentActivity(agents, packet) {
  if (!packet?.agentId) return agents;
  return sortAgents(agents.map((agent) => (
    agent.id === packet.agentId
      ? {
        ...agent,
        activity: normalizeAgentActivity(packet.activity) || agent.activity,
        activityDetail: String(packet.detail || '').trim() || agent.activityDetail,
      }
      : agent
  )));
}

function agentDotStatus(agent) {
  if (!agent || agent.status !== 'active') return 'offline';
  if (agent.activity === 'working') return 'working';
  if (agent.activity === 'thinking') return 'thinking';
  if (agent.activity === 'error') return 'error';
  if (agent.activity === 'online') return 'online';
  return 'offline';
}

function agentIsLive(agent) {
  if (!agent || agent.status !== 'active') return false;
  return agent.activity === 'working' || agent.activity === 'thinking' || agent.activity === 'error';
}

function agentIsThinking(agent) {
  if (!agent || agent.status !== 'active') return false;
  return agent.activity === 'working' || agent.activity === 'thinking';
}

function isSystemMessage(message) {
  return message?.senderType === 'system' || message?.senderName === 'system';
}

function mergeMessage(messages, incoming) {
  if (!incoming?.id || isSystemMessage(incoming)) return messages;
  if (messages.some((message) => message.id === incoming.id)) return messages;
  return [...messages, incoming].slice(-120);
}

function mergeThreadReply(messages, reply) {
  if (!reply?.id || isSystemMessage(reply)) return messages;
  const threadId = reply.threadId || reply.channelName || '';
  const parentId = reply.parentMessageId || '';
  return messages.map((message) => {
    const matchesParent = parentId
      ? message.id === parentId
      : String(message.id || '').slice(0, 8) === threadId;
    if (!matchesParent) return message;
    const replies = Array.isArray(message.replies) ? message.replies : [];
    if (replies.some((item) => item.id === reply.id)) return message;
    const nextReplies = [...replies, reply].slice(-3);
    return {
      ...message,
      replies: nextReplies,
      replyCount: Math.max((message.replyCount || 0) + 1, nextReplies.length),
    };
  });
}

function threadTargetForMessage(message) {
  return `#${CONFIG.channel}:${String(message?.id || '').slice(0, 8)}`;
}

function threadStateKeyForReply(reply, states = {}) {
  if (reply.parentMessageId) return reply.parentMessageId;
  const threadId = reply.threadId || reply.channelName || '';
  return Object.keys(states).find((key) => key.slice(0, 8) === threadId) || '';
}

function mergeThreadStateReply(states, reply) {
  const key = threadStateKeyForReply(reply, states);
  const current = key ? states[key] : null;
  if (!current?.messages || current.messages.some((item) => item.id === reply.id)) return states;
  return {
    ...states,
    [key]: {
      ...current,
      messages: [...current.messages, reply],
    },
  };
}

function buildInjectedContext(sourceUrl, referencedText, includeUrl) {
  const lines = ['<zouk-context>'];
  if (includeUrl) lines.push(`  <url>${escapeContextText(sourceUrl, 1600)}</url>`);
  const reference = compactText(referencedText);
  if (reference) lines.push(`  <referenced-text>${escapeContextText(reference)}</referenced-text>`);
  lines.push('</zouk-context>');
  return lines.join('\n');
}

function messageWithInjectedContext(message, sourceUrl, referencedText, includeUrl, shouldInject) {
  const trimmed = message.trim();
  if (!shouldInject) return trimmed;
  return `${buildInjectedContext(sourceUrl, referencedText, includeUrl)}\n\n${trimmed}`;
}

function isComposingInput(event) {
  const nativeEvent = event?.nativeEvent || event || {};
  return Boolean(
    event?.isComposing
      || nativeEvent.isComposing
      || event?.keyCode === 229
      || nativeEvent.keyCode === 229
      || event?.which === 229
      || nativeEvent.which === 229,
  );
}

function shouldSubmitOnEnter(event) {
  return event.key === 'Enter' && !event.shiftKey && !isComposingInput(event);
}

function parseInjectedMessage(content) {
  const text = String(content || '');
  const xmlMatch = text.match(/^<zouk-context>\n?([\s\S]*?)\n?<\/zouk-context>\n*/i);
  if (xmlMatch) {
    const markup = xmlMatch[1];
    const readTag = (tag) => {
      const tagMatch = markup.match(new RegExp(`<${tag}>([\\s\\S]*?)<\\/${tag}>`, 'i'));
      return tagMatch ? unescapeContextText(tagMatch[1].trim()) : '';
    };
    const context = [
      { key: 'url', value: readTag('url') },
      { key: 'referenced', value: readTag('referenced-text') || readTag('selected-text') },
    ].filter((item) => item.value);
    return { context: context.length ? context : null, body: text.slice(xmlMatch[0].length).trimStart() };
  }

  const match = text.match(/^\/\*\s*(?:auto-injected context\s*\n)?([\s\S]*?)\n\*\/\n*/i);
  if (!match) return { context: null, body: text };
  const contextLines = match[1].split('\n').map((line) => line.trim()).filter(Boolean);
  const context = contextLines.map((line) => {
    const index = line.indexOf(':');
    if (index < 0) return { key: 'context', value: line };
    const rawKey = line.slice(0, index).trim();
    const key = rawKey === 'site_url'
      ? 'url'
      : rawKey === 'selected_text'
        ? 'referenced'
        : rawKey.replace(/_/g, ' ');
    let value = line.slice(index + 1).trim();
    if (value.startsWith('"') && value.endsWith('"')) {
      try {
        value = JSON.parse(value);
      } catch {
        value = value.slice(1, -1);
      }
    }
    return { key, value };
  });
  return { context, body: text.slice(match[0].length).trimStart() };
}

function avatarLabel(name) {
  const clean = String(name || 'z').replace(/^@/, '').trim();
  return (clean[0] || 'z').toUpperCase();
}

function Avatar({ name, src, status = '', compact = false, kind = 'human' }) {
  const [imageFailed, setImageFailed] = useState(false);
  const imageSrc = !imageFailed ? normalizeAvatarUrl(src) : '';
  const dotStatus = ['working', 'thinking', 'online', 'offline', 'error'].includes(status) ? status : '';
  const avatarKind = kind === 'agent' ? 'agent' : 'human';
  return (
    <div
      className={`zouk-reader-avatar is-${avatarKind}${imageSrc ? ' has-image' : ''}${dotStatus ? ' has-status' : ''}${compact ? ' is-compact' : ''}`}
      aria-hidden="true"
    >
      {imageSrc ? (
        <img src={imageSrc} alt="" loading="lazy" decoding="async" onError={() => setImageFailed(true)} />
      ) : avatarLabel(name)}
      {dotStatus ? <span className={`zouk-reader-avatar-dot is-${dotStatus}`} /> : null}
    </div>
  );
}

function MessageIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4Z" />
    </svg>
  );
}

function MessagesIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M17 12a4 4 0 0 1-4 4H7l-4 3V8a4 4 0 0 1 4-4h6a4 4 0 0 1 4 4Z" />
      <path d="M9 16v1a3 3 0 0 0 3 3h5l4 2V11a3 3 0 0 0-3-3h-1" />
    </svg>
  );
}

function ContextPreview({ sourceUrl, referencedText, includeUrl }) {
  const reference = compactText(referencedText, 180);
  return (
    <div className="zouk-context-preview" aria-label="Injected context">
      {includeUrl ? (
        <div className="zouk-context-preview__row">
          <span>url</span>
          <strong>{sourceUrl}</strong>
        </div>
      ) : null}
      {reference ? (
        <div className="zouk-context-preview__row">
          <span>referenced</span>
          <strong>{reference}</strong>
        </div>
      ) : null}
    </div>
  );
}

function MessageBody({ content }) {
  const parsed = parseInjectedMessage(content);
  return (
    <>
      {parsed.context ? (
        <div className="zouk-message-context">
          {parsed.context.map((item) => (
            <div className="zouk-message-context__row" key={`${item.key}:${item.value}`}>
              <span>{item.key}</span>
              <strong>{item.value}</strong>
            </div>
          ))}
        </div>
      ) : null}
      {parsed.body ? <div className="zouk-message-text">{parsed.body}</div> : null}
    </>
  );
}

function previewThreadContent(content) {
  const parsed = parseInjectedMessage(content);
  return compactText(parsed.body || content, 220);
}

function ThreadBlock({ message, state, agentsBySender, onToggle }) {
  const inlineReplies = Array.isArray(message.replies) ? message.replies : [];
  const stateReplies = Array.isArray(state?.messages) ? state.messages : [];
  const availableReplies = stateReplies.length ? stateReplies : inlineReplies;
  const replyCount = message.replyCount || availableReplies.length || 0;
  if (!replyCount) return null;
  const expanded = Boolean(state?.open);
  const replies = expanded ? availableReplies : availableReplies.slice(-1);
  const label = replyCount === 1 ? '1 reply' : `${replyCount} replies`;
  return (
    <div className={`zouk-reader-thread${expanded ? ' is-expanded' : ''}`}>
      <button
        type="button"
        className="zouk-reader-thread__toggle"
        onClick={() => onToggle(message)}
        aria-expanded={expanded}
      >
        <span>{expanded ? 'Hide thread' : label}</span>
        <span aria-hidden="true">{expanded ? '-' : '+'}</span>
      </button>
      {expanded ? (
        <div className="zouk-reader-thread__body">
          {state?.loading ? <div className="zouk-reader-thread__state">Loading thread...</div> : null}
          {state?.error ? <div className="zouk-reader-thread__state is-error">{state.error}</div> : null}
          {!state?.loading && replies.map((reply) => {
            const replyAgent = agentsBySender.get(senderKey(reply.senderName));
            const avatarKind = replyAgent || reply.senderType === 'agent' ? 'agent' : 'human';
            return (
              <div className="zouk-reader-thread__reply" key={reply.id}>
                <Avatar
                  name={reply.senderName}
                  src={reply.avatarUrl || replyAgent?.avatarUrl}
                  status={replyAgent ? agentDotStatus(replyAgent) : ''}
                  compact
                  kind={avatarKind}
                />
                <div className="zouk-reader-thread__reply-main">
                  <div className="zouk-reader-sender">{reply.senderName}</div>
                  <div className="zouk-reader-thread__reply-text">
                    <MessageBody content={reply.content} />
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      ) : replies.length ? (
        <button
          type="button"
          className="zouk-reader-thread__preview"
          onClick={() => onToggle(message)}
          aria-label={`Expand ${label}`}
        >
          <span>{replies[0].senderName}</span>
          <strong>{previewThreadContent(replies[0].content)}</strong>
        </button>
      ) : null}
    </div>
  );
}

function LiveAgents({ agents }) {
  if (!agents.length) return null;
  const visible = agents.slice(0, 4);
  const extra = agents.length - visible.length;
  return (
    <div className="zouk-live-agents" aria-label="Live Zouk agents">
      <span className="zouk-live-agents__label">LIVE</span>
      {visible.map((agent) => {
        const label = agent.displayName || agent.name;
        const detail = agent.activityDetail || agent.activity || 'working';
        const dotStatus = agentDotStatus(agent);
        return (
          <div className={`zouk-live-agent is-${dotStatus}`} key={agent.id} title={`${label} · ${detail}`}>
            <Avatar name={label} src={agent.avatarUrl} status={dotStatus} compact kind="agent" />
            <span className="zouk-live-agent__name">{label}</span>
            <span className="zouk-live-agent__detail">{detail}</span>
          </div>
        );
      })}
      {extra > 0 ? <span className="zouk-live-agent-more">+{extra}</span> : null}
    </div>
  );
}

function ThinkingMessage({ agent }) {
  const label = agent?.displayName || agent?.name || 'agent';
  const dotStatus = agentDotStatus(agent);
  return (
    <article className="zouk-reader-message is-thinking-placeholder" aria-live="polite">
      <div className="zouk-reader-message-profile">
        <Avatar name={label} src={agent?.avatarUrl} status={dotStatus} kind="agent" />
        <div className="zouk-reader-bubble-column">
          <div className="zouk-reader-sender">{label}</div>
          <div className="zouk-reader-bubble zouk-reader-thinking-bubble">
            <span className="zouk-reader-thinking-text" aria-label="thinking...">
              <span>thinking</span>
              <span className="zouk-reader-thinking-dots" aria-hidden="true">
                <span>.</span><span>.</span><span>.</span>
              </span>
            </span>
          </div>
        </div>
      </div>
    </article>
  );
}

export function ZoukInteractiveBlog({ route }) {
  const [browserId] = useState(getBrowserId);
  const [open, setOpen] = useState(false);
  const [closing, setClosing] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [dragY, setDragY] = useState(0);
  const [isDesktop, setIsDesktop] = useState(false);
  const [token, setToken] = useState('');
  const [userName, setUserName] = useState('');
  const [status, setStatus] = useState('idle');
  const [error, setError] = useState('');
  const [messages, setMessages] = useState([]);
  const [agents, setAgents] = useState([]);
  const [composer, setComposer] = useState('');
  const [threadStates, setThreadStates] = useState({});
  const [selectedText, setSelectedText] = useState('');
  const [sourceUrl, setSourceUrl] = useState(currentSourceUrl);
  const [lastContextUrl, setLastContextUrl] = useState('');
  const [selectionAction, setSelectionAction] = useState(null);
  const [headerSlot, setHeaderSlot] = useState(null);
  const [dismissedThinkingKey, setDismissedThinkingKey] = useState('');
  const scrollRef = useRef(null);
  const textareaRef = useRef(null);
  const wsRef = useRef(null);
  const closeTimerRef = useRef(null);
  const dragRef = useRef(null);
  const sheetHeightRef = useRef(0);
  const thinkingMessageKeyRef = useRef('');
  const target = `#${CONFIG.channel}`;
  const panelVisible = open || closing;
  const referencedText = compactText(selectedText);
  const contextUrlChanged = Boolean(sourceUrl && sourceUrl !== lastContextUrl);
  const includeContextUrl = contextUrlChanged || Boolean(referencedText);
  const shouldInjectContext = includeContextUrl || Boolean(referencedText);

  const authHeaders = useMemo(() => ({
    'Content-Type': 'application/json',
    Authorization: `Bearer ${token}`,
    'X-Workspace-Id': CONFIG.workspaceId,
  }), [token]);

  const visibleMessages = useMemo(
    () => messages.filter((message) => !isSystemMessage(message)),
    [messages],
  );
  const channelAgents = useMemo(
    () => agents.filter((agent) => agentBelongsToChannel(agent, CONFIG.channel)),
    [agents],
  );
  const agentsBySender = useMemo(() => {
    const next = new Map();
    channelAgents.forEach((agent) => {
      [agent.name, agent.displayName, agent.id].forEach((key) => {
        const normalized = senderKey(key);
        if (normalized && !next.has(normalized)) next.set(normalized, agent);
      });
    });
    return next;
  }, [channelAgents]);
  const liveAgents = useMemo(
    () => channelAgents.filter(agentIsLive),
    [channelAgents],
  );
  const thinkingAgent = useMemo(
    () => liveAgents.find(agentIsThinking) || null,
    [liveAgents],
  );
  const thinkingMessageKey = thinkingAgent ? thinkingAgent.id : '';
  const showThinkingMessage = Boolean(thinkingAgent && thinkingMessageKey !== dismissedThinkingKey);
  const launcherStatus = useMemo(() => {
    const statuses = liveAgents.map(agentDotStatus);
    if (statuses.includes('error')) return 'error';
    if (statuses.includes('working')) return 'working';
    if (statuses.includes('thinking')) return 'thinking';
    if (statuses.includes('online')) return 'online';
    return liveAgents.length ? 'offline' : '';
  }, [liveAgents]);

  const rememberSource = useCallback(() => {
    const next = currentSourceUrl();
    setSourceUrl(next);
    return next;
  }, []);

  const dismissThinkingMessage = useCallback(() => {
    const key = thinkingMessageKeyRef.current;
    if (key) setDismissedThinkingKey(key);
  }, []);

  const openChat = useCallback((nextSelectedText = '') => {
    if (!browserAvailable()) return;
    rememberSource();
    if (closeTimerRef.current) window.clearTimeout(closeTimerRef.current);
    setClosing(false);
    setDragY(0);
    setOpen(true);
    setSelectionAction(null);
    setSelectedText(compactText(nextSelectedText || currentPostSelectionText()));
    if (isDesktop) window.setTimeout(() => textareaRef.current?.focus(), 80);
  }, [isDesktop, rememberSource]);

  const closeChat = useCallback(() => {
    if (!browserAvailable()) return;
    if (closeTimerRef.current) window.clearTimeout(closeTimerRef.current);
    setDragging(false);
    setDragY(0);
    setClosing(true);
    closeTimerRef.current = window.setTimeout(() => {
      setOpen(false);
      setClosing(false);
      closeTimerRef.current = null;
    }, CLOSE_ANIMATION_MS);
  }, []);

  const toggleChat = useCallback(() => {
    if (panelVisible) closeChat();
    else openChat();
  }, [closeChat, openChat, panelVisible]);

  const loadHistory = useCallback(async (nextToken = token) => {
    if (!nextToken) return;
    const res = await fetch(`${CONFIG.serverUrl}/api/messages`, {
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${nextToken}`,
        'X-Workspace-Id': CONFIG.workspaceId,
        'X-Channel': target,
        'X-Limit': '80',
      },
      cache: 'no-store',
    });
    const body = await parseJsonResponse(res);
    setMessages((body.messages || []).map(normalizeMessage).filter((message) => message && !isSystemMessage(message)));
  }, [target, token]);

  const connect = useCallback(async () => {
    if (!browserId || status === 'connecting') return;
    setStatus('connecting');
    setError('');
    try {
      const res = await fetch(`${CONFIG.serverUrl}/api/auth/embed-guest-session`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          workspaceId: CONFIG.workspaceId,
          channel: CONFIG.channel,
          name: CONFIG.guestName,
          browserId,
          picture: currentGuestPictureUrl(),
        }),
      });
      const body = await parseJsonResponse(res);
      setToken(body.token);
      setUserName(body.user?.name || CONFIG.guestName);
      await loadHistory(body.token);
      setStatus('connected');
    } catch (err) {
      setStatus('error');
      setError(err instanceof Error ? err.message : 'Unable to connect');
    }
  }, [browserId, loadHistory, status]);

  useEffect(() => {
    if (!panelVisible || token || status === 'connecting' || status === 'error') return undefined;
    const timer = window.setTimeout(() => connect(), 0);
    return () => window.clearTimeout(timer);
  }, [connect, panelVisible, status, token]);

  useEffect(() => {
    if (!token) return undefined;
    const ws = new WebSocket(wsUrlFor(CONFIG.serverUrl, token, CONFIG.workspaceId));
    wsRef.current = ws;
    ws.onopen = () => setStatus('connected');
    ws.onclose = () => setStatus((prev) => (prev === 'error' ? prev : 'closed'));
    ws.onerror = () => setStatus('error');
    ws.onmessage = (event) => {
      try {
        const packet = JSON.parse(event.data);
        if (packet.type === 'ping') return;
        if (packet.type === 'init') {
          setAgents(mergeAgents([], packet.agents || []));
          return;
        }
        if (packet.type === 'agent_started' && packet.agent) {
          setAgents((prev) => mergeAgents(prev, packet.agent));
          return;
        }
        if (packet.type === 'agent_status') {
          setAgents((prev) => updateAgentStatus(prev, packet));
          return;
        }
        if (packet.type === 'agent_activity') {
          setAgents((prev) => updateAgentActivity(prev, packet));
          return;
        }
        if ((packet.type === 'message' || packet.type === 'new_message') && packet.message) {
          const next = normalizeMessage(packet.message);
          if (next?.channelType === 'thread' && next.parentChannelName === CONFIG.channel) {
            dismissThinkingMessage();
            setMessages((prev) => mergeThreadReply(prev, next));
            setThreadStates((prev) => mergeThreadStateReply(prev, next));
            return;
          }
          if (next?.channelName === CONFIG.channel) {
            dismissThinkingMessage();
            setMessages((prev) => mergeMessage(prev, next));
          }
        }
      } catch {
        // Ignore non-JSON websocket frames.
      }
    };
    return () => {
      ws.close();
      if (wsRef.current === ws) wsRef.current = null;
    };
  }, [dismissThinkingMessage, token]);

  useEffect(() => {
    thinkingMessageKeyRef.current = thinkingMessageKey;
  }, [thinkingMessageKey]);

  useEffect(() => {
    if (!thinkingAgent && dismissedThinkingKey) setDismissedThinkingKey('');
  }, [dismissedThinkingKey, thinkingAgent]);

  useEffect(() => {
    if (!browserAvailable()) return undefined;
    setHeaderSlot(document.getElementById('zouk-reader-header-slot'));
    return undefined;
  }, []);

  useEffect(() => {
    if (!browserAvailable()) return undefined;
    const media = window.matchMedia('(min-width: 900px)');
    const update = () => setIsDesktop(media.matches);
    update();
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);

  useEffect(() => {
    if (!browserAvailable()) return undefined;
    const root = document.documentElement;
    root.classList.toggle('zouk-reader-open-desktop', open && !closing && isDesktop);
    return () => root.classList.remove('zouk-reader-open-desktop');
  }, [closing, isDesktop, open]);

  useEffect(() => {
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [panelVisible, showThinkingMessage, visibleMessages.length]);

  useEffect(() => {
    const node = textareaRef.current;
    if (!node) return;
    node.style.height = '0px';
    node.style.height = `${Math.min(Math.max(node.scrollHeight, 44), 132)}px`;
  }, [composer, panelVisible]);

  useEffect(() => {
    if (!browserAvailable() || !panelVisible || isDesktop) return undefined;
    const root = document.documentElement;
    let raf = 0;
    const layoutHeight = Math.max(
      document.documentElement.clientHeight || 0,
      window.innerHeight || 0,
      window.visualViewport?.height || 0,
    );
    sheetHeightRef.current = layoutHeight * 0.5;
    root.style.setProperty('--zouk-blog-sheet-height', `${Math.round(sheetHeightRef.current)}px`);

    const sync = () => {
      raf = 0;
      const viewport = window.visualViewport;
      root.style.setProperty('--zouk-blog-vv-top', `${Math.round(viewport?.offsetTop || 0)}px`);
      root.style.setProperty('--zouk-blog-vv-height', `${Math.round(viewport?.height || window.innerHeight)}px`);
    };
    const schedule = () => {
      if (!raf) raf = requestAnimationFrame(sync);
    };
    sync();
    window.addEventListener('resize', schedule, { passive: true });
    window.visualViewport?.addEventListener('resize', schedule, { passive: true });
    window.visualViewport?.addEventListener('scroll', schedule, { passive: true });
    return () => {
      window.removeEventListener('resize', schedule);
      window.visualViewport?.removeEventListener('resize', schedule);
      window.visualViewport?.removeEventListener('scroll', schedule);
      if (raf) cancelAnimationFrame(raf);
      root.style.removeProperty('--zouk-blog-sheet-height');
      root.style.removeProperty('--zouk-blog-vv-top');
      root.style.removeProperty('--zouk-blog-vv-height');
    };
  }, [isDesktop, panelVisible]);

  useEffect(() => {
    if (!browserAvailable()) return undefined;
    const updateSelectionAction = () => {
      const selection = window.getSelection?.();
      const text = currentPostSelectionText();
      if (!text || !selection?.rangeCount) {
        setSelectionAction(null);
        return;
      }
      if (panelVisible) setSelectedText(text);
      if (!isDesktop) {
        setSelectionAction(null);
        return;
      }
      const rect = selection.getRangeAt(0).getBoundingClientRect();
      if (!rect || (!rect.width && !rect.height)) {
        setSelectionAction(null);
        return;
      }
      setSelectionAction({
        text,
        top: Math.max(74, rect.top - 10),
        left: Math.min(window.innerWidth - 86, Math.max(86, rect.left + rect.width / 2)),
      });
    };
    const schedule = () => window.setTimeout(updateSelectionAction, 0);
    document.addEventListener('mouseup', schedule);
    document.addEventListener('keyup', schedule);
    document.addEventListener('selectionchange', schedule);
    document.addEventListener('touchend', schedule, { passive: true });
    return () => {
      document.removeEventListener('mouseup', schedule);
      document.removeEventListener('keyup', schedule);
      document.removeEventListener('selectionchange', schedule);
      document.removeEventListener('touchend', schedule);
    };
  }, [isDesktop, panelVisible, route?.name, route?.slug]);

  useEffect(() => {
    setSelectedText('');
    setSelectionAction(null);
    setSourceUrl(currentSourceUrl());
  }, [route?.name, route?.slug]);

  useEffect(() => () => {
    if (closeTimerRef.current) window.clearTimeout(closeTimerRef.current);
  }, []);

  const startDrag = useCallback((event) => {
    if (isDesktop || event.button > 0) return;
    dragRef.current = {
      pointerId: event.pointerId,
      startY: event.clientY,
      lastY: event.clientY,
      lastTime: performance.now(),
      velocity: 0,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
    setDragging(true);
    setDragY(0);
  }, [isDesktop]);

  const moveDrag = useCallback((event) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const now = performance.now();
    const delta = Math.max(0, event.clientY - drag.startY);
    const dt = Math.max(1, now - drag.lastTime);
    drag.velocity = (event.clientY - drag.lastY) / dt;
    drag.lastY = event.clientY;
    drag.lastTime = now;
    setDragY(delta);
  }, []);

  const endDrag = useCallback((event) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const delta = Math.max(0, event.clientY - drag.startY);
    const shouldClose = delta > 92 || (delta > 42 && drag.velocity > 0.7);
    dragRef.current = null;
    setDragging(false);
    if (shouldClose) closeChat();
    else setDragY(0);
  }, [closeChat]);

  const sendMessage = useCallback(async () => {
    const trimmed = composer.trim();
    if (!trimmed || !token || status === 'sending') return;
    const nextSourceUrl = rememberSource();
    const nextReferencedText = compactText(selectedText);
    const nextIncludeUrl = Boolean(nextSourceUrl && (nextSourceUrl !== lastContextUrl || nextReferencedText));
    const nextShouldInject = nextIncludeUrl || Boolean(nextReferencedText);
    const content = messageWithInjectedContext(
      trimmed,
      nextSourceUrl,
      nextReferencedText,
      nextIncludeUrl,
      nextShouldInject,
    );
    setStatus('sending');
    setError('');
    try {
      const res = await fetch(`${CONFIG.serverUrl}/api/messages`, {
        method: 'POST',
        headers: authHeaders,
        body: JSON.stringify({ target, content }),
      });
      const body = await parseJsonResponse(res);
      dismissThinkingMessage();
      setMessages((prev) => mergeMessage(prev, normalizeMessage(body.message)));
      if (nextIncludeUrl) setLastContextUrl(nextSourceUrl);
      setSelectedText('');
      setComposer('');
      setStatus('connected');
    } catch (err) {
      setStatus('error');
      setError(err instanceof Error ? err.message : 'Send failed');
    }
  }, [authHeaders, composer, dismissThinkingMessage, lastContextUrl, rememberSource, selectedText, status, target, token]);

  const loadThreadMessages = useCallback(async (parentMessage) => {
    const parentId = parentMessage?.id;
    if (!parentId) return;
    if (!token) {
      setThreadStates((prev) => ({
        ...prev,
        [parentId]: { ...(prev[parentId] || {}), open: true, loading: false, error: 'Connect before loading thread.' },
      }));
      return;
    }
    setThreadStates((prev) => ({
      ...prev,
      [parentId]: { ...(prev[parentId] || {}), open: true, loading: true, error: '' },
    }));
    try {
      const res = await fetch(`${CONFIG.serverUrl}/api/messages`, {
        headers: {
          ...authHeaders,
          'X-Channel': threadTargetForMessage(parentMessage),
          'X-Limit': '100',
        },
        cache: 'no-store',
      });
      const body = await parseJsonResponse(res);
      const threadMessages = (body.messages || []).map(normalizeMessage).filter(Boolean);
      setThreadStates((prev) => ({
        ...prev,
        [parentId]: { ...(prev[parentId] || {}), open: true, loading: false, error: '', messages: threadMessages },
      }));
    } catch (err) {
      setThreadStates((prev) => ({
        ...prev,
        [parentId]: {
          ...(prev[parentId] || {}),
          open: true,
          loading: false,
          error: err instanceof Error ? err.message : 'Failed to load thread.',
        },
      }));
    }
  }, [authHeaders, token]);

  const toggleThread = useCallback((parentMessage) => {
    const parentId = parentMessage?.id;
    if (!parentId) return;
    const current = threadStates[parentId];
    if (current?.open) {
      setThreadStates((prev) => ({
        ...prev,
        [parentId]: { ...prev[parentId], open: false },
      }));
      return;
    }
    setThreadStates((prev) => ({
      ...prev,
      [parentId]: { ...(prev[parentId] || {}), open: true, loading: !prev[parentId]?.messages, error: '' },
    }));
    if (!current?.messages) loadThreadMessages(parentMessage);
  }, [loadThreadMessages, threadStates]);

  const onSubmit = (event) => {
    event.preventDefault();
    sendMessage();
  };

  const launcher = (
    <button
      type="button"
      className={`zouk-reader-launcher${panelVisible ? ' is-active' : ''}${launcherStatus ? ` has-live is-${launcherStatus}` : ''}`}
      aria-label={panelVisible ? 'Close blog chat' : 'Open blog chat'}
      aria-pressed={panelVisible}
      onClick={toggleChat}
    >
      <MessagesIcon />
    </button>
  );

  return (
    <>
      {selectionAction && !open ? (
        <button
          type="button"
          className="zouk-selection-action"
          aria-label="Chat about selected text"
          style={{ top: selectionAction.top, left: selectionAction.left }}
          onClick={() => openChat(selectionAction.text)}
        >
          <MessageIcon />
        </button>
      ) : null}

      {headerSlot ? createPortal(launcher, headerSlot) : null}

      {panelVisible ? (
        <aside
          className={`zouk-reader-panel${closing ? ' is-closing' : ''}${dragging ? ' is-dragging' : ''}`}
          style={!isDesktop ? { '--zouk-blog-drag': `${dragY}px` } : undefined}
          aria-label="Zouk blog chat"
        >
          <button
            type="button"
            className="zouk-reader-edge-toggle"
            aria-label="Close chat"
            onClick={closeChat}
          />

          <div
            className="zouk-reader-drag"
            onPointerDown={startDrag}
            onPointerMove={moveDrag}
            onPointerUp={endDrag}
            onPointerCancel={endDrag}
          >
            <div className="zouk-reader-handle" />
          </div>

          <LiveAgents agents={liveAgents} />

          <div className="zouk-reader-messages" ref={scrollRef}>
            {status === 'connecting' && !visibleMessages.length ? (
              <div className="zouk-reader-empty">Connecting to Zouk...</div>
            ) : null}
            {status === 'error' && !visibleMessages.length ? (
              <div className="zouk-reader-empty">
                <span>Zouk connection unavailable.</span>
                <button type="button" onClick={connect}>Retry</button>
              </div>
            ) : null}
            {visibleMessages.map((message) => {
              const mine = message.senderName === userName;
              const messageAgent = !mine ? agentsBySender.get(senderKey(message.senderName)) : null;
              const avatarKind = messageAgent || message.senderType === 'agent' ? 'agent' : 'human';
              return (
                <article key={message.id} className={`zouk-reader-message${mine ? ' is-mine' : ''}`}>
                  {!mine ? (
                    <div className="zouk-reader-message-profile">
                      <Avatar
                        name={message.senderName}
                        src={message.avatarUrl || messageAgent?.avatarUrl}
                        status={messageAgent ? agentDotStatus(messageAgent) : ''}
                        kind={avatarKind}
                      />
                      <div className="zouk-reader-bubble-column">
                        <div className="zouk-reader-sender">{message.senderName}</div>
                        <div className="zouk-reader-bubble">
                          <MessageBody content={message.content} />
                        </div>
                        <ThreadBlock
                          message={message}
                          state={threadStates[message.id]}
                          agentsBySender={agentsBySender}
                          onToggle={toggleThread}
                        />
                      </div>
                    </div>
                  ) : (
                    <>
                      <div className="zouk-reader-bubble">
                        <MessageBody content={message.content} />
                      </div>
                      <ThreadBlock
                        message={message}
                        state={threadStates[message.id]}
                        agentsBySender={agentsBySender}
                        onToggle={toggleThread}
                      />
                    </>
                  )}
                </article>
              );
            })}
            {showThinkingMessage ? <ThinkingMessage agent={thinkingAgent} /> : null}
          </div>

          <form className="zouk-reader-composer" onSubmit={onSubmit}>
            {shouldInjectContext ? (
              <ContextPreview sourceUrl={sourceUrl} referencedText={referencedText} includeUrl={includeContextUrl} />
            ) : null}
            <div className="zouk-reader-input-shell">
              <textarea
                ref={textareaRef}
                value={composer}
                rows={1}
                enterKeyHint="send"
                placeholder={`Message #${CONFIG.channel}`}
                onChange={(event) => setComposer(event.target.value)}
                onKeyDown={(event) => {
                  if (shouldSubmitOnEnter(event)) {
                    event.preventDefault();
                    sendMessage();
                  }
                }}
              />
            </div>
            {error && visibleMessages.length ? (
              <div className="zouk-reader-error">
                <span>{error}</span>
                <button type="button" onClick={connect}>Retry</button>
              </div>
            ) : null}
          </form>
        </aside>
      ) : null}
    </>
  );
}
