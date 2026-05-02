import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Wallet, Activity, Target, Clock, TrendingUp, TrendingDown, RefreshCw } from 'lucide-react'
import { apiClient } from '../api/client'
import { useAuthStore } from '../store/authStore'
import { useState, useEffect, useRef } from 'react'
import ForecastModal from '../components/ForecastModal'
import OrderModal, { OrderDefaults } from '../components/OrderModal'
import TickerLogo from '../components/TickerLogo'
import { useSignalStream } from '../hooks/useSignalStream'

interface IndexQuote {
  symbol: string; price: number; change: number; change_pct: number; name?: string
}
interface Signal {
  id: string; symbol: string; signal_type: string; confidence: number
  entry_price: number | null; target_price: number | null; ts: string
  // Phase 12: institutional alpha metrics from Phase 11 delivery engine
  delivery_pct?: number | null; pcr_ratio?: number | null;
}
interface PortfolioSummary {
  cash_balance: number; open_positions: number; open_value: number
  realized_pnl: number; total_trades: number; closed_trades: number; win_rate: number | null
}
interface ScreenerRow {
  symbol: string; name?: string; sector?: string
  price: number | null; change_pct: number | null
}

// ── Stat Card ─────────────────────────────────────────────────────────────────
function StatCard({ icon, label, value, sub, accent }: {
  icon: React.ReactNode; label: string; value: string; sub: string
  accent?: 'green' | 'red' | 'blue' | 'yellow'
}) {
  const colors = { green: 'var(--green)', red: 'var(--red)', blue: 'var(--blue)', yellow: 'var(--yellow)' }
  const color = accent ? colors[accent] : 'var(--green)'
  return (
    <div className="stat-card" style={{ ['--accent' as string]: color }}>
      <div className="stat-icon" style={{ color, background: `color-mix(in srgb, ${color} 12%, var(--bg-hover))` }}>
        {icon}
      </div>
      <div className="stat-label">{label}</div>
      <div className="stat-value" style={accent === 'green' || accent === 'red' ? { color } : undefined}>{value}</div>
      <div className="stat-sub text-muted">{sub}</div>
      <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 2, background: `linear-gradient(90deg, ${color}, transparent)`, borderRadius: '16px 16px 0 0' }} />
    </div>
  )
}

// ── Ticker Tape ───────────────────────────────────────────────────────────────
function TickerTape({ items }: { items: ScreenerRow[] }) {
  if (!items.length) return null
  const doubled = [...items, ...items] // duplicate for seamless loop
  return (
    <div className="ticker-tape-wrap">
      <div className="ticker-tape-track">
        {doubled.map((r, i) => {
          const up = (r.change_pct ?? 0) >= 0
          return (
            <div key={i} className="ticker-tape-item">
              <span className="ticker-tape-sym">{r.symbol.replace('.NS', '')}</span>
              <span className="ticker-tape-price">
                {r.price != null ? `₹${r.price.toLocaleString('en-IN', { maximumFractionDigits: 2 })}` : '—'}
              </span>
              {r.change_pct != null && (
                <span className={`ticker-tape-chg ${up ? 'up' : 'down'}`}>
                  {up ? '▲' : '▼'} {Math.abs(r.change_pct).toFixed(2)}%
                </span>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Live Price Row ─────────────────────────────────────────────────────────────
function LivePriceItem({ row }: { row: ScreenerRow }) {
  const ref = useRef<HTMLDivElement>(null)
  const prevPrice = useRef<number | null>(null)
  useEffect(() => {
    if (row.price == null || prevPrice.current == null) { prevPrice.current = row.price; return }
    const el = ref.current
    if (!el) return
    const cls = row.price > prevPrice.current ? 'flash-green' : row.price < prevPrice.current ? 'flash-red' : ''
    if (cls) {
      el.classList.remove('flash-green', 'flash-red')
      void el.offsetWidth
      el.classList.add(cls)
    }
    prevPrice.current = row.price
  }, [row.price])

  const up = (row.change_pct ?? 0) >= 0
  return (
    <div ref={ref} className="live-price-row" style={{ borderRadius: 8, padding: '10px 4px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <TickerLogo symbol={row.symbol} size={28} />
        <div>
          <div className="live-price-sym">{row.symbol.replace('.NS', '')}</div>
          {row.name && <div className="live-price-name">{row.name.length > 22 ? row.name.slice(0, 22) + '…' : row.name}</div>}
        </div>
      </div>
      <div className="live-price-val">
        <div className="live-price-num">
          {row.price != null ? `₹${row.price.toLocaleString('en-IN', { maximumFractionDigits: 2 })}` : '—'}
        </div>
        {row.change_pct != null && (
          <div className={`live-price-chg ${up ? 'up' : 'down'}`}>
            {up ? '▲' : '▼'} {Math.abs(row.change_pct).toFixed(2)}%
          </div>
        )}
      </div>
    </div>
  )
}

// ── Page ───────────────────────────────────────────────────────────────────────
export default function DashboardPage() {
  const tradingMode = useAuthStore(s => s.tradingMode)
  const brokerUpdatedAt = useAuthStore(s => s.brokerUpdatedAt)
  const navigate = useNavigate()
  const [forecastSym, setForecastSym] = useState<string | null>(null)
  const [orderDefaults, setOrderDefaults] = useState<OrderDefaults | null>(null)

  const now = new Date()
  const dateStr = now.toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })

  const { data: indicesData, isLoading: indicesLoading, error: indicesError } = useQuery({
    queryKey: ['indices', brokerUpdatedAt],
    queryFn: () => apiClient.get('/prices/indices').then(r => r.data),
    refetchInterval: 30_000,
    staleTime: 0,
  })
  const { data: signalsData, isLoading: signalsLoading } = useQuery({
    queryKey: ['signals-recent'],
    queryFn: () => apiClient.get('/signals?per_page=8&active=true').then(r => r.data),
    refetchInterval: 60_000,
  })
  // Real-time signal stream overlay — latest signals pushed via WS
  const { signals: wsSignals } = useSignalStream()
  const { data: portfolioData } = useQuery<PortfolioSummary>({
    queryKey: ['portfolio-summary'],
    queryFn: () => apiClient.get('/portfolio/paper/summary').then(r => {
      const d = r.data
      return { ...d, cash_balance: Number(d.cash_balance), open_value: Number(d.open_value), realized_pnl: Number(d.realized_pnl) } as PortfolioSummary
    }),
    refetchInterval: 30_000,
  })
  const { data: screenerData, isLoading: screenerLoading, refetch: refetchScreener, dataUpdatedAt } = useQuery({
    queryKey: ['dash-live-prices', brokerUpdatedAt],
    queryFn: () => apiClient.get('/screener?page=1&per_page=20').then(r => r.data),
    refetchInterval: 15_000,
    staleTime: 0,
  })

  const indices: IndexQuote[] = (indicesData?.indices ?? [])
  // Merge WS real-time signals with REST-fetched signals (WS takes priority)
  const restSignals: Signal[] = signalsData?.signals ?? []
  const mergedMap = new Map<string, Signal>()
  for (const s of restSignals) mergedMap.set(s.id, s)
  for (const s of wsSignals) mergedMap.set(s.id ?? s.symbol, s)
  const signals: Signal[] = Array.from(mergedMap.values())
    .sort((a, b) => new Date(b.ts).getTime() - new Date(a.ts).getTime())
    .slice(0, 8)
  const screenerRows: ScreenerRow[] = screenerData?.rows ?? screenerData?.results ?? screenerData?.items ?? []
  const loading = indicesLoading || signalsLoading
  const activeCount: number = signalsData?.total ?? signals.filter(s => s.signal_type !== 'HOLD').length

  const portfolioValue = portfolioData ? portfolioData.cash_balance + portfolioData.open_value : null
  const portfolioValueStr = portfolioValue !== null ? '₹' + portfolioValue.toLocaleString('en-IN', { maximumFractionDigits: 0 }) : '…'
  const portfolioSub = portfolioData ? `${portfolioData.open_positions} position${portfolioData.open_positions !== 1 ? 's' : ''} · ₹${portfolioData.cash_balance.toLocaleString('en-IN', { maximumFractionDigits: 0 })} cash` : 'Loading…'
  const pnlPositive = portfolioData ? portfolioData.realized_pnl >= 0 : null
  const pnlStr = portfolioData ? (portfolioData.realized_pnl >= 0 ? '+' : '') + '₹' + portfolioData.realized_pnl.toLocaleString('en-IN', { maximumFractionDigits: 0 }) : '…'
  const pnlSub = portfolioData ? (portfolioData.closed_trades > 0 ? `${portfolioData.closed_trades} closed trades` : 'No closed trades yet') : 'Loading…'
  const winRateStr = portfolioData?.win_rate != null ? portfolioData.win_rate.toFixed(1) + '%' : '—'

  const lastUpdate = dataUpdatedAt ? new Date(dataUpdatedAt).toLocaleTimeString('en-IN', { timeStyle: 'short' }) : null

  return (
    <div className="dashboard">
      {/* ── Page Header ───────────────────────────────────────────────────── */}
      <div className="page-header">
        <div className="page-header-left">
          <div className="page-header-title">Dashboard</div>
          <div className="page-header-sub">{dateStr}</div>
        </div>
        <div className="page-header-actions">
          {lastUpdate && <span className="text-muted text-sm">Updated {lastUpdate}</span>}
          <button className="btn btn-outline flex-center gap-2" onClick={() => refetchScreener()} disabled={screenerLoading} style={{ padding: '6px 12px' }}>
            <RefreshCw size={13} style={screenerLoading ? { animation: 'spin 1s linear infinite' } : undefined} />
            Refresh
          </button>
        </div>
      </div>

      {/* ── Stat Row ──────────────────────────────────────────────────────── */}
      <div className="stat-grid">
        <StatCard icon={<Wallet size={18} />}
          label={tradingMode === 'paper' ? 'Paper Portfolio' : 'Portfolio Value'}
          value={portfolioValueStr} sub={portfolioSub} />
        <StatCard icon={pnlPositive !== false ? <TrendingUp size={18} /> : <TrendingDown size={18} />}
          label="Realized P&L" value={pnlStr} sub={pnlSub}
          accent={pnlPositive === true ? 'green' : pnlPositive === false ? 'red' : undefined} />
        <StatCard icon={<Target size={18} />}
          label="Win Rate" value={winRateStr} sub={pnlSub.includes('closed') ? pnlSub : 'No closed trades'} accent="blue" />
        <StatCard icon={<Clock size={18} />}
          label="Active Signals" value={loading ? '…' : String(activeCount)} sub="From ML pipeline" accent="yellow" />
      </div>

      {/* ── Live Ticker Tape ──────────────────────────────────────────────── */}
      {screenerRows.length > 0 && <TickerTape items={screenerRows} />}

      {/* ── Index Strip ───────────────────────────────────────────────────── */}
      {indices.length > 0 && (
        <div className="index-strip">
          {indices.map((q: IndexQuote) => {
            const up = q.change_pct >= 0
            return (
              <div key={q.symbol} className="index-pill">
                <span className="index-name">{q.name ?? q.symbol}</span>
                <span className="index-price">{q.price.toLocaleString('en-IN', { maximumFractionDigits: 2 })}</span>
                <span className={`index-chg ${up ? 'green' : 'red'}`}>
                  {up ? <TrendingUp size={11} /> : <TrendingDown size={11} />}
                  {up ? '+' : ''}{q.change_pct.toFixed(2)}%
                </span>
              </div>
            )
          })}
        </div>
      )}

      {/* ── Main Grid ─────────────────────────────────────────────────────── */}
      <div className="dash-grid">
        {/* Left: Recent Signals */}
        <div className="card card-glow">
          <div className="card-header">
            <span className="card-title">Recent Signals</span>
            {!loading && <span className="text-muted text-sm">{activeCount} active</span>}
          </div>
          {loading ? (
            <div className="empty-state"><Activity size={28} /><p>Loading…</p></div>
          ) : signals.length === 0 ? (
            <div className="empty-state">
              <Activity size={36} />
              <p>Signals appear here once the ML pipeline is running.</p>
            </div>
          ) : (
            <table className="data-table">
              <thead><tr>
                <th>Symbol</th><th>Signal</th><th>Inst. Alpha</th><th>Confidence</th><th>Entry</th><th>Target</th><th>Time</th><th></th>
              </tr></thead>
              <tbody>
                {signals.map(s => (
                  <tr key={s.id}>
                    <td>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <TickerLogo symbol={s.symbol} size={26} />
                        <span className="text-mono" style={{ fontWeight: 600 }}>{s.symbol.replace('.NS', '')}</span>
                      </div>
                    </td>
                    <td>
                      <span className={`signal-badge ${s.signal_type}`}
                        style={{ cursor: s.signal_type !== 'HOLD' ? 'pointer' : undefined }}
                        onClick={() => s.signal_type !== 'HOLD' && setOrderDefaults({ symbol: s.symbol, direction: s.signal_type === 'SELL' ? 'SELL' : 'BUY', entryPrice: s.entry_price, targetPrice: s.target_price, stopLoss: null })}
                      >{s.signal_type}</span>
                    </td>
                    {/* Phase 12: Institutional Alpha — delivery % + PCR */}
                    <td>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
                        <div style={{ fontSize: 11, fontWeight: 600, color: s.delivery_pct && s.delivery_pct > 0.4 ? 'var(--green)' : 'var(--text-muted)' }}>
                          {s.delivery_pct != null ? `${(s.delivery_pct * 100).toFixed(0)}% Deliv` : '—'}
                        </div>
                        <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>
                          {s.pcr_ratio != null ? `PCR ${s.pcr_ratio.toFixed(2)}` : '—'}
                        </div>
                      </div>
                    </td>
                    <td>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <div style={{ flex: 1, height: 4, background: 'var(--bg-hover)', borderRadius: 2, overflow: 'hidden', minWidth: 48 }}>
                          <div style={{ height: '100%', width: `${(s.confidence * 100).toFixed(0)}%`, background: 'linear-gradient(90deg,var(--blue),var(--purple))', borderRadius: 2 }} />
                        </div>
                        <span className="text-mono text-sm">{(s.confidence * 100).toFixed(0)}%</span>
                      </div>
                    </td>
                    <td className="text-mono">{s.entry_price != null ? `₹${s.entry_price.toLocaleString('en-IN')}` : '—'}</td>
                    <td className="text-mono">{s.target_price != null ? `₹${s.target_price.toLocaleString('en-IN')}` : '—'}</td>
                    <td className="text-muted text-sm">{new Date(s.ts + 'Z').toLocaleTimeString('en-IN', { timeStyle: 'short' })}</td>
                    <td>
                      <button className="btn-outline btn" style={{ padding: '3px 8px', fontSize: 11 }} onClick={() => setForecastSym(s.symbol)}>📈</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Right column */}
        <div className="dash-col">
          {/* Live Prices */}
          <div className="card" style={{ flex: 1 }}>
            <div className="card-header">
              <span className="card-title">Live Prices</span>
              {screenerLoading && <RefreshCw size={12} style={{ color: 'var(--text-muted)', animation: 'spin 1s linear infinite' }} />}
            </div>
            {screenerRows.length === 0 ? (
              <div className="empty-state" style={{ padding: '24px 0' }}>
                <Activity size={28} />
                <p style={{ fontSize: 12 }}>No broker configured.</p>
                <button className="btn btn-outline" style={{ fontSize: 12, marginTop: 8 }} onClick={() => navigate('/settings')}>
                  Set up broker →
                </button>
              </div>
            ) : (
              <div>
                {screenerRows.slice(0, 10).map(r => <LivePriceItem key={r.symbol} row={r} />)}
              </div>
            )}
          </div>

          {/* Market Indices */}
          {indices.length > 0 && (
            <div className="card">
              <div className="card-header"><span className="card-title">Indices</span></div>
              {indices.map((q: IndexQuote) => (
                <div key={q.symbol} className="live-price-row" style={{ padding: '8px 4px' }}>
                  <div>
                    <div style={{ fontWeight: 600, fontSize: 13 }}>{q.name ?? q.symbol}</div>
                    <div className="text-muted text-sm">{q.symbol}</div>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <div className="text-mono text-sm" style={{ fontWeight: 600 }}>{q.price.toLocaleString('en-IN', { maximumFractionDigits: 2 })}</div>
                    <div className={`live-price-chg ${q.change_pct >= 0 ? 'up' : 'down'}`}>
                      {q.change_pct >= 0 ? '▲' : '▼'} {Math.abs(q.change_pct).toFixed(2)}%
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <ForecastModal symbol={forecastSym} onClose={() => setForecastSym(null)} />
      <OrderModal defaults={orderDefaults} onClose={() => setOrderDefaults(null)} />
    </div>
  )
}
