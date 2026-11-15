import { useEffect, useState } from 'react'
import { Play, Plus, RotateCw, Square } from 'lucide-react'
import { api, isDemoMode } from '../api'
import { Card, CardHeader, ErrorState, Loading, StatusBadge } from '../components'
import { ActionFeedback, ConfirmDialog, DemoNotice, useAction } from '../components/Controls'
import { dateTime, reasonText } from '../format'
import { useApi } from '../hooks'
import type { Capabilities, Configuration } from '../types'

export function ConfigurationPage({ instance, capabilities }: { instance: string; capabilities: Capabilities }) {
  const config = useApi(() => api.configuration(instance), `configuration:${instance}`)
  const operations = useApi(() => api.operations(instance), `operations:${instance}`)
  const [desired, setDesired] = useState<Configuration['desired'] | null>(null)
  const [confirm, setConfirm] = useState<'save' | 'restart' | 'stop' | 'start' | null>(null)
  const action = useAction()
  const canEdit = Boolean(capabilities.modules['configuration.write']?.effective && isDemoMode())
  const canProcess = Boolean(capabilities.modules['instances.process']?.effective && isDemoMode())
  useEffect(() => { if (config.data) setDesired(structuredClone(config.data.desired)) }, [config.data])
  if (config.loading) return <Loading />
  if (config.error || !config.data || !desired) return <ErrorState error={config.error ?? new Error('Configuration unavailable')} retry={config.reload} />
  const current = config.data
  const update = <K extends keyof Configuration['desired']>(key: K, value: Configuration['desired'][K]) => setDesired((old) => old ? { ...old, [key]: value } : old)
  const dirty = JSON.stringify(desired) !== JSON.stringify(current.desired)
  const runAction = async () => {
    const selected = confirm
    if (!selected) return
    const ok = await action.run(async () => {
      if (selected === 'save') return api.saveConfiguration(instance, desired, current.revision)
      return api.processAction(instance, selected, current.revision)
    }, selected === 'save' ? 'Configuration apply Operation created. The desired state may still require restart.' : `Instance ${selected} Operation created; readiness is a separate verification step.`)
    if (ok) { setConfirm(null); await Promise.all([config.reload(), operations.reload()]) }
  }
  return <div className="page-stack">
    <div className="page-heading"><div><div className="eyebrow">OPERATIONS / CONFIGURATION</div><h1>Configuration</h1><p>Review desired and effective state, validate changes, and follow persistent Operations.</p></div><code>{current.revision}</code></div>
    <DemoNotice />
    {(!canEdit || !canProcess) && <div className="notice neutral"><Square size={17} /><div><strong>Some controls are unavailable</strong><span>{!isDemoMode() ? 'Mutations are only enabled for the isolated mock demo.' : reasonText(capabilities.modules['configuration.write']?.reasonCode)}</span></div></div>}
    <ActionFeedback error={action.error} success={action.success} />
    <div className="overview-grid config-grid">
      <Card><CardHeader title="Desired configuration" description="Only the RFC allowlist is editable; secrets and environment variables are never returned" action={dirty ? <StatusBadge status="pending" label="Unsaved" /> : <StatusBadge status="synced" label="Loaded revision" />} />
        <div className="form-grid">
          <label className="field"><span>Display name</span><input value={desired.displayName} onChange={(e) => update('displayName', e.target.value)} disabled={!canEdit} /></label>
          <label className="field"><span>Workspace</span><input value={desired.workspace} onChange={(e) => update('workspace', e.target.value)} disabled={!canEdit} /></label>
          <label className="field"><span>Runtime</span><select value={desired.runtime} onChange={(e) => update('runtime', e.target.value)} disabled={!canEdit}>{['pi', 'durable', 'dsh', 'codex', 'claude'].map((x) => <option key={x}>{x}</option>)}</select></label>
          <label className="field"><span>Scheduler</span><select value={desired.scheduler} onChange={(e) => update('scheduler', e.target.value)} disabled={!canEdit}><option>workers</option><option>inbox</option></select></label>
          <label className="field full-field"><span>Default model</span><input value={desired.model} onChange={(e) => update('model', e.target.value)} disabled={!canEdit} /></label>
          <label className="field"><span>Effort</span><select value={desired.effort} onChange={(e) => update('effort', e.target.value)} disabled={!canEdit}><option>low</option><option>medium</option><option>high</option></select></label>
          <label className="field"><span>Connectors</span><input value={desired.connectors.join(', ')} onChange={(e) => update('connectors', e.target.value.split(',').map((x) => x.trim()).filter(Boolean))} disabled={!canEdit} /></label>
        </div>
        <div className="form-actions"><button className="button secondary" disabled={!canEdit || !dirty || action.busy} onClick={() => action.run(() => api.validateConfiguration(instance, desired), 'Schema, cross-field, Runtime, OV, and Connector probes passed without saving.')}>Validate only</button><button className="button primary" disabled={!canEdit || !dirty || action.busy} onClick={() => setConfirm('save')}>Save with If-Match</button></div>
      </Card>
      <Card><CardHeader title="Effective state & drift" description="Saving is not proof that a process has applied the change" />
        <dl className="definitions"><div className="definition"><dt>Runtime</dt><dd>{current.effective.runtime ?? '—'}</dd></div><div className="definition"><dt>Model</dt><dd>{current.effective.model ?? '—'}</dd></div><div className="definition"><dt>Ready</dt><dd>{String(current.effective.ready)}</dd></div><div className="definition"><dt>Restart required</dt><dd>{String(current.restartRequired)}</dd></div><div className="definition"><dt>Ignored fields</dt><dd>{current.ignoredFields.length ? current.ignoredFields.join(', ') : 'None'}</dd></div><div className="definition"><dt>Worker mode</dt><dd>{String(current.capabilities.workerMode)}</dd></div></dl>
        <div className="boundary-note">Concurrent writes return 412 revision_conflict. The frontend reloads instead of overwriting another writer.</div>
      </Card>
    </div>
    <Card><CardHeader title="Instance process" description="Predefined actions only. Restart waits for ready and can report rollback or uncertainty" />
      <div className="form-actions process-actions"><button className="button secondary" disabled={!canProcess || action.busy} onClick={() => setConfirm('start')}><Play size={15} /> Start</button><button className="button secondary" disabled={!canProcess || action.busy} onClick={() => setConfirm('restart')}><RotateCw size={15} /> Restart & verify</button><button className="button secondary" disabled={!canProcess || action.busy} onClick={() => setConfirm('stop')}><Square size={15} /> Stop</button></div>
    </Card>
    <Card><CardHeader title="Recent Operations" description="Persisted steps survive a Web restart in the target architecture" />
      <div className="operation-list">{operations.data?.items.map((operation) => <div className="operation-row" key={operation.id}><div><strong>{operation.kind}</strong><span>{operation.id} · {dateTime(operation.updatedAt)}</span></div><div className="step-list">{operation.steps.map((step) => <span key={step.name} className={`step ${step.state}`}>{step.name}</span>)}</div><StatusBadge status={operation.state} /></div>)}</div>
    </Card>
    {confirm && <ConfirmDialog title={confirm === 'save' ? 'Apply configuration change?' : `${confirm[0].toUpperCase()}${confirm.slice(1)} this mock instance?`} confirmLabel={confirm === 'save' ? 'Validate & apply' : `Create ${confirm} Operation`} danger={confirm === 'stop'} busy={action.busy} onClose={() => setConfirm(null)} onConfirm={runAction}>
      <p>{confirm === 'save' ? 'The mock will validate, compare If-Match, save an allowlisted projection, and create an apply Operation.' : 'The mock records process steps and readiness separately. External side effects cannot be assumed to roll back.'}</p>
    </ConfirmDialog>}
  </div>
}

export function SetupInstanceButton({ instance }: { instance: string }) {
  const [open, setOpen] = useState(false)
  const [id, setId] = useState('research-2')
  const [label, setLabel] = useState('Research Agent 2')
  const action = useAction()
  if (!isDemoMode()) return null
  return <><button className="button secondary" onClick={() => setOpen(true)}><Plus size={15} /> Setup mock instance</button>{open && <ConfirmDialog title="Setup a mock instance" confirmLabel="Create setup Operation" busy={action.busy} onClose={() => setOpen(false)} onConfirm={async () => { const ok = await action.run(() => api.createInstance({ id, label, runtime: 'pi', model: 'demo/model-small' }), `Setup Operation created from ${instance}; no PM2 app was created.`); if (ok) setOpen(false) }}><div className="form-stack"><label className="field"><span>Instance ID</span><input value={id} onChange={(e) => setId(e.target.value)} /></label><label className="field"><span>Display name</span><input value={label} onChange={(e) => setLabel(e.target.value)} /></label><ActionFeedback error={action.error} success={action.success} /></div></ConfirmDialog>}</>
}
