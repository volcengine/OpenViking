import { Activity, AlertTriangle, ArrowUpRight, Bot, CheckCircle2, Clock3, ListChecks, Radio, Server, Zap } from 'lucide-react'
import { api } from '../api'
import { Card, CardHeader, EmptyState, ErrorState, Loading, StatusBadge, TextLink } from '../components'
import { dateTime, duration, number, reasonText, tokens } from '../format'
import { useApi } from '../hooks'
import type { Capabilities } from '../types'

export function OverviewPage({ instance, capabilities }: { instance: string; capabilities: Capabilities }) {
  const tasksEnabled = capabilities.modules['tasks.read']?.effective ?? false
  const overview = useApi(() => api.overview(instance), `overview:${instance}`, 15_000)
  const tasks = useApi(() => tasksEnabled ? api.tasks(instance, { limit: 6 }) : Promise.resolve(null), `recent:${instance}:${tasksEnabled}`, 15_000)
  const metrics = useApi(() => capabilities.modules['metrics.read']?.effective ? api.metrics(instance) : Promise.resolve(null), `metrics:${instance}`, 30_000)

  if (overview.loading) return <Loading />
  if (overview.error || !overview.data) return <ErrorState error={overview.error ?? new Error('No overview returned')} retry={overview.reload} />
  const data = overview.data
  const taskCounts = data.tasks ?? metrics.data?.tasks ?? {}
  const running = (taskCounts.running ?? 0) + (taskCounts.waiting ?? 0)
  const attention = (data.outbox?.uncertain ?? 0) + (data.alerts?.filter((alert) => alert.severity === 'error').length ?? 0)

  return <div className="page-stack">
    <div className="page-heading">
      <div><div className="eyebrow">WORKSPACE / {instance.toUpperCase()}</div><h1>Instance overview</h1><p>Live health, execution progress, and delivery signals.</p></div>
      <div className="heading-status"><StatusBadge status={data.health} /><span>{data.ready ? 'Accepting work' : 'Not ready'}</span></div>
    </div>

    {data.source === 'legacy' && <div className="notice info"><Radio size={17} /><div><strong>Legacy observation mode</strong><span>This instance is live through the existing GET API. Task and metric panels stay unavailable until the RFC v1 workers backend is present.</span></div></div>}
    {data.alerts?.map((alert) => <div className={`notice ${alert.severity === 'error' ? 'danger' : 'warning'}`} key={alert.code}><AlertTriangle size={17} /><div><strong>{alert.message}</strong>{alert.repairHint && <span>{alert.repairHint}</span>}</div></div>)}

    <div className="stat-grid">
      <Card className="stat-card"><div className="stat-icon violet"><ListChecks size={19} /></div><div><span>Active tasks</span><strong>{tasksEnabled ? number(running) : '—'}</strong><small>{tasksEnabled ? `${number(taskCounts.queued)} waiting in queue` : reasonText(capabilities.modules['tasks.read']?.reasonCode)}</small></div></Card>
      <Card className="stat-card"><div className="stat-icon green"><Zap size={19} /></div><div><span>Worker slots</span><strong>{data.workers?.active !== undefined ? `${number(data.workers.active)} / ${number(data.workers.limit)}` : number(data.runtime?.work?.activeTurns)}</strong><small>{data.workers?.limit !== undefined ? 'Currently occupied' : 'Active runtime turns'}</small></div></Card>
      <Card className="stat-card"><div className="stat-icon blue"><CheckCircle2 size={19} /></div><div><span>Completed</span><strong>{tasksEnabled ? number(taskCounts.done) : '—'}</strong><small>{metrics.data ? `Last ${metrics.data.window}` : 'Reported by instance'}</small></div></Card>
      <Card className="stat-card"><div className={`stat-icon ${attention ? 'amber' : 'slate'}`}><AlertTriangle size={19} /></div><div><span>Needs attention</span><strong>{number(attention)}</strong><small>{data.outbox?.uncertain ? `${data.outbox.uncertain} uncertain deliveries` : 'No uncertain deliveries'}</small></div></Card>
    </div>

    <div className="overview-grid">
      <Card>
        <CardHeader title="Runtime" description="Effective state reported by the daemon" action={<Bot size={19} className="muted-icon" />} />
        <div className="runtime-hero"><div className="runtime-logo">{data.runtime?.runtime?.slice(0, 2).toUpperCase() ?? '—'}</div><div><strong>{data.runtime?.runtime ?? 'Unavailable'}</strong><span>{data.runtime?.model ?? 'Model information not exposed'}</span></div><StatusBadge status={data.runtime?.work?.state ?? (data.ready ? 'ready' : 'offline')} /></div>
        <dl className="definition-grid">
          <div><dt>Effort</dt><dd>{data.runtime?.effort ?? '—'}</dd></div><div><dt>Revision</dt><dd>{data.runtime?.revision ?? '—'}</dd></div>
          <div><dt>Cognition epoch</dt><dd>{data.runtime?.epoch ?? '—'}</dd></div><div><dt>Active turns</dt><dd>{number(data.runtime?.work?.activeTurns)}</dd></div>
        </dl>
        <div className="card-footnote">Runtime switching is disabled. This console does not modify the manifest or runtime.</div>
      </Card>

      <Card>
        <CardHeader title="Connectors" description="One failed connector degrades readiness; it does not mark a ready instance unhealthy" action={<Server size={19} className="muted-icon" />} />
        <div className="connector-list">{data.connectors.length ? data.connectors.map((connector) => <div className="connector-row" key={connector.id}>
          <div className={`connector-icon ${connector.ready === false ? 'offline' : ''}`}><Radio size={17} /></div>
          <div><strong>{connector.id}</strong><span>{connector.kind ?? 'Message connector'}{connector.message ? ` · ${connector.message}` : ''}</span>{connector.repairHint && <small>{connector.repairHint}</small>}</div>
          <StatusBadge status={connector.state} />
        </div>) : <EmptyState title="No connector details" description="The daemon did not include connector states in this snapshot." />}</div>
      </Card>
    </div>

    <div className="overview-grid lower-grid">
      <Card>
        <CardHeader title="Recent tasks" description="Newest work across this instance only" action={tasksEnabled && <TextLink href="#/tasks">View all</TextLink>} />
        {!tasksEnabled ? <EmptyState icon={<ListChecks size={24} />} title="Task observation unavailable" description={capabilities.modules['tasks.read']?.message ?? reasonText(capabilities.modules['tasks.read']?.reasonCode)} />
          : tasks.loading ? <Loading /> : tasks.error ? <ErrorState error={tasks.error} retry={tasks.reload} /> : tasks.data?.items.length ? <div className="compact-list">{tasks.data.items.map((task) => <a className="compact-row" href={`#/tasks/${encodeURIComponent(task.id)}`} key={task.id}>
            <span className={`task-status-dot ${task.status}`} /><div><strong>{task.title}</strong><span>{task.id} · {task.runtime} · updated {dateTime(task.updatedAt)}</span></div><StatusBadge status={task.recovering ? 'recovering' : task.status} /><ArrowUpRight size={15} />
          </a>)}</div> : <EmptyState title="No tasks yet" description="Tasks assigned by the Router will appear here." />}
      </Card>

      <Card>
        <CardHeader title="Execution & delivery" description="Token and queue metrics use only persisted data" />
        {metrics.data ? <div className="metrics-list">
          <div><span><Clock3 size={15} /> Oldest queued</span><strong>{duration(metrics.data.queue?.oldestWaitMs)}</strong></div>
          <div><span><CheckCircle2 size={15} /> Successful turns</span><strong>{number(metrics.data.turns?.succeeded)}</strong></div>
          <div><span><Activity size={15} /> Recovered turns</span><strong>{number(metrics.data.turns?.recovered)}</strong></div>
          <div><span><Zap size={15} /> Output tokens</span><strong>{tokens(metrics.data.usage?.outputTokens)}</strong></div>
          <div><span><AlertTriangle size={15} /> Uncertain outbox</span><strong>{number(metrics.data.outbox?.uncertain)}</strong></div>
          <small>{metrics.data.usage?.costCoverage === 'available' ? 'Cost data is available for this window.' : 'Only token usage is shown; no total cost is inferred.'}</small>
        </div> : <EmptyState icon={<Activity size={24} />} title="Metrics unavailable" description={capabilities.modules['metrics.read']?.message ?? 'This instance does not expose aggregated task metrics.'} />}
      </Card>
    </div>
  </div>
}
