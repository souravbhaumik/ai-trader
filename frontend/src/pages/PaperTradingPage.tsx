import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Wallet, TrendingUp, TrendingDown, Target, Activity,
  PlusCircle, XCircle, ChevronDown, ChevronUp, Clock, BookOpen, Zap,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { apiClient } from '../api/client'
import TickerLogo from '../components/TickerLogo'

// ── Types ──────────────────────────────────────────────────────────────────────

interface Summary {
  cash_balance: number
  open_positions: number
  open_value: number
  realized_pnl: number
  total_trades: number
  closed_trades: number
  win_rate: number | null
}

interface PaperTrade {
  id: string
  symbol: string
  direction: 'BUY' | 'SELL'
  qty: number
  entry_price: number
  target_price: number | null
  stop_loss: number | null
  exit_price: number | null
  signal_id: string | null
  status: string
  pnl: number | null
  pnl_pct: number | null
  entry_at: string
  exit_at: string | null
  notes: string | null
}

// ── Stat card ──────────────────────────────────────────────────────────────────

function StatCard({
  icon, label, value, sub, color,
}: {
  icon: React.ReactNode; label: string; value: string; sub: string; color?: 'green' | 'red'
}) {
  return (
    <div className="stat-card">
      <div className="stat-card-inner">
        <div>
          <div className="stat-icon">{icon}</div>
          <div className="stat-label">{label}</div>
          <div className="stat-value" style={color ? { color: `var(--${color})` } : undefined}>{value}</div>
          <div className="stat-sub text-muted">{sub}</div>
        </div>
      </div>
    </div>
  )
}

// ── Order form ─────────────────────────────────────────────────────────────────

function OrderForm({ onSuccess }: { onSuccess: () => void }) {
  const [symbol, setSymbol]           = useState('')
  const [symQuery, setSymQuery]       = useState('')
  const [showDrop, setShowDrop]       = useState(false)
  const [direction, setDirection]     = useState<'BUY' | 'SELL'>('BUY')
  const [qty, setQty]                 = useState(1)
  const [entryPrice, setEntryPrice]   = useState<string>('')
  const [targetPrice, setTargetPrice] = useState<string>('')
  const [stopLoss, setStopLoss]       = useState<string>('')
  const [notes, setNotes]             = useState('')

  interface UniverseResult { symbol: string; name: string; exchange: string }

  const { data: suggestions = [] } = useQuery<UniverseResult[]>({
    queryKey: ['universe-search', symQuery],
    queryFn: () => apiClient.get<UniverseResult[]>(`/screener/universe/search?q=${encodeURIComponent(symQuery)}`).then(r => r.data),
    enabled: symQuery.length >= 2,
    staleTime: 30_000,
  })

  const qc = useQueryClient()

  const mutation = useMutation({
    mutationFn: () =>
      apiClient.post('/portfolio/paper/orders', {
        symbol: symbol.trim().toUpperCase(),
        direction,
        qty,
        entry_price: parseFloat(entryPrice),
        target_price: targetPrice ? parseFloat(targetPrice) : null,
        stop_loss:    stopLoss    ? parseFloat(stopLoss)    : null,
        notes:        notes.trim() || null,
      }).then(r => r.data),
    onSuccess: () => {
      toast.success(`Paper ${direction} — ${qty} × ${symbol.toUpperCase()} opened`)
      qc.invalidateQueries({ queryKey: ['paper-summary'] })
      qc.invalidateQueries({ queryKey: ['paper-positions'] })
      setSymbol(''); setSymQuery(''); setQty(1); setEntryPrice(''); setTargetPrice(''); setStopLoss(''); setNotes('')
      onSuccess()
    },
    onError: (err: any) => {
      toast.error(err?.response?.data?.detail ?? 'Could not place order')
    },
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!symbol.trim() && !symQuery.trim()) { toast.error('Enter a symbol'); return }
    if (!symbol) { toast.error('Select a symbol from the dropdown'); return }
    if (!entryPrice)      { toast.error('Entry price is required'); return }
    if (isNaN(parseFloat(entryPrice)) || parseFloat(entryPrice) <= 0) {
      toast.error('Enter a valid entry price'); return
    }
    mutation.mutate()
  }

  return (
    <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>

      {/* Row 1 — Symbol + Direction */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <div style={{ position:'relative' }}>
          <label className="form-label">Symbol</label>
          <input
            className="form-input"
            placeholder="e.g. RELIANCE.NS"
            value={symQuery || symbol}
            onChange={e => {
              setSymQuery(e.target.value.toUpperCase())
              setSymbol('')
              setShowDrop(true)
            }}
            onFocus={() => setShowDrop(true)}
            onBlur={() => setTimeout(() => setShowDrop(false), 150)}
          />
          {showDrop && suggestions.length > 0 && (
            <div style={{
              position:'absolute', top:'100%', left:0, right:0, zIndex:100,
              background:'var(--bg-card)', border:'1px solid var(--border)',
              borderRadius:8, marginTop:2, maxHeight:200, overflowY:'auto',
              boxShadow:'0 4px 20px rgba(0,0,0,0.4)',
            }}>
              {suggestions.map(s => (
                <div
                  key={s.symbol}
                  onMouseDown={() => {
                    setSymbol(s.symbol)
                    setSymQuery(s.symbol)
                    setShowDrop(false)
                  }}
                  style={{ padding:'8px 12px', cursor:'pointer', fontSize:12,
                            borderBottom:'1px solid var(--border)' }}
                  className="hover-row"
                >
                  <span style={{ fontWeight:600, fontFamily:'monospace' }}>{s.symbol}</span>
                  <span style={{ color:'var(--text-muted)', marginLeft:8 }}>{s.name}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        <div>
          <label className="form-label">Direction</label>
          <select
            className="form-input"
            value={direction}
            onChange={e => setDirection(e.target.value as 'BUY' | 'SELL')}
          >
            <option value="BUY">BUY</option>
            <option value="SELL">SELL</option>
          </select>
        </div>
      </div>

      {/* Row 2 — Qty + Entry Price */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <div>
          <label className="form-label">Quantity (shares)</label>
          <input
            className="form-input"
            type="number"
            min={1}
            value={qty}
            onChange={e => setQty(Math.max(1, parseInt(e.target.value) || 1))}
          />
        </div>
        <div>
          <label className="form-label">Entry Price (₹) *</label>
          <input
            className="form-input"
            type="number"
            min={0.01}
            step={0.05}
            placeholder="Current market price"
            value={entryPrice}
            onChange={e => setEntryPrice(e.target.value)}
          />
        </div>
      </div>

      {/* Row 3 — Target + Stop Loss */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <div>
          <label className="form-label">Target Price (₹) <span className="text-muted">optional</span></label>
          <input
            className="form-input"
            type="number"
            min={0.01}
            step={0.05}
            placeholder="—"
            value={targetPrice}
            onChange={e => setTargetPrice(e.target.value)}
          />
        </div>
        <div>
          <label className="form-label">Stop Loss (₹) <span className="text-muted">optional</span></label>
          <input
            className="form-input"
            type="number"
            min={0.01}
            step={0.05}
            placeholder="—"
            value={stopLoss}
            onChange={e => setStopLoss(e.target.value)}
          />
        </div>
      </div>

      {/* Notes */}
      <div>
        <label className="form-label">Notes <span className="text-muted">optional</span></label>
        <input
          className="form-input"
          placeholder="Your trade thesis…"
          value={notes}
          onChange={e => setNotes(e.target.value)}
          maxLength={200}
        />
      </div>

      <button
        type="submit"
        className="btn"
        style={{
          background: direction === 'BUY' ? 'var(--green)' : 'var(--red)',
          color: '#fff',
          padding: '10px 20px',
          fontWeight: 600,
          marginTop: 4,
        }}
        disabled={mutation.isPending}
      >
        {mutation.isPending ? 'Opening…' : `Open ${direction} — ${qty} × ${symbol || '?'}`}
      </button>
    </form>
  )
}

// ── Direction badge ────────────────────────────────────────────────────────────

function DirBadge({ dir }: { dir: string }) {
  const bg = dir === 'BUY' ? 'var(--green-10)' : 'var(--red-10)'
  const fg = dir === 'BUY' ? 'var(--green)'    : 'var(--red)'
  return (
    <span style={{ fontSize: 11, padding: '2px 7px', borderRadius: 4, background: bg, color: fg, fontWeight: 700 }}>
      {dir}
    </span>
  )
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    open: '#3498db', closed: 'var(--text-muted)',
    sl_hit: 'var(--red)', target_hit: 'var(--green)',
  }
  const c = map[status] ?? '#888'
  return (
    <span style={{ fontSize: 11, padding: '2px 7px', borderRadius: 4, background: `${c}22`, color: c, fontWeight: 600 }}>
      {status.replace('_', ' ').toUpperCase()}
    </span>
  )
}

function fmt(v: number | null, prefix = '₹'): string {
  if (v == null) return '—'
  return prefix + v.toLocaleString('en-IN', { maximumFractionDigits: 2 })
}

function fmtDate(ts: string): string {
  return new Date(ts + (ts.endsWith('Z') ? '' : 'Z')).toLocaleString('en-IN', {
    dateStyle: 'short', timeStyle: 'short',
  })
}

// ── Close position button ──────────────────────────────────────────────────────

function CloseBtn({ trade }: { trade: PaperTrade }) {
  const qc = useQueryClient()
  const mutation = useMutation({
    mutationFn: () =>
      apiClient.post(`/portfolio/paper/orders/${trade.id}/close`, {}).then(r => r.data),
    onSuccess: (data: any) => {
      const pnl: number = data.pnl ?? 0
      toast.success(
        `${trade.symbol} closed — P&L: ${pnl >= 0 ? '+' : ''}₹${pnl.toFixed(2)}`,
        { duration: 5000 },
      )
      qc.invalidateQueries({ queryKey: ['paper-summary'] })
      qc.invalidateQueries({ queryKey: ['paper-positions'] })
      qc.invalidateQueries({ queryKey: ['paper-history'] })
    },
    onError: (err: any) => {
      toast.error(err?.response?.data?.detail ?? 'Could not close trade')
    },
  })

  return (
    <button
      className="btn-outline btn"
      style={{ padding: '3px 10px', fontSize: 12, color: 'var(--red)', borderColor: 'var(--red-10)' }}
      onClick={() => mutation.mutate()}
      disabled={mutation.isPending}
      title="Close at current market price"
    >
      <XCircle size={13} style={{ marginRight: 4 }} />
      {mutation.isPending ? '…' : 'Close'}
    </button>
  )
}

// ── History sort ───────────────────────────────────────────────────────────────

type SortDir = 'asc' | 'desc'

// ── Main page ──────────────────────────────────────────────────────────────────

export default function PaperTradingPage() {
  const [showForm, setShowForm] = useState(false)
  const [histSort, setHistSort] = useState<SortDir>('desc')
  const [tab, setTab]           = useState<'positions' | 'history'>('positions')

  const qc = useQueryClient()

  const { data: summary } = useQuery<Summary>({
    queryKey: ['paper-summary'],
    queryFn: () => apiClient.get('/portfolio/paper/summary').then(r => {
      const d = r.data
      return {
        ...d,
        cash_balance:  Number(d.cash_balance),
        open_value:    Number(d.open_value),
        realized_pnl:  Number(d.realized_pnl),
        win_rate:      d.win_rate != null ? Number(d.win_rate) : null,
      } as Summary
    }),
    refetchInterval: 30_000,
  })

  const { data: positions = [], isLoading: posLoading } = useQuery<PaperTrade[]>({
    queryKey: ['paper-positions'],
    queryFn: () => apiClient.get('/portfolio/paper/positions').then(r =>
      (r.data as PaperTrade[]).map(t => ({
        ...t,
        entry_price:  Number(t.entry_price),
        target_price: t.target_price != null ? Number(t.target_price) : null,
        stop_loss:    t.stop_loss    != null ? Number(t.stop_loss)    : null,
        pnl:          t.pnl         != null ? Number(t.pnl)          : null,
        pnl_pct:      t.pnl_pct     != null ? Number(t.pnl_pct)      : null,
      }))
    ),
    refetchInterval: 60_000,
  })

  const { data: history = [], isLoading: histLoading } = useQuery<PaperTrade[]>({
    queryKey: ['paper-history'],
    queryFn: () => apiClient.get('/portfolio/paper/history?limit=100').then(r =>
      (r.data as PaperTrade[]).map(t => ({
        ...t,
        entry_price:  Number(t.entry_price),
        exit_price:   t.exit_price   != null ? Number(t.exit_price)   : null,
        target_price: t.target_price != null ? Number(t.target_price) : null,
        stop_loss:    t.stop_loss    != null ? Number(t.stop_loss)    : null,
        pnl:          t.pnl         != null ? Number(t.pnl)          : null,
        pnl_pct:      t.pnl_pct     != null ? Number(t.pnl_pct)      : null,
      }))
    ),
    enabled: tab === 'history',
  })

  const sortedHistory = [...history].sort((a, b) => {
    const ta = new Date(a.exit_at ?? a.entry_at).getTime()
    const tb = new Date(b.exit_at ?? b.entry_at).getTime()
    return histSort === 'desc' ? tb - ta : ta - tb
  })

  // ── Summary numbers ──────────────────────────────────────────────────────
  const portfolioValue = summary ? summary.cash_balance + summary.open_value : null
  const pnlUp = summary ? summary.realized_pnl >= 0 : null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>

      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div className="section-header">
        <div>
          <h2 className="section-title">Paper Trading</h2>
          <p className="text-muted text-sm" style={{ marginTop: 4 }}>
            Practice trades with virtual money — no real risk
          </p>
        </div>
        <button
          className="btn"
          style={{ display: 'flex', alignItems: 'center', gap: 6 }}
          onClick={() => setShowForm(f => !f)}
        >
          <PlusCircle size={15} />
          {showForm ? 'Cancel' : 'New Order'}
        </button>
      </div>

      {/* ── Order form ─────────────────────────────────────────────────────── */}
      {showForm && (
        <div className="card card-glow" style={{ padding: 20 }}>
          <div className="card-header" style={{ marginBottom: 16 }}>
            <span className="card-title">Place Paper Order</span>
            <span className="text-muted text-sm">Cash available: {summary ? fmt(summary.cash_balance) : '…'}</span>
          </div>
          <OrderForm onSuccess={() => setShowForm(false)} />
        </div>
      )}

      {/* ── Stat row ───────────────────────────────────────────────────────── */}
      <div className="stat-grid">
        <StatCard
          icon={<Wallet size={18} />}
          label="Portfolio Value"
          value={portfolioValue != null ? fmt(portfolioValue) : '…'}
          sub={summary ? `₹${summary.cash_balance.toLocaleString('en-IN', { maximumFractionDigits: 0 })} cash + ₹${summary.open_value.toLocaleString('en-IN', { maximumFractionDigits: 0 })} positions` : 'Loading…'}
        />
        <StatCard
          icon={pnlUp ? <TrendingUp size={18} /> : <TrendingDown size={18} />}
          label="Realized P&L"
          value={summary ? `${summary.realized_pnl >= 0 ? '+' : ''}${fmt(summary.realized_pnl)}` : '…'}
          sub={summary ? `From ${summary.closed_trades} closed trade${summary.closed_trades !== 1 ? 's' : ''}` : 'Loading…'}
          color={pnlUp === true ? 'green' : pnlUp === false ? 'red' : undefined}
        />
        <StatCard
          icon={<Target size={18} />}
          label="Win Rate"
          value={summary?.win_rate != null ? summary.win_rate.toFixed(1) + '%' : '—'}
          sub={summary ? `${summary.closed_trades} closed trades` : 'Loading…'}
        />
        <StatCard
          icon={<Activity size={18} />}
          label="Open Positions"
          value={summary != null ? String(summary.open_positions) : '…'}
          sub={summary ? `${summary.total_trades} total trades` : 'Loading…'}
        />
      </div>

      {/* ── Tab bar ────────────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', gap: 8 }}>
        <button
          className={`filter-chip ${tab === 'positions' ? 'active' : ''}`}
          onClick={() => setTab('positions')}
          style={{ display: 'flex', alignItems: 'center', gap: 5 }}
        >
          <Zap size={12} /> Open ({positions.length})
        </button>
        <button
          className={`filter-chip ${tab === 'history' ? 'active' : ''}`}
          onClick={() => { setTab('history'); qc.invalidateQueries({ queryKey: ['paper-history'] }) }}
          style={{ display: 'flex', alignItems: 'center', gap: 5 }}
        >
          <Clock size={12} /> History
        </button>
      </div>

      {/* ── Open positions ──────────────────────────────────────────────────── */}
      {tab === 'positions' && (
        <div className="card card-glow" style={{ padding: 0, overflow: 'hidden' }}>
          {posLoading ? (
            <div className="empty-state"><Activity size={28} /><p>Loading…</p></div>
          ) : positions.length === 0 ? (
            <div className="empty-state">
              <BookOpen size={36} />
              <p>No open positions. Place your first paper order above.</p>
            </div>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Symbol</th><th>Dir</th><th>Qty</th>
                  <th>Entry ₹</th><th>Target ₹</th><th>SL ₹</th>
                  <th>Source</th><th>Opened</th><th></th>
                </tr>
              </thead>
              <tbody>
                {positions.map(t => (
                  <tr key={t.id}>
                    <td>
                      <div style={{ display:'flex', alignItems:'center', gap:8 }}>
                        <TickerLogo symbol={t.symbol} size={26} />
                        <span className="text-mono" style={{ fontWeight:600 }}>{t.symbol.replace('.NS', '')}</span>
                      </div>
                    </td>
                    <td><DirBadge dir={t.direction} /></td>
                    <td className="text-mono">{fmt(t.stop_loss)}</td>
                    <td className="text-muted text-sm">{t.signal_id ? '🤖 AI' : '👤 Manual'}</td>
                    <td className="text-muted text-sm">{fmtDate(t.entry_at)}</td>
                    <td><CloseBtn trade={t} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* ── History ─────────────────────────────────────────────────────────── */}
      {tab === 'history' && (
        <div className="card card-glow" style={{ padding: 0, overflow: 'hidden' }}>
          {histLoading ? (
            <div className="empty-state"><Activity size={28} /><p>Loading…</p></div>
          ) : history.length === 0 ? (
            <div className="empty-state">
              <Clock size={36} />
              <p>No closed trades yet.</p>
            </div>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Symbol</th><th>Dir</th><th>Qty</th>
                  <th>Entry ₹</th><th>Exit ₹</th>
                  <th>P&L</th><th>%</th><th>Status</th>
                  <th>Source</th>
                  <th onClick={() => setHistSort(d => d === 'asc' ? 'desc' : 'asc')} style={{ cursor: 'pointer', whiteSpace: 'nowrap' }}>
                    Closed {histSort === 'asc' ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
                  </th>
                </tr>
              </thead>
              <tbody>
                {sortedHistory.map(t => {
                  const pnlUp = t.pnl != null && t.pnl >= 0
                  return (
                    <tr key={t.id}>
                      <td>
                        <div style={{ display:'flex', alignItems:'center', gap:8 }}>
                          <TickerLogo symbol={t.symbol} size={26} />
                          <span className="text-mono" style={{ fontWeight:600 }}>{t.symbol.replace('.NS', '')}</span>
                        </div>
                      </td>
                      <td><DirBadge dir={t.direction} /></td>
                      <td className="text-mono">{t.qty}</td>
                      <td className="text-mono">{fmt(t.entry_price)}</td>
                      <td className="text-mono">{fmt(t.exit_price)}</td>
                      <td className="text-mono" style={{ color: pnlUp ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>
                        {t.pnl != null ? `${t.pnl >= 0 ? '+' : ''}${fmt(t.pnl)}` : '—'}
                      </td>
                      <td className="text-mono" style={{ color: pnlUp ? 'var(--green)' : 'var(--red)' }}>
                        {t.pnl_pct != null ? `${t.pnl_pct >= 0 ? '+' : ''}${t.pnl_pct.toFixed(2)}%` : '—'}
                      </td>
                      <td><StatusBadge status={t.status} /></td>
                      <td className="text-muted text-sm">{t.signal_id ? '🤖 AI' : '👤 Manual'}</td>
                      <td className="text-muted text-sm">{t.exit_at ? fmtDate(t.exit_at) : '—'}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  )
}
