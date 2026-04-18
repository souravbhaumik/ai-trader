import { useQuery } from '@tanstack/react-query'
import { Wallet, Activity, Target, Clock, TrendingUp, TrendingDown } from 'lucide-react'
import { apiClient } from '../api/client'
import { useAuthStore } from '../store/authStore'

interface IndexQuote {
  symbol: string; price: number; change: number; change_pct: number; name?: string
}
interface Signal {
  id: string; symbol: string; signal_type: string; confidence: number
  entry_price: number | null; target_price: number | null; ts: string
}
interface PortfolioSummary {
  cash_balance: number
  open_positions: number
  open_value: number
  realized_pnl: number
  total_trades: number
  closed_trades: number
  win_rate: number | null
}

function StatCard({ icon, label, value, sub, valueColor }: { icon: React.ReactNode; label: string; value: string; sub: string; valueColor?: 'green' | 'red' }) {
  return (
    <div className="stat-card">
      <div className="stat-card-inner">
        <div>
          <div className="stat-icon">{icon}</div>
          <div className="stat-label">{label}</div>
          <div className="stat-value" style={valueColor ? { color: `var(--${valueColor})` } : undefined}>{value}</div>
          <div className="stat-sub text-muted">{sub}</div>
        </div>
      </div>
    </div>
  )
}

function IndexPill({ q }: { q: IndexQuote }) {
  const up = q.change_pct >= 0
  const label = q.name ?? q.symbol
  return (
    <div className="index-pill">
      <span className="index-name">{label}</span>
      <span className="index-price">{q.price.toLocaleString('en-IN', { maximumFractionDigits: 2 })}</span>
      <span className={`index-chg ${up ? 'green' : 'red'}`}>
        {up ? <TrendingUp size={11}/> : <TrendingDown size={11}/>}
        {up ? '+' : ''}{q.change_pct.toFixed(2)}%
      </span>
    </div>
  )
}

export default function DashboardPage() {
  const tradingMode = useAuthStore(s => s.tradingMode)
  const { data: indicesData, isLoading: indicesLoading } = useQuery({
    queryKey: ['indices'],
    queryFn: () => apiClient.get('/prices/indices').then(r => r.data),
  })
  const { data: signalsData, isLoading: signalsLoading } = useQuery({
    queryKey: ['signals-recent'],
    queryFn: () => apiClient.get('/signals?per_page=5&active=true').then(r => r.data),
  })
  const { data: portfolioData } = useQuery<PortfolioSummary>({
    queryKey: ['portfolio-summary'],
    queryFn: () => apiClient.get('/portfolio/paper/summary').then(r => r.data),
    refetchInterval: 30_000,
  })

  const indices: IndexQuote[] = (indicesData?.indices ?? []).map((q: IndexQuote & { name?: string }) => ({
    ...q, name: q.name ?? q.symbol,
  }))
  const signals: Signal[] = signalsData?.signals ?? []
  const loading = indicesLoading || signalsLoading
  const activeCount: number = signalsData?.total ?? signals.filter(s => s.signal_type !== 'HOLD').length

  // Portfolio derived values
  const portfolioValue = portfolioData
    ? portfolioData.cash_balance + portfolioData.open_value
    : null
  const portfolioValueStr = portfolioValue !== null
    ? '₹' + portfolioValue.toLocaleString('en-IN', { maximumFractionDigits: 0 })
    : '…'
  const portfolioSub = portfolioData
    ? `${portfolioData.open_positions} open position${portfolioData.open_positions !== 1 ? 's' : ''} · ₹${portfolioData.cash_balance.toLocaleString('en-IN', { maximumFractionDigits: 0 })} cash`
    : 'Loading…'

  const pnlPositive = portfolioData ? portfolioData.realized_pnl >= 0 : null
  const pnlStr = portfolioData
    ? (portfolioData.realized_pnl >= 0 ? '+' : '') + '₹' + portfolioData.realized_pnl.toLocaleString('en-IN', { maximumFractionDigits: 0 })
    : '…'
  const pnlSub = portfolioData
    ? portfolioData.closed_trades > 0 ? `From ${portfolioData.closed_trades} closed trade${portfolioData.closed_trades !== 1 ? 's' : ''}` : 'No closed trades yet'
    : 'Loading…'

  const winRateStr = portfolioData?.win_rate != null
    ? portfolioData.win_rate.toFixed(1) + '%'
    : '—'
  const winRateSub = portfolioData
    ? portfolioData.closed_trades > 0 ? `${portfolioData.closed_trades} closed trade${portfolioData.closed_trades !== 1 ? 's' : ''}` : 'No closed trades yet'
    : 'Loading…'

  return (
    <div className="dashboard">
      {/* ── Stat row ─────────────────────────────────────────────────────── */}
      <div className="stat-grid">
        <StatCard icon={<Wallet size={18}/>}   label={tradingMode === 'paper' ? 'Paper Portfolio' : 'Portfolio Value'} value={portfolioValueStr} sub={portfolioSub} />
        <StatCard icon={pnlPositive === true ? <TrendingUp size={18}/> : pnlPositive === false ? <TrendingDown size={18}/> : <Activity size={18}/>} label="Net Profit" value={pnlStr} sub={pnlSub} valueColor={pnlPositive === true ? 'green' : pnlPositive === false ? 'red' : undefined} />
        <StatCard icon={<Target size={18}/>}   label="Win Rate"        value={winRateStr}         sub={winRateSub} />
        <StatCard icon={<Clock size={18}/>}    label="Active Signals"  value={loading ? '…' : String(activeCount)} sub="From ML pipeline" />
      </div>

      {/* ── Index strip ──────────────────────────────────────────────────── */}
      {indices.length > 0 && (
        <div className="index-strip">
          {indices.map(q => <IndexPill key={q.symbol} q={q} />)}
        </div>
      )}

      {/* ── Content ──────────────────────────────────────────────────────── */}
      <div className="dash-split">
        <div className="card card-glow">
          <div className="card-header"><span className="card-title">Recent Signals</span></div>
          {loading ? (
            <div className="empty-state"><Activity size={28}/><p>Loading…</p></div>
          ) : signals.length === 0 ? (
            <div className="empty-state">
              <Activity size={36}/>
              <p>Signals appear here once the ML pipeline is running.</p>
            </div>
          ) : (
            <table className="data-table">
              <thead><tr>
                <th>Symbol</th><th>Signal</th><th>Confidence</th><th>Entry</th><th>Time</th>
              </tr></thead>
              <tbody>
                {signals.map(s => (
                  <tr key={s.id}>
                    <td className="text-mono">{s.symbol}</td>
                    <td><span className={`signal-badge ${s.signal_type}`}>{s.signal_type}</span></td>
                    <td className="text-mono">{(s.confidence * 100).toFixed(0)}%</td>
                    <td className="text-mono">{s.entry_price != null ? `₹${s.entry_price.toLocaleString('en-IN')}` : '—'}</td>
                    <td className="text-muted text-sm">{new Date(s.ts + 'Z').toLocaleTimeString('en-IN', { timeStyle:'short' })}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="card card-glow">
          <div className="card-header"><span className="card-title">Market Indices</span></div>
          {loading ? (
            <div className="empty-state"><Target size={28}/><p>Loading…</p></div>
          ) : indices.length === 0 ? (
            <div className="empty-state">
              <Target size={36}/>
              <p>Market data loads via yfinance. Check your connection.</p>
            </div>
          ) : (
            <div style={{ display:'flex', flexDirection:'column', gap:2 }}>
              {indices.map(q => (
                <div key={q.symbol} className="mover-row">
                  <div>
                    <div className="text-sm" style={{ fontWeight:600 }}>{q.name ?? q.symbol}</div>
                    <div className="text-muted text-sm">{q.symbol}</div>
                  </div>
                  <div style={{ textAlign:'right' }}>
                    <div className="text-mono text-sm">{q.price.toLocaleString('en-IN', { maximumFractionDigits:2 })}</div>
                    <div className={`text-mono text-sm ${q.change_pct >= 0 ? 'text-green' : 'text-red'}`}>
                      {q.change_pct >= 0 ? '+' : ''}{q.change_pct.toFixed(2)}%
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
