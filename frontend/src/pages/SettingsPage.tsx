import { useState, useEffect } from 'react'
import { User, Shield, Bell, Sliders, Link } from 'lucide-react'
import toast from 'react-hot-toast'
import { useAuthStore } from '../store/authStore'
import { apiClient } from '../api/client'

function Toggle({ checked, onChange }: { checked: boolean, onChange: (v: boolean) => void }) {
  return (
    <label className="toggle">
      <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} />
      <span className="toggle-slider" />
    </label>
  )
}

interface BrokerCreds { client_id: string; api_key: string; api_secret: string; totp_secret: string }

export default function SettingsPage() {
  const user = useAuthStore(s => s.user)

  // Settings state — loaded from API
  const [loaded, setLoaded]           = useState(false)
  const [saving, setSaving]           = useState(false)
  const [mode, setMode]               = useState<'paper'|'live'>('paper')
  const [maxPosPct, setMaxPosPct]     = useState(10)
  const [dailyLoss, setDailyLoss]     = useState(5)
  const [notifSig, setNotifSig]       = useState(true)
  const [notifOrders, setNotifOrders] = useState(true)
  const [broker, setBroker]           = useState('yfinance')

  // Broker credential form
  const [brokerForm, setBrokerForm]   = useState<BrokerCreds>({ client_id:'', api_key:'', api_secret:'', totp_secret:'' })
  const [savingCreds, setSavingCreds] = useState(false)

  useEffect(() => {
    apiClient.get('/settings').then(r => {
      const d = r.data
      setMode(d.trading_mode)
      setMaxPosPct(d.max_position_pct)
      setDailyLoss(d.daily_loss_limit_pct)
      setNotifSig(d.notification_signals)
      setNotifOrders(d.notification_orders)
      setBroker(d.preferred_broker ?? 'yfinance')
      setLoaded(true)
    }).catch(() => setLoaded(true))
  }, [])

  async function saveSettings() {
    setSaving(true)
    try {
      await apiClient.patch('/settings', {
        trading_mode: mode,
        max_position_pct: maxPosPct,
        daily_loss_limit_pct: dailyLoss,
        notification_signals: notifSig,
        notification_orders: notifOrders,
        preferred_broker: broker,
      })
      toast.success('Settings saved.')
    } catch {
      toast.error('Failed to save settings.')
    } finally {
      setSaving(false)
    }
  }

  async function saveBrokerCreds() {
    if (broker === 'yfinance') return
    setSavingCreds(true)
    try {
      await apiClient.put(`/broker-credentials/${broker}`, brokerForm)
      toast.success('Credentials saved.')
      setBrokerForm({ client_id:'', api_key:'', api_secret:'', totp_secret:'' })
    } catch {
      toast.error('Failed to save credentials.')
    } finally {
      setSavingCreds(false)
    }
  }

  return (
    <div className="settings-page">
      <div className="section-header" style={{ marginBottom:0 }}>
        <div>
          <h2 className="section-title">Settings</h2>
          <p className="text-muted text-sm" style={{ marginTop:4 }}>Manage your account, risk and notification preferences</p>
        </div>
      </div>

      {/* ── Account ─────────────────────────────────────────────────────────── */}
      <div className="settings-section">
        <div className="settings-title" style={{ display:'flex', alignItems:'center', gap:8 }}>
          <User size={15} /> Account
        </div>
        <div className="settings-row">
          <div>
            <div className="settings-row-label">Full Name</div>
            <div className="settings-row-sub">Display name across the platform</div>
          </div>
          <div className="text-mono text-sm" style={{ color:'var(--text-secondary)' }}>
            {user?.full_name ?? '—'}
          </div>
        </div>
        <div className="settings-row">
          <div>
            <div className="settings-row-label">Email</div>
            <div className="settings-row-sub">Login email · cannot be changed here</div>
          </div>
          <div className="text-mono text-sm" style={{ color:'var(--text-secondary)' }}>
            {user?.email ?? '—'}
          </div>
        </div>
        <div className="settings-row">
          <div>
            <div className="settings-row-label">Role</div>
            <div className="settings-row-sub">Access level</div>
          </div>
          <span className={`signal-badge ${user?.role === 'admin' ? 'BUY' : 'HOLD'}`}
            style={{ textTransform:'capitalize' }}>
            {user?.role ?? '—'}
          </span>
        </div>
        <div className="settings-row">
          <div>
            <div className="settings-row-label">Two-Factor Auth (TOTP)</div>
            <div className="settings-row-sub">Authenticator app — setup available in Phase 2</div>
          </div>
          <span className={`risk-badge ${user?.is_totp_configured ? 'low' : 'high'}`}>
            {user?.is_totp_configured ? 'ENABLED' : 'DISABLED'}
          </span>
        </div>
      </div>

      {/* ── Trading Mode ─────────────────────────────────────────────────────── */}
      <div className="settings-section">
        <div className="settings-title" style={{ display:'flex', alignItems:'center', gap:8 }}>
          <Shield size={15} /> Trading Mode
        </div>
        <div className="settings-row">
          <div>
            <div className="settings-row-label">Active Mode</div>
            <div className="settings-row-sub">
              {mode === 'paper'
                ? 'Simulated trades with ₹10,00,000 virtual capital. Safe to experiment.'
                : 'Real capital at risk. Ensure you understand the risks involved.'}
            </div>
          </div>
          <div className="mode-switch">
            <button className={`mode-btn ${mode==='paper' ? 'active paper' : ''}`} onClick={() => setMode('paper')}>
              Paper
            </button>
            <button className={`mode-btn ${mode==='live' ? 'active live' : ''}`} onClick={() => setMode('live')}>
              Live
            </button>
          </div>
        </div>
        {mode === 'live' && (
          <div style={{
            marginTop:12, padding:'12px 16px', background:'var(--red-dim)',
            border:'1px solid var(--red)', borderRadius:10, fontSize:13, color:'var(--red)',
          }}>
            ⚠ Live trading connects to a real broker API. Losses are real. This feature requires broker integration (Phase 2).
          </div>
        )}
      </div>

      {/* ── Risk Parameters ───────────────────────────────────────────────────── */}
      <div className="settings-section">
        <div className="settings-title" style={{ display:'flex', alignItems:'center', gap:8 }}>
          <Sliders size={15} /> Risk Parameters
        </div>
        <div className="settings-row">
          <div>
            <div className="settings-row-label">Max Position Size</div>
            <div className="settings-row-sub">Maximum % of capital in a single trade</div>
          </div>
          <div style={{ display:'flex', alignItems:'center', gap:12 }}>
            <input type="range" min={2} max={25} step={1} value={maxPosPct}
              onChange={e => setMaxPosPct(+e.target.value)} />
            <span className="text-mono" style={{ minWidth:36, textAlign:'right' }}>{maxPosPct}%</span>
          </div>
        </div>
        <div className="settings-row">
          <div>
            <div className="settings-row-label">Daily Loss Limit</div>
            <div className="settings-row-sub">Auto-halt trading if daily P&L drops below this</div>
          </div>
          <div style={{ display:'flex', alignItems:'center', gap:12 }}>
            <input type="range" min={1} max={15} step={0.5} value={dailyLoss}
              onChange={e => setDailyLoss(+e.target.value)} />
            <span className="text-mono" style={{ minWidth:36, textAlign:'right' }}>{dailyLoss}%</span>
          </div>
        </div>
        <div style={{ marginTop:16, display:'flex', justifyContent:'flex-end' }}>
          <button className="btn btn-green" style={{ fontSize:13 }} onClick={saveSettings} disabled={saving || !loaded}>
            {saving ? 'Saving…' : 'Save Settings'}
          </button>
        </div>
      </div>

      {/* ── Notifications ────────────────────────────────────────────────────── */}
      <div className="settings-section">
        <div className="settings-title" style={{ display:'flex', alignItems:'center', gap:8 }}>
          <Bell size={15} /> Notifications
        </div>
        <div className="settings-row">
          <div>
            <div className="settings-row-label">Signal Alerts</div>
            <div className="settings-row-sub">Notify when a new BUY/SELL signal is generated</div>
          </div>
          <Toggle checked={notifSig} onChange={setNotifSig} />
        </div>
        <div className="settings-row">
          <div>
            <div className="settings-row-label">Order Updates</div>
            <div className="settings-row-sub">Notify on order fill, partial, or reject</div>
          </div>
          <Toggle checked={notifOrders} onChange={setNotifOrders} />
        </div>
        <div className="settings-row">
          <div>
            <div className="settings-row-label">News &amp; Market Events</div>
            <div className="settings-row-sub">Breaking news that may affect your positions</div>
          </div>
          <span className="text-muted text-sm">Phase 3</span>
        </div>
      </div>

      {/* ── Broker ──────────────────────────────────────────────────────────── */}
      <div className="settings-section">
        <div className="settings-title" style={{ display:'flex', alignItems:'center', gap:8 }}>
          <Link size={15} /> Data Source &amp; Broker
        </div>
        <div className="settings-row">
          <div>
            <div className="settings-row-label">Data Source</div>
            <div className="settings-row-sub">yfinance = free 15-min delayed. Angel One / Upstox = real-time (requires credentials below).</div>
          </div>
          <select value={broker} onChange={e => { setBroker(e.target.value); saveSettings() }}
            style={{ background:'var(--bg-card)', color:'var(--text-primary)', border:'1px solid var(--border)', borderRadius:8, padding:'6px 12px', fontSize:13 }}>
            <option value="yfinance">yfinance (free, delayed)</option>
            <option value="angel_one">Angel One</option>
            <option value="upstox">Upstox</option>
          </select>
        </div>

        {broker !== 'yfinance' && (
          <div style={{ marginTop:16, display:'flex', flexDirection:'column', gap:10 }}>
            <div className="text-sm text-muted">Enter your {broker === 'angel_one' ? 'Angel One' : 'Upstox'} API credentials. They are stored encrypted on the server.</div>
            {(['client_id','api_key','api_secret', ...(broker === 'angel_one' ? ['totp_secret'] : [])] as (keyof BrokerCreds)[]).map(field => (
              <div key={field} className="form-group" style={{ margin:0 }}>
                <label style={{ fontSize:12, color:'var(--text-muted)', marginBottom:4, display:'block' }}>
                  {field.replace(/_/g,' ').toUpperCase()}
                </label>
                <input type="password" autoComplete="off"
                  placeholder="Paste value here…"
                  value={brokerForm[field]}
                  onChange={e => setBrokerForm(f => ({ ...f, [field]: e.target.value }))}
                />
              </div>
            ))}
            <div style={{ display:'flex', justifyContent:'flex-end', marginTop:4 }}>
              <button className="btn btn-green" style={{ fontSize:13 }} onClick={saveBrokerCreds} disabled={savingCreds}>
                {savingCreds ? 'Saving…' : 'Save Credentials'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

