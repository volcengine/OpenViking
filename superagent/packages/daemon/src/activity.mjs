// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { redactSensitive } from './logger.mjs';

const DEFINITIONS = Object.freeze({
  'message.received': { category: 'message', severity: 'info', priority: 55, title: '收到消息' },
  'message.duplicate': { category: 'message', severity: 'info', priority: 5, title: '重复事件' },
  'message.ignored': { category: 'message', severity: 'info', priority: 20, title: '规则忽略' },
  'agent.turn_started': { category: 'agent', severity: 'info', priority: 60, title: '开始一轮' },
  'agent.turn_completed': { category: 'agent', severity: 'info', priority: 62, title: '完成一轮' },
  'agent.steered': { category: 'agent', severity: 'info', priority: 58, title: '中途追加' },
  'timer.completed': { category: 'system', severity: 'success', priority: 45, title: '定时任务完成' },
  'agent.note': { category: 'agent', severity: 'info', priority: 45, title: 'Agent 输出' },
  'agent.tool': { category: 'agent', severity: 'info', priority: 48, title: '工具调用' },
  'agent.tool_called': { category: 'agent', severity: 'info', priority: 46, title: '调用工具' },
  'agent.tool_completed': { category: 'agent', severity: 'info', priority: 48, title: '工具完成' },
  'agent.tool_waiting': { category: 'agent', severity: 'info', priority: 20, title: '等待工具执行' },
  'agent.thinking': { category: 'agent', severity: 'info', priority: 30, title: '思考' },
  'agent.turn_recovered': { category: 'agent', severity: 'warn', priority: 70, title: '恢复一轮' },
  'agent.fallback': { category: 'agent', severity: 'warn', priority: 80, title: '切换运行时' },
  'agent.session_rotated': { category: 'agent', severity: 'info', priority: 12, title: '会话上下文已更新' },
  'agent.failure_notice': { category: 'agent', severity: 'warn', priority: 88, title: '已通知本轮失败' },
  'agent.interaction_declined': { category: 'agent', severity: 'warn', priority: 72, title: '已拒绝交互请求' },
  'agent.decision_missing': { category: 'agent', severity: 'warn', priority: 78, title: '本轮未表态' },
  'agent.loop_stopped': { category: 'agent', severity: 'warn', priority: 82, title: '已中止原地打转的一轮' },
  'inbox.notified': { category: 'agent', severity: 'info', priority: 40, title: '提醒有输入等待' },
  'inbox.delivered': { category: 'agent', severity: 'info', priority: 44, title: '等待的输入交给模型' },
  'task.created': { category: 'agent', severity: 'info', priority: 56, title: '交给 worker 的任务' },
  'task.turn_started': { category: 'agent', severity: 'info', priority: 42, title: 'worker 开始一轮' },
  'task.turn_completed': { category: 'agent', severity: 'info', priority: 43, title: 'worker 完成一轮' },
  'task.reported': { category: 'agent', severity: 'info', priority: 57, title: 'worker 汇报结果' },
  'task.ended': { category: 'agent', severity: 'info', priority: 50, title: '任务结束' },
  'delivery.queued': { category: 'delivery', severity: 'info', priority: 60, title: '等待发送' },
  'delivery.sent': { category: 'delivery', severity: 'success', priority: 70, title: '已回复' },
  'delivery.uncertain': { category: 'delivery', severity: 'error', priority: 100, title: '发送结果不确定' },
  'delivery.blocked': { category: 'delivery', severity: 'warn', priority: 75, title: '投递被拦截' },
  'delivery.silent': { category: 'delivery', severity: 'info', priority: 52, title: '选择不回复' },
  'connector.event_received': { category: 'message', severity: 'info', priority: 50, title: '收到事件' },
  'command.executed': { category: 'system', severity: 'info', priority: 58, title: '本机命令' },
  'command.reply_failed': { category: 'system', severity: 'error', priority: 95, title: '命令回复失败' },
  'system.starting': { category: 'system', severity: 'info', priority: 20, title: '守护进程启动' },
  'system.preflight_ok': { category: 'system', severity: 'success', priority: 25, title: '命令行检查通过' },
  'system.subscriber_started': { category: 'system', severity: 'success', priority: 30, title: '飞书监听已启动' },
  'system.ready': { category: 'system', severity: 'success', priority: 40, title: '守护进程就绪' },
  'system.event_dropped': { category: 'system', severity: 'warn', priority: 65, title: '事件未处理' },
  'system.persist_failed': { category: 'system', severity: 'error', priority: 100, title: '事件落库失败' },
  'system.connector_failed': { category: 'system', severity: 'error', priority: 100, title: 'Connector 不可用' },
  'system.connector_recovered': { category: 'system', severity: 'success', priority: 30, title: 'Connector 已恢复' },
  'system.startup_failed': { category: 'system', severity: 'error', priority: 100, title: '守护进程启动失败' },
  'system.maintenance': { category: 'system', severity: 'info', priority: 10, title: '状态清理完成' },
  'system.stopping': { category: 'system', severity: 'info', priority: 15, title: '守护进程停止中' },
  'system.stopped': { category: 'system', severity: 'info', priority: 15, title: '守护进程已停止' },
});

const SENSITIVE_KEY = /(?:authorization|api[_-]?key|token|secret|credential|password|cookie|session|idempotency|raw)/i;
// Token counts, not credentials.
const COUNT_KEY = /^(?:input|cachedInput|output|reasoning)Tokens$/;

export function activityDefinition(kind) {
  return DEFINITIONS[kind] ?? {
    category: 'system',
    severity: 'info',
    priority: 10,
    title: kind,
  };
}

export function cleanActivityText(value, limit = 4000) {
  const text = redactSensitive(value ?? '')
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, '')
    .trim();
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}

function cleanMetadataValue(value, depth = 0) {
  if (value === null || value === undefined) return value ?? null;
  if (depth >= 4) return '[TRUNCATED]';
  if (typeof value === 'string') return cleanActivityText(value, 4000);
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value === 'boolean') return value;
  if (typeof value === 'bigint') return Number(value);
  if (Array.isArray(value)) return value.slice(0, 50).map((item) => cleanMetadataValue(item, depth + 1));
  if (typeof value === 'object') {
    const cleaned = {};
    for (const [key, child] of Object.entries(value).slice(0, 50)) {
      if (child === undefined || (SENSITIVE_KEY.test(key) && !COUNT_KEY.test(key))) continue;
      cleaned[key] = cleanMetadataValue(child, depth + 1);
    }
    return cleaned;
  }
  return cleanActivityText(String(value), 1000);
}

// Tool input/output for the Activity page: objects become JSON, strings stay
// as-is; cleanActivityMetadata later redacts and caps the length.
export function activityPayload(value) {
  if (value === null || value === undefined || value === '') return undefined;
  if (typeof value === 'string') return value;
  if (Array.isArray(value) && value.every((block) => typeof block?.text === 'string')) {
    return value.map((block) => block.text).join('\n');
  }
  try {
    return JSON.stringify(value, (key, child) => (key && SENSITIVE_KEY.test(key) ? undefined : child));
  } catch {
    return String(value);
  }
}

export function cleanActivityMetadata(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
  return cleanMetadataValue(value);
}

export function reasonLabel(reason) {
  const labels = {
    ignored_sender: '发送者在忽略列表',
    unsupported_message_type: '暂不支持的消息类型',
    empty_content: '消息没有可处理的正文',
    stale_event: '消息到达时已经过旧',
    slash_command: '本机命令',
  };
  return labels[reason] ?? cleanActivityText(reason || '未说明原因', 120);
}
