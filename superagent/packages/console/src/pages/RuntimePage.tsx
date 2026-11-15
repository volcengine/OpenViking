import { useEffect, useState } from 'react'
import { Bot, RefreshCw } from 'lucide-react'
import { api, isDemoMode } from '../api'
import { Card, CardHeader, EmptyState, ErrorState, Loading, StatusBadge } from '../components'
import { ActionFeedback, DemoNotice, useAction } from '../components/Controls'
import { reasonText } from '../format'
import { useApi } from '../hooks'
import type { Capabilities } from '../types'

const RUNTIMES = ['pi', 'durable', 'dsh', 'codex', 'claude']
const EFFORTS = ['low', 'medium', 'high']

export function RuntimePage({ instance, capabilities }: { instance: string; capabilities: Capabilities }) {
  const overview = useApi(() => api.overview(instance), `runtime-page:${instance}`)
  const models = useApi(() => api.runtimeModels(instance), `models:${instance}`)
  const control = capabilities.modules['runtime.control']
  const canControl = Boolean(control?.effective && isDemoMode())
  const runtime = overview.data?.runtime
  const [selectedRuntime, setSelectedRuntime] = useState('')
  const [model, setModel] = useState('')
  const [effort, setEffort] = useState('medium')
  const action = useAction()
  useEffect(() => { if (runtime) { setSelectedRuntime(runtime.runtime); setModel(runtime.model ?? ''); setEffort(runtime.effort ?? 'medium') } }, [runtime?.runtime, runtime?.model, runtime?.effort, runtime?.revision])
  if (overview.loading) return <Loading />
  if (overview.error || !runtime) return <ErrorState error={overview.error ?? new Error('Runtime data unavailable')} retry={overview.reload} />

  const submit = async (kind: 'runtime' | 'model' | 'effort') => {
    const ok = await action.run(async () => {
      if (kind === 'runtime') return api.selectRuntime(instance, { runtime: selectedRuntime, model: model || undefined, expectedRevision: runtime.revision, expectedRuntime: runtime.runtime })
      if (kind === 'model') return api.selectModel(instance, model, runtime.revision)
      return api.selectEffort(instance, effort, runtime.revision)
    }, `${kind === 'runtime' ? 'Runtime' : kind === 'model' ? 'Model' : 'Effort'} transition returned a new effective snapshot.`)
    if (ok) { await overview.reload(); await models.reload() }
  }
  return <div className="page-stack">
    <div className="page-heading"><div><div className="eyebrow">CONTROL / RUNTIME</div><h1>Runtime</h1><p>Inspect the effective Runtime, model catalog, and revision-checked transitions.</p></div><StatusBadge status={runtime.work?.state ?? 'ready'} /></div>
    <DemoNotice />
    {!canControl && <div className="notice neutral"><Bot size={17} /><div><strong>Controls unavailable</strong><span>{isDemoMode() ? control?.message ?? reasonText(control?.reasonCode) : 'This frontend build only enables mutations against the isolated mock server.'}</span></div></div>}
    <ActionFeedback error={action.error} success={action.success} />
    <div className="overview-grid">
      <Card><CardHeader title="Effective snapshot" description="A successful response is the daemon snapshot, not a manifest-only change" />
        <div className="runtime-hero"><div className="runtime-logo">{runtime.runtime.slice(0, 2).toUpperCase()}</div><div><strong>{runtime.runtime}</strong><span>{runtime.model ?? 'No model reported'}</span></div><StatusBadge status={runtime.work?.state} /></div>
        <dl className="definition-grid"><div><dt>Effort</dt><dd>{runtime.effort ?? '—'}</dd></div><div><dt>Revision</dt><dd>{runtime.revision ?? '—'}</dd></div><div><dt>Cognition epoch</dt><dd>{runtime.epoch ?? '—'}</dd></div><div><dt>Active turns</dt><dd>{runtime.work?.activeTurns ?? 0}</dd></div></dl>
        <div className="card-footnote">Switching Runtime preserves OV long-term memory but does not promise native transcript migration.</div>
      </Card>
      <Card><CardHeader title="Runtime transition" description="expectedRuntime and expectedRevision prevent stale changes" />
        <div className="form-stack">
          <label className="field"><span>Runtime</span><select value={selectedRuntime} onChange={(e) => setSelectedRuntime(e.target.value)} disabled={!canControl}>{RUNTIMES.map((item) => <option key={item}>{item}</option>)}</select></label>
          <label className="field"><span>Model (optional native identifier)</span><input value={model} onChange={(e) => setModel(e.target.value)} disabled={!canControl} /></label>
          <button className="button primary" disabled={!canControl || action.busy || !selectedRuntime} onClick={() => submit('runtime')}>Apply Runtime transition</button>
        </div>
      </Card>
    </div>
    <div className="overview-grid">
      <Card><CardHeader title="Model & effort" description="Immediate changes; not the saved startup default" />
        <div className="form-stack"><label className="field"><span>Model</span><select value={model} onChange={(e) => setModel(e.target.value)} disabled={!canControl}>{!models.data?.items.some((item) => item.id === model) && <option value={model}>{model}</option>}{models.data?.items.map((item) => <option key={item.id}>{item.id}</option>)}</select></label><button className="button secondary" disabled={!canControl || action.busy || !model} onClick={() => submit('model')}>Switch model</button><label className="field"><span>Effort</span><select value={effort} onChange={(e) => setEffort(e.target.value)} disabled={!canControl}>{EFFORTS.map((item) => <option key={item}>{item}</option>)}</select></label><button className="button secondary" disabled={!canControl || action.busy} onClick={() => submit('effort')}>Set effort</button></div>
      </Card>
      <Card><CardHeader title="Model catalog" description="Current Runtime only; no billable model turn is created" action={<button className="icon-button" aria-label="Reload models" onClick={() => models.reload()}><RefreshCw size={16} /></button>} />
        {models.loading ? <Loading /> : models.error ? <ErrorState error={models.error} retry={models.reload} /> : models.data?.items.length ? <div className="compact-list">{models.data.items.map((item) => <div className="compact-row" key={item.id}><div><strong>{item.id}</strong><span>Effort: {item.efforts.join(', ')}</span></div></div>)}</div> : <EmptyState title="No models reported" description="The Runtime did not return a model catalog." />}
      </Card>
    </div>
  </div>
}
