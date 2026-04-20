import { useState, FormEvent, useEffect, useCallback, useRef } from 'react'
import {
  Database, Server, Cpu, UserPlus, Copy, Check, Users, Play,
  RefreshCw, Activity, Globe, Layers, ExternalLink,
  Clock, CheckCircle, XCircle, Loader, X, Search, ChevronRight,
  Key, Table2, BarChart2, Terminal, Trash2, TrendingUp, HelpCircle,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { apiClient } from '../api/client'

interface UserListItem {
  id: string
  email: string
  full_name: string | null
  role: string
  is_active: boolean
  is_totp_configured: boolean
  last_login_at: string | null
  created_at: string
}

interface InviteResult { registration_url: string; invite_token: string; email: string; expires_at: string }

interface TaskResult { task_id: string; message: string }

interface InviteListItem {
  id: string
  email: string
  status: 'pending' | 'used' | 'expired' | 'revoked'
  expires_at: string
  used_at: string | null
  revoked_at: string | null
  created_at: string
}

interface ModelInfo {
  id: string
  model_type: string
  version: string
  is_active: boolean
  metrics: Record<string, number>
  artifact_path: string
  trained_at: string
  promoted_at?: string
}

interface TaskStatusEntry {
  task_name: string
  status: 'running' | 'done' | 'error' | 'idle' | 'unknown'
  message: string
  started_at?: string
  finished_at?: string
  summary: Record<string, unknown>
  ts?: string
}

interface TaskLogEntry {
  ts: string
  level: 'info' | 'error' | 'warn'
  msg: string
}

// ── Simple one-shot trigger button ───────────────────────────────────────────
function TriggerBtn({
  label, endpoint, body, icon, disabled, onSuccess,
}: {
  label: string
  endpoint: string
  body?: Record<string, unknown>
  icon?: React.ReactNode
  disabled?: boolean
  onSuccess?: () => void
}) {
  const [busy, setBusy] = useState(false)
  const run = async () => {
    setBusy(true)
    try {
      const { data } = await apiClient.post<TaskResult>(endpoint, body ?? {})
      toast.success(data.message)
      onSuccess?.()
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Failed.'
      toast.error(msg)
    } finally {
      setBusy(false)
    }
  }
  return (
    <button
      className="btn btn-outline"
      onClick={run}
      disabled={busy || disabled}
      style={{ display:'flex', alignItems:'center', gap:6, fontSize:12, padding:'5px 14px' }}
    >
      {busy ? <Loader size={12} style={{ animation:'spin 1s linear infinite' }}/> : (icon ?? <Play size={12}/>)}
      {busy ? 'Enqueueing…' : label}
    </button>
  )
}

// ── Pipeline task status badge ────────────────────────────────────────────────
function StatusBadge({ status }: { status: string }) {
  const cfg: Record<string, { color: string; icon: React.ReactNode }> = {
    done:    { color: 'var(--green)',      icon: <CheckCircle size={12}/> },
    running: { color: 'var(--blue)',       icon: <Loader size={12}/> },
    error:   { color: 'var(--red)',        icon: <XCircle size={12}/> },
    idle:    { color: 'var(--text-muted)', icon: <Clock size={12}/> },
    unknown: { color: 'var(--text-muted)', icon: <HelpCircle size={12}/> },
  }
  const { color, icon } = cfg[status] ?? cfg.idle
  return (
    <span style={{ display:'inline-flex', alignItems:'center', gap:4, color, fontSize:11, fontWeight:600 }}>
      {icon} {status.toUpperCase()}
    </span>
  )
}

// ── Task Log Modal ────────────────────────────────────────────────────────────
function TaskLogModal({
  taskName, taskLabel, taskStatus, onClose,
}: {
  taskName: string; taskLabel: string; taskStatus: string; onClose: () => void
}) {
  const [logs, setLogs] = useState<TaskLogEntry[]>([])
  const logBoxRef = useRef<HTMLDivElement | null>(null)
  const pollRef   = useRef<ReturnType<typeof setInterval> | null>(null)

  const fetchLogs = async (scrollBottom = false) => {
    try {
      const { data } = await apiClient.get<TaskLogEntry[]>(`/admin/pipeline/${taskName}/logs?limit=500`)
      setLogs(data)
      if (scrollBottom && logBoxRef.current) {
        logBoxRef.current.scrollTop = logBoxRef.current.scrollHeight
      }
    } catch { /* silent */ }
  }

  useEffect(() => {
    fetchLogs(true)
    if (taskStatus === 'running') {
      pollRef.current = setInterval(() => fetchLogs(true), 2000)
    }
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [taskName]) // eslint-disable-line react-hooks/exhaustive-deps

  // Start/stop poll when status changes while modal is open
  useEffect(() => {
    if (taskStatus === 'running') {
      if (!pollRef.current) {
        pollRef.current = setInterval(() => fetchLogs(true), 2000)
      }
    } else {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
      fetchLogs(true) // one final fetch on completion
    }
  }, [taskStatus]) // eslint-disable-line react-hooks/exhaustive-deps

  const isRunning = taskStatus === 'running'

  return (
    <Modal
      title={`${taskLabel} — Logs`}
      icon={<Terminal size={18}/>}
      onClose={onClose}
    >
      <div style={{ display:'flex', flexDirection:'column', height:'70vh' }}>
        {/* Sub-header */}
        <div style={{
          display:'flex', alignItems:'center', gap:10,
          padding:'10px 20px', borderBottom:'1px solid var(--border)',
          flexShrink:0, background:'var(--bg-hover)',
        }}>
          <StatusBadge status={taskStatus}/>
          {isRunning && (
            <span style={{ fontSize:11, color:'var(--text-muted)' }}>Auto-refreshing every 2s…</span>
          )}
          <span style={{ marginLeft:'auto', fontSize:11, color:'var(--text-muted)' }}>
            {logs.length} line{logs.length !== 1 ? 's' : ''}
          </span>
          <button
            onClick={() => fetchLogs(false)}
            style={{ background:'none', border:'1px solid var(--border)', borderRadius:6, padding:'3px 10px', fontSize:11, color:'var(--text)', cursor:'pointer', display:'flex', alignItems:'center', gap:5 }}
          >
            <RefreshCw size={11}/> Refresh
          </button>
          <button
            onClick={() => { setLogs([]); logBoxRef.current && (logBoxRef.current.scrollTop = 0) }}
            style={{ background:'none', border:'1px solid var(--border)', borderRadius:6, padding:'3px 10px', fontSize:11, color:'var(--text-muted)', cursor:'pointer', display:'flex', alignItems:'center', gap:5 }}
          >
            <Trash2 size={11}/> Clear view
          </button>
        </div>
        {/* Log body */}
        <div
          ref={logBoxRef}
          style={{
            flex:1, overflowY:'auto',
            background:'#0d1117',
            padding:'12px 16px',
            fontFamily:'"Cascadia Code", "Fira Code", "JetBrains Mono", monospace',
            fontSize:12, lineHeight:1.7,
          }}
        >
          {logs.length === 0 ? (
            <div style={{ color:'#484f58', paddingTop:32, textAlign:'center' }}>
              No log entries yet. Trigger the task to see output here.
            </div>
          ) : (
            logs.map((line, idx) => (
              <div key={idx} style={{
                color: line.level === 'error' ? '#f85149' : line.level === 'warn' ? '#e3b341' : '#e6edf3',
                paddingLeft: line.level !== 'info' ? 0 : undefined,
              }}>
                <span style={{ color:'#484f58', marginRight:10, userSelect:'none', fontSize:11 }}>
                  {line.ts ? new Date(line.ts).toLocaleTimeString('en-IN', { hour12:false, hour:'2-digit', minute:'2-digit', second:'2-digit' }) : ''}
                </span>
                {line.level !== 'info' && (
                  <span style={{ marginRight:8, fontWeight:700, fontSize:11 }}>[{line.level.toUpperCase()}]</span>
                )}
                {line.msg}
              </div>
            ))
          )}
        </div>
        {/* Scroll-to-bottom strip */}
        <div style={{ padding:'8px 16px', borderTop:'1px solid var(--border)', flexShrink:0, display:'flex', justifyContent:'flex-end' }}>
          <button
            onClick={() => { if (logBoxRef.current) logBoxRef.current.scrollTop = logBoxRef.current.scrollHeight }}
            style={{ background:'none', border:'none', fontSize:11, color:'var(--text-muted)', cursor:'pointer', display:'flex', alignItems:'center', gap:4 }}
          >
            ↓ Scroll to bottom
          </button>
        </div>
      </div>
    </Modal>
  )
}

// ── Unified Pipeline Panel ────────────────────────────────────────────────────
function PipelinePanel() {
  const [entries,      setEntries]      = useState<TaskStatusEntry[]>([])
  const [models,       setModels]       = useState<ModelInfo[]>([])
  const [promoting,    setPromoting]    = useState<string | null>(null)
  const [mlLoading,    setMlLoading]    = useState(false)
  const [brokerPeriod, setBrokerPeriod] = useState<'1y'|'2y'|'5y'>('1y')
  const [nifty500Only, setNifty500Only] = useState(false)
  const [dateVal,      setDateVal]      = useState('')
  const [logModal,     setLogModal]     = useState<{ taskName: string; taskLabel: string } | null>(null)

  const statusMap = Object.fromEntries(entries.map(e => [e.task_name, e]))

  const refresh = async () => {
    try {
      const [s, m] = await Promise.all([
        apiClient.get<TaskStatusEntry[]>('/admin/pipeline/status'),
        apiClient.get<ModelInfo[]>('/admin/pipeline/models').catch(() => ({ data: [] as ModelInfo[] })),
      ])
      setEntries(s.data)
      setModels(m.data)
      return s.data
    } catch { /* silent */ }
  }

  useEffect(() => {
    refresh()

    // Continuously poll every 3s while the page is mounted
    const autoPoll = setInterval(() => { refresh() }, 3000)

    return () => { clearInterval(autoPoll) }
  }, [])

  const trainModel = async () => {
    setMlLoading(true)
    try {
      const { data } = await apiClient.post<TaskResult>('/admin/pipeline/train-model', {})
      toast.success(data.message)
      setTimeout(refresh, 5000)
    } catch (e: unknown) {
      toast.error((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Failed.')
    } finally { setMlLoading(false) }
  }

  const promote = async (id: string) => {
    setPromoting(id)
    try {
      await apiClient.post(`/admin/pipeline/models/${id}/promote`, {})
      toast.success('Model promoted.')
      refresh()
    } catch (e: unknown) {
      toast.error((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Failed.')
    } finally { setPromoting(null) }
  }

  const rollback = async (id: string) => {
    try {
      await apiClient.post(`/admin/pipeline/models/${id}/rollback`, {})
      toast.success('Rolled back.')
      refresh()
    } catch (e: unknown) {
      toast.error((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Failed.')
    }
  }

  const steps: { n: number; label: string; task: string; desc: string; actions: React.ReactNode }[] = [
    {
      n: 1, label: 'Populate Universe', task: 'universe_population',
      desc: 'Download all NSE EQ symbols into stock_universe. Safe to re-run.',
      actions: (
        <div style={{ display:'flex', alignItems:'center', gap:8, flexWrap:'wrap' }}>
          <label style={{ display:'flex', alignItems:'center', gap:5, fontSize:11, cursor:'pointer' }}>
            <input type="checkbox" checked={nifty500Only} onChange={e => setNifty500Only(e.target.checked)}/>
            Nifty 500 only
          </label>
          <TriggerBtn label="Populate" endpoint="/admin/pipeline/populate-universe"
            body={{ nifty500_only: nifty500Only }} icon={<Globe size={12}/>} onSuccess={refresh}/>
        </div>
      ),
    },
    {
      n: 2, label: 'NSE Bhavcopy Backfill', task: 'backfill',
      desc: 'Historical OHLCV via NSE Bhavcopy (one request per trading day covers all symbols). Run once after first setup.',
      actions: (
        <div style={{ display:'flex', alignItems:'center', gap:8, flexWrap:'wrap' }}>
          <select value={brokerPeriod} onChange={e => setBrokerPeriod(e.target.value as '1y'|'2y'|'5y')}
            style={{ background:'var(--bg)', border:'1px solid var(--border)', borderRadius:6, padding:'4px 8px', color:'var(--text)', fontSize:11 }}>
            <option value="1y">1 Year</option>
            <option value="2y">2 Years</option>
            <option value="5y">5 Years</option>
          </select>
          <TriggerBtn label="Run Backfill" endpoint="/admin/pipeline/backfill"
            body={{ period: brokerPeriod }} icon={<Layers size={12}/>} onSuccess={refresh}/>
        </div>
      ),
    },
    {
      n: 3, label: 'NSE Bhavcopy', task: 'bhavcopy',
      desc: 'Daily OHLCV for all EQ symbols in one download. Auto-runs Mon–Fri 19:30 IST.',
      actions: (
        <div style={{ display:'flex', alignItems:'center', gap:8, flexWrap:'wrap' }}>
          <input type="date" value={dateVal} onChange={e => setDateVal(e.target.value)}
            style={{ background:'var(--bg)', border:'1px solid var(--border)', borderRadius:6, padding:'4px 8px', color:'var(--text)', fontSize:11 }}/>
          <TriggerBtn label={dateVal ? `Run ${dateVal}` : 'Run Today'}
            endpoint="/admin/pipeline/bhavcopy" body={{ trade_date: dateVal || null }} icon={<RefreshCw size={12}/>} onSuccess={refresh}/>
        </div>
      ),
    },
    {
      n: 4, label: 'Feature Engineering', task: 'feature_engineering',
      desc: 'Validate technical indicators (RSI, MACD, Bollinger, ATR, OBV, ADX, SMA) across all active symbols. Run after backfill to confirm training readiness.',
      actions: <TriggerBtn label="Run Check" endpoint="/admin/pipeline/feature-engineering" icon={<Play size={12}/>} onSuccess={refresh}/>,
    },
    {
      n: 5, label: 'Train ML Model', task: 'ml_training',
      desc: 'Train LightGBM on OHLCV features. Needs ≥60 days of data per symbol.',
      actions: (
        <button className="btn btn-outline" onClick={trainModel} disabled={mlLoading}
          style={{ display:'flex', alignItems:'center', gap:6, fontSize:12, padding:'5px 14px' }}>
          {mlLoading ? <Loader size={12}/> : <Play size={12}/>}
          {mlLoading ? 'Enqueueing…' : 'Train New Model'}
        </button>
      ),
    },
    {
      n: 6, label: 'Promote Model', task: 'ml_training',
      desc: 'Activate a trained model. Signals will use 45% ML blend immediately.',
      actions: models.length === 0 ? (
        <span style={{ fontSize:11, color:'var(--text-muted)' }}>No models yet — run Step 5 first.</span>
      ) : (
        <div style={{ display:'flex', flexDirection:'column', gap:6, width:'100%' }}>
          {models.slice(0, 3).map(m => (
            <div key={m.id} style={{ display:'flex', alignItems:'center', gap:8, flexWrap:'wrap' }}>
              <span style={{ fontSize:11, fontWeight:500, flex:1 }}>
                {m.version}
                {m.is_active && (
                  <span style={{ marginLeft:6, background:'var(--green)', color:'#fff', borderRadius:10, padding:'1px 6px', fontSize:10 }}>ACTIVE</span>
                )}
                <span style={{ color:'var(--text-muted)', marginLeft:6 }}>AUC {(m.metrics?.auc ?? 0).toFixed(3)}</span>
              </span>
              {!m.is_active && (
                <button className="btn btn-outline" onClick={() => promote(m.id)} disabled={promoting === m.id}
                  style={{ fontSize:11, padding:'3px 10px' }}>
                  {promoting === m.id ? 'Promoting…' : 'Promote'}
                </button>
              )}
              {m.is_active && (
                <button className="btn btn-outline" onClick={() => rollback(m.id)}
                  style={{ fontSize:11, padding:'3px 10px', color:'var(--red)', borderColor:'var(--red)' }}>
                  Rollback
                </button>
              )}
            </div>
          ))}
        </div>
      ),
    },
    {
      n: 7, label: 'Generate Signals', task: 'signal_generator',
      desc: 'Auto-scheduled Mon–Fri 16:45 IST. Tech 40% + ML 45% + Sentiment 15%.',
      actions: <TriggerBtn label="Run Now" endpoint="/admin/pipeline/generate-signals" icon={<Play size={12}/>} onSuccess={refresh}/>,
    },
    {
      n: 8, label: 'EOD Ingest', task: 'eod_ingest',
      desc: 'Auto-scheduled Mon–Fri 16:30 IST. Bhavcopy runs separately at 19:30 IST.',
      actions: <TriggerBtn label="Run Now" endpoint="/admin/pipeline/eod-ingest" icon={<RefreshCw size={12}/>} onSuccess={refresh}/>,
    },
    {
      n: 9, label: 'Download Logos', task: 'logo_download',
      desc: 'Fetch ticker logos from logo.dev and cache locally. Incremental — new symbols get logos, existing ones are skipped.',
      actions: <TriggerBtn label="Run Now" endpoint="/admin/pipeline/download-logos" icon={<RefreshCw size={12}/>} onSuccess={refresh}/>,
    },
  ]

  return (
    <div className="settings-section">
      <div className="settings-title" style={{ display:'flex', alignItems:'center', gap:8, justifyContent:'space-between' }}>
        <span style={{ display:'flex', alignItems:'center', gap:8 }}><Activity size={15}/> Pipeline</span>
        <button className="btn btn-outline" onClick={refresh}
          style={{ fontSize:11, padding:'3px 10px', display:'flex', alignItems:'center', gap:4 }}>
          <RefreshCw size={11}/> Refresh Status
        </button>
      </div>

      <div style={{ display:'flex', flexDirection:'column', gap:0 }}>
        {steps.map((step, i) => {
          const entry  = statusMap[step.task]
          const isLast = i === steps.length - 1
          const circleColor =
            entry?.status === 'done'    ? 'var(--green)' :
            entry?.status === 'running' ? 'var(--blue)'  :
            entry?.status === 'error'   ? 'var(--red)'   : 'var(--bg-hover)'
          const circleBorder =
            entry?.status === 'done'    ? 'var(--green)' :
            entry?.status === 'running' ? 'var(--blue)'  :
            entry?.status === 'error'   ? 'var(--red)'   : 'var(--border)'
          return (
            <div key={step.n} style={{ display:'grid', gridTemplateColumns:'28px 1fr auto', gap:'0 14px', paddingBottom: isLast ? 0 : 20, position:'relative' }}>
              {!isLast && (
                <div style={{ position:'absolute', left:13, top:28, width:2, height:'calc(100% - 4px)', background:'var(--border)' }} />
              )}
              <div style={{
                width:28, height:28, borderRadius:'50%',
                background: circleColor, border:`2px solid ${circleBorder}`,
                color: entry?.status && entry.status !== 'idle' ? '#fff' : 'var(--text-muted)',
                display:'flex', alignItems:'center', justifyContent:'center',
                fontSize:11, fontWeight:700, flexShrink:0, zIndex:1,
              }}>
                {step.n}
              </div>
              <div style={{ paddingBottom:4 }}>
                <div style={{ display:'flex', alignItems:'center', gap:8, marginBottom:2 }}>
                  <span style={{ fontWeight:600, fontSize:13 }}>{step.label}</span>
                  {entry && <StatusBadge status={entry.status}/>}
                </div>
                <div style={{ fontSize:11, color:'var(--text-muted)', marginBottom:8 }}>{step.desc}</div>
                {entry?.message && entry.status !== 'idle' && (
                  <div style={{ fontSize:11, color:'var(--text-muted)', marginBottom:6, fontStyle:'italic' }}>{entry.message}</div>
                )}
                {step.actions}
                <button
                  onClick={() => setLogModal({ taskName: step.task, taskLabel: step.label })}
                  style={{
                    marginTop:8, display:'inline-flex', alignItems:'center', gap:5,
                    fontSize:11, color:'var(--text-muted)',
                    background:'none', border:'1px solid var(--border)', borderRadius:6,
                    padding:'3px 10px', cursor:'pointer',
                  }}
                >
                  <Terminal size={11}/> View Logs
                </button>
              </div>
              {(entry?.finished_at || entry?.started_at) && (
                <div style={{ fontSize:10, color:'var(--text-muted)', whiteSpace:'nowrap', paddingTop:6, textAlign:'right' }}>
                  <div style={{ opacity:0.6 }}>{entry.finished_at ? 'Last run' : 'Started'}</div>
                  {new Date((entry.finished_at ?? entry.started_at)!).toLocaleString('en-IN', { dateStyle:'short', timeStyle:'short' })}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* Task log modal */}
      {logModal && (
        <TaskLogModal
          taskName={logModal.taskName}
          taskLabel={logModal.taskLabel}
          taskStatus={statusMap[logModal.taskName]?.status ?? 'idle'}
          onClose={() => setLogModal(null)}
        />
      )}
    </div>
  )
}

// ── Modal overlay wrapper ─────────────────────────────────────────────────────
function Modal({ title, icon, onClose, children }: {
  title: string; icon: React.ReactNode; onClose: () => void; children: React.ReactNode
}) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  return (
    <div style={{
      position:'fixed', inset:0, zIndex:1000,
      background:'rgba(0,0,0,0.65)', backdropFilter:'blur(3px)',
      display:'flex', alignItems:'center', justifyContent:'center',
      padding:24,
    }} onClick={onClose}>
      <div style={{
        background:'var(--bg)', border:'1px solid var(--border)', borderRadius:12,
        width:'min(92vw, 1100px)', maxHeight:'88vh',
        display:'flex', flexDirection:'column', overflow:'hidden',
        boxShadow:'0 24px 64px rgba(0,0,0,0.6)',
      }} onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div style={{
          display:'flex', alignItems:'center', gap:10,
          padding:'14px 20px', borderBottom:'1px solid var(--border)',
          flexShrink:0,
        }}>
          <span style={{ color:'var(--blue)' }}>{icon}</span>
          <span style={{ fontWeight:700, fontSize:15 }}>{title}</span>
          <button onClick={onClose} style={{
            marginLeft:'auto', background:'none', border:'none',
            color:'var(--text-muted)', cursor:'pointer', padding:4,
          }}><X size={18}/></button>
        </div>
        {/* Body */}
        <div style={{ flex:1, overflow:'auto', padding:'0' }}>
          {children}
        </div>
      </div>
    </div>
  )
}

// ── Shared: scrollable data table ─────────────────────────────────────────────
function DataTable({ columns, rows }: { columns: string[]; rows: Record<string, unknown>[] }) {
  if (rows.length === 0) return (
    <div style={{ padding:'32px 20px', textAlign:'center', color:'var(--text-muted)', fontSize:13 }}>No rows returned.</div>
  )
  return (
    <div style={{ overflowX:'auto' }}>
      <table style={{ width:'100%', borderCollapse:'collapse', fontSize:12 }}>
        <thead>
          <tr style={{ background:'var(--bg-hover)', position:'sticky', top:0 }}>
            {columns.map(c => (
              <th key={c} style={{
                padding:'8px 12px', textAlign:'left', fontWeight:600,
                borderBottom:'1px solid var(--border)', whiteSpace:'nowrap',
                color:'var(--text-muted)',
              }}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} style={{ borderBottom:'1px solid var(--border)', background: i % 2 === 0 ? 'transparent' : 'var(--bg-hover)' }}>
              {columns.map(c => {
                const val = row[c]
                const str = val === null || val === undefined ? '' : typeof val === 'object' ? JSON.stringify(val) : String(val)
                return (
                  <td key={c} style={{
                    padding:'6px 12px', maxWidth:300,
                    overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap',
                    color: val === null ? 'var(--text-muted)' : 'var(--text)',
                    fontFamily: typeof val === 'number' ? 'monospace' : 'inherit',
                  }} title={str}>{str === '' ? <span style={{ color:'var(--text-muted)', fontStyle:'italic' }}>null</span> : str}</td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── DB Browser modal ──────────────────────────────────────────────────────────
interface DbTable { table_name: string }
interface QueryResult { columns: string[]; rows: Record<string, unknown>[]; count?: number }

function DBBrowserModal({ onClose }: { onClose: () => void }) {
  const [tables,      setTables]      = useState<DbTable[]>([])
  const [selected,    setSelected]    = useState<string | null>(null)
  const [queryResult, setQueryResult] = useState<QueryResult | null>(null)
  const [sql,         setSql]         = useState('')
  const [loadingTbls, setLoadingTbls] = useState(true)
  const [loadingRows, setLoadingRows] = useState(false)
  const [rowLimit,    setRowLimit]    = useState(20)
  const [tab,         setTab]         = useState<'browse'|'query'>('browse')

  useEffect(() => {
    apiClient.get<DbTable[]>('/admin/browser/db/tables')
      .then(r => setTables(r.data))
      .catch(() => {})
      .finally(() => setLoadingTbls(false))
  }, [])

  const loadTable = async (name: string, limit: number) => {
    setSelected(name); setLoadingRows(true); setQueryResult(null)
    try {
      const { data } = await apiClient.get<QueryResult>(`/admin/browser/db/tables/${name}/rows`, { params: { limit } })
      setQueryResult(data)
    } catch (e: unknown) {
      toast.error((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Load failed.')
    } finally { setLoadingRows(false) }
  }

  const runQuery = async () => {
    if (!sql.trim()) return
    setLoadingRows(true); setQueryResult(null); setSelected(null)
    try {
      const { data } = await apiClient.post<QueryResult>('/admin/browser/db/query', { sql })
      if (data.columns.length === 0) {
        // DML statement — no rows returned
        toast.success(`Query OK — ${data.count} row(s) affected.`)
        setQueryResult({ columns: ['result'], rows: [{ result: `${data.count} row(s) affected` }], count: data.count })
      } else {
        setQueryResult(data)
      }
    } catch (e: unknown) {
      toast.error((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Query failed.')
    } finally { setLoadingRows(false) }
  }

  return (
    <Modal title="Database Browser" icon={<Database size={18}/>} onClose={onClose}>
      <div style={{ display:'flex', height:'calc(88vh - 57px)' }}>
        {/* Sidebar — table list */}
        <div style={{
          width:220, flexShrink:0, borderRight:'1px solid var(--border)',
          overflowY:'auto', padding:'8px 0',
        }}>
          <div style={{ padding:'6px 14px 4px', fontSize:11, color:'var(--text-muted)', fontWeight:600, textTransform:'uppercase', letterSpacing:1 }}>Tables</div>
          {loadingTbls ? (
            <div style={{ padding:'16px 14px', color:'var(--text-muted)', fontSize:12 }}>Loading…</div>
          ) : tables.map(t => (
            <div key={t.table_name}
              onClick={() => { setTab('browse'); loadTable(t.table_name, rowLimit) }}
              style={{
                padding:'7px 14px', cursor:'pointer', fontSize:12,
                background: selected === t.table_name ? 'var(--bg-hover)' : 'transparent',
                borderLeft: selected === t.table_name ? '2px solid var(--blue)' : '2px solid transparent',
                display:'flex', alignItems:'center', gap:6,
              }}
            >
              <Table2 size={12} style={{ color:'var(--text-muted)', flexShrink:0 }}/>
              <div style={{ overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', flex:1 }}>{t.table_name}</div>
              <ChevronRight size={11} style={{ color:'var(--text-muted)', flexShrink:0 }}/>
            </div>
          ))}
        </div>

        {/* Main area */}
        <div style={{ flex:1, display:'flex', flexDirection:'column', minWidth:0 }}>
          {/* Tab bar */}
          <div style={{ display:'flex', borderBottom:'1px solid var(--border)', flexShrink:0 }}>
            {(['browse','query'] as const).map(t => (
              <button key={t} onClick={() => setTab(t)} style={{
                padding:'10px 18px', background:'none', border:'none', cursor:'pointer',
                fontSize:12, fontWeight:600,
                color: tab === t ? 'var(--blue)' : 'var(--text-muted)',
                borderBottom: tab === t ? '2px solid var(--blue)' : '2px solid transparent',
              }}>
                {t === 'browse' ? <><Table2 size={12} style={{ marginRight:5, verticalAlign:'middle' }}/>Browse</> : <><Terminal size={12} style={{ marginRight:5, verticalAlign:'middle' }}/>SQL Query</>}
              </button>
            ))}
            {selected && tab === 'browse' && (
              <div style={{ marginLeft:'auto', display:'flex', alignItems:'center', gap:8, padding:'0 14px' }}>
                {queryResult && queryResult.count !== undefined && (
                  <span style={{ fontSize:11, color:'var(--text-muted)' }}>
                    {queryResult.count.toLocaleString()} total rows
                  </span>
                )}
                <span style={{ fontSize:11, color:'var(--text-muted)' }}>Limit</span>
                {[20, 100, 500].map(n => (
                  <button key={n} onClick={() => { setRowLimit(n); loadTable(selected, n) }}
                    style={{ padding:'3px 9px', borderRadius:4, border:'1px solid var(--border)', background: rowLimit === n ? 'var(--blue)' : 'transparent', color: rowLimit === n ? '#fff' : 'var(--text)', fontSize:11, cursor:'pointer' }}>
                    {n}
                  </button>
                ))}
              </div>
            )}
          </div>

          {tab === 'query' && (
            <div style={{ padding:'14px 16px', borderBottom:'1px solid var(--border)', flexShrink:0 }}>
              <div style={{ fontSize:11, color:'var(--text-muted)', marginBottom:6 }}>SELECT / WITH / EXPLAIN only. Max 200 rows returned.</div>
              <div style={{ display:'flex', gap:8 }}>
                <textarea
                  value={sql} onChange={e => setSql(e.target.value)}
                  placeholder="SELECT * FROM ohlcv_daily WHERE symbol = 'RELIANCE.NS' LIMIT 20"
                  style={{
                    flex:1, height:72, resize:'vertical',
                    background:'var(--bg-hover)', border:'1px solid var(--border)', borderRadius:6,
                    padding:'8px 10px', color:'var(--text)', fontSize:12, fontFamily:'monospace',
                  }}
                  onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) runQuery() }}
                />
                <button onClick={runQuery} disabled={loadingRows || !sql.trim()}
                  className="btn btn-outline"
                  style={{ display:'flex', alignItems:'center', gap:6, fontSize:12, alignSelf:'flex-end', padding:'8px 16px' }}>
                  {loadingRows ? <Loader size={12}/> : <Play size={12}/>} Run
                </button>
              </div>
              <div style={{ fontSize:10, color:'var(--text-muted)', marginTop:4 }}>Ctrl+Enter to run</div>
            </div>
          )}

          {/* Results */}
          <div style={{ flex:1, overflowY:'auto' }}>
            {loadingRows ? (
              <div style={{ padding:'40px', textAlign:'center', color:'var(--text-muted)' }}><Loader size={20}/></div>
            ) : queryResult ? (
              <>
                {queryResult.count !== undefined && (
                  <div style={{ padding:'8px 14px', fontSize:11, color:'var(--text-muted)', borderBottom:'1px solid var(--border)' }}>
                    {queryResult.count} row{queryResult.count !== 1 ? 's' : ''} returned
                  </div>
                )}
                <DataTable columns={queryResult.columns} rows={queryResult.rows}/>
              </>
            ) : tab === 'browse' ? (
              <div style={{ padding:'40px', textAlign:'center', color:'var(--text-muted)', fontSize:13 }}>Select a table from the left sidebar.</div>
            ) : (
              <div style={{ padding:'40px', textAlign:'center', color:'var(--text-muted)', fontSize:13 }}>Write a query above and press Run.</div>
            )}
          </div>
        </div>
      </div>
    </Modal>
  )
}

// ── Redis Browser modal ────────────────────────────────────────────────────────
interface RedisKey { key: string; type: string; ttl: number }
interface RedisValue { key: string; type: string; ttl: number; value: unknown }

function RedisBrowserModal({ onClose }: { onClose: () => void }) {
  const [pattern,  setPattern]  = useState('*')
  const [keys,     setKeys]     = useState<RedisKey[]>([])
  const [selected, setSelected] = useState<RedisValue | null>(null)
  const [loading,  setLoading]  = useState(false)
  const [loadingVal, setLoadingVal] = useState(false)

  const search = useCallback(async () => {
    setLoading(true); setSelected(null)
    try {
      const { data } = await apiClient.get<RedisKey[]>('/admin/browser/redis/keys', { params: { pattern } })
      setKeys(data)
    } catch { toast.error('Failed to list keys.') }
    finally { setLoading(false) }
  }, [pattern])

  useEffect(() => { search() }, [])

  const loadKey = async (key: string) => {
    setLoadingVal(true)
    try {
      const { data } = await apiClient.get<RedisValue>(`/admin/browser/redis/keys/${encodeURIComponent(key)}`)
      setSelected(data)
    } catch { toast.error('Failed to load key.') }
    finally { setLoadingVal(false) }
  }

  const TYPE_COLOR: Record<string, string> = {
    string: 'var(--blue)', hash: 'var(--green)', list: 'var(--yellow)',
    set: '#a78bfa', zset: '#f472b6', none: 'var(--text-muted)',
  }

  return (
    <Modal title="Redis Browser" icon={<Server size={18}/>} onClose={onClose}>
      <div style={{ display:'flex', height:'calc(88vh - 57px)' }}>
        {/* Sidebar */}
        <div style={{ width:280, flexShrink:0, borderRight:'1px solid var(--border)', display:'flex', flexDirection:'column' }}>
          <div style={{ padding:'10px 12px', borderBottom:'1px solid var(--border)', flexShrink:0, display:'flex', gap:6 }}>
            <input value={pattern} onChange={e => setPattern(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') search() }}
              placeholder="Key pattern (e.g. pipeline:*)" style={{
                flex:1, background:'var(--bg-hover)', border:'1px solid var(--border)', borderRadius:6,
                padding:'5px 8px', color:'var(--text)', fontSize:12,
              }}/>
            <button onClick={search} disabled={loading}
              style={{ background:'var(--blue)', border:'none', borderRadius:6, padding:'5px 10px', color:'#fff', cursor:'pointer', display:'flex', alignItems:'center' }}>
              {loading ? <Loader size={13}/> : <Search size={13}/>}
            </button>
          </div>
          <div style={{ overflowY:'auto', flex:1 }}>
            {keys.length === 0 && !loading && (
              <div style={{ padding:'20px 12px', color:'var(--text-muted)', fontSize:12 }}>No keys found.</div>
            )}
            {keys.map(k => (
              <div key={k.key} onClick={() => loadKey(k.key)} style={{
                padding:'7px 12px', cursor:'pointer', fontSize:12,
                background: selected?.key === k.key ? 'var(--bg-hover)' : 'transparent',
                borderLeft: selected?.key === k.key ? '2px solid var(--blue)' : '2px solid transparent',
                display:'flex', alignItems:'center', gap:6,
              }}>
                <Key size={11} style={{ color:'var(--text-muted)', flexShrink:0 }}/>
                <div style={{ flex:1, minWidth:0 }}>
                  <div style={{ overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', fontFamily:'monospace' }}>{k.key}</div>
                  <div style={{ fontSize:10, display:'flex', gap:8, marginTop:1 }}>
                    <span style={{ color: TYPE_COLOR[k.type] ?? 'var(--text-muted)', fontWeight:600 }}>{k.type}</span>
                    <span style={{ color:'var(--text-muted)' }}>{k.ttl < 0 ? '∞' : `TTL ${k.ttl}s`}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
          <div style={{ padding:'8px 12px', borderTop:'1px solid var(--border)', fontSize:11, color:'var(--text-muted)' }}>
            {keys.length} key{keys.length !== 1 ? 's' : ''} (max 200)
          </div>
        </div>

        {/* Value panel */}
        <div style={{ flex:1, overflowY:'auto', padding:'16px 20px' }}>
          {loadingVal ? (
            <div style={{ textAlign:'center', padding:'40px', color:'var(--text-muted)' }}><Loader size={20}/></div>
          ) : selected ? (
            <>
              <div style={{ display:'flex', alignItems:'center', gap:10, marginBottom:14 }}>
                <code style={{ fontSize:13, fontWeight:600, fontFamily:'monospace', wordBreak:'break-all' }}>{selected.key}</code>
                <span style={{ color: TYPE_COLOR[selected.type], fontSize:11, fontWeight:600, textTransform:'uppercase', background:'var(--bg-hover)', padding:'2px 8px', borderRadius:10 }}>{selected.type}</span>
                <span style={{ color:'var(--text-muted)', fontSize:11 }}>{selected.ttl < 0 ? 'no expiry' : `expires in ${selected.ttl}s`}</span>
              </div>
              <pre style={{
                background:'var(--bg-hover)', border:'1px solid var(--border)', borderRadius:8,
                padding:'14px 16px', fontSize:12, fontFamily:'monospace',
                overflowX:'auto', whiteSpace:'pre-wrap', wordBreak:'break-all',
                color:'var(--text)', lineHeight:1.6,
              }}>{JSON.stringify(selected.value, null, 2)}</pre>
            </>
          ) : (
            <div style={{ padding:'40px', textAlign:'center', color:'var(--text-muted)', fontSize:13 }}>Select a key from the left to view its value.</div>
          )}
        </div>
      </div>
    </Modal>
  )
}

// ── ML Engine Browser modal ───────────────────────────────────────────────────
function MLBrowserModal({ onClose }: { onClose: () => void }) {
  const [models, setModels] = useState<ModelInfo[]>([])
  const [signals, setSignals] = useState<Record<string, unknown>[]>([])
  const [sigCols, setSigCols] = useState<string[]>([])
  const [tab, setTab] = useState<'models'|'signals'|'features'>('models')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    apiClient.get<ModelInfo[]>('/admin/pipeline/models')
      .then(r => setModels(r.data)).catch(() => {})
  }, [])

  const loadSignals = async () => {
    if (signals.length > 0) return
    setLoading(true)
    try {
      const { data } = await apiClient.post<QueryResult>('/admin/browser/db/query', {
        sql: `SELECT symbol, signal_date, signal_score, direction, confidence, tech_score, ml_score, sentiment_score, model_version, created_at FROM signals ORDER BY signal_date DESC, signal_score DESC`,
        limit: 200,
      })
      setSigCols(data.columns)
      setSignals(data.rows)
    } catch { toast.error('Failed to load signals.') }
    finally { setLoading(false) }
  }

  useEffect(() => {
    if (tab === 'signals') loadSignals()
  }, [tab])

  return (
    <Modal title="ML Engine Browser" icon={<Cpu size={18}/>} onClose={onClose}>
      {/* Tab bar */}
      <div style={{ display:'flex', borderBottom:'1px solid var(--border)', flexShrink:0 }}>
        {([['models','Models'], ['signals','Latest Signals'], ['features','Feature Info']] as const).map(([t, label]) => (
          <button key={t} onClick={() => setTab(t)} style={{
            padding:'10px 18px', background:'none', border:'none', cursor:'pointer',
            fontSize:12, fontWeight:600,
            color: tab === t ? 'var(--blue)' : 'var(--text-muted)',
            borderBottom: tab === t ? '2px solid var(--blue)' : '2px solid transparent',
          }}>{label}</button>
        ))}
      </div>

      <div style={{ overflowY:'auto', height:'calc(88vh - 105px)', padding:'20px' }}>
        {tab === 'models' && (
          <div style={{ display:'flex', flexDirection:'column', gap:10 }}>
            {models.length === 0 && <div style={{ color:'var(--text-muted)', fontSize:13 }}>No models trained yet.</div>}
            {models.map(m => (
              <div key={m.id} style={{
                background:'var(--bg-hover)', border:`1px solid ${m.is_active ? 'var(--green)' : 'var(--border)'}`,
                borderRadius:10, padding:'14px 18px',
              }}>
                <div style={{ display:'flex', alignItems:'center', gap:10, marginBottom:8 }}>
                  <span style={{ fontWeight:700, fontSize:14, fontFamily:'monospace' }}>{m.version}</span>
                  {m.is_active && <span style={{ background:'var(--green)', color:'#fff', borderRadius:10, padding:'2px 10px', fontSize:11, fontWeight:700 }}>ACTIVE</span>}
                  <span style={{ color:'var(--text-muted)', fontSize:12, marginLeft:'auto' }}>
                    Trained {new Date(m.trained_at).toLocaleString('en-IN', { dateStyle:'medium', timeStyle:'short' })}
                  </span>
                </div>
                <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fill, minmax(140px, 1fr))', gap:8 }}>
                  {Object.entries(m.metrics ?? {}).map(([k, v]) => (
                    <div key={k} style={{ background:'var(--bg)', borderRadius:6, padding:'8px 12px' }}>
                      <div style={{ fontSize:10, color:'var(--text-muted)', textTransform:'uppercase', marginBottom:2 }}>{k}</div>
                      <div style={{ fontWeight:700, fontSize:16, fontFamily:'monospace' }}>
                        {typeof v === 'number' ? (v > 1 ? v.toFixed(0) : v.toFixed(4)) : String(v)}
                      </div>
                    </div>
                  ))}
                </div>
                <div style={{ fontSize:11, color:'var(--text-muted)', marginTop:8 }}>
                  Type: {m.model_type.toUpperCase()} · Artifact: <code style={{ fontFamily:'monospace' }}>{m.artifact_path}</code>
                </div>
              </div>
            ))}
          </div>
        )}

        {tab === 'signals' && (
          loading ? (
            <div style={{ textAlign:'center', padding:'40px', color:'var(--text-muted)' }}><Loader size={20}/></div>
          ) : (
            <DataTable columns={sigCols} rows={signals}/>
          )
        )}

        {tab === 'features' && (
          <div style={{ fontFamily:'monospace', fontSize:12, color:'var(--text)', lineHeight:2 }}>
            <div style={{ color:'var(--text-muted)', marginBottom:16, fontFamily:'sans-serif', fontSize:13 }}>Features fed into the LightGBM model during training and inference:</div>
            {[
              ['sma_5, sma_10, sma_20, sma_50', 'Simple moving averages'],
              ['ema_12, ema_26', 'Exponential moving averages'],
              ['rsi_14', 'Relative Strength Index (14-period)'],
              ['macd, macd_signal, macd_hist', 'MACD line, signal, histogram'],
              ['bb_upper, bb_lower, bb_width', 'Bollinger Bands (20, 2σ)'],
              ['atr_14', 'Average True Range'],
              ['volume_ratio', 'Volume / 20-day avg volume'],
              ['price_momentum_5, price_momentum_10', 'Return over N days'],
              ['high_low_range', '(High − Low) / Close'],
              ['close_vs_sma20', '(Close − SMA20) / SMA20'],
            ].map(([feat, desc]) => (
              <div key={feat} style={{ display:'flex', gap:20, padding:'6px 0', borderBottom:'1px solid var(--border)' }}>
                <span style={{ color:'var(--blue)', minWidth:300 }}>{feat}</span>
                <span style={{ color:'var(--text-muted)', fontFamily:'sans-serif' }}>{desc}</span>
              </div>
            ))}
            <div style={{ marginTop:16, fontFamily:'sans-serif', fontSize:12, color:'var(--text-muted)' }}>
              Target: binary classification — price ≥ 2% rise in 5 trading days (1 = BUY signal).
              Blend weights: Tech 40% · ML 45% · Sentiment 15%.
            </div>
          </div>
        )}
      </div>
    </Modal>
  )
}

// ── Status Card (clickable) ───────────────────────────────────────────────────
function StatusCard({ icon, label, status, detail, onClick }:
  { icon: React.ReactNode; label: string; status: 'ok'|'warn'|'err'; detail: string; onClick?: () => void }) {
  const COLOR = { ok: 'var(--green)', warn: 'var(--yellow)', err: 'var(--red)' }
  const TEXT  = { ok: 'Healthy', warn: 'Degraded', err: 'Offline' }
  return (
    <div className="status-card" onClick={onClick} style={{ cursor: onClick ? 'pointer' : 'default' }}>
      <div className={`status-pill ${status}`}>{icon}</div>
      <div>
        <div className="status-label">{label}</div>
        <div className="status-value" style={{ color: COLOR[status] }}>{TEXT[status]}</div>
        <div className="text-muted text-sm" style={{ marginTop:4 }}>{detail}</div>
      </div>
      <div style={{ marginLeft:'auto' }}>
        <span className="status-dot" style={{
          width:8, height:8, borderRadius:'50%', display:'block',
          background: COLOR[status], boxShadow:`0 0 8px ${COLOR[status]}`,
          animation: status === 'ok' ? 'pulse 2s infinite' : 'none',
        }} />
      </div>
    </div>
  )
}

// ── Invite History ───────────────────────────────────────────────────────────
// ── User List ─────────────────────────────────────────────────────────────
function UserList() {
  const [users, setUsers] = useState<UserListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [deleting, setDeleting] = useState<string | null>(null)

  function load() {
    setLoading(true)
    apiClient.get<UserListItem[]>('/admin/users')
      .then(r => setUsers(r.data))
      .catch(() => {})
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  async function handleDelete(u: UserListItem) {
    if (!window.confirm(`Permanently delete ${u.email}? This cannot be undone.`)) return
    setDeleting(u.id)
    try {
      await apiClient.delete(`/admin/users/${u.id}`)
      toast.success(`${u.email} deleted.`)
      load()
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Failed to delete user.'
      toast.error(msg)
    } finally {
      setDeleting(null)
    }
  }

  const ROLE_COLOR: Record<string, string> = {
    admin:  'var(--blue)',
    trader: 'var(--text-secondary)',
  }

  return (
    <div className="settings-section">
      <div className="settings-title" style={{ display:'flex', alignItems:'center', gap:8, justifyContent:'space-between' }}>
        <span style={{ display:'flex', alignItems:'center', gap:8 }}><Users size={15}/> Registered Users</span>
        <button className="btn btn-outline" style={{ fontSize:11, padding:'3px 10px' }} onClick={load}>
          <RefreshCw size={12}/>
        </button>
      </div>
      {loading ? (
        <div style={{ fontSize:12, color:'var(--text-muted)', padding:'12px 0' }}>Loading…</div>
      ) : users.length === 0 ? (
        <div className="empty-state" style={{ padding:'24px 0' }}>
          <Users size={28}/><p style={{ fontSize:12 }}>No users yet.</p>
        </div>
      ) : (
        <div style={{ display:'flex', flexDirection:'column', gap:6 }}>
          {users.map(u => (
            <div key={u.id} style={{
              display:'flex', alignItems:'center', gap:10, padding:'8px 10px',
              background:'var(--bg-hover)', borderRadius:6, fontSize:12,
            }}>
              <div style={{ flex:1, minWidth:0 }}>
                <div style={{ fontWeight:500, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>
                  {u.full_name ?? u.email}
                </div>
                <div style={{ fontSize:10, color:'var(--text-muted)', marginTop:1 }}>{u.email}</div>
              </div>
              <span style={{ color: ROLE_COLOR[u.role] ?? 'var(--text-muted)', fontWeight:600, fontSize:11, textTransform:'uppercase', flexShrink:0 }}>
                {u.role}
              </span>
              <span style={{ color: u.is_active ? 'var(--green)' : 'var(--red)', fontSize:10, flexShrink:0 }}>
                {u.is_active ? 'active' : 'inactive'}
              </span>
              <button
                onClick={() => handleDelete(u)}
                disabled={deleting === u.id}
                title="Delete user"
                style={{
                  display:'flex', alignItems:'center', justifyContent:'center',
                  width:26, height:26, borderRadius:5, border:'1px solid var(--red)',
                  background:'transparent', color:'var(--red)', cursor:'pointer', flexShrink:0,
                  opacity: deleting === u.id ? 0.5 : 1,
                }}
              >
                {deleting === u.id ? <Loader size={12} style={{ animation:'spin 1s linear infinite' }}/> : <Trash2 size={12}/>}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Invite Panel (form + history in one card) ─────────────────────────────
function InvitePanel() {
  const [email,   setEmail]   = useState('')
  const [loading, setLoading] = useState(false)
  const [result,  setResult]  = useState<InviteResult | null>(null)
  const [copied,  setCopied]  = useState(false)

  const [invites, setInvites]   = useState<InviteListItem[]>([])
  const [histLoading, setHistLoading] = useState(true)
  const [revoking, setRevoking] = useState<string | null>(null)
  const [limit, setLimit]       = useState<10 | 20 | 50>(10)

  function loadInvites() {
    setHistLoading(true)
    apiClient.get<InviteListItem[]>('/admin/users/invites')
      .then(r => setInvites(r.data))
      .catch(() => {})
      .finally(() => setHistLoading(false))
  }

  useEffect(() => { loadInvites() }, [])

  async function handleInvite(e: FormEvent) {
    e.preventDefault()
    if (!email.trim()) return
    setLoading(true); setResult(null)
    try {
      const { data } = await apiClient.post<InviteResult>('/admin/users/invite', { email: email.trim() })
      setResult(data)
      toast.success(`Invite sent for ${data.email}`)
      setEmail('')
      loadInvites()
    } catch (err: unknown) {
      const msg: string = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Invite failed.'
      toast.error(msg)
    } finally { setLoading(false) }
  }

  async function handleCopy() {
    if (!result) return
    await navigator.clipboard.writeText(result.registration_url)
    setCopied(true); setTimeout(() => setCopied(false), 2000)
  }

  async function handleRevoke(inv: InviteListItem) {
    if (!window.confirm(`Revoke invite for ${inv.email}? They will no longer be able to register with this link.`)) return
    setRevoking(inv.id)
    try {
      await apiClient.delete(`/admin/users/invites/${inv.id}`)
      toast.success(`Invite for ${inv.email} revoked.`)
      loadInvites()
    } catch {
      toast.error('Failed to revoke invite.')
    } finally {
      setRevoking(null)
    }
  }

  const STATUS_COLOR: Record<string, string> = {
    pending: 'var(--blue)',
    used:    'var(--green)',
    expired: 'var(--text-muted)',
    revoked: 'var(--red)',
  }

  const visibleInvites = invites.slice(0, limit)

  return (
    <div className="settings-section" style={{ display:'flex', gap:0, padding:0, overflow:'hidden' }}>
      {/* ── Left: Invite form ── */}
      <div style={{ flex:'0 0 280px', padding:'18px 20px', borderRight:'1px solid var(--border)' }}>
        <div className="settings-title" style={{ display:'flex', alignItems:'center', gap:8, marginBottom:14 }}>
          <UserPlus size={15}/> Invite New User
        </div>
        <form onSubmit={handleInvite} style={{ display:'flex', flexDirection:'column', gap:14 }}>
          <div className="form-group">
            <label htmlFor="invite-email">Email Address</label>
            <input id="invite-email" type="email" value={email} onChange={e => setEmail(e.target.value)}
              placeholder="trader@example.com" required disabled={loading}/>
          </div>
          <button type="submit" className="btn-primary" disabled={loading || !email.trim()}>
            {loading ? 'Generating…' : 'Generate Invite Link'}
          </button>
        </form>
        {result && (
          <div className="invite-result" style={{ marginTop:14 }}>
            <div className="text-sm" style={{ color:'var(--green)', fontWeight:600 }}>
              ✓ Invite ready · expires {new Date(result.expires_at).toLocaleString(undefined, { dateStyle:'short', timeStyle:'short' })}
            </div>
            <div className="invite-url">{result.registration_url}</div>
            <button className="copy-btn" onClick={handleCopy}>
              {copied ? <><Check size={12}/> Copied!</> : <><Copy size={12}/> Copy Link</>}
            </button>
          </div>
        )}
      </div>

      {/* ── Right: Invite history ── */}
      <div style={{ flex:1, minWidth:0, padding:'18px 20px', display:'flex', flexDirection:'column', gap:10 }}>
        <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:2 }}>
          <div className="settings-title" style={{ display:'flex', alignItems:'center', gap:8, marginBottom:0 }}>
            <Users size={15}/> Invite History
          </div>
          <div style={{ display:'flex', alignItems:'center', gap:8 }}>
            <span style={{ fontSize:11, color:'var(--text-muted)' }}>Show</span>
            {([10, 20, 50] as const).map(n => (
              <button
                key={n}
                onClick={() => setLimit(n)}
                style={{
                  fontSize:11, padding:'2px 8px', borderRadius:5, cursor:'pointer',
                  border: limit === n ? '1px solid var(--blue)' : '1px solid var(--border)',
                  background: limit === n ? 'var(--blue-dim, rgba(59,130,246,0.15))' : 'transparent',
                  color: limit === n ? 'var(--blue)' : 'var(--text-muted)',
                }}
              >{n}</button>
            ))}
            <button className="btn btn-outline" style={{ fontSize:11, padding:'2px 8px' }} onClick={loadInvites}>
              <RefreshCw size={11}/>
            </button>
          </div>
        </div>

        {histLoading ? (
          <div style={{ fontSize:12, color:'var(--text-muted)', padding:'12px 0' }}>Loading…</div>
        ) : invites.length === 0 ? (
          <div className="empty-state" style={{ padding:'20px 0' }}>
            <Users size={26}/><p style={{ fontSize:12 }}>No invites sent yet.</p>
          </div>
        ) : (
          <div style={{ display:'flex', flexDirection:'column', gap:5 }}>
            {visibleInvites.map(inv => (
              <div key={inv.id} style={{
                display:'flex', alignItems:'center', gap:10, padding:'6px 10px',
                background:'var(--bg-hover)', borderRadius:6, fontSize:12,
              }}>
                <div style={{ flex:1, minWidth:0 }}>
                  <div style={{ fontWeight:500, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{inv.email}</div>
                  <div style={{ fontSize:10, color:'var(--text-muted)', marginTop:1 }}>
                    {new Date(inv.created_at).toLocaleString('en-IN', { dateStyle:'short', timeStyle:'short' })}
                  </div>
                </div>
                <span style={{ color: STATUS_COLOR[inv.status] ?? 'var(--text-muted)', fontWeight:600, fontSize:10, textTransform:'uppercase', flexShrink:0 }}>
                  {inv.status}
                </span>
                {inv.status === 'pending' && (
                  <button
                    onClick={() => handleRevoke(inv)}
                    disabled={revoking === inv.id}
                    style={{
                      fontSize:11, padding:'2px 8px', borderRadius:5, border:'1px solid var(--red)',
                      background:'transparent', color:'var(--red)', cursor:'pointer', flexShrink:0,
                      opacity: revoking === inv.id ? 0.5 : 1,
                    }}
                  >
                    {revoking === inv.id ? '…' : 'Revoke'}
                  </button>
                )}
              </div>
            ))}
            {invites.length > limit && (
              <div style={{ fontSize:11, color:'var(--text-muted)', textAlign:'center', paddingTop:4 }}>
                +{invites.length - limit} more — increase limit to see all
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Deep Learning Models Panel ────────────────────────────────────────────────
interface DLModelInfo {
  id: string
  model_type: string
  version: string
  is_active: boolean
  metrics: Record<string, number>
  hyperparams: Record<string, unknown>
  trained_at: string
}

function DeepLearningModelsPanel() {
  const [models,  setModels]  = useState<DLModelInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [downloading, setDownloading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const { data } = await apiClient.get<DLModelInfo[]>('/admin/models/deep-learning')
      setModels(data)
    } catch { /* silent */ }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  const triggerDownload = async () => {
    setDownloading(true)
    try {
      const { data } = await apiClient.post<{ message: string }>('/admin/models/download-from-drive', {})
      toast.success(data.message)
      setTimeout(load, 3000)
    } catch (e: unknown) {
      toast.error((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Download failed.')
    } finally { setDownloading(false) }
  }

  const MODEL_LABEL: Record<string, string> = {
    lstm_ae:  'LSTM Autoencoder',
    tft:      'TFT Forecaster',
  }

  const MODEL_DESC: Record<string, string> = {
    lstm_ae:  'Anomaly detection via reconstruction error. Penalises signal score by 20% on anomaly.',
    tft:      '5-day price forecaster. Results visible on the AI Forecast page.',
  }

  return (
    <div className="settings-section">
      <div className="settings-title" style={{ display:'flex', alignItems:'center', gap:8, justifyContent:'space-between' }}>
        <span style={{ display:'flex', alignItems:'center', gap:8 }}><TrendingUp size={15}/> Deep Learning Models</span>
        <div style={{ display:'flex', gap:8 }}>
          <button className="btn btn-outline" onClick={load}
            style={{ fontSize:11, padding:'3px 10px', display:'flex', alignItems:'center', gap:4 }}>
            <RefreshCw size={11}/> Refresh
          </button>
          <button className="btn btn-outline" onClick={triggerDownload} disabled={downloading}
            style={{ fontSize:11, padding:'3px 10px', display:'flex', alignItems:'center', gap:4 }}>
            {downloading ? <Loader size={11}/> : <Play size={11}/>}
            {downloading ? 'Downloading…' : 'Re-download from Drive'}
          </button>
        </div>
      </div>

      {loading ? (
        <div style={{ color:'var(--text-muted)', fontSize:12, padding:'12px 0', display:'flex', alignItems:'center', gap:8 }}>
          <Loader size={13}/> Loading…
        </div>
      ) : models.length === 0 ? (
        <div style={{ color:'var(--text-muted)', fontSize:12, padding:'12px 0' }}>
          No deep learning models registered yet. Run the Colab notebooks and use "Re-download from Drive".
        </div>
      ) : (
        <div style={{ display:'flex', flexDirection:'column', gap:12 }}>
          {models.map(m => {
            const label   = MODEL_LABEL[m.model_type] ?? m.model_type
            const desc    = MODEL_DESC[m.model_type]  ?? ''
            return (
              <div key={m.id} style={{
                background:'var(--bg-hover)', borderRadius:10,
                border:`1px solid ${m.is_active ? 'var(--blue)' : 'var(--border)'}`,
                padding:'14px 16px',
              }}>
                <div style={{ display:'flex', alignItems:'flex-start', gap:10, flexWrap:'wrap' }}>
                  <div style={{ flex:1, minWidth:0 }}>
                    <div style={{ display:'flex', alignItems:'center', gap:8, marginBottom:4, flexWrap:'wrap' }}>
                      <span style={{ fontWeight:700, fontSize:13 }}>{label}</span>
                      {m.is_active && (
                        <span style={{ background:'var(--blue)', color:'#fff', borderRadius:10, padding:'1px 8px', fontSize:10, fontWeight:600 }}>ACTIVE</span>
                      )}
                      <span style={{ fontFamily:'monospace', fontSize:11, color:'var(--text-muted)' }}>{m.version}</span>
                    </div>
                    {desc && <div style={{ fontSize:11, color:'var(--text-muted)', marginBottom:8 }}>{desc}</div>}
                    <div style={{ display:'flex', gap:10, flexWrap:'wrap' }}>
                      {Object.entries(m.metrics ?? {}).map(([k, v]) => (
                        <div key={k} style={{ background:'var(--bg)', border:'1px solid var(--border)', borderRadius:6, padding:'3px 9px', fontSize:11 }}>
                          <span style={{ color:'var(--text-muted)', marginRight:4 }}>{k}:</span>
                          <span style={{ fontFamily:'monospace', fontWeight:600 }}>{typeof v === 'number' ? v.toFixed(4) : String(v)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                  <div style={{ fontSize:10, color:'var(--text-muted)', whiteSpace:'nowrap', marginTop:2 }}>
                    Trained {m.trained_at ? new Date(m.trained_at).toLocaleString('en-IN', { dateStyle:'short', timeStyle:'short' }) : '—'}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────
type HealthStatus = 'ok' | 'err' | 'pending'
interface Health { db: HealthStatus; redis: HealthStatus }

export default function AdminPage() {
  const [health, setHealth] = useState<Health>({ db:'pending', redis:'pending' })
  const [openBrowser, setOpenBrowser] = useState<'db'|'redis'|'ml'|null>(null)

  useEffect(() => {
    apiClient.get('/health').then(r => {
      setHealth({ db: r.data.db === 'ok' ? 'ok' : 'err', redis: r.data.redis === 'ok' ? 'ok' : 'err' })
    }).catch(() => setHealth({ db:'err', redis:'err' }))
  }, [])

  return (
    <div className="admin-page">
      <div className="page-header">
        <div className="page-header-left">
          <div className="page-header-title">Pipeline &amp; Admin</div>
          <div className="page-header-sub">System status · User management · ML pipeline</div>
        </div>
        <div className="page-header-actions">
          <a href="http://localhost:5555" target="_blank" rel="noreferrer"
            className="btn btn-outline"
            style={{ display:'flex', alignItems:'center', gap:6, fontSize:12, textDecoration:'none' }}>
            <ExternalLink size={12}/> Flower
          </a>
        </div>
      </div>

      {/* System status */}
      <div className="status-grid">
        <StatusCard icon={<Database size={20}/>} label="Database"  status={health.db === 'pending' ? 'warn' : health.db} detail="TimescaleDB · pg16" onClick={() => setOpenBrowser('db')} />
        <StatusCard icon={<Server size={20}/>}   label="Redis"     status={health.redis === 'pending' ? 'warn' : health.redis} detail="7.x · session store" onClick={() => setOpenBrowser('redis')} />
        <StatusCard icon={<Cpu size={20}/>}       label="ML Engine" status="ok" detail="LightGBM · signal blending active" onClick={() => setOpenBrowser('ml')} />
      </div>

      {/* Browser modals */}
      {openBrowser === 'db'    && <DBBrowserModal    onClose={() => setOpenBrowser(null)}/>}
      {openBrowser === 'redis' && <RedisBrowserModal onClose={() => setOpenBrowser(null)}/>}
      {openBrowser === 'ml'   && <MLBrowserModal    onClose={() => setOpenBrowser(null)}/>}

      {/* User invite */}
      <InvitePanel/>

      {/* Registered users */}
      <UserList/>

      {/* Deep learning models: LSTM + TFT */}
      <DeepLearningModelsPanel/>

      {/* Unified pipeline: runbook + actions + status in one */}
      <PipelinePanel/>
    </div>
  )
}
