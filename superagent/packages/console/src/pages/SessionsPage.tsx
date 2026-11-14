import { ArrowRight, History, MessageCircleMore } from 'lucide-react'
import { api } from '../api'
import { Card, EmptyState, ErrorState, Loading, StatusBadge } from '../components'
import { dateTime, number } from '../format'
import { useApi } from '../hooks'

export function SessionsPage({ instance }: { instance: string }) {
  const sessions = useApi(() => api.sessions(instance), `sessions:${instance}`, 30_000)
  return <div className="page-stack">
    <div className="page-heading"><div><div className="eyebrow">CONTEXT</div><h1>Sessions</h1><p>Router cognition epochs, separate from Tasks and native Runtime sessions.</p></div><span className="read-only-chip">Read only</span></div>
    <Card>
      {sessions.loading ? <Loading /> : sessions.error ? <ErrorState error={sessions.error} retry={sessions.reload} /> : sessions.data?.items.length ? <div className="session-list">{[...sessions.data.items].reverse().map((session) => <a className="session-row" href={`/activity?instance=${encodeURIComponent(instance)}&epoch=${session.epoch}`} key={session.epoch}>
        <div className="session-icon"><MessageCircleMore size={18} /></div><div><strong>Epoch {session.epoch} {session.current && <StatusBadge status="current" label="Current" />}</strong><span>{dateTime(session.startedAt)} — {session.endedAt ? dateTime(session.endedAt) : 'Ongoing'}</span></div><dl><div><dt>Activities</dt><dd>{number(session.activityCount)}</dd></div><div><dt>Journal</dt><dd>{number(session.journalCount)}</dd></div></dl><ArrowRight size={16} />
      </a>)}</div> : <EmptyState icon={<History size={26} />} title="No cognition sessions" description="Session epochs will appear after the Router records activity." />}
    </Card>
    <p className="page-footnote">Historical Runtime details can be null. Creating a new Session is a control operation and is intentionally unavailable in this read-only release.</p>
  </div>
}
