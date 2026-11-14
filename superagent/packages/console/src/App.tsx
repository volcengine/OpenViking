import { useEffect, useState } from 'react'
import { Activity, Bot, ChevronDown, CircleGauge, ExternalLink, ListChecks, Menu, Moon, RefreshCw, Server, Settings2, ShieldCheck, Sun, X } from 'lucide-react'
import { api } from './api'
import { ErrorState, Loading } from './components'
import { useApi, useHashRoute } from './hooks'
import { ActivityPage } from './pages/ActivityPage'
import { CapabilitiesPage } from './pages/CapabilitiesPage'
import { NotFoundPage } from './pages/NotFoundPage'
import { OverviewPage } from './pages/OverviewPage'
import { SessionsPage } from './pages/SessionsPage'
import { TaskDetailPage } from './pages/TaskDetailPage'
import { TasksPage } from './pages/TasksPage'

const NAVIGATION = [
  { id: 'overview', label: 'Overview', icon: CircleGauge },
  { id: 'tasks', label: 'Tasks', icon: ListChecks },
  { id: 'sessions', label: 'Sessions', icon: Bot },
  { id: 'activity', label: 'Activity', icon: Activity },
]

export function App() {
  const route = useHashRoute()
  const instances = useApi(api.instances, 'instances')
  const initialInstance = new URLSearchParams(window.location.search).get('instance')
  const [instance, setInstance] = useState(initialInstance ?? '')
  const [mobileOpen, setMobileOpen] = useState(false)
  const [refreshKey, setRefreshKey] = useState(0)
  const [dark, setDark] = useState(() => window.localStorage.getItem('ovsa-theme') === 'dark')
  const selected = instance || instances.data?.defaultInstance || instances.data?.items[0]?.id || ''
  const capabilities = useApi(() => selected ? api.capabilities(selected) : Promise.resolve(null), `capabilities:${selected}`, 60_000)

  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
    window.localStorage.setItem('ovsa-theme', dark ? 'dark' : 'light')
  }, [dark])
  useEffect(() => { setMobileOpen(false) }, [route.page, route.taskId])
  useEffect(() => {
    if (instance && instances.data && !instances.data.items.some((item) => item.id === instance)) setInstance('')
  }, [instance, instances.data])

  const changeInstance = (id: string) => {
    setInstance(id)
    const url = new URL(window.location.href)
    url.searchParams.set('instance', id)
    window.history.replaceState(null, '', url)
  }
  const currentInstance = instances.data?.items.find((item) => item.id === selected)
  const activePage = route.page === 'task' ? 'tasks' : route.page

  return <div className="app-shell">
    {mobileOpen && <button className="sidebar-scrim" aria-label="Close navigation" onClick={() => setMobileOpen(false)} />}
    <aside className={`sidebar ${mobileOpen ? 'open' : ''}`}>
      <div className="brand"><div className="brand-mark"><span /><span /><span /></div><div><strong>OpenViking</strong><span>SUPERAGENT CONSOLE</span></div><button className="mobile-close" onClick={() => setMobileOpen(false)} aria-label="Close navigation"><X size={19} /></button></div>
      <div className="workspace-label">WORKSPACE</div>
      <label className="instance-switcher"><div className="instance-avatar">{selected.slice(0, 2).toUpperCase() || '—'}</div><div><strong>{currentInstance?.label ?? selected ?? 'Select instance'}</strong><span><i className={currentInstance?.ready === false ? 'offline' : ''} />{currentInstance?.ready === false ? 'Offline' : 'Local instance'}</span></div><ChevronDown size={15} /><select aria-label="Select instance" value={selected} onChange={(event) => changeInstance(event.target.value)} disabled={!instances.data}>{instances.data?.items.map((item) => <option value={item.id} key={item.id}>{item.label}{item.default ? ' (default)' : ''}</option>)}</select></label>
      <nav className="main-nav" aria-label="Console navigation"><span className="nav-section-label">MONITOR</span>{NAVIGATION.map(({ id, label, icon: Icon }) => <a href={`#/${id}`} className={activePage === id ? 'active' : ''} aria-current={activePage === id ? 'page' : undefined} key={id}><Icon size={18} /><span>{label}</span>{id === 'tasks' && capabilities.data && !capabilities.data.modules['tasks.read']?.effective && <i className="nav-unavailable" title="Tasks unavailable" />}</a>)}<span className="nav-section-label secondary-label">SYSTEM</span><a href="#/capabilities" className={activePage === 'capabilities' ? 'active' : ''} aria-current={activePage === 'capabilities' ? 'page' : undefined}><Settings2 size={18} /><span>Capabilities</span></a></nav>
      <div className="sidebar-bottom"><div className="read-only-panel"><ShieldCheck size={18} /><div><strong>Read-only console</strong><span>Slice A · No control actions</span></div></div><a className="legacy-link" href={`/activity?instance=${encodeURIComponent(selected)}`}><Activity size={15} /> Legacy activity timeline <ExternalLink size={12} /></a><div className="version"><span>OVSA Console</span><span>RFC 001</span></div></div>
    </aside>
    <main className="main-area">
      <header className="topbar"><button className="mobile-menu" onClick={() => setMobileOpen(true)} aria-label="Open navigation"><Menu size={20} /></button><div className="breadcrumb"><Server size={15} /><span>{currentInstance?.label ?? selected}</span><span>/</span><strong>{route.page === 'task' ? 'Task detail' : activePage[0]?.toUpperCase() + activePage.slice(1)}</strong></div><div className="topbar-actions"><span className="local-only"><i />Local only</span><button className="icon-button" aria-label="Refresh current view" title="Refresh current view" onClick={() => setRefreshKey((value) => value + 1)}><RefreshCw size={17} className={capabilities.refreshing ? 'spin' : ''} /></button><button className="icon-button" aria-label={dark ? 'Use light theme' : 'Use dark theme'} onClick={() => setDark((value) => !value)}>{dark ? <Sun size={17} /> : <Moon size={17} />}</button></div></header>
      <div className="content" key={`${selected}:${refreshKey}`}>
        {instances.loading ? <Loading /> : instances.error ? <ErrorState error={instances.error} retry={instances.reload} /> : !selected ? <ErrorState error={new Error('No SuperAgent instances are configured.')} /> : capabilities.loading ? <Loading /> : capabilities.error || !capabilities.data ? <ErrorState error={capabilities.error ?? new Error('Capabilities unavailable')} retry={capabilities.reload} /> : <Page route={route} instance={selected} capabilities={capabilities.data} />}
      </div>
    </main>
  </div>
}

function Page({ route, instance, capabilities }: { route: ReturnType<typeof useHashRoute>; instance: string; capabilities: NonNullable<ReturnType<typeof api.capabilities>> extends Promise<infer T> ? T : never }) {
  if (route.page === 'overview') return <OverviewPage instance={instance} capabilities={capabilities} />
  if (route.page === 'tasks') return <TasksPage instance={instance} capabilities={capabilities} />
  if (route.page === 'task' && route.taskId) {
    return capabilities.modules['tasks.read']?.effective
      ? <TaskDetailPage instance={instance} taskId={route.taskId} />
      : <TasksPage instance={instance} capabilities={capabilities} />
  }
  if (route.page === 'sessions') return <SessionsPage instance={instance} />
  if (route.page === 'activity') return <ActivityPage instance={instance} />
  if (route.page === 'capabilities') return <CapabilitiesPage capabilities={capabilities} />
  return <NotFoundPage />
}
