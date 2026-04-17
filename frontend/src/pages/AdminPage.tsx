import { useState, FormEvent, useEffect, useRef } from 'react'
import { Database, Server, Cpu, UserPlus, Copy, Check, Users, Play, RefreshCw, Activity } from 'lucide-react'
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

// ── Backfill Progress Bar ─────────────────────────────────────────────────────
function BackfillCard() {
  const [progress, setProgress]   = useState<BackfillProgress | null>(null)
  const [loading,  setLoading]    = useState(false)
  const [period,   setPeriod]     = useState<'1y'|'2y'|'5y'>('2y')
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stopPoll = () => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
  }

  const pollProgress = async () => {
    try {
      const { data } = await apiClient.get<BackfillProgress>('/admin/pipeline/backfill/progress')
      setProgress(data)
      if (data.status !== 'running') stopPoll()
    } catch {
      stopPoll()
    }
  }

  useEffect(() => {
    pollProgress()
    return stopPoll
  }, [])

  const startBackfill = async () => {
    setLoading(true)
    try {
      const { data } = await apiClient.post<TaskResult>('/admin/pipeline/backfill', { period, force: false })
      toast.success(data.message)
      // start polling
      pollRef.current = setInterval(pollProgress, 2000)
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Failed to start backfill.'
      toast.error(msg)
    } finally {
      setLoading(false)
    }
  }

  const triggerEod = async () => {
    try {
      const { data } = await apiClient.post<TaskResult>('/admin/pipeline/eod-ingest')
      toast.success(data.message)
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Failed to trigger EOD ingest.'
      toast.error(msg)
    }
  }

  const triggerSignals = async () => {
    try {
      const { data } = await apiClient.post<TaskResult>('/admin/pipeline/generate-signals')
      toast.success(data.message)
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Failed to trigger signal generation.'
      toast.error(msg)
    }
  }

  const isRunning = progress?.status === 'running'
  const pct       = progress?.pct ?? 0
  const statusColor = progress?.status === 'done'    ? 'var(--green)'
                    : progress?.status === 'error'   ? 'var(--red)'
                    : progress?.status === 'running' ? 'var(--blue)'
                    : 'var(--text-muted)'

  return (
    <div className="settings-section">
      <div className="settings-title" style={{ display:'flex', alignItems:'center', gap:8 }}>
        <Activity size={15}/> Data Pipeline Controls
      </div>

      {/* Backfill */}
      <div style={{ marginBottom:20 }}>
        <div style={{ fontWeight:600, fontSize:13, marginBottom:8 }}>Historical Backfill (OHLCV)</div>
        <div style={{ display:'flex', alignItems:'center', gap:10, flexWrap:'wrap' }}>
          <select
            value={period}
            onChange={e => setPeriod(e.target.value as '1y'|'2y'|'5y')}
            style={{
              background:'var(--bg-hover)', border:'1px solid var(--border)',
              borderRadius:6, padding:'6px 10px', color:'var(--text)', fontSize:13,
            }}
          >
            <option value="1y">1 Year</option>
            <option value="2y">2 Years</option>
            <option value="5y">5 Years</option>
          </select>
          <button
            className="btn btn-outline"
            onClick={startBackfill}
            disabled={loading || isRunning}
            style={{ display:'flex', alignItems:'center', gap:6 }}
          >
            <Play size={13}/> {isRunning ? 'Running…' : loading ? 'Enqueueing…' : 'Run Backfill'}
          </button>
        </div>

        {/* Progress bar */}
        {progress && progress.status !== 'idle' && (
          <div style={{ marginTop:12 }}>
            <div style={{
              background:'var(--bg-hover)', borderRadius:6, overflow:'hidden',
              height:8, marginBottom:6,
            }}>
              <div style={{
                width:`${pct}%`, height:'100%',
                background: progress.status === 'error' ? 'var(--red)' : 'var(--blue)',
                transition:'width 0.4s ease',
              }}/>
            </div>
            <div style={{ display:'flex', justifyContent:'space-between', fontSize:11, color:'var(--text-muted)' }}>
              <span style={{ color: statusColor }}>{progress.message}</span>
              <span>{pct}%</span>
            </div>
          </div>
        )}
      </div>

      {/* EOD Ingest + Signal Gen */}
      <div style={{ display:'flex', gap:12, flexWrap:'wrap' }}>
        <div style={{
          flex:1, minWidth:200, background:'var(--bg-hover)',
          border:'1px solid var(--border)', borderRadius:10, padding:'14px 16px',
        }}>
          <div style={{ fontSize:13, fontWeight:600, marginBottom:4 }}>EOD Data Ingest</div>
          <div style={{ fontSize:11, color:'var(--text-muted)', marginBottom:10 }}>
            Scheduled: Mon–Fri 4:30 PM IST
          </div>
          <button className="btn btn-outline" onClick={triggerEod}
            style={{ display:'flex', alignItems:'center', gap:6, fontSize:11, padding:'5px 12px' }}>
            <RefreshCw size={12}/> Run Now
          </button>
        </div>

        <div style={{
          flex:1, minWidth:200, background:'var(--bg-hover)',
          border:'1px solid var(--border)', borderRadius:10, padding:'14px 16px',
        }}>
          <div style={{ fontSize:13, fontWeight:600, marginBottom:4 }}>Signal Generation</div>
          <div style={{ fontSize:11, color:'var(--text-muted)', marginBottom:10 }}>
            Scheduled: Mon–Fri 4:45 PM IST · RSI / MACD / Bollinger
          </div>
          <button className="btn btn-outline" onClick={triggerSignals}
            style={{ display:'flex', alignItems:'center', gap:6, fontSize:11, padding:'5px 12px' }}>
            <Play size={12}/> Run Now
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Status Card ───────────────────────────────────────────────────────────────
function StatusCard({ icon, label, status, detail }:
  { icon: React.ReactNode, label: string, status: 'ok'|'warn'|'err', detail: string }) {
  const COLOR = { ok: 'var(--green)', warn: 'var(--yellow)', err: 'var(--red)' }
  const TEXT  = { ok: 'Healthy', warn: 'Degraded', err: 'Offline' }
  return (
    <div className="status-card">
      <div className={`status-pill ${status}`}>{icon}</div>
      <div>
        <div className="status-label">{label}</div>
        <div className="status-value" style={{ color: COLOR[status] }}>{TEXT[status]}</div>
        <div className="text-muted text-sm" style={{ marginTop:4 }}>{detail}</div>
      </div>
      <div style={{ marginLeft:'auto' }}>
        <span className="status-dot" style={{
          width:8, height:8, borderRadius:'50%', display:'block',
          background: COLOR[status],
          boxShadow: `0 0 8px ${COLOR[status]}`,
          animation: status === 'ok' ? 'pulse 2s infinite' : 'none',
        }} />
      </div>
    </div>
  )
}

// ── Invite Form ───────────────────────────────────────────────────────────────
function InviteForm() {
  const [email, setEmail]     = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult]   = useState<InviteResult | null>(null)
  const [copied, setCopied]   = useState(false)

  async function handleInvite(e: FormEvent) {
    e.preventDefault()
    if (!email.trim()) return
    setLoading(true)
    setResult(null)
    try {
      const { data } = await apiClient.post<InviteResult>('/admin/users/invite', { email: email.trim() })
      setResult(data)
      toast.success(`Invite sent for ${data.email}`)
      setEmail('')
    } catch (err: unknown) {
      const msg: string = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Invite failed.'
      toast.error(msg)
    } finally {
      setLoading(false)
    }
  }

  async function handleCopy() {
    if (!result) return
    await navigator.clipboard.writeText(result.registration_url)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="settings-section" style={{ height:'fit-content' }}>
      <div className="settings-title" style={{ display:'flex', alignItems:'center', gap:8 }}>
        <UserPlus size={16} /> Invite New User
      </div>
      <form onSubmit={handleInvite} style={{ display:'flex', flexDirection:'column', gap:14 }}>
        <div className="form-group">
          <label htmlFor="invite-email">Email Address</label>
          <input
            id="invite-email"
            type="email"
            value={email}
            onChange={e => setEmail(e.target.value)}
            placeholder="trader@example.com"
            required
            disabled={loading}
          />
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
          <p className="text-muted text-sm" style={{ marginTop:4 }}>System status · User management · ML pipeline controls</p>
        </div>
      </div>

      {/* System status */}
      <div>
        <div className="card-title" style={{ marginBottom:12 }}>System Status</div>
        <div className="status-grid">
          <StatusCard icon={<Database size={20}/>} label="Database" status={health.db === 'pending' ? 'warn' : health.db} detail="TimescaleDB · pg16" />
          <StatusCard icon={<Server size={20}/>}   label="Redis"    status={health.redis === 'pending' ? 'warn' : health.redis} detail="7.x · session store" />
          <StatusCard icon={<Cpu size={20}/>}       label="ML Models" status="warn" detail="Phase 3 — not loaded" />
        </div>
      </div>

      {/* Invite + invite log */}
      <div className="admin-split">
        <InviteForm />

        <div className="settings-section">
          <div className="settings-title" style={{ display:'flex', alignItems:'center', gap:8 }}>
            <Users size={15}/> Invite History
          </div>
          <div className="empty-state" style={{ padding:'32px 0' }}>
            <Users size={32} />
            <p>Invite history loads from the API in Phase 2.</p>
          </div>
        </div>
      </div>

      {/* Pipeline controls */}
      <BackfillCard />
    </div>
  )
}

