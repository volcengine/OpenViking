import { useState } from 'react'
import { AlertTriangle, ArrowLeft, ArrowRight, Box, Check, CircleDashed, Clock3, FileInput, MessageSquareText, Radio, RotateCcw } from 'lucide-react'
import { api } from '../api'
import { Card, CardHeader, Definition, EmptyState, ErrorState, Loading, StatusBadge } from '../components'
import { dateTime, relativeTime, titleCase, tokens } from '../format'
import { useApi } from '../hooks'
import type { Activity, TaskReport } from '../types'

type Tab = 'overview' | 'inputs' | 'reports' | 'events'

export function TaskDetailPage({ instance, taskId }: { instance: string; taskId: string }) {
  const [tab, setTab] = useState<Tab>('overview')
  const task = useApi(() => api.task(instance, taskId), `task:${instance}:${taskId}`, 10_000)
  const inputs = useApi(() => api.taskInputs(instance, taskId, { limit: 50 }), `inputs:${instance}:${taskId}`)
  const reports = useApi(() => api.taskReports(instance, taskId, { limit: 50 }), `reports:${instance}:${taskId}`, 10_000)
  const events = useApi(() => api.taskEvents(instance, taskId, { limit: 100 }), `events:${instance}:${taskId}`, 10_000)

  if (task.loading) return <Loading />
  if (task.error || !task.data) return <ErrorState error={task.error ?? new Error('Task not found')} retry={task.reload} />
  const value = task.data
  return <div className="page-stack task-detail">
    <a className="back-link" href="#/tasks"><ArrowLeft size={15} /> All tasks</a>
    <div className="page-heading task-title-row"><div><div className="eyebrow">{value.id} / {value.runtime.toUpperCase()}</div><h1>{value.title}</h1><p>Created {dateTime(value.createdAt)} · Updated {relativeTime(value.updatedAt)}</p></div><StatusBadge status={value.recovering ? 'recovering' : value.status} /></div>
    {value.recovering && <div className="notice warning"><RotateCcw size={17} /><div><strong>Recovery in progress</strong><span>The previous Worker process is no longer assumed to be running. Persisted turns, usage, and reports remain available.</span></div></div>}
    <div className="detail-summary">
      <Card><span>Current status</span><strong>{titleCase(value.status)}</strong><small>Revision {value.revision}</small></Card>
      <Card><span>Worker turn</span><strong>{value.worker?.turn ?? value.turns} <small>of {value.turns}</small></strong><small>{titleCase(value.worker?.state)}</small></Card>
      <Card><span>Token usage</span><strong>{tokens((value.usage?.inputTokens ?? 0) + (value.usage?.outputTokens ?? 0))}</strong><small>{tokens(value.usage?.inputTokens)} in · {tokens(value.usage?.outputTokens)} out</small></Card>
      <Card><span>Origin</span><strong>{value.origin?.source ?? 'Router'}</strong><small>Instance-scoped task</small></Card>
    </div>
    <div className="detail-tabs" role="tablist">{(['overview', 'inputs', 'reports', 'events'] as Tab[]).map((item) => <button role="tab" aria-selected={tab === item} className={tab === item ? 'active' : ''} onClick={() => setTab(item)} key={item}>{item === 'overview' ? 'Overview' : item === 'inputs' ? `Inputs ${inputs.data ? `(${inputs.data.items.length})` : ''}` : item === 'reports' ? `Reports ${reports.data ? `(${reports.data.items.length})` : ''}` : 'Events'}</button>)}</div>

    {tab === 'overview' && <div className="detail-grid">
      <Card><CardHeader title="Current Worker" description="Worker is a projection of the current Task turn, not a separate persistent resource" />
        <dl className="definitions"><Definition label="State"><StatusBadge status={value.worker?.state} /></Definition><Definition label="Turn">{value.worker?.turn ?? '—'}</Definition><Definition label="Started">{dateTime(value.worker?.startedAt)}</Definition><Definition label="Native session"><code>{value.worker?.sessionRef ?? 'Not exposed'}</code></Definition><Definition label="Termination">{titleCase(value.worker?.termination)}</Definition></dl>
        <div className="boundary-note"><Radio size={16} /><span>Task cancellation belongs to Slice B and is not exposed here. No stop request or external-side-effect rollback is implied.</span></div>
      </Card>
      <Card><CardHeader title="Task metadata" description="Persisted Router assignment" />
        <dl className="definitions"><Definition label="Task ID"><code>{value.id}</code></Definition><Definition label="Runtime">{value.runtime}</Definition><Definition label="Turn count">{value.turns}</Definition><Definition label="Recovering">{value.recovering ? 'Yes' : 'No'}</Definition><Definition label="Error kind">{value.errorKind ?? 'None'}</Definition><Definition label="Source reference"><code>{value.origin?.sourceRef ?? '—'}</code></Definition></dl>
        {value.summary && <p className="task-summary">{value.summary}</p>}
      </Card>
      <Card className="full-width"><CardHeader title="Latest report delivery" description="Worker completion, Router relay, and IM delivery are separate states" action={<button className="button ghost small" onClick={() => setTab('reports')}>All reports <ArrowRight size={14} /></button>} />
        {reports.loading ? <Loading /> : reports.error ? <ErrorState error={reports.error} retry={reports.reload} /> : reports.data?.items[0] ? <ReportCard report={reports.data.items[0]} /> : <EmptyState title="No report yet" description="A Worker report will appear after the turn settles." />}
      </Card>
    </div>}

    {tab === 'inputs' && <Card><CardHeader title="Task inputs" description="Persisted input claim and delivery state; content is only loaded on this detail view" />
      {inputs.loading ? <Loading /> : inputs.error ? <ErrorState error={inputs.error} retry={inputs.reload} /> : inputs.data?.items.length ? <div className="timeline">{inputs.data.items.map((input) => <div className="timeline-item" key={input.id}><div className="timeline-icon"><FileInput size={16} /></div><div><div className="timeline-heading"><strong>Input {input.id}</strong><StatusBadge status={input.state} /><time>{dateTime(input.createdAt)}</time></div>{input.text && <p>{input.text}</p>}<small>Claimed {dateTime(input.claimedAt)} · Delivered {dateTime(input.deliveredAt)}</small></div></div>)}</div> : <EmptyState title="No persisted inputs" description="This task has no input records." />}</Card>}

    {tab === 'reports' && <div className="report-list">{reports.loading ? <Card><Loading /></Card> : reports.error ? <Card><ErrorState error={reports.error} retry={reports.reload} /></Card> : reports.data?.items.length ? reports.data.items.map((report) => <Card key={report.id}><ReportCard report={report} /></Card>) : <Card><EmptyState title="No reports yet" description="Worker reports and their delivery states will appear here." /></Card>}</div>}

    {tab === 'events' && <Card><CardHeader title="Task events" description="Redacted Activity entries linked by task ID and turn" />
      {events.loading ? <Loading /> : events.error ? <ErrorState error={events.error} retry={events.reload} /> : events.data?.items.length ? <EventList events={events.data.items} /> : <EmptyState title="No task events" description="No Activity entries are linked to this task." />}</Card>}
  </div>
}

function ReportCard({ report }: { report: TaskReport }) {
  const final = report.finalDelivery?.status
  const uncertain = final?.toUpperCase() === 'UNCERTAIN'
  return <div className="report-card">
    <div className="report-heading"><div><span className="report-icon"><MessageSquareText size={17} /></span><div><strong>Report {report.id}</strong><span>{dateTime(report.createdAt)}</span></div></div><StatusBadge status={report.state} /></div>
    {report.summary && <p className="report-summary">{report.summary}</p>}
    {report.draft && <pre className="report-content">{report.draft}</pre>}
    {report.question && <div className="notice warning"><AlertTriangle size={16} /><div><strong>Worker needs a decision</strong><span>{report.question}</span></div></div>}
    <div className="delivery-flow">
      <DeliveryStep icon={<Check size={16} />} title="Worker reported" detail={dateTime(report.createdAt)} state="done" />
      <FlowArrow />
      <DeliveryStep icon={<Box size={16} />} title="Router inbox" detail={report.relay?.inboxId ?? 'Not relayed'} state={report.relay?.state ?? 'pending'} />
      <FlowArrow />
      <DeliveryStep icon={uncertain ? <AlertTriangle size={16} /> : <MessageSquareText size={16} />} title="Final IM delivery" detail={report.finalDelivery?.outboxId ?? 'No outbox record'} state={final ?? 'pending'} />
    </div>
    {uncertain && <div className="notice danger"><AlertTriangle size={17} /><div><strong>Final delivery is uncertain</strong><span>The report reached the outbox, but the IM result could not be confirmed. Check the IM manually before retrying; sending again may duplicate the message.</span></div></div>}
    {!!report.artifacts?.length && <div className="artifact-list">{report.artifacts.map((artifact) => artifact.url && /^https?:\/\//.test(artifact.url) ? <a href={artifact.url} target="_blank" rel="noreferrer" key={artifact.label}>{artifact.label} <ArrowRight size={13} /></a> : <span key={artifact.label}>{artifact.label}</span>)}</div>}
  </div>
}

function DeliveryStep({ icon, title, detail, state }: { icon: React.ReactNode; title: string; detail: string; state: string }) {
  return <div className={`delivery-step ${state.toLowerCase()}`}><div>{icon}</div><span>{title}</span><StatusBadge status={state} /><small>{detail}</small></div>
}
function FlowArrow() { return <ArrowRight className="flow-arrow" size={18} /> }

function EventList({ events }: { events: Activity[] }) {
  return <div className="event-list">{events.map((event) => <div className="event-row" key={event.id}><div className={`event-marker ${event.severity ?? ''}`}><CircleDashed size={15} /></div><div><strong>{event.summary || titleCase(event.kind)}</strong><span><code>{event.kind}</code>{event.turn !== undefined && ` · Turn ${event.turn}`}</span></div><time><Clock3 size={13} />{dateTime(event.createdAt)}</time></div>)}</div>
}
