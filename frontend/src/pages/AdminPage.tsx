import { useState, FormEvent, useEffect, useRef, useCallback } from 'react'
import {
  Database, Server, Cpu, UserPlus, Copy, Check, Users, Play,
  RefreshCw, Activity, Globe, Layers, ExternalLink,
  Clock, CheckCircle, XCircle, Loader, X, Search, ChevronRight,
  Key, Table2, BarChart2, Terminal,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { apiClient } from '../api/client'

interface InviteResult { registration_url: string; invite_token: string; email: string; expires_at: string }

interface BackfillProgress {
  pct: number
  message: string
  status: 'running' | 'done' | 'error' | 'idle'
  ts?: string
}

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
  status: 'running' | 'done' | 'error' | 'idle'
  message: string
  started_at?: string
  finished_at?: string
  summary: Record<string, unknown>
  ts?: string
}

// ── Simple one-shot trigger button ───────────────────────────────────────────
function TriggerBtn({
  label, endpoint, body, icon, disabled,
}: {
  label: string
  endpoint: string
  body?: Record<string, unknown>
  icon?: React.ReactNode
  disabled?: boolean
}) {
  const [busy, setBusy] = useState(false)
  const run = async () => {
    setBusy(true)
    try {
      const { data } = await apiClient.post<TaskResult>(endpoint, body ?? {})
      toast.success(data.message)
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
    done:    { color: 'var(--green)', icon: <CheckCircle size={12}/> },
    running: { color: 'var(--blue)',  icon: <Loader size={12}/> },
    error:   { color: 'var(--red)',   icon: <XCircle size={12}/> },
    idle:    { color: 'var(--text-muted)', icon: <Clock size={12}/> },
  }
  const { color, icon } = cfg[status] ?? cfg.idle
  return (
    <span style={{ display:'inline-flex', alignItems:'center', gap:4, color, fontSize:11, fontWeight:600 }}>
      {icon} {status.toUpperCase()}
    </span>
  )
}

// ── Unified Pipeline Panel ────────────────────────────────────────────────────
function PipelinePanel() {
  const [entries,    setEntries]    = useState<TaskStatusEntry[]>([])
  const [models,     setModels]     = useState<ModelInfo[]>([])
  const [promoting,  setPromoting]  = useState<string | null>(null)
  const [bfProgress, setBfProgress] = useState<BackfillProgress | null>(null)
  const [bfLoading,  setBfLoading]  = useState(false)
  const [bfPeriod,   setBfPeriod]   = useState<'1y'|'2y'|'5y'>('2y')
  const [mlLoading,  setMlLoading]  = useState(false)
  const [brokerPeriod, setBrokerPeriod] = useState<'1y'|'2y'|'5y'>('1y')
  const [nifty500Only, setNifty500Only] = useState(false)
  const [dateVal,    setDateVal]    = useState('')
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

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
    apiClient.get<BackfillProgress>('/admin/pipeline/backfill/progress')
      .then(r => setBfProgress(r.data)).catch(() => {})

    // Auto-poll every 3s while any task is running
    const autoPoll = setInterval(async () => {
      const data = await refresh()
      if (data && !data.some(e => e.status === 'running')) clearInterval(autoPoll)
    }, 3000)

    return () => { clearInterval(autoPoll); if (pollRef.current) clearInterval(pollRef.current) }
  }, [])

  const startLegacyBackfill = async () => {
    setBfLoading(true)
    try {
      const { data } = await apiClient.post<TaskResult>('/admin/pipeline/backfill', { period: bfPeriod, force: false })
      toast.success(data.message)
      pollRef.current = setInterval(async () => {
        const r = await apiClient.get<BackfillProgress>('/admin/pipeline/backfill/progress').catch(() => null)
        if (r) { setBfProgress(r.data); if (r.data.status !== 'running') clearInterval(pollRef.current!) }
      }, 2000)
    } catch (e: unknown) {
      toast.error((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Failed.')
    } finally { setBfLoading(false) }
  }

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
            body={{ nifty500_only: nifty500Only }} icon={<Globe size={12}/>}/>
        </div>
      ),
    },
    {
      n: 2, label: 'Broker Backfill (Angel One)', task: 'broker_backfill',
      desc: 'Historical OHLCV via Angel One SmartAPI. Run once after first setup.',
      actions: (
        <div style={{ display:'flex', alignItems:'center', gap:8, flexWrap:'wrap' }}>
          <select value={brokerPeriod} onChange={e => setBrokerPeriod(e.target.value as '1y'|'2y'|'5y')}
            style={{ background:'var(--bg)', border:'1px solid var(--border)', borderRadius:6, padding:'4px 8px', color:'var(--text)', fontSize:11 }}>
            <option value="1y">1 Year</option>
            <option value="2y">2 Years</option>
            <option value="5y">5 Years</option>
          </select>
          <TriggerBtn label="Run Backfill" endpoint="/admin/pipeline/broker-backfill"
            body={{ period: brokerPeriod }} icon={<Layers size={12}/>}/>
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
            endpoint="/admin/pipeline/bhavcopy" body={{ trade_date: dateVal || null }} icon={<RefreshCw size={12}/>}/>
        </div>
      ),
    },
    {
      n: 4, label: 'Train ML Model', task: 'ml_training',
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
      n: 5, label: 'Promote Model', task: 'ml_training',
      desc: 'Activate a trained model. Signals will use 45% ML blend immediately.',
      actions: models.length === 0 ? (
        <span style={{ fontSize:11, color:'var(--text-muted)' }}>No models yet — run Step 4 first.</span>
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
      n: 6, label: 'Generate Signals', task: 'signal_generator',
      desc: 'Auto-scheduled Mon–Fri 16:45 IST. Tech 40% + ML 45% + Sentiment 15%.',
      actions: <TriggerBtn label="Run Now" endpoint="/admin/pipeline/generate-signals" icon={<Play size={12}/>}/>,
    },
    {
      n: 7, label: 'EOD Ingest', task: 'eod_ingest',
      desc: 'Auto-scheduled Mon–Fri 16:30 IST. Bhavcopy runs separately at 19:30 IST.',
      actions: <TriggerBtn label="Run Now" endpoint="/admin/pipeline/eod-ingest" icon={<RefreshCw size={12}/>}/>,
    },
  ]

  const isRunning = bfProgress?.status === 'running'
  const bfPct     = bfProgress?.pct ?? 0

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
                {/* Progress bar for long-running tasks with done/total in summary */}
                {entry?.status === 'running' && typeof entry.summary?.total === 'number' && (entry.summary.total as number) > 0 && (() => {
                  const done  = (entry.summary.done  as number) ?? 0
                  const total = entry.summary.total  as number
                  const pct   = Math.round((done / total) * 100)
                  return (
                    <div style={{ marginBottom:8 }}>
                      <div style={{ background:'var(--bg-hover)', borderRadius:6, overflow:'hidden', height:5, marginBottom:3 }}>
                        <div style={{ width:`${pct}%`, height:'100%', background:'var(--blue)', transition:'width 0.5s ease' }}/>
                      </div>
                      <div style={{ fontSize:10, color:'var(--text-muted)', display:'flex', justifyContent:'space-between' }}>
                        <span>{done} / {total} symbols</span>
                        <span>{pct}%{typeof entry.summary.rows === 'number' ? ` · ${(entry.summary.rows as number).toLocaleString()} rows` : ''}</span>
                      </div>
                    </div>
                  )
                })()}
                {step.actions}
              </div>
              {entry?.ts && (
                <div style={{ fontSize:10, color:'var(--text-muted)', whiteSpace:'nowrap', paddingTop:6 }}>
                  {new Date(entry.ts).toLocaleString('en-IN', { dateStyle:'short', timeStyle:'short' })}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* Legacy yfinance backfill — hidden by default */}
      <details style={{ marginTop:16, borderTop:'1px solid var(--border)', paddingTop:12 }}>
        <summary style={{ fontSize:12, color:'var(--text-muted)', cursor:'pointer', userSelect:'none' }}>
          Legacy Backfill (yfinance — may rate-limit)
        </summary>
        <div style={{ marginTop:10 }}>
          <div style={{ fontSize:11, color:'var(--text-muted)', marginBottom:8 }}>
            Prefer Broker Backfill (Step 2) over this. Yahoo Finance rate-limits large universes.
          </div>
          <div style={{ display:'flex', alignItems:'center', gap:8, flexWrap:'wrap' }}>
            <select value={bfPeriod} onChange={e => setBfPeriod(e.target.value as '1y'|'2y'|'5y')}
              style={{ background:'var(--bg-hover)', border:'1px solid var(--border)', borderRadius:6, padding:'5px 8px', color:'var(--text)', fontSize:12 }}>
              <option value="1y">1 Year</option>
              <option value="2y">2 Years</option>
              <option value="5y">5 Years</option>
            </select>
            <button className="btn btn-outline" onClick={startLegacyBackfill} disabled={bfLoading || isRunning}
              style={{ display:'flex', alignItems:'center', gap:6, fontSize:12 }}>
              <Play size={12}/> {isRunning ? 'Running…' : bfLoading ? 'Enqueueing…' : 'Run Backfill'}
            </button>
          </div>
          {bfProgress && bfProgress.status !== 'idle' && (
            <div style={{ marginTop:10 }}>
              <div style={{ background:'var(--bg-hover)', borderRadius:6, overflow:'hidden', height:6, marginBottom:4 }}>
                <div style={{ width:`${bfPct}%`, height:'100%', background: bfProgress.status === 'error' ? 'var(--red)' : 'var(--blue)', transition:'width 0.4s ease' }}/>
              </div>
              <div style={{ fontSize:11, color:'var(--text-muted)', display:'flex', justifyContent:'space-between' }}>
                <span>{bfProgress.message}</span><span>{bfPct}%</span>
              </div>
            </div>
          )}
        </div>
      </details>
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
interface DbTable { table_name: string; size: string; row_estimate: number | null }
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
      const { data } = await apiClient.post<QueryResult>('/admin/browser/db/query', { sql, limit: 200 })
      setQueryResult(data)
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
              <div style={{ flex:1, minWidth:0 }}>
                <div style={{ overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{t.table_name}</div>
                <div style={{ fontSize:10, color:'var(--text-muted)' }}>
                  ~{(t.row_estimate ?? 0).toLocaleString()} rows · {t.size}
                </div>
              </div>
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
function InviteHistory({ refreshKey }: { refreshKey: number }) {
  const [invites, setInvites] = useState<InviteListItem[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    apiClient.get<InviteListItem[]>('/admin/users/invites')
      .then(r => setInvites(r.data))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [refreshKey])

  const STATUS_COLOR: Record<string, string> = {
    pending:  'var(--blue)',
    used:     'var(--green)',
    expired:  'var(--text-muted)',
    revoked:  'var(--red)',
  }

  return (
    <div className="settings-section">
      <div className="settings-title" style={{ display:'flex', alignItems:'center', gap:8 }}>
        <Users size={15}/> Invite History
      </div>
      {loading ? (
        <div style={{ fontSize:12, color:'var(--text-muted)', padding:'12px 0' }}>Loading…</div>
      ) : invites.length === 0 ? (
        <div className="empty-state" style={{ padding:'24px 0' }}>
          <Users size={28}/>
          <p style={{ fontSize:12 }}>No invites sent yet.</p>
        </div>
      ) : (
        <div style={{ display:'flex', flexDirection:'column', gap:6 }}>
          {invites.map(inv => (
            <div key={inv.id} style={{
              display:'flex', alignItems:'center', gap:10, padding:'7px 10px',
              background:'var(--bg-hover)', borderRadius:6, fontSize:12,
            }}>
              <div style={{ flex:1, minWidth:0 }}>
                <div style={{ fontWeight:500, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{inv.email}</div>
                <div style={{ fontSize:10, color:'var(--text-muted)', marginTop:1 }}>
                  {new Date(inv.created_at).toLocaleString('en-IN', { dateStyle:'short', timeStyle:'short' })}
                </div>
              </div>
              <span style={{ color: STATUS_COLOR[inv.status] ?? 'var(--text-muted)', fontWeight:600, fontSize:11, textTransform:'uppercase', flexShrink:0 }}>
                {inv.status}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Invite Form ───────────────────────────────────────────────────────────────
function InviteForm({ onSent }: { onSent?: () => void }) {
  const [email,   setEmail]   = useState('')
  const [loading, setLoading] = useState(false)
  const [result,  setResult]  = useState<InviteResult | null>(null)
  const [copied,  setCopied]  = useState(false)

  async function handleInvite(e: FormEvent) {
    e.preventDefault()
    if (!email.trim()) return
    setLoading(true); setResult(null)
    try {
      const { data } = await apiClient.post<InviteResult>('/admin/users/invite', { email: email.trim() })
      setResult(data)
      toast.success(`Invite sent for ${data.email}`)
      setEmail('')
      onSent?.()
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

  return (
    <div className="settings-section" style={{ height:'fit-content' }}>
      <div className="settings-title" style={{ display:'flex', alignItems:'center', gap:8 }}>
        <UserPlus size={16}/> Invite New User
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
        <div className="invite-result">
          <div className="text-sm" style={{ color:'var(--green)', fontWeight:600 }}>
            ✓ Invite ready · expires {new Date(result.expires_at + 'Z').toLocaleString('en-IN', { dateStyle:'short', timeStyle:'short' })}
          </div>
          <div className="invite-url">{result.registration_url}</div>
          <button className="copy-btn" onClick={handleCopy}>
            {copied ? <><Check size={12}/> Copied!</> : <><Copy size={12}/> Copy Link</>}
          </button>
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
  const [inviteRefreshKey, setInviteRefreshKey] = useState(0)
  const [openBrowser, setOpenBrowser] = useState<'db'|'redis'|'ml'|null>(null)

  useEffect(() => {
    apiClient.get('/health').then(r => {
      setHealth({ db: r.data.db === 'ok' ? 'ok' : 'err', redis: r.data.redis === 'ok' ? 'ok' : 'err' })
    }).catch(() => setHealth({ db:'err', redis:'err' }))
  }, [])

  return (
    <div className="admin-page">
      <div className="section-header">
        <div>
          <h2 className="section-title">Pipeline &amp; Admin</h2>
          <p className="text-muted text-sm" style={{ marginTop:4 }}>System status · User management · ML pipeline</p>
        </div>
        <a href="http://localhost:5555" target="_blank" rel="noreferrer"
          className="btn btn-outline"
          style={{ display:'flex', alignItems:'center', gap:6, fontSize:12, textDecoration:'none' }}>
          <ExternalLink size={12}/> Flower
        </a>
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
      <div className="admin-split">
        <InviteForm onSent={() => setInviteRefreshKey(k => k + 1)} />
        <InviteHistory refreshKey={inviteRefreshKey}/>
      </div>

      {/* Unified pipeline: runbook + actions + status in one */}
      <PipelinePanel/>
    </div>
  )
}
