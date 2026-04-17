import { useEffect, useState } from 'react'
import { Wallet, Activity, Target, Clock, TrendingUp, TrendingDown } from 'lucide-react'
import { apiClient } from '../api/client'

interface IndexQuote {
  symbol: string; price: number; change: number; change_pct: number; name?: string
}
interface Signal {
  id: string; symbol: string; signal_type: string; confidence: number
  entry_price: number | null; target_price: number | null; ts: string
}

function StatCard({ icon, label, value, sub }: { icon: React.ReactNode; label: string; value: string; sub: string }) {
  return (
    <div className="stat-card">
      <div className="stat-card-inner">
        <div>
          <div className="stat-icon">{icon}</div>
          <div className="stat-label">{label}</div>
          <div className="stat-value">{value}</div>
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
  const [indices, setIndices] = useState<IndexQuote[]>([])
  const [signals, setSignals] = useState<Signal[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      apiClient.get('/prices/indices').then(r => {
        const data = r.data
        const raw: IndexQuote[] = (data.indices ?? []).map((q: IndexQuote & { name?: string }) => ({
          ...q,
          name: q.name ?? q.symbol,
        }))
        setIndices(raw)
      }).catch(() => {}),
      apiClient.get('/signals?per_page=5&active=true').then(r => {
        setSignals(r.data.signals ?? [])
      }).catch(() => {}),
    ]).finally(() => setLoading(false))
  }, [])

  const activeCount = signals.filter(s => s.signal_type !== 'HOLD').length

  return (
    <div className="dashboard">
      {/* ── Stat row ─────────────────────────────────────────────────────── */}
      <div className="stat-grid">
        <StatCard icon={<Wallet size={18}/>}   label="Portfolio Value" value="—"            sub="Connect broker to see balance" />
        <StatCard icon={<Activity size={18}/>} label="Day P&L"         value="—"            sub="No live trades yet" />
        <StatCard icon={<Target size={18}/>}   label="Win Rate"        value="—"            sub="No closed trades yet" />
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
