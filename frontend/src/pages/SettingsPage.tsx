import { useState, useEffect } from 'react'
import { User, Shield, Bell, Sliders, Link, Trash2, ExternalLink } from 'lucide-react'
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
interface UpstoxCreds { api_key: string; api_secret: string }

export default function SettingsPage() {
  const user = useAuthStore(s => s.user)
  const setPreferredBroker = useAuthStore(s => s.setPreferredBroker)
  const markBrokerUpdated  = useAuthStore(s => s.markBrokerUpdated)
  const setTradingMode    = useAuthStore(s => s.setTradingMode)
  const clearAuth         = useAuthStore(s => s.clearAuth)

  const navigate           = useNavigate()
  const [loaded, setLoaded]           = useState(false)
  const [saving, setSaving]           = useState(false)
  const [mode, setMode]               = useState<'paper'|'live'>('paper')
  const [maxPosPct, setMaxPosPct]     = useState(10)
  const [dailyLoss, setDailyLoss]     = useState(5)
  const [notifSig, setNotifSig]       = useState(true)
  const [notifOrders, setNotifOrders] = useState(true)
  const [notifNews, setNotifNews]     = useState(true)

  // Broker credential forms — both shown simultaneously
  const [angelForm, setAngelForm]     = useState<BrokerCreds>({ client_id:'', api_key:'', api_secret:'', totp_secret:'' })
  const [upstoxForm, setUpstoxForm]   = useState<UpstoxCreds>({ api_key:'', api_secret:'' })
  const [angelPoolEligible, setAngelPoolEligible]   = useState(false)
  const [upstoxPoolEligible, setUpstoxPoolEligible] = useState(false)
  const [savingAngel, setSavingAngel]               = useState(false)
  const [savingUpstox, setSavingUpstox]             = useState(false)
  const [angelConnected, setAngelConnected]         = useState(false)
  const [upstoxConnected, setUpstoxConnected]       = useState(false)
  const [connectingUpstox, setConnectingUpstox]     = useState(false)
  const [upstoxRedirectUri, setUpstoxRedirectUri]   = useState('http://localhost:8000/api/v1/broker-credentials/upstox/callback')

  // Settings state — loaded from API

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
      setLoaded(true)
    }).catch(() => setLoaded(true))
  }, [])

  useEffect(() => {
    apiClient.get('/broker-credentials/angel_one')
      .then(r => { setAngelPoolEligible(Boolean(r.data?.pool_eligible)); setAngelConnected(Boolean(r.data?.is_configured)) })
      .catch(() => {})
  }, [])

  useEffect(() => {
    apiClient.get('/broker-credentials/upstox')
      .then(r => { setUpstoxPoolEligible(Boolean(r.data?.pool_eligible)); setUpstoxConnected(Boolean(r.data?.is_configured)) })
      .catch(() => {})
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
        notification_news: notifNews,
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

  async function saveAngelCreds() {
    setSavingAngel(true)
    try {
      await apiClient.put('/broker-credentials/angel_one', {
        ...angelForm,
        pool_eligible: angelPoolEligible,
      })
      setAngelConnected(true)
      setPreferredBroker('angel_one')
      await apiClient.patch('/settings', { preferred_broker: 'angel_one' })
      markBrokerUpdated()
      toast.success('Angel One credentials saved. Live prices are now active.')
      setAngelForm({ client_id:'', api_key:'', api_secret:'', totp_secret:'' })
    } catch {
      toast.error('Failed to save Angel One credentials.')
    } finally {
      setSavingAngel(false)
    }
  }

  async function saveUpstoxCreds() {
    setSavingUpstox(true)
    try {
      await apiClient.put('/broker-credentials/upstox', {
        ...upstoxForm,
        pool_eligible: upstoxPoolEligible,
      })
      toast.success('Upstox credentials saved. Opening Upstox login…')
      setUpstoxForm({ api_key:'', api_secret:'' })
      // Automatically trigger OAuth — open Upstox login in new tab
      await triggerUpstoxOAuth()
      markBrokerUpdated()
    } catch {
      toast.error('Failed to save Upstox credentials.')
    } finally {
      setSavingUpstox(false)
    }
  }

  async function triggerUpstoxOAuth() {
    setConnectingUpstox(true)
    try {
      const r = await apiClient.get('/broker-credentials/upstox/authorize')
      const url: string = r.data?.authorization_url
      if (r.data?.redirect_uri) setUpstoxRedirectUri(r.data.redirect_uri)
      if (url) {
        window.open(url, '_blank', 'noopener,noreferrer')
        toast.success('Upstox login opened in a new tab. After authorising, come back — live prices will activate.')
      }
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(msg ?? 'Could not get Upstox authorization URL. Save your App Key & Secret first.')
    } finally {
      setConnectingUpstox(false)
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

      {/* ── 2-column layout: Account + Notifications (left) | Trading + Risk + Broker (right) ── */}
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:24, alignItems:'start' }}>

        {/* ── LEFT: Account + Notifications ─────────────────────────────────── */}
        <div style={{ display:'flex', flexDirection:'column', gap:24 }}>
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
              <div style={{ fontSize:11, color:'var(--text-secondary)', marginTop:2 }}>
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
        </div>{/* end Account card */}

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

        </div>{/* end left column */}

        {/* ── RIGHT: Trading Mode + Risk + Broker ───────────────────────────── */}
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

          {/* Broker Credentials */}
          <div className="settings-section">
            <div className="settings-title" style={{ display:'flex', alignItems:'center', gap:8 }}>
              <Link size={15} /> Broker Credentials
            </div>
            <div style={{ fontSize:12, color:'var(--text-muted)', marginBottom:16 }}>
              Credentials are stored encrypted. Select the active broker from the header.
            </div>

            {/* Angel One */}
            <div style={{ marginBottom:20 }}>
              <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:12 }}>
                <span style={{ background:'rgba(59,130,246,0.15)', color:'#3b82f6', borderRadius:6, padding:'2px 10px', fontSize:12, fontWeight:600 }}>Angel One</span>
                <span style={{ fontSize:11, display:'flex', alignItems:'center', gap:4 }}>
                  <span style={{ width:7, height:7, borderRadius:'50%', background: angelConnected ? 'var(--green)' : 'var(--text-muted)', display:'inline-block' }} />
                  {angelConnected ? 'Connected — live prices active' : 'Not connected'}
                </span>
              </div>
              <div style={{ display:'flex', flexDirection:'column', gap:10 }}>
                {(['client_id','api_key','api_secret','totp_secret'] as (keyof BrokerCreds)[]).map(field => {
                  const label = field === 'api_secret' ? 'MPIN (4-digit login PIN)'
                    : field === 'totp_secret' ? 'TOTP Secret (from authenticator app)'
                    : field.replace(/_/g,' ').toUpperCase()
                  const ph = field === 'api_secret' ? 'Your 4-digit Angel One MPIN' : 'Paste value here…'
                  return (
                    <div key={field} className="form-group" style={{ margin:0 }}>
                      <label style={{ fontSize:12, color:'var(--text-muted)', marginBottom:4, display:'block' }}>{label}</label>
                      <input type="password" autoComplete="off" placeholder={ph}
                        value={angelForm[field]}
                        onChange={e => setAngelForm(f => ({ ...f, [field]: e.target.value }))}
                      />
                    </div>
                  )
                })}
                <div className="settings-row" style={{ marginTop:4 }}>
                  <div>
                    <div className="settings-row-label">Contribute to shared data pool</div>
                    <div className="settings-row-sub">Allow your session to help fetch shared market quotes.</div>
                  </div>
                  <Toggle checked={angelPoolEligible} onChange={setAngelPoolEligible} />
                </div>
                <div style={{ display:'flex', justifyContent:'flex-end', gap:8 }}>
                  <button className="btn btn-green" style={{ fontSize:13 }} onClick={saveAngelCreds} disabled={savingAngel}>
                    {savingAngel ? 'Saving…' : angelConnected ? 'Update Credentials' : 'Save & Connect'}
                  </button>
                </div>
              </div>
            </div>

            <div style={{ borderTop:'1px solid var(--border)', margin:'4px 0 20px' }} />

            {/* Upstox */}
            <div>
              <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:12 }}>
                <span style={{ background:'rgba(139,92,246,0.15)', color:'#8b5cf6', borderRadius:6, padding:'2px 10px', fontSize:12, fontWeight:600 }}>Upstox</span>
                <span style={{ fontSize:11, display:'flex', alignItems:'center', gap:4 }}>
                  <span style={{ width:7, height:7, borderRadius:'50%', background: upstoxConnected ? 'var(--green)' : 'var(--text-muted)', display:'inline-block' }} />
                  {upstoxConnected ? 'Authorised — live prices active' : 'Not authorised'}
                </span>
              </div>
              <div style={{ fontSize:12, color:'var(--text-secondary)', marginBottom:10, padding:'10px 12px', background:'var(--bg-hover)', borderRadius:8, lineHeight:1.6 }}>
                <div style={{ marginBottom:6 }}>Upstox uses OAuth. You must register this exact <strong>Redirect URL</strong> in your <a href="https://developer.upstox.com/apps" target="_blank" rel="noopener noreferrer" style={{ color:'var(--blue)' }}>Upstox developer app</a>:</div>
                <div style={{ fontFamily:'var(--font-mono)', fontSize:11, background:'var(--bg-card)', border:'1px solid var(--border)', borderRadius:6, padding:'6px 10px', color:'var(--green)', wordBreak:'break-all', userSelect:'all' }}>
                  {upstoxRedirectUri}
                </div>
                <div style={{ marginTop:6, fontSize:11, color:'var(--text-muted)' }}>Copy the URL above → Upstox developer portal → Your App → Edit → Redirect URL → Save.</div>
              </div>
              <div style={{ display:'flex', flexDirection:'column', gap:10 }}>
                {(['api_key','api_secret'] as (keyof UpstoxCreds)[]).map(field => (
                  <div key={field} className="form-group" style={{ margin:0 }}>
                    <label style={{ fontSize:12, color:'var(--text-muted)', marginBottom:4, display:'block' }}>
                      {field === 'api_key' ? 'App Key (API Key)' : 'App Secret (API Secret)'}
                    </label>
                    <input type="password" autoComplete="off" placeholder="Paste value here…"
                      value={upstoxForm[field]}
                      onChange={e => setUpstoxForm(f => ({ ...f, [field]: e.target.value }))}
                    />
                  </div>
                ))}
                <div className="settings-row" style={{ marginTop:4 }}>
                  <div>
                    <div className="settings-row-label">Contribute to shared data pool</div>
                    <div className="settings-row-sub">Allow your session to help fetch shared market quotes.</div>
                  </div>
                  <Toggle checked={upstoxPoolEligible} onChange={setUpstoxPoolEligible} />
                </div>
                <div style={{ display:'flex', justifyContent:'flex-end', gap:8 }}>
                  {upstoxConnected && (
                    <button className="btn btn-outline" style={{ fontSize:13, display:'flex', alignItems:'center', gap:6 }}
                      onClick={triggerUpstoxOAuth} disabled={connectingUpstox}>
                      <ExternalLink size={13}/>
                      {connectingUpstox ? 'Opening…' : 'Reconnect Upstox'}
                    </button>
                  )}
                  <button className="btn btn-green" style={{ fontSize:13 }} onClick={saveUpstoxCreds} disabled={savingUpstox}>
                    {savingUpstox ? 'Saving & opening login…' : upstoxConnected ? 'Update & Reconnect' : 'Save & Connect Upstox →'}
                  </button>
                </div>
              </div>
            </div>
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

