import { useState } from 'react'
import { ArrowRight, ChevronLeft, ChevronRight, Filter, ListChecks, Search } from 'lucide-react'
import { api } from '../api'
import { Card, EmptyState, ErrorState, Loading, StatusBadge } from '../components'
import { dateTime, reasonText, tokens } from '../format'
import { useApi } from '../hooks'
import type { Capabilities } from '../types'

const STATUSES = ['all', 'queued', 'running', 'waiting', 'done', 'stopped', 'failed']

export function TasksPage({ instance, capabilities }: { instance: string; capabilities: Capabilities }) {
  const [status, setStatus] = useState('all')
  const [runtime, setRuntime] = useState('')
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [cursors, setCursors] = useState<string[]>([])
  const cursor = cursors.at(-1)
  const enabled = capabilities.modules['tasks.read']?.effective ?? false
  const filterKey = `${status}:${runtime}:${query}`
  const tasks = useApi(
    () => enabled ? api.tasks(instance, { limit: 20, status: status === 'all' ? undefined : status, runtime: runtime || undefined, q: query || undefined, before: cursor }) : Promise.resolve(null),
    `tasks:${instance}:${filterKey}:${cursor ?? 'latest'}:${enabled}`,
    cursor ? 0 : 10_000,
  )

  const reset = () => setCursors([])
  if (!enabled) return <div className="page-stack"><PageTitle /><Card><EmptyState icon={<ListChecks size={28} />} title="Task observation is not available" description={capabilities.modules['tasks.read']?.message ?? reasonText(capabilities.modules['tasks.read']?.reasonCode)} /></Card></div>

  return <div className="page-stack">
    <PageTitle />
    <div className="status-tabs" role="tablist" aria-label="Filter tasks by status">{STATUSES.map((value) => <button role="tab" aria-selected={status === value} className={status === value ? 'active' : ''} key={value} onClick={() => { setStatus(value); reset() }}>{value === 'all' ? 'All tasks' : value[0].toUpperCase() + value.slice(1)}</button>)}</div>
    <Card className="task-table-card">
      <div className="table-toolbar">
        <form className="search-box" onSubmit={(event) => { event.preventDefault(); setQuery(search.trim()); reset() }}><Search size={16} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search task title or ID" aria-label="Search tasks" /><button type="submit" hidden /></form>
        <label className="select-filter"><Filter size={15} /><select aria-label="Filter runtime" value={runtime} onChange={(event) => { setRuntime(event.target.value); reset() }}><option value="">All runtimes</option><option value="pi">Pi</option><option value="dsh">DSH</option><option value="codex">Codex</option><option value="claude">Claude</option></select></label>
        <span className="toolbar-meta">{tasks.refreshing ? 'Refreshing…' : cursor ? 'Historical page' : 'Live · 10s polling'}</span>
      </div>
      {tasks.loading ? <Loading /> : tasks.error ? <ErrorState error={tasks.error} retry={tasks.reload} /> : tasks.data?.items.length ? <>
        <div className="table-scroll"><table className="task-table"><thead><tr><th>Task</th><th>Status</th><th>Runtime</th><th>Turns</th><th>Tokens</th><th>Updated</th><th><span className="sr-only">Open</span></th></tr></thead>
          <tbody>{tasks.data.items.map((task) => <tr key={task.id} onClick={() => { window.location.hash = `/tasks/${encodeURIComponent(task.id)}` }}>
            <td><a href={`#/tasks/${encodeURIComponent(task.id)}`} onClick={(event) => event.stopPropagation()}><strong>{task.title}</strong><span>{task.id}{task.origin?.source ? ` · ${task.origin.source}` : ''}</span></a></td>
            <td><StatusBadge status={task.recovering ? 'recovering' : task.status} /></td><td><span className="runtime-tag">{task.runtime}</span></td><td>{task.turns}</td>
            <td title={`${task.usage?.inputTokens ?? 0} input / ${task.usage?.outputTokens ?? 0} output`}>{tokens((task.usage?.inputTokens ?? 0) + (task.usage?.outputTokens ?? 0))}</td>
            <td><span className="date-cell">{dateTime(task.updatedAt)}</span></td><td><ArrowRight size={16} className="row-arrow" /></td>
          </tr>)}</tbody></table></div>
        <div className="pagination"><button className="button ghost small" disabled={!cursors.length} onClick={() => setCursors((values) => values.slice(0, -1))}><ChevronLeft size={15} /> Newer</button><span>{cursors.length ? `History page ${cursors.length + 1}` : 'Latest tasks'}</span><button className="button ghost small" disabled={!tasks.data.nextCursor} onClick={() => { if (tasks.data?.nextCursor) setCursors((values) => [...values, tasks.data!.nextCursor!]) }}>Older <ChevronRight size={15} /></button></div>
      </> : <EmptyState icon={<Search size={25} />} title="No matching tasks" description={query || status !== 'all' || runtime ? 'Try clearing a filter or searching for a different title.' : 'Tasks assigned by the Router will appear here. The list polls without changing historical cursors.'} />}
    </Card>
    <p className="page-footnote">Task state is persisted per instance. A recovered task is shown as queued/recovering, never assumed to still be running.</p>
  </div>
}

function PageTitle() {
  return <div className="page-heading"><div><div className="eyebrow">OBSERVE</div><h1>Tasks</h1><p>Follow Router assignments, Worker turns, reports, and final delivery.</p></div><span className="read-only-chip">Read only</span></div>
}
