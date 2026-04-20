import { useState, useEffect } from 'react'
import { User, Shield, Bell, Sliders, Link, Trash2 } from 'lucide-react'
import toast from 'react-hot-toast'
import { useAuthStore } from '../store/authStore'
import { apiClient } from '../api/client'
import { useNavigate } from 'react-router-dom'

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
  const setTradingMode = useAuthStore(s => s.setTradingMode)
  const clearAuth = useAuthStore(s => s.clearAuth)
  const navigate = useNavigate()

  // Settings state — loaded from API
  const [loaded, setLoaded]           = useState(false)
  const [saving, setSaving]           = useState(false)
  const [mode, setMode]               = useState<'paper'|'live'>('paper')
  const [maxPosPct, setMaxPosPct]     = useState(10)
  const [dailyLoss, setDailyLoss]     = useState(5)
  const [notifSig, setNotifSig]       = useState(true)
  const [notifOrders, setNotifOrders] = useState(true)
  const [notifNews, setNotifNews]     = useState(true)
  const [broker, setBroker]           = useState('angel_one')

  // Broker credential form
  const [brokerForm, setBrokerForm]   = useState<BrokerCreds>({ client_id:'', api_key:'', api_secret:'', totp_secret:'' })
  const [poolEligible, setPoolEligible] = useState(false)
  const [savingCreds, setSavingCreds] = useState(false)

  // OTP gate state for live trading enablement
  const [showOtpModal, setShowOtpModal] = useState(false)
  const [otpCode, setOtpCode]           = useState('')
  const [otpSending, setOtpSending]     = useState(false)
  const [otpVerifying, setOtpVerifying] = useState(false)

  // Delete account
  const [showDeleteModal, setShowDeleteModal] = useState(false)
  const [deletePassword, setDeletePassword]   = useState('')
  const [deleting, setDeleting]               = useState(false)

  useEffect(() => {
    apiClient.get('/settings').then(r => {
      const d = r.data
      setMode(d.trading_mode)
      setMaxPosPct(d.max_position_pct)
      setDailyLoss(d.daily_loss_limit_pct)
      setNotifSig(d.notification_signals)
      setNotifOrders(d.notification_orders)
      setNotifNews(d.notification_news ?? true)
      setBroker(d.preferred_broker ?? 'angel_one')
      setLoaded(true)
    }).catch(() => setLoaded(true))
  }, [])

  useEffect(() => {
    apiClient.get(`/broker-credentials/${broker}`)
      .then(r => setPoolEligible(Boolean(r.data?.pool_eligible)))
      .catch(() => setPoolEligible(false))
  }, [broker])

  async function saveSettings() {
    setSaving(true)
    try {
      await apiClient.patch('/settings', {
        trading_mode: mode,
        max_position_pct: maxPosPct,
        daily_loss_limit_pct: dailyLoss,
        notification_signals: notifSig,
        notification_orders: notifOrders,
        notification_news: notifNews,
        preferred_broker: broker,
      })
      setTradingMode(mode)
      toast.success('Settings saved.')
    } catch {
      toast.error('Failed to save settings.')
    } finally {
      setSaving(false)
    }
  }

  async function handleModeChange(newMode: 'paper' | 'live') {
    if (newMode === 'live') {
      // Require OTP verification before switching to live
      setOtpSending(true)
      try {
        await apiClient.post('/settings/live-trading/enable')
        setShowOtpModal(true)
        setOtpCode('')
        toast.success('OTP sent to your email.')
      } catch (e: unknown) {
        const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        if (msg?.includes('already enabled')) {
          // Already verified — just switch
          setMode('live')
          await apiClient.patch('/settings', { trading_mode: 'live' })
          setTradingMode('live')
        } else {
          toast.error(msg ?? 'Failed to send OTP.')
        }
      } finally {
        setOtpSending(false)
      }
      return
    }
    // Switching to paper — no verification needed
    setMode(newMode)
    try {
      await apiClient.patch('/settings', { trading_mode: newMode })
      setTradingMode(newMode)
    } catch {
      toast.error('Failed to save trading mode.')
      setMode(mode)
    }
  }

  async function handleOtpConfirm() {
    if (!otpCode || otpCode.length !== 6) { toast.error('Enter the 6-digit code.'); return }
    setOtpVerifying(true)
    try {
      await apiClient.post('/settings/live-trading/confirm', { code: otpCode })
      setMode('live')
      await apiClient.patch('/settings', { trading_mode: 'live' })
      setTradingMode('live')
      setShowOtpModal(false)
      toast.success('Live trading enabled!')
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(msg ?? 'Invalid OTP.')
    } finally {
      setOtpVerifying(false)
    }
  }

  async function handleBrokerChange(newBroker: string) {
    setBroker(newBroker)
    try {
      await apiClient.patch('/settings', { preferred_broker: newBroker })
    } catch {
      toast.error('Failed to save data source.')
    }
  }

  async function saveBrokerCreds() {
    setSavingCreds(true)
    try {
      await apiClient.put(`/broker-credentials/${broker}`, {
        ...brokerForm,
        pool_eligible: poolEligible,
      })
      toast.success('Credentials saved.')
      setBrokerForm({ client_id:'', api_key:'', api_secret:'', totp_secret:'' })
    } catch {
      toast.error('Failed to save credentials.')
    } finally {
      setSavingCreds(false)
    }
  }

  async function deleteAccount() {
    if (!deletePassword) { toast.error('Enter your password.'); return }
    setDeleting(true)
    try {
      await apiClient.delete('/auth/me', { data: { password: deletePassword } })
      toast.success('Account deleted.')
      clearAuth()
      navigate('/login', { replace: true })
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Failed to delete account.'
      toast.error(msg)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="settings-page">
      <div className="page-header">
        <div className="page-header-left">
          <div className="page-header-title">Settings</div>
          <div className="page-header-sub">Manage your account, risk and notification preferences</div>
        </div>
      </div>

      {/* ── 2-column layout: Account (left) | Settings (right) ───────────────── */}
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:24, alignItems:'start' }}>

        {/* ── LEFT: Account ─────────────────────────────────────────────────── */}
        <div className="settings-section">
          <div className="settings-title" style={{ display:'flex', alignItems:'center', gap:8 }}>
            <User size={15} /> Account
          </div>

          {/* Profile info grid — 2 cards per row */}
          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:12, marginBottom:16 }}>
            {[
              { label:'Full Name',  value: user?.full_name ?? '—' },
              { label:'Email',      value: user?.email ?? '—' },
              { label:'Role',       value: user?.role ?? '—', chip: true, chipClass: user?.role === 'admin' ? 'BUY' : 'HOLD' },
              { label:'2FA (TOTP)', value: user?.is_totp_configured ? 'Enabled' : 'Disabled', riskChip: true, riskClass: user?.is_totp_configured ? 'low' : 'high' },
            ].map(item => (
              <div key={item.label} style={{
                background:'var(--bg-hover)', borderRadius:8, padding:'12px 16px',
                border:'1px solid var(--border)',
              }}>
                <div style={{ fontSize:10, color:'var(--text-muted)', marginBottom:6, textTransform:'uppercase', letterSpacing:'0.05em' }}>
                  {item.label}
                </div>
                {item.chip ? (
                  <span className={`signal-badge ${item.chipClass}`} style={{ textTransform:'capitalize', fontSize:12 }}>
                    {item.value}
                  </span>
                ) : item.riskChip ? (
                  <span className={`risk-badge ${item.riskClass}`} style={{ fontSize:12 }}>
                    {item.value}
                  </span>
                ) : (
                  <div className="text-mono" style={{ fontSize:13, color:'var(--text-primary)', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>
                    {item.value}
                  </div>
                )}
              </div>
            ))}
          </div>

          {/* Danger zone */}
          <div style={{
            padding:'12px 16px',
            background:'var(--red-dim, rgba(239,68,68,0.08))',
            border:'1px solid var(--red)', borderRadius:10,
            display:'flex', alignItems:'center', justifyContent:'space-between', gap:12,
          }}>
            <div>
              <div style={{ fontWeight:600, fontSize:13, color:'var(--red)' }}>Delete Account</div>
              <div style={{ fontSize:11, color:'var(--text-muted)', marginTop:2 }}>
                Permanently removes your account and all associated data. This cannot be undone.
              </div>
            </div>
            <button
              className="btn"
              onClick={() => setShowDeleteModal(true)}
              style={{
                display:'flex', alignItems:'center', gap:6, fontSize:12,
                background:'var(--red)', color:'#fff', border:'none', flexShrink:0,
              }}
            >
              <Trash2 size={13}/> Delete
            </button>
          </div>
        </div>

        {/* ── RIGHT: Trading Mode + Risk + Notifications + Broker ──────────── */}
        <div style={{ display:'flex', flexDirection:'column', gap:24 }}>

          {/* Trading Mode */}
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
                <button className={`mode-btn ${mode==='paper' ? 'active paper' : ''}`} onClick={() => handleModeChange('paper')}>
                  Paper
                </button>
                <button className={`mode-btn ${mode==='live' ? 'active live' : ''}`} onClick={() => handleModeChange('live')}>
                  Live
                </button>
              </div>
            </div>
            {mode === 'live' && (
              <div style={{
                marginTop:12, padding:'12px 16px', background:'var(--red-dim)',
                border:'1px solid var(--red)', borderRadius:10, fontSize:13, color:'var(--red)',
              }}>
                ⚠ Live trading connects to a real broker API. Losses are real. Ensure your Angel One credentials are saved in the Broker section below.
              </div>
            )}
          </div>

          {/* Risk Parameters */}
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

          {/* Notifications */}
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
              <Toggle checked={notifNews} onChange={setNotifNews} />
            </div>
          </div>

          {/* Broker */}
          <div className="settings-section">
            <div className="settings-title" style={{ display:'flex', alignItems:'center', gap:8 }}>
              <Link size={15} /> Data Source &amp; Broker
            </div>
            <div className="settings-row">
              <div>
                <div className="settings-row-label">Data Source</div>
                <div className="settings-row-sub">Angel One / Upstox = real-time. No credentials configured = prices show as 0.00.</div>
              </div>
              <select value={broker} onChange={e => handleBrokerChange(e.target.value)}
                style={{ background:'var(--bg-card)', color:'var(--text-primary)', border:'1px solid var(--border)', borderRadius:8, padding:'6px 12px', fontSize:13 }}>
                <option value="angel_one">Angel One</option>
                <option value="upstox">Upstox</option>
              </select>
            </div>

            {(
              <div style={{ marginTop:16, display:'flex', flexDirection:'column', gap:10 }}>
                <div className="text-sm text-muted">Enter your {broker === 'angel_one' ? 'Angel One' : 'Upstox'} API credentials. They are stored encrypted on the server.</div>
                {(['client_id','api_key','api_secret', ...(broker === 'angel_one' ? ['totp_secret'] : [])] as (keyof BrokerCreds)[]).map(field => {
                  const label = broker === 'angel_one' && field === 'api_secret'
                    ? 'MPIN (4-digit login PIN)'
                    : broker === 'angel_one' && field === 'totp_secret'
                    ? 'TOTP Secret (from authenticator app)'
                    : field.replace(/_/g,' ').toUpperCase()
                  const ph = broker === 'angel_one' && field === 'api_secret'
                    ? 'Your 4-digit Angel One MPIN'
                    : 'Paste value here…'
                  return (
                    <div key={field} className="form-group" style={{ margin:0 }}>
                      <label style={{ fontSize:12, color:'var(--text-muted)', marginBottom:4, display:'block' }}>
                        {label}
                      </label>
                      <input type="password" autoComplete="off"
                        placeholder={ph}
                        value={brokerForm[field]}
                        onChange={e => setBrokerForm(f => ({ ...f, [field]: e.target.value }))}
                      />
                    </div>
                  )
                })}
                <div className="settings-row" style={{ marginTop: 6 }}>
                  <div>
                    <div className="settings-row-label">Contribute to shared data pool</div>
                    <div className="settings-row-sub">Allow your broker session to help fetch shared market quotes.</div>
                  </div>
                  <Toggle checked={poolEligible} onChange={setPoolEligible} />
                </div>
                <div style={{ display:'flex', justifyContent:'flex-end', marginTop:4 }}>
                  <button className="btn btn-green" style={{ fontSize:13 }} onClick={saveBrokerCreds} disabled={savingCreds}>
                    {savingCreds ? 'Saving…' : 'Save Credentials'}
                  </button>
                </div>
              </div>
            )}
          </div>

        </div>{/* end right column */}
      </div>{/* end 2-col grid */}

      {/* OTP verification modal for live trading */}
      {showOtpModal && (
        <div style={{
          position:'fixed', inset:0, background:'rgba(0,0,0,0.6)', zIndex:1000,
          display:'flex', alignItems:'center', justifyContent:'center',
        }} onClick={() => setShowOtpModal(false)}>
          <div style={{
            background:'var(--bg-card)', borderRadius:12, padding:28, width:360,
            border:'1px solid var(--border)', boxShadow:'0 20px 60px rgba(0,0,0,0.4)',
          }} onClick={e => e.stopPropagation()}>
            <div style={{ fontSize:20, marginBottom:8 }}>🔐</div>
            <div style={{ fontWeight:700, fontSize:16, marginBottom:6 }}>Enable Live Trading</div>
            <div style={{ fontSize:13, color:'var(--text-muted)', marginBottom:18 }}>
              A 6-digit code was sent to your email. Enter it below to enable live trading with real money.
            </div>
            <input
              type="text"
              placeholder="Enter 6-digit OTP"
              value={otpCode}
              onChange={e => setOtpCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
              onKeyDown={e => e.key === 'Enter' && handleOtpConfirm()}
              style={{ width:'100%', marginBottom:16, textAlign:'center', fontSize:18, letterSpacing:'0.3em' }}
              autoFocus
              maxLength={6}
            />
            <div style={{ display:'flex', gap:10, justifyContent:'flex-end' }}>
              <button className="btn btn-outline" onClick={() => setShowOtpModal(false)}>
                Cancel
              </button>
              <button
                className="btn"
                onClick={handleOtpConfirm}
                disabled={otpVerifying || otpCode.length !== 6}
                style={{ background:'var(--green)', color:'#fff', border:'none' }}
              >
                {otpVerifying ? 'Verifying…' : 'Confirm'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete account confirmation modal */}
      {showDeleteModal && (
        <div style={{
          position:'fixed', inset:0, background:'rgba(0,0,0,0.6)', zIndex:1000,
          display:'flex', alignItems:'center', justifyContent:'center',
        }} onClick={() => setShowDeleteModal(false)}>
          <div style={{
            background:'var(--bg-card)', borderRadius:12, padding:28, width:360,
            border:'1px solid var(--border)', boxShadow:'0 20px 60px rgba(0,0,0,0.4)',
          }} onClick={e => e.stopPropagation()}>
            <div style={{ fontSize:20, marginBottom:8 }}>⚠️</div>
            <div style={{ fontWeight:700, fontSize:16, marginBottom:6 }}>Delete your account?</div>
            <div style={{ fontSize:13, color:'var(--text-muted)', marginBottom:18 }}>
              This is permanent. Enter your password to confirm.
            </div>
            <input
              type="password"
              placeholder="Your password"
              value={deletePassword}
              onChange={e => setDeletePassword(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && deleteAccount()}
              style={{ width:'100%', marginBottom:16 }}
              autoFocus
            />
            <div style={{ display:'flex', gap:10, justifyContent:'flex-end' }}>
              <button className="btn btn-outline" onClick={() => { setShowDeleteModal(false); setDeletePassword('') }}>
                Cancel
              </button>
              <button
                className="btn"
                onClick={deleteAccount}
                disabled={deleting}
                style={{ background:'var(--red)', color:'#fff', border:'none' }}
              >
                {deleting ? 'Deleting…' : 'Delete Account'}
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  )
}

