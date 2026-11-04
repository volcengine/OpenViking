// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

/* Agent Activity —— 按回合分组的上下文流 + 一条压缩时间带。
   数据：/api/activity（daemon activities 表）。首屏取最近一页再向前补齐，
   之后每秒用 after=<最大 id> 增量拉取，新行即时并入。 */
'use strict';

const INSTANCES_API = '/api/instances';
const ACTIVITY_API = '/api/activity';
const PAGE_LIMIT = 100;
const POLL_MS = 1000;
const INITIAL_ROWS = 400;
const MAX_ROWS = 5000;
const GAP_MS = 120_000;       // 超过 2 分钟的空白在时间带上折叠
const PREVIEW_CHARS = 160;
const HIDDEN_KINDS = new Set(['message.duplicate', 'agent.tool_waiting']);
const TURN_STEP_KINDS = new Set([
  'agent.tool', 'agent.tool_called', 'agent.tool_completed', 'agent.note', 'agent.thinking', 'agent.turn_completed',
]);
const INTERNAL_MARKERS = new Set(['DELIVERED', 'NO_DELIVERY']);
const LARK_DELIVERY = /(reply|send|react_to|retract)_lark_message$/;

const $ = (id) => document.getElementById(id);
const dom = {
  instance: $('instance-select'),
  session: $('session-select'),
  sessionPrev: $('session-prev'),
  sessionNext: $('session-next'),
  live: $('live'),
  liveText: $('live-text'),
  stats: $('stats'),
  system: $('system'),
  privacy: $('privacy-toggle'),
  track: $('strip-track'),
  axis: $('strip-axis'),
  feed: $('feed'),
  entries: $('entries'),
  empty: $('empty'),
  loadOlder: $('load-older'),
  followLive: $('follow-live'),
  notice: $('notice'),
};

const state = {
  instance: null,
  epoch: 'current',      // 'current' | number | null(全部)
  currentEpoch: null,
  sessions: [],
  rows: new Map(),       // id -> activity
  nextCursor: null,
  service: null,
  overview: null,
  follow: true,
  polling: false,
  loaded: false,
  generation: 0,         // 切换实例/会话时作废在途请求
  model: null,
  openKeys: new Set(),   // 展开过的 <details>，重绘后保持
  cache: new Map(),      // key -> { sig, el }
};

// ---------- 格式化 ----------
const pad = (n) => String(n).padStart(2, '0');
function fmtTime(ms) {
  const d = new Date(ms);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
function fmtDay(ms) {
  const d = new Date(ms);
  return `${d.getMonth() + 1}月${d.getDate()}日 ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function fmtDur(ms) {
  if (!Number.isFinite(ms) || ms < 0) return '';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)}m${pad(Math.round((ms % 60_000) / 1000))}s`;
  if (ms < 86_400_000) return `${(ms / 3_600_000).toFixed(1)}h`;
  return `${(ms / 86_400_000).toFixed(1)}d`;
}
function fmtTokens(n) {
  if (!Number.isFinite(n)) return '';
  return n >= 1000 ? `${(n / 1000).toFixed(n >= 100_000 ? 0 : 1)}k` : String(n);
}
function oneLine(text, limit = PREVIEW_CHARS) {
  const flat = String(text ?? '').replace(/\s+/g, ' ').trim();
  return flat.length > limit ? `${flat.slice(0, limit)}…` : flat;
}
function viewingHistorical() {
  return typeof state.epoch === 'number' && state.epoch !== state.currentEpoch;
}

// ---------- DOM 小工具（只用安全 API） ----------
function h(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text !== undefined && text !== null && text !== '') el.textContent = String(text);
  return el;
}
function since(el, startMs) {
  el.dataset.since = String(startMs);
  el.textContent = fmtDur(Date.now() - startMs);
  return el;
}
function block(key, label, text, { open = false, cls = '' } = {}) {
  const details = h('details', `blk ${cls}`.trim());
  details.dataset.key = key;
  details.open = open || state.openKeys.has(key);
  const summary = h('summary');
  summary.append(h('span', 'blk-label', label), h('span', 'blk-preview txt', oneLine(text)));
  details.append(summary, h('pre', 'txt', text));
  return details;
}

// ---------- activity 行 → 回合模型 ----------
function parseJson(text) {
  if (typeof text !== 'string' || !text.startsWith('{')) return null;
  try { return JSON.parse(text); } catch { return null; }
}

function inputPreview(input) {
  const parsed = parseJson(input);
  if (!parsed) return oneLine(input);
  const primary = parsed.command ?? parsed.cmd ?? parsed.query ?? parsed.uri ?? parsed.text ?? parsed.markdown;
  if (primary !== undefined) return oneLine(Array.isArray(primary) ? primary.join(' ') : primary);
  return oneLine(Object.entries(parsed)
    .filter(([, value]) => typeof value !== 'object')
    .map(([key, value]) => `${key}=${value}`).join(' '));
}

// 兼容旧版 MCP 投递与方舟 Worker 的 lark-cli；Core 投递以 outbox 结算为准。
const CLI_DELIVERY = /lark-cli\S*\s+im\s+(?:\+messages-(reply|send)|reactions\s+create|messages\s+delete)\b/;

function shellArg(command, flag) {
  const match = command.match(new RegExp(`--${flag}(?:=|\\s+)(?:"((?:[^"\\\\]|\\\\.)*)"|'([^']*)'|(\\S+))`));
  if (!match) return '';
  if (match[2] !== undefined) return match[2];
  return (match[1] ?? match[3]).replace(/\\(["\\$`])/g, '$1').replace(/\\n/g, '\n');
}

function deliveryOf(tool) {
  if (tool.delivery) return tool.delivery;
  const match = String(tool.name || '').match(LARK_DELIVERY);
  const args = parseJson(tool.input) || {};
  if (match) {
    const text = args.text ?? args.markdown ?? args.emoji_type ?? (tool.summary !== tool.label ? tool.summary : '');
    const mode = { reply: '回复', send: '发送', react_to: '表态', retract: '撤回' }[match[1]];
    return { mode: args.reply_in_thread ? '话题回复' : mode, text: text || '' };
  }
  const command = typeof args.command === 'string' ? args.command : typeof tool.input === 'string' ? tool.input : '';
  const cli = command.match(CLI_DELIVERY);
  if (!cli || /--dry-run\b/.test(command)) return null;
  if (!cli[1]) return { mode: /reactions/.test(cli[0]) ? '表态' : '撤回', text: command.match(/emoji_type[^A-Za-z]*([A-Za-z_]+)/)?.[1] || '' };
  const text = shellArg(command, 'markdown') || shellArg(command, 'text') || shellArg(command, 'content');
  const mode = cli[1] === 'send' ? '发送' : /--reply-in-thread\b/.test(command) ? '话题回复' : '回复';
  return { mode, text };
}

function newTool(row) {
  const meta = row.metadata || {};
  return {
    type: 'tool',
    key: `tool:${meta.toolId ?? row.id}`,
    lastId: row.id,
    name: meta.toolName || meta.toolLabel || row.summary || '工具',
    label: meta.toolLabel || meta.toolName || row.summary || '工具',
    server: meta.toolServer,
    summary: row.summary,
    input: meta.input,
    output: meta.output,
    error: meta.error,
    startMs: row.createdAtMs,
    durationMs: meta.durationMs,
    running: row.kind === 'agent.tool_called',
    failed: row.status === 'FAILED',
    cwd: meta.cwd,
    exitCode: meta.exitCode,
  };
}

function completeTool(tool, row) {
  const meta = row.metadata || {};
  tool.lastId = row.id;
  tool.running = false;
  tool.failed = row.status === 'FAILED';
  tool.output = meta.output ?? tool.output;
  tool.error = meta.error ?? tool.error;
  tool.input = tool.input ?? meta.input;
  tool.durationMs = meta.durationMs ?? (row.createdAtMs - tool.startMs);
  tool.exitCode = meta.exitCode ?? tool.exitCode;
  // codex 的 agent.tool 把飞书回复正文放在 summary；其他 runtime 的完成行只带工具名
  if (row.kind === 'agent.tool' && row.summary && row.summary !== tool.label) tool.summary = row.summary;
}

function buildModel(rows) {
  const items = [];               // 顶层：回合 + 回合外的单行
  const system = [];
  const turns = [];
  const bySeq = new Map();        // inboundSeq -> 尚未归入回合的消息项
  const turnBySeq = new Map();    // inboundSeq -> 处理它的回合
  let open = null;
  let tools = new Map();

  const claim = (turn, seqs) => {
    for (const seq of seqs || []) {
      const message = bySeq.get(seq);
      if (!message || message.claimed) continue;
      message.claimed = true;
      turn.messages.push(message);
      turnBySeq.set(seq, turn);
    }
  };

  for (const row of rows) {
    if (HIDDEN_KINDS.has(row.kind)) continue;
    const meta = row.metadata || {};
    const kind = row.kind;
    // 守护进程生命周期和对话无关，进侧栏
    if (kind.startsWith('system.')) { system.push(row); continue; }

    if (kind.startsWith('message.')) {
      const message = {
        type: 'msg', key: `r:${row.id}`, lastId: row.id, row,
        ignored: kind !== 'message.received',
      };
      if (row.inboundSeq != null && !message.ignored) bySeq.set(row.inboundSeq, message);
      items.push(message);
      continue;
    }

    if (kind === 'agent.turn_started') {
      open = {
        type: 'turn', key: `t:${row.id}`, startRow: row, endRow: null, lastId: row.id,
        startMs: row.createdAtMs, endMs: null, messages: [], steps: [],
        meta: { ...meta },
      };
      tools = new Map();
      claim(open, meta.inboundSeqs);
      if (meta.input) open.steps.push({ type: 'input', key: `in:${row.id}`, row, text: meta.input, historyChars: meta.historyChars });
      turns.push(open);
      items.push(open);
      continue;
    }

    // 回合结束后才记的「本轮未表态」挂回处理那条消息的回合
    if (kind === 'agent.decision_missing' && !open && turnBySeq.has(row.inboundSeq)) {
      const turn = turnBySeq.get(row.inboundSeq);
      turn.steps.push({ type: 'event', key: `e:${row.id}`, row });
      turn.lastId = Math.max(turn.lastId, row.id);
      continue;
    }

    const agentRow = kind.startsWith('agent.') || kind.startsWith('delivery.');
    if (!open && agentRow && TURN_STEP_KINDS.has(kind)) {
      // 回合开头不在已加载范围内（或旧版本没记 turn_started）：补一个隐式回合
      open = {
        type: 'turn', key: `t:${row.id}`, startRow: null, endRow: null, lastId: row.id,
        startMs: row.createdAtMs, endMs: null, messages: [], steps: [], meta: { ...meta }, implicit: true,
      };
      tools = new Map();
      turns.push(open);
      items.push(open);
    }
    if (!open || !agentRow) {
      if (kind === 'agent.thinking' && !meta.thinking) continue;
      items.push({ type: 'row', key: `r:${row.id}`, lastId: row.id, row });
      continue;
    }

    open.lastId = row.id;
    if (kind === 'agent.turn_completed') {
      open.endRow = row;
      open.endMs = row.createdAtMs;
      Object.assign(open.meta, meta);
      open = null;
      continue;
    }
    if (kind === 'agent.steered') {
      claim(open, meta.inboundSeqs ?? [row.inboundSeq]);
      open.steps.push({ type: 'input', key: `in:${row.id}`, row, text: meta.input || row.summary, steered: true });
      continue;
    }
    if (kind === 'delivery.sent') {
      open.steps.push({
        ...newTool(row),
        key: `delivery:${row.id}`,
        delivery: {
          mode: ({ 'message.send': '发送', 'message.reply': '回复', 'message.react': '表态',
            'message.retract': '撤回', 'message.reaction.delete': '移除表态' })[meta.action] || '投递',
          text: row.summary || '',
        },
      });
      continue;
    }
    if (kind === 'delivery.silent') {
      // 模型明确选择不回复：记在输出区，但不算发出消息
      open.steps.push({
        ...newTool(row),
        key: `delivery:${row.id}`,
        delivery: { mode: '不回复', text: row.summary || '', silent: true },
      });
      continue;
    }
    if (kind === 'agent.tool_called' || kind === 'agent.tool_completed' || kind === 'agent.tool') {
      const toolId = meta.toolId;
      const existing = toolId != null ? tools.get(toolId) : null;
      if (existing && kind !== 'agent.tool_called') {
        completeTool(existing, row);
      } else {
        const tool = newTool(row);
        if (kind !== 'agent.tool_called') completeTool(tool, row);
        if (toolId != null) tools.set(toolId, tool);
        open.steps.push(tool);
      }
      continue;
    }
    if (kind === 'agent.thinking') {
      if (meta.thinking) open.steps.push({ type: 'think', key: `th:${row.id}`, row, text: meta.thinking });
      continue;
    }
    if (kind === 'agent.note') {
      // 兼容旧版 runtime 的内部完成标记。
      if (INTERNAL_MARKERS.has(String(row.summary || '').trim())) continue;
      open.steps.push({ type: 'note', key: `n:${row.id}`, row, text: row.summary });
      continue;
    }
    open.steps.push({ type: 'event', key: `e:${row.id}`, row });
  }

  for (const turn of turns) turn.steps = foldBlankTools(turn.steps);

  // 已归入回合的消息不再单独占一行；回合进行中到达、尚未 steer 的消息保持在原位。
  const visible = items.filter((item) => !(item.type === 'msg' && item.claimed));
  for (const turn of turns) {
    turn.messages.sort((a, b) => a.row.id - b.row.id);
    turn.lastId = Math.max(turn.lastId, ...turn.messages.map((message) => message.lastId));
  }
  return { items: visible, turns, system };
}

// 旧版本只记了工具名：连续的空工具行合成一行，避免满屏 "bash"
function foldBlankTools(steps) {
  const out = [];
  for (const step of steps) {
    const blank = step.type === 'tool' && !deliveryOf(step) && !step.running && !step.input && !step.output && !step.error;
    const prev = out.at(-1);
    if (blank && prev?.type === 'blank' && prev.name === step.label) {
      prev.count += 1;
      prev.durationMs += step.durationMs || 0;
      prev.failed += step.failed ? 1 : 0;
      continue;
    }
    if (blank) {
      out.push({
        type: 'blank', key: step.key, name: step.label, count: 1, startMs: step.startMs,
        durationMs: step.durationMs || 0, failed: step.failed ? 1 : 0,
      });
      continue;
    }
    out.push(step);
  }
  return out;
}

// ---------- 渲染：上下文流 ----------
function tag(text, cls) {
  return h('span', `tag ${cls}`, text);
}

function renderMessage(item) {
  const row = item.row;
  const meta = row.metadata || {};
  const el = h('div', `line msg${item.ignored ? ' muted' : ''}`);
  el.dataset.id = String(row.id);
  const head = h('div', 'line-head');
  head.append(
    h('time', 'ts', fmtTime(row.createdAtMs)),
    tag(item.ignored ? row.title : '收', item.ignored ? 'tag-muted' : 'tag-in'),
    h('span', 'src', row.sourceName || (row.sourceType === 'p2p' ? '私聊' : row.sourceType === 'group' ? '群聊' : '')),
  );
  if (meta.messageType && meta.messageType !== 'text') head.append(h('span', 'aux', meta.messageType));
  if (Number.isFinite(meta.latencyMs) && meta.latencyMs > 3000) head.append(h('span', 'aux warn', `延迟 ${fmtDur(meta.latencyMs)}`));
  el.append(head, h('div', 'msg-body txt', row.summary));
  return el;
}

function renderTool(tool) {
  const delivery = deliveryOf(tool);
  const el = h('div', `step tool${tool.failed ? ' failed' : ''}${tool.running ? ' running' : ''}${delivery ? ' delivery' : ''}`);
  const head = h('div', 'step-head');
  head.append(h('time', 'ts', fmtTime(tool.startMs)));
  if (delivery) {
    head.append(tag(delivery.mode, delivery.silent ? 'tag-muted' : 'tag-out'));
    el.append(head, h('div', 'reply-body txt', delivery.text || tool.summary || ''));
  } else {
    const description = parseJson(tool.input)?.description;
    head.append(tag('工具', 'tag-tool'), h('span', 'tool-name', tool.label));
    if (description) head.append(h('span', 'tool-desc', oneLine(description, 80)));
    head.append(h('span', 'tool-preview txt', inputPreview(tool.input)));
    el.append(head);
  }
  const status = h('span', 'tool-status');
  if (tool.running) status.append(h('span', 'spin'), since(h('span', 'elapsed'), tool.startMs));
  else {
    if (Number.isFinite(tool.exitCode) && tool.exitCode !== 0) status.append(h('span', 'aux bad', `exit ${tool.exitCode}`));
    status.append(h('span', tool.failed ? 'aux bad' : 'aux', tool.failed ? `失败 ${fmtDur(tool.durationMs)}` : fmtDur(tool.durationMs)));
  }
  head.append(status);
  const body = h('div', 'step-body');
  if (tool.input) body.append(block(`${tool.key}:in`, '输入', tool.input));
  if (tool.error) body.append(block(`${tool.key}:err`, '错误', tool.error, { open: true, cls: 'bad' }));
  if (tool.output) body.append(block(`${tool.key}:out`, '输出', tool.output));
  if (body.childNodes.length) el.append(body);
  return el;
}

function renderStep(step) {
  if (step.type === 'tool') return renderTool(step);
  if (step.type === 'blank') {
    const el = h('div', 'step tool blank');
    const head = h('div', 'step-head');
    head.append(
      h('time', 'ts', fmtTime(step.startMs)),
      tag('工具', 'tag-tool'),
      h('span', 'tool-name', step.count > 1 ? `${step.name} ×${step.count}` : step.name),
      h('span', 'tool-preview', '旧数据未记录参数和输出'),
    );
    const status = h('span', 'tool-status');
    if (step.failed) status.append(h('span', 'aux bad', `${step.failed} 次失败`));
    status.append(h('span', 'aux', `共 ${fmtDur(step.durationMs)}`));
    head.append(status);
    el.append(head);
    return el;
  }
  const row = step.row;
  const el = h('div', `step ${step.type}`);
  const head = h('div', 'step-head');
  head.append(h('time', 'ts', fmtTime(row.createdAtMs)));
  if (step.type === 'input') {
    head.append(tag(step.steered ? '追加' : '输入', 'tag-model'));
    if (step.historyChars > 0) head.append(h('span', 'aux', `+ 历史前缀 ${fmtTokens(step.historyChars)} 字`));
    el.append(head, block(step.key, '模型实际收到', step.text));
    return el;
  }
  if (step.type === 'think') {
    head.append(tag('思考', 'tag-think'));
    el.append(head, block(step.key, '', step.text, { cls: 'think-blk' }));
    return el;
  }
  if (step.type === 'note') {
    head.append(tag('模型文本', 'tag-note'), h('span', 'aux', '未发送给用户'));
    el.append(head, block(step.key, '', step.text));
    return el;
  }
  const bad = row.severity === 'error';
  const missing = row.kind === 'agent.decision_missing';
  const text = missing
    ? ({ NUDGED: '已提醒模型补一次决定', MISSING: '没有发出消息，也没有记录不回复' })[row.status] || ''
    : row.summary;
  head.append(tag(row.title, bad ? 'tag-bad' : missing ? 'tag-warn' : 'tag-muted'), h('span', 'step-text txt', oneLine(text, 400)));
  el.append(head);
  if (bad) el.classList.add('failed');
  return el;
}

function turnStatus(turn) {
  if (!turn.endRow) {
    // 隐式回合没有结束行且后面已有别的回合：只是数据不全，不是还在跑
    if (turn.implicit && state.model?.turns.at(-1) !== turn) return { text: '记录不全', cls: 'ok' };
    return { text: '进行中', cls: 'running' };
  }
  const status = turn.endRow.status || turn.meta.status;
  if (status === 'failed') return { text: '失败', cls: 'failed' };
  if (status === 'interrupted') return { text: '中断', cls: 'failed' };
  return { text: '完成', cls: 'ok' };
}

function renderTurn(turn, index) {
  const status = turnStatus(turn);
  const meta = turn.meta;
  const el = h('article', `turn ${status.cls}`);
  el.dataset.key = turn.key;
  const head = h('header', 'turn-head');
  head.append(
    h('span', 'turn-no', `#${index + 1}`),
    h('time', 'ts', fmtTime(turn.startMs)),
    h('span', `pill ${status.cls}`, status.text),
  );
  if (turn.implicit) head.append(h('span', 'aux', '未记录开始'));
  const dur = h('span', 'aux');
  if (turn.endRow) dur.textContent = fmtDur(meta.durationMs ?? (turn.endMs - turn.startMs));
  else since(dur, turn.startMs);
  head.append(dur);
  if (Number.isFinite(meta.inputTokens)) {
    const ctx = Number.isFinite(meta.contextWindow) ? ` / ${fmtTokens(meta.contextWindow)}` : '';
    const cached = Number.isFinite(meta.cachedInputTokens) ? ` (缓存 ${fmtTokens(meta.cachedInputTokens)})` : '';
    head.append(h('span', 'aux', `上下文 ${fmtTokens(meta.inputTokens)}${ctx}${cached} · 输出 ${fmtTokens(meta.outputTokens)}`));
  }
  if (Number.isFinite(meta.costUsd)) head.append(h('span', 'aux', `$${meta.costUsd.toFixed(4)}`));
  head.append(h('span', 'aux right', [meta.runtime, meta.model, meta.effort].filter(Boolean).join(' · ')));
  el.append(head);

  // 输入：turn start 时收到的消息 + 模型实际收到的原文
  const firstInput = turn.steps.find((step) => step.type === 'input' && !step.steered);
  const input = h('section', 'io io-in');
  input.append(h('div', 'io-label', '输入'));
  for (const message of turn.messages) input.append(renderMessage(message));
  if (firstInput) {
    const exact = block(firstInput.key, '模型实际收到', firstInput.text, { open: !turn.messages.length });
    if (firstInput.historyChars > 0) exact.querySelector('summary').append(h('span', 'aux', `+ 历史前缀 ${fmtTokens(firstInput.historyChars)} 字`));
    input.append(exact);
  }
  if (!turn.messages.length && !firstInput) input.append(h('div', 'io-empty', '未记录'));
  el.append(input);

  // 过程：飞书投递挪到输出区，其余按时间排
  const deliveries = turn.steps.filter((step) => step.type === 'tool' && deliveryOf(step));
  const steps = h('div', 'steps');
  for (const step of turn.steps) {
    if (step === firstInput || deliveries.includes(step)) continue;
    steps.append(renderStep(step));
  }
  if (steps.childNodes.length) el.append(steps);

  // 输出：turn end 时实际发出去的内容
  const output = h('section', `io io-out${status.cls === 'failed' ? ' failed' : ''}`);
  output.append(h('div', 'io-label', '输出'));
  for (const step of deliveries) output.append(renderTool(step));
  if (turn.endRow && status.cls === 'failed' && turn.endRow.summary) {
    output.append(h('div', 'turn-error txt', turn.endRow.summary));
  } else if (!deliveries.length) {
    output.append(h('div', 'io-empty', !turn.endRow ? '进行中…' : '没有发出消息（静默或仅模型文本）'));
  }
  el.append(output);
  return el;
}

function renderLooseRow(item) {
  const row = item.row;
  const bad = row.severity === 'error';
  const el = h('div', `line loose${bad ? ' failed' : ''}`);
  const head = h('div', 'line-head');
  head.append(
    h('time', 'ts', fmtTime(row.createdAtMs)),
    tag(row.title, bad ? 'tag-bad' : row.severity === 'warn' ? 'tag-warn' : 'tag-muted'),
    h('span', 'step-text txt', oneLine(row.summary, 300)),
  );
  el.append(head);
  return el;
}

function renderFeed(model) {
  const atBottom = state.follow;
  const desired = [];
  const turnIndex = new Map(model.turns.map((turn, index) => [turn, index]));
  for (const item of model.items) {
    const sig = item.type === 'turn' ? `${item.lastId}:${item.endRow ? 1 : 0}:${turnIndex.get(item)}:${item === model.turns.at(-1) ? 1 : 0}` : String(item.lastId);
    let cached = state.cache.get(item.key);
    if (!cached || cached.sig !== sig) {
      const el = item.type === 'turn' ? renderTurn(item, turnIndex.get(item))
        : item.type === 'msg' ? renderMessage(item)
          : renderLooseRow(item);
      el.dataset.key = item.key;
      cached = { sig, el };
      state.cache.set(item.key, cached);
    }
    desired.push(cached.el);
  }
  const keep = new Set(desired);
  for (const child of [...dom.entries.children]) {
    if (!keep.has(child)) { observer.unobserve(child); child.remove(); }
  }
  for (const [key, cached] of state.cache) if (!keep.has(cached.el)) state.cache.delete(key);
  let cursor = dom.entries.firstElementChild;
  for (const el of desired) {
    if (el === cursor) cursor = cursor.nextElementSibling;
    else dom.entries.insertBefore(el, cursor);
  }
  dom.empty.hidden = desired.length > 0;
  dom.empty.textContent = '这个会话还没有活动';
  observeTurns();
  if (atBottom) dom.feed.scrollTop = dom.feed.scrollHeight;
}

// ---------- 渲染：时间带 ----------
// 有活动的时段按真实比例，超过 GAP_MS 的空白压成固定小段，整条带始终全览已加载范围。
function buildTimeMap(spans) {
  const points = [...new Set(spans.flatMap(([a, b]) => [a, b]))].sort((a, b) => a - b);
  const segs = [];
  let active = 0;
  for (let i = 1; i < points.length; i++) {
    const real = points[i] - points[i - 1];
    if (real <= GAP_MS) active += real;
  }
  const cap = Math.max(active * 0.015, 1000);
  let disp = 0;
  for (let i = 1; i < points.length; i++) {
    const real = points[i] - points[i - 1];
    const width = real > GAP_MS ? cap : real;
    segs.push({ a: points[i - 1], b: points[i], d0: disp, w: width, gap: real > GAP_MS });
    disp += width;
  }
  const total = Math.max(disp, 1);
  const pos = (t) => {
    if (!segs.length) return 0;
    let lo = 0, hi = segs.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (segs[mid].b < t) lo = mid + 1; else hi = mid;
    }
    const seg = segs[lo];
    const f = seg.b === seg.a ? 0 : (Math.min(Math.max(t, seg.a), seg.b) - seg.a) / (seg.b - seg.a);
    return ((seg.d0 + f * seg.w) / total) * 100;
  };
  return { pos, gaps: segs.filter((seg) => seg.gap), first: points[0], last: points.at(-1) };
}

function renderStrip(model) {
  const now = Date.now();
  const spans = [];
  for (const turn of model.turns) spans.push([turn.startMs, turn.endMs ?? now]);
  for (const item of model.items) {
    if (item.type !== 'turn') spans.push([item.row.createdAtMs, item.row.createdAtMs]);
  }
  for (const turn of model.turns) for (const m of turn.messages) spans.push([m.row.createdAtMs, m.row.createdAtMs]);
  const fragment = document.createDocumentFragment();
  if (!spans.length) {
    dom.track.replaceChildren();
    dom.axis.replaceChildren();
    return;
  }
  const map = buildTimeMap(spans);
  // 轴：起点 + 每段折叠空白之后的时刻，太挤的跳过
  const axis = document.createDocumentFragment();
  let lastLabel = -100;
  let lastDay = '';
  const label = (t, idleMs = 0) => {
    const x = map.pos(t);
    if (x - lastLabel < 8) return;
    const day = new Date(t).toDateString();
    const when = day === lastDay ? fmtTime(t).slice(0, 5) : fmtDay(t);
    const el = h('span', 'axis-label', idleMs ? `${when}（前空 ${fmtDur(idleMs)}）` : when);
    el.style.left = `${x}%`;
    axis.append(el);
    lastLabel = x;
    lastDay = day;
  };
  label(map.first);
  for (const gap of map.gaps) {
    const el = h('span', 'gap');
    const left = map.pos(gap.a);
    el.style.left = `${left}%`;
    el.style.width = `${Math.max(map.pos(gap.b) - left, 0.2)}%`;
    el.title = `空闲 ${fmtDur(gap.b - gap.a)}（已折叠）`;
    fragment.append(el);
    label(gap.b, gap.b - gap.a);
  }
  model.turns.forEach((turn, index) => {
    const status = turnStatus(turn);
    const bar = h('button', `bar ${status.cls}`);
    bar.type = 'button';
    bar.dataset.key = turn.key;
    const left = map.pos(turn.startMs);
    bar.style.left = `${left}%`;
    bar.style.width = `${Math.max(map.pos(turn.endMs ?? now) - left, 0)}%`;
    const first = turn.messages[0]?.row.summary || '';
    bar.title = `#${index + 1} ${fmtTime(turn.startMs)} · ${status.text} · ${fmtDur((turn.endMs ?? now) - turn.startMs)}${first ? `\n${oneLine(first, 80)}` : ''}`;
    fragment.append(bar);
    for (const message of turn.messages) fragment.append(tick(map, message.row.createdAtMs, 'in'));
    for (const step of turn.steps) {
      const delivery = step.type === 'tool' ? deliveryOf(step) : null;
      if (delivery) fragment.append(tick(map, step.startMs, step.failed ? 'bad' : delivery.silent ? 'muted' : 'out'));
    }
  });
  for (const item of model.items) {
    if (item.type === 'msg') fragment.append(tick(map, item.row.createdAtMs, item.ignored ? 'muted' : 'in'));
    else if (item.type === 'row' && item.row.severity === 'error') fragment.append(tick(map, item.row.createdAtMs, 'bad'));
  }
  dom.track.replaceChildren(fragment);
  dom.axis.replaceChildren(axis);
  markInView();
}

function tick(map, t, cls) {
  const el = h('span', `tick ${cls}`);
  el.style.left = `${map.pos(t)}%`;
  return el;
}

dom.track.addEventListener('click', (event) => {
  const bar = event.target.closest('.bar');
  if (!bar) return;
  const card = dom.entries.querySelector(`[data-key="${CSS.escape(bar.dataset.key)}"]`);
  if (!card) return;
  card.scrollIntoView({ block: 'start', behavior: 'smooth' });
  card.classList.remove('flash');
  void card.offsetWidth;
  card.classList.add('flash');
});

// 时间带高亮当前在视口里的回合
const inView = new Set();
const observer = new IntersectionObserver((records) => {
  for (const record of records) {
    const key = record.target.dataset.key;
    if (record.isIntersecting) inView.add(key); else inView.delete(key);
  }
  markInView();
}, { root: dom.feed });
const observed = new WeakSet();
function observeTurns() {
  for (const el of dom.entries.querySelectorAll('.turn')) {
    if (!observed.has(el)) { observed.add(el); observer.observe(el); }
  }
}
function markInView() {
  for (const bar of dom.track.querySelectorAll('.bar')) bar.classList.toggle('inview', inView.has(bar.dataset.key));
}

// ---------- 状态栏 ----------
function renderLive() {
  const service = state.service;
  const lastTurn = state.model?.turns.at(-1);
  dom.live.className = 'live';
  if (!service) return;
  if (!service.ready) {
    dom.live.classList.add('bad');
    dom.liveText.textContent = '服务未就绪';
    return;
  }
  if (viewingHistorical()) {
    dom.live.classList.add('idle');
    dom.liveText.textContent = '历史会话';
    return;
  }
  if (lastTurn && !lastTurn.endRow) {
    const step = lastTurn.steps.at(-1);
    const what = step?.type === 'tool' && step.running ? `工具 ${step.label}` : '思考中';
    dom.live.classList.add('busy');
    dom.liveText.replaceChildren(`${what} · `, since(h('span'), step?.type === 'tool' && step.running ? step.startMs : lastTurn.startMs));
    return;
  }
  dom.live.classList.add('ok');
  dom.liveText.textContent = `空闲 · 运行 ${fmtDur((service.uptimeSeconds || 0) * 1000)}`;
}

function renderStats() {
  const o = state.overview;
  const service = state.service;
  const lastTurn = state.model?.turns.at(-1);
  const session = state.sessions.find((item) => item.current);
  const rows = [
    ['状态', dom.liveText.textContent, dom.live.classList.contains('bad') ? 'bad' : ''],
    ['运行时', [session?.runtime ?? lastTurn?.meta.runtime, lastTurn?.meta.model].filter(Boolean).join(' · ') || '—'],
    ['已运行', service ? fmtDur((service.uptimeSeconds || 0) * 1000) : '—'],
  ];
  if (o) {
    rows.push(['今日', `收 ${o.receivedToday} · 回 ${o.sentToday}`]);
    rows.push(['需关注', String(o.attention), o.attention ? 'bad' : '']);
  }
  dom.stats.replaceChildren(...rows.flatMap(([key, value, cls]) => [h('dt', '', key), h('dd', cls || '', value)]));
}

function renderSystem(model) {
  const recent = model.system.slice(-80).reverse();
  dom.system.replaceChildren(...recent.map((row) => {
    const li = h('li', row.severity === 'error' ? 'bad' : row.severity === 'warn' ? 'warn' : '');
    const head = h('div', 'sys-head');
    head.append(h('time', 'ts', `${fmtDay(row.createdAtMs).split(' ')[0]} ${fmtTime(row.createdAtMs)}`), h('span', 'sys-title', row.title));
    li.append(head);
    if (row.summary) li.append(h('div', 'sys-text', oneLine(row.summary, 300)));
    return li;
  }));
  if (!recent.length) dom.system.replaceChildren(h('li', 'muted', '暂无'));
}

function render() {
  const rows = [...state.rows.values()].sort((a, b) => a.id - b.id);
  state.model = buildModel(rows);
  renderFeed(state.model);
  renderStrip(state.model);
  renderLive();
  renderStats();
  renderSystem(state.model);
  dom.loadOlder.hidden = state.nextCursor == null;
}

// 每秒刷新进行中计时，不重建 DOM
setInterval(() => {
  const now = Date.now();
  for (const el of document.querySelectorAll('[data-since]')) el.textContent = fmtDur(now - Number(el.dataset.since));
}, 1000);

// ---------- 数据层 ----------
function epochFromUrl() {
  const value = new URL(window.location.href).searchParams.get('epoch');
  if (value === 'all') return null;
  if (value && /^[1-9]\d{0,8}$/.test(value)) return Number(value);
  return 'current';
}

function syncUrl() {
  const url = new URL(window.location.href);
  if (state.instance) url.searchParams.set('instance', state.instance);
  else url.searchParams.delete('instance');
  if (state.epoch === null) url.searchParams.set('epoch', 'all');
  else if (typeof state.epoch === 'number') url.searchParams.set('epoch', String(state.epoch));
  else url.searchParams.delete('epoch');
  history.replaceState(null, '', url);
}

function sessionLabel(session) {
  const d = new Date(session.startMs);
  const when = `${d.getMonth() + 1}月${d.getDate()}日`;
  if (session.current) return `当前 · ${session.runtime ? `${session.runtime} · ` : ''}${when}至今`;
  return `${when} · ${session.activityCount} 条`;
}

function renderSessions() {
  const selected = state.epoch === null
    ? 'all'
    : String(state.epoch === 'current' ? (state.currentEpoch ?? '') : state.epoch);
  const fragment = document.createDocumentFragment();
  for (const session of state.sessions) {
    const option = h('option', '', sessionLabel(session));
    option.value = String(session.epoch);
    option.title = `${fmtDay(session.startMs)} – ${session.endMs ? fmtDay(session.endMs) : '至今'} · ${session.activityCount} 条`;
    fragment.append(option);
  }
  const all = h('option', '', '全部会话');
  all.value = 'all';
  fragment.append(all);
  dom.session.replaceChildren(fragment);
  if ([...dom.session.options].some((option) => option.value === selected)) dom.session.value = selected;
  dom.session.disabled = false;
  const epochs = state.sessions.map((session) => session.epoch);
  const index = epochs.indexOf(Number(dom.session.value));
  dom.sessionPrev.disabled = state.epoch === null || index < 0 || index >= epochs.length - 1;
  dom.sessionNext.disabled = state.epoch === null || index <= 0;
}

function applyMeta(data) {
  state.service = data.service ?? state.service;
  state.overview = data.overview ?? state.overview;
  const previousCurrent = state.currentEpoch;
  state.sessions = Array.isArray(data.sessions) ? data.sessions : state.sessions;
  state.currentEpoch = state.sessions.find((session) => session.current)?.epoch ?? null;
  if (typeof state.epoch === 'number' && !state.sessions.some((session) => session.epoch === state.epoch)) {
    state.epoch = 'current';
  }
  renderSessions();
  // /new 或切换 runtime 产生新会话：跟随当前会话时整页切到新会话
  return state.epoch === 'current' && previousCurrent != null && previousCurrent !== state.currentEpoch;
}

function activityUrl(params) {
  const url = new URL(ACTIVITY_API, window.location.origin);
  url.searchParams.set('limit', String(PAGE_LIMIT));
  if (state.instance) url.searchParams.set('instance', state.instance);
  if (state.epoch === 'current') url.searchParams.set('epoch', 'current');
  else if (typeof state.epoch === 'number') url.searchParams.set('epoch', String(state.epoch));
  for (const [key, value] of Object.entries(params)) url.searchParams.set(key, String(value));
  return url;
}

async function fetchActivity(params = {}) {
  const response = await fetch(activityUrl(params), { headers: { accept: 'application/json' } });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

function mergeRows(list) {
  let added = 0;
  for (const row of list || []) {
    if (!state.rows.has(row.id)) { state.rows.set(row.id, row); added += 1; }
  }
  if (state.rows.size > MAX_ROWS) {
    const ids = [...state.rows.keys()].sort((a, b) => a - b);
    for (const id of ids.slice(0, ids.length - MAX_ROWS)) state.rows.delete(id);
  }
  return added;
}

function maxId() {
  let max = 0;
  for (const id of state.rows.keys()) if (id > max) max = id;
  return max;
}

function showNotice(text) {
  dom.notice.hidden = !text;
  dom.notice.textContent = text || '';
}

async function loadInitial() {
  const generation = ++state.generation;
  state.loaded = false;
  state.rows.clear();
  state.cache.clear();
  dom.entries.replaceChildren();
  state.nextCursor = null;
  state.follow = true;
  try {
    let data = await fetchActivity();
    if (generation !== state.generation) return;
    applyMeta(data);
    mergeRows(data.activities);
    state.nextCursor = data.nextCursor ?? null;
    while (state.nextCursor != null && state.rows.size < INITIAL_ROWS) {
      data = await fetchActivity({ before: state.nextCursor });
      if (generation !== state.generation) return;
      mergeRows(data.activities);
      state.nextCursor = data.nextCursor ?? null;
    }
    showNotice('');
    state.loaded = true;
    render();
  } catch (error) {
    showNotice(`无法连接 daemon：${error.message}`);
    dom.live.className = 'live bad';
    dom.liveText.textContent = '连接失败';
  }
}

async function poll() {
  if (state.polling || document.hidden || viewingHistorical()) return;
  if (!state.loaded) { void loadInitial(); return; }
  state.polling = true;
  const generation = state.generation;
  try {
    let added = 0;
    let data;
    do {
      // after 游标不按会话过滤；空会话先取最新一页，拿到首个 id 后再增量
      data = await fetchActivity(state.rows.size ? { after: maxId() } : {});
      if (generation !== state.generation) return;
      if (applyMeta(data)) { void loadInitial(); return; }
      added += mergeRows(data.activities);
    } while (data.hasNewer && state.rows.size);
    showNotice('');
    if (added > 0) render();
    else { renderLive(); renderStats(); }
  } catch (error) {
    showNotice(`无法连接 daemon：${error.message}`);
    dom.live.className = 'live bad';
    dom.liveText.textContent = '连接失败';
  } finally {
    state.polling = false;
  }
}

async function loadOlder() {
  if (state.nextCursor == null) return;
  dom.loadOlder.disabled = true;
  const generation = state.generation;
  try {
    const data = await fetchActivity({ before: state.nextCursor });
    if (generation !== state.generation) return;
    state.nextCursor = data.nextCursor ?? null;
    if (mergeRows(data.activities) > 0) {
      const before = dom.feed.scrollHeight - dom.feed.scrollTop;
      state.follow = false;
      render();
      dom.feed.scrollTop = dom.feed.scrollHeight - before;
    }
  } catch (error) {
    showNotice(`加载更早失败：${error.message}`);
  } finally {
    dom.loadOlder.disabled = false;
    dom.loadOlder.hidden = state.nextCursor == null;
  }
}

function selectSession(value) {
  if (value === 'all') state.epoch = null;
  else {
    const epoch = Number(value);
    state.epoch = epoch === state.currentEpoch ? 'current' : epoch;
  }
  syncUrl();
  void loadInitial();
}

function stepSession(delta) {
  if (state.epoch === null || !state.sessions.length) return;
  const epochs = state.sessions.map((session) => session.epoch);
  const current = state.epoch === 'current' ? state.currentEpoch : state.epoch;
  const next = epochs[epochs.indexOf(current) + delta];
  if (next != null) selectSession(String(next));
}

async function loadInstances() {
  try {
    const data = await (await fetch(INSTANCES_API, { headers: { accept: 'application/json' } })).json();
    dom.instance.replaceChildren(...data.instances.map((inst) => {
      const option = h('option', '', inst.label);
      option.value = inst.id;
      return option;
    }));
    const fromUrl = new URL(window.location.href).searchParams.get('instance');
    state.instance = data.instances.some((inst) => inst.id === fromUrl) ? fromUrl : data.defaultInstance;
    state.epoch = epochFromUrl();
    dom.instance.value = state.instance;
    dom.instance.disabled = data.instances.length < 2;
  } catch {
    showNotice('实例列表读取失败');
  }
}

// ---------- 交互 ----------
function jumpLatest() {
  if (viewingHistorical()) { selectSession(String(state.currentEpoch)); return; }
  state.follow = true;
  dom.feed.scrollTop = dom.feed.scrollHeight;
  dom.followLive.hidden = true;
}

dom.feed.addEventListener('scroll', () => {
  const atBottom = dom.feed.scrollHeight - dom.feed.scrollTop - dom.feed.clientHeight < 48;
  state.follow = atBottom;
  dom.followLive.hidden = atBottom && !viewingHistorical();
}, { passive: true });

// 长消息默认 6 行，点击展开/收起
dom.entries.addEventListener('click', (event) => {
  const body = event.target.closest('.msg-body, .reply-body');
  if (body && !window.getSelection()?.toString()) body.classList.toggle('expanded');
});

dom.entries.addEventListener('toggle', (event) => {
  const key = event.target.dataset?.key;
  if (!key) return;
  if (event.target.open) state.openKeys.add(key); else state.openKeys.delete(key);
}, true);

dom.instance.addEventListener('change', () => {
  state.instance = dom.instance.value;
  state.epoch = 'current';
  syncUrl();
  void loadInitial();
});
dom.session.addEventListener('change', () => selectSession(dom.session.value));
dom.sessionPrev.addEventListener('click', () => stepSession(1));
dom.sessionNext.addEventListener('click', () => stepSession(-1));
dom.loadOlder.addEventListener('click', () => void loadOlder());
dom.followLive.addEventListener('click', jumpLatest);
dom.privacy.addEventListener('click', () => {
  const on = document.body.classList.toggle('privacy');
  dom.privacy.setAttribute('aria-pressed', String(on));
  dom.privacy.textContent = on ? '显示正文' : '隐藏正文';
});
document.addEventListener('keydown', (event) => {
  if (event.target.closest('input, select, textarea') || event.metaKey || event.ctrlKey || event.altKey) return;
  if (event.key === '[') stepSession(1);
  else if (event.key === ']') stepSession(-1);
  else if (event.key === 'l' || event.key === 'L' || event.key === 'End') jumpLatest();
  else if (event.key === 'p' || event.key === 'P') dom.privacy.click();
});
document.addEventListener('visibilitychange', () => { if (!document.hidden) void poll(); });

(async () => {
  await loadInstances();
  syncUrl();
  await loadInitial();
  setInterval(() => void poll(), POLL_MS);
})();
