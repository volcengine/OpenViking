import { useState } from 'react'
import { Activity as ActivityIcon, ArrowRight, ChevronLeft, ChevronRight, Clock3, ShieldCheck } from 'lucide-react'
import { api } from '../api'
import { Card, EmptyState, ErrorState, Loading } from '../components'
import { dateTime, titleCase } from '../format'
import { useApi } from '../hooks'

export function ActivityPage({ instance }: { instance: string }) {
  const [cursors, setCursors] = useState<string[]>([])
  const [kind, setKind] = useState('')
  const cursor = cursors.at(-1)
  const activity = useApi(() => api.activities(instance, { limit: 50, before: cursor, kind: kind || undefined }), `activity:${instance}:${cursor}:${kind}`, cursor ? 0 : 5_000)
  return <div className="page-stack">
    <div className="page-heading"><div><div className="eyebrow">OBSERVE</div><h1>Activity</h1><p>Redacted operational events with stable cursor replay.</p></div><a className="button secondary small" href={`/activity?instance=${encodeURIComponent(instance)}`}>Open legacy timeline <ArrowRight size={14} /></a></div>
    <div className="notice neutral"><ShieldCheck size={17} /><div><strong>Observation only</strong><span>Native transcripts, secrets, and arbitrary log paths are not exposed. The legacy timeline remains available for message-level context.</span></div></div>
    <Card className="activity-card">
      <div className="table-toolbar"><label className="select-filter"><ActivityIcon size={15} /><select aria-label="Filter activity kind" value={kind} onChange={(event) => { setKind(event.target.value); setCursors([]) }}><option value="">All event kinds</option><option value="task">Task events</option><option value="system">System events</option><option value="delivery">Delivery events</option><option value="agent">Agent events</option></select></label><span className="toolbar-meta">{activity.refreshing ? 'Refreshing…' : cursor ? 'Historical page' : 'Live · 5s polling'}</span></div>
      {activity.loading ? <Loading /> : activity.error ? <ErrorState error={activity.error} retry={activity.reload} /> : activity.data?.items.length ? <><div className="activity-list">{activity.data.items.map((event) => <div className="activity-row" key={event.id}>
        <div className={`activity-symbol ${event.severity ?? ''}`}><ActivityIcon size={16} /></div><div className="activity-body"><div><strong>{event.summary || titleCase(event.kind)}</strong><code>{event.kind}</code></div><span>{event.category ?? 'Runtime'}{event.taskId && <> · <a href={`#/tasks/${encodeURIComponent(event.taskId)}`}>{event.taskId}</a></>}{event.turn !== undefined && ` · Turn ${event.turn}`}</span></div><time><Clock3 size={13} />{dateTime(event.createdAt)}</time>
      </div>)}</div><div className="pagination"><button className="button ghost small" disabled={!cursors.length} onClick={() => setCursors((values) => values.slice(0, -1))}><ChevronLeft size={15} /> Newer</button><span>{cursors.length ? `History page ${cursors.length + 1}` : 'Latest activity'}</span><button className="button ghost small" disabled={!activity.data.nextCursor} onClick={() => { if (activity.data?.nextCursor) setCursors((values) => [...values, activity.data!.nextCursor!]) }}>Older <ChevronRight size={15} /></button></div></> : <EmptyState title="No activity" description="New operational events will appear here." />}
    </Card>
  </div>
}
