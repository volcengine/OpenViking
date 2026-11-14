import { Check, LockKeyhole, ShieldCheck, Wrench, X } from 'lucide-react'
import { Card, StatusBadge } from '../components'
import { reasonText } from '../format'
import type { Capabilities, ModuleState } from '../types'

const DESCRIPTIONS: Record<string, string> = {
  'instances.read': 'Instances, processes, readiness, and Connector health',
  'capabilities.read': 'Module states and environment-specific reasons',
  'runtime.read': 'Runtime, model, effort, work, and model catalog',
  'sessions.read': 'Cognition epochs and associated activities',
  'activity.read': 'Redacted operational activity; no native transcript',
  'tasks.read': 'Tasks, Worker turns, inputs, reports, and delivery',
  'metrics.read': 'Persisted token, queue, retry, and delivery metrics',
  'tasks.control': 'Task cancellation with explicit termination semantics',
  'runtime.control': 'Runtime, model, and effort transitions',
  'sessions.control': 'Create a new cognition Session',
  'configuration.read': 'Redacted manifest projection and drift',
  'configuration.write': 'Validated configuration updates with revisions',
  'instances.setup': 'Instance setup and persistent Operations',
  'instances.process': 'Start, stop, restart, and ready verification',
  'providers.read': 'Provider health without credentials',
  'providers.write': 'Provider bindings and credential rotation',
  'runtimePackages.install': 'Verified on-demand Runtime installation',
  'logs.read': 'Bounded, redacted predefined log streams',
  'diagnostics.bundle': 'Bounded redacted diagnostic artifacts',
  'audit.read': 'Mutation actor, revision, diff, and result',
  'events.stream': 'SSE or WebSocket transport over the same event IDs',
  'tasks.retry': 'Explicit retry as a new Task with side-effect warning',
  'tasks.pause': 'Not provided before durable checkpoint support',
}

export function CapabilitiesPage({ capabilities }: { capabilities: Capabilities }) {
  const entries = Object.entries(capabilities.modules)
  const effective = entries.filter(([, value]) => value.effective).length
  return <div className="page-stack">
    <div className="page-heading"><div><div className="eyebrow">SYSTEM / RFC OVSA-CONSOLE-001</div><h1>Capabilities</h1><p>What this instance exposes, what the owner enabled, and what can actually run.</p></div><StatusBadge status={capabilities.source === 'legacy' ? 'degraded' : 'ready'} label={capabilities.source === 'legacy' ? 'Legacy discovery' : 'RFC v1 discovery'} /></div>
    <div className="capability-summary"><Card><ShieldCheck size={22} /><div><strong>{effective} effective modules</strong><span>Effective = enabled by owner ∧ available in this environment.</span></div></Card><Card><LockKeyhole size={22} /><div><strong>Read-only delivery</strong><span>No mutation endpoint is called by this frontend, even when a control module is available.</span></div></Card></div>
    <Card className="capability-card"><div className="capability-header"><span>Module</span><span>Enabled</span><span>Available</span><span>Effective</span><span>Reason</span></div>
      {entries.sort(([a], [b]) => a.localeCompare(b)).map(([key, value]) => <CapabilityRow key={key} moduleKey={key} value={value} />)}
    </Card>
    <p className="page-footnote">These states are informational, not authorization. A future write action must re-check permission and capability on the backend at execution time.</p>
  </div>
}

function CapabilityRow({ moduleKey, value }: { moduleKey: string; value: ModuleState }) {
  return <div className="capability-row"><div><strong>{moduleKey}</strong><span>{DESCRIPTIONS[moduleKey] ?? 'Console module'}</span>{value.message && <small>{value.message}</small>}</div><TriState value={value.enabled} label="Enabled" /><TriState value={value.available} label="Available" /><TriState value={value.effective} label="Effective" /><div className="capability-reason">{value.effective ? <><Check size={14} /> Ready</> : <><Wrench size={14} />{reasonText(value.reasonCode)}</>}</div></div>
}
function TriState({ value, label }: { value: boolean; label: string }) {
  return <span className={`tri-state ${value ? 'yes' : 'no'}`} aria-label={`${label}: ${value ? 'yes' : 'no'}`}>{value ? <Check size={15} /> : <X size={15} />}<span>{value ? 'Yes' : 'No'}</span></span>
}
