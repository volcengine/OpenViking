import { useState } from 'react'
import { AlertTriangle, ArrowRight, History, MessageCircleMore, Plus } from 'lucide-react'
import { api, isDemoMode } from '../api'
import { Card, EmptyState, ErrorState, Loading, StatusBadge } from '../components'
import { ActionFeedback, ConfirmDialog, useAction } from '../components/Controls'
import { dateTime, number } from '../format'
import { useApi } from '../hooks'
import type { Capabilities } from '../types'

export function SessionsPage({ instance, capabilities }: { instance: string; capabilities?: Capabilities }) {
  const sessions = useApi(() => api.sessions(instance), `sessions:${instance}`, 30_000)
  const [confirm, setConfirm] = useState(false)
  const action = useAction()
  const canControl = Boolean(capabilities?.modules['sessions.control']?.effective && isDemoMode())
  return <div className="page-stack">
    <div className="page-heading"><div><div className="eyebrow">CONTEXT</div><h1>Sessions</h1><p>Router cognition epochs, separate from Tasks and native Runtime sessions.</p></div>{canControl ? <button className="button secondary" onClick={() => setConfirm(true)}><Plus size={15} /> New Session</button> : <span className="read-only-chip">Read only</span>}</div>
    <ActionFeedback error={action.error} success={action.success} />
    <Card>
      {sessions.loading ? <Loading /> : sessions.error ? <ErrorState error={sessions.error} retry={sessions.reload} /> : sessions.data?.items.length ? <div className="session-list">{[...sessions.data.items].reverse().map((session) => <a className="session-row" href={`/activity?instance=${encodeURIComponent(instance)}&epoch=${session.epoch}`} key={session.epoch}>
        <div className="session-icon"><MessageCircleMore size={18} /></div><div><strong>Epoch {session.epoch} {session.current && <StatusBadge status="current" label="Current" />}</strong><span>{dateTime(session.startedAt)} — {session.endedAt ? dateTime(session.endedAt) : 'Ongoing'}</span></div><dl><div><dt>Activities</dt><dd>{number(session.activityCount)}</dd></div><div><dt>Journal</dt><dd>{number(session.journalCount)}</dd></div></dl><ArrowRight size={16} />
      </a>)}</div> : <EmptyState icon={<History size={26} />} title="No cognition sessions" description="Session epochs will appear after the Router records activity." />}
    </Card>
    <p className="page-footnote">Historical Runtime details can be null. A new Session changes Router cognition context and does not inject a Worker prompt.</p>
    {confirm && <ConfirmDialog title="Start a new cognition Session?" confirmLabel="Create new Session" busy={action.busy} onClose={() => setConfirm(false)} onConfirm={async () => { const ok = await action.run(() => api.newSession(instance), 'New cognition epoch created. The previous epoch remains available as history.'); if (ok) { setConfirm(false); await sessions.reload() } }}><div className="notice warning"><AlertTriangle size={17} /><div><strong>Current turn may be interrupted</strong><span>This has the same semantics as /new. It does not delete OV long-term memory, and it does not cancel or roll back external task side effects.</span></div></div></ConfirmDialog>}
  </div>
}
