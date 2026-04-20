import { useQuery } from '@tanstack/react-query'
import { Zap, TrendingUp, TrendingDown } from 'lucide-react'
import { apiClient } from '../api/client'
import ForecastModal from '../components/ForecastModal'
import OrderModal, { OrderDefaults } from '../components/OrderModal'
import TickerLogo from '../components/TickerLogo'
import { useState } from 'react'

interface Signal {
  id: string; symbol: string; ts: string; signal_type: 'BUY' | 'SELL' | 'HOLD'
  confidence: number; entry_price: number | null; target_price: number | null
  stop_loss: number | null; model_version: string
}

function ScoreBar({ value }: { value: number }) {
  const pct = Math.round(value * 100)
  const color = pct >= 75 ? 'var(--green)' : pct >= 55 ? 'var(--yellow)' : 'var(--red)'
  return (
    <div style={{ display:'flex', alignItems:'center', gap:8 }}>
      <div style={{ width:60, height:5, background:'var(--bg-hover)', borderRadius:3, overflow:'hidden' }}>
        <div style={{ width:`${pct}%`, height:'100%', background:color, borderRadius:3 }}/>
      </div>
      <span className="text-mono text-sm" style={{ color }}>{pct}%</span>
    </div>
  )
}

export default function OpportunitiesPage() {
  const [filter, setFilter]   = useState<'ALL'|'BUY'|'SELL'>('ALL')
  const [forecastSym, setForecastSym]   = useState<string | null>(null)
  const [orderDefaults, setOrderDefaults] = useState<OrderDefaults | null>(null)

  const { data, isLoading: loading } = useQuery<{ signals: Signal[] }>({
    queryKey: ['opportunities-signals'],
    queryFn: () => apiClient.get('/signals?per_page=50&active=true').then(r => r.data),
    refetchInterval: 60_000,
  })
  const signals = data?.signals ?? []

  const visible = filter === 'ALL' ? signals
    : signals.filter(s => s.signal_type === filter)

  return (
    <div className="opp-page">
      <div className="section-header">
        <div>
          <h2 className="section-title">AI Opportunities</h2>
          <p className="text-muted text-sm" style={{ marginTop:4 }}>
            Ensemble model · AI-ranked active signals
          </p>
        </div>
        <div style={{ display:'flex', gap:8 }}>
          {(['ALL','BUY','SELL'] as const).map(f => (
            <button key={f} onClick={() => setFilter(f)}
              className={`filter-chip ${filter === f ? 'active' : ''}`}>{f}</button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="card card-glow">
          <div className="empty-state"><Zap size={28}/><p>Loading…</p></div>
        </div>
      ) : visible.length === 0 ? (
        <div className="card card-glow">
          <div className="empty-state">
            <Zap size={40}/>
            <p>No active signals yet. Run the ML pipeline to generate opportunities.</p>
          </div>
        </div>
      ) : (
        <div className="opp-grid">
          {visible.map(s => {
            const isBuy = s.signal_type === 'BUY'
            const rr = s.entry_price && s.target_price && s.stop_loss
              ? ((Math.abs(s.target_price - s.entry_price)) / Math.abs(s.entry_price - s.stop_loss)).toFixed(1)
              : null
            return (
              <div key={s.id} className={`opp-card ${s.signal_type.toLowerCase()}`}>
                <div className="opp-card-header">
                  <div>
                    <div className="opp-ticker">{s.symbol.replace('.NS','')}</div>
                    <div className="text-muted text-sm">{s.model_version}</div>
                  </div>
                  <span
                    className={`signal-badge ${s.signal_type}`}
                    style={{ cursor:'pointer' }}
                    title={`Click to ${s.signal_type}`}
                    onClick={() => setOrderDefaults({ symbol: s.symbol, direction: s.signal_type === 'SELL' ? 'SELL' : 'BUY', entryPrice: s.entry_price, targetPrice: s.target_price, stopLoss: s.stop_loss })}
                  >{s.signal_type}</span>
                </div>
                <div className="opp-score-row">
                  <span className="text-sm text-muted">Confidence</span>
                  <ScoreBar value={s.confidence} />
                </div>
                <div className="opp-prices">
                  <div><div className="text-muted text-sm">Entry</div>
                    <div className="text-mono">{s.entry_price != null ? `₹${s.entry_price.toLocaleString('en-IN')}` : '—'}</div>
                  </div>
                  <div style={{ color: isBuy ? 'var(--green)' : 'var(--red)' }}>
                    {isBuy ? <TrendingUp size={14}/> : <TrendingDown size={14}/>}
                  </div>
                  <div><div className="text-muted text-sm">Target</div>
                    <div className="text-mono">{s.target_price != null ? `₹${s.target_price.toLocaleString('en-IN')}` : '—'}</div>
                  </div>
                  <div><div className="text-muted text-sm">R:R</div>
                    <div className="text-mono">{rr ? `${rr}×` : '—'}</div>
                  </div>
                </div>
                <div className="text-muted text-sm" style={{ marginTop:8 }}>
                  {new Date(s.ts + 'Z').toLocaleString('en-IN', { dateStyle:'short', timeStyle:'short' })}
                </div>

                {/* Action buttons */}
                <div style={{ display:'flex', gap:6, marginTop:12 }}>
                  <button
                    className="btn-outline btn"
                    style={{ flex:1, padding:'7px 10px', fontSize:12 }}
                    onClick={() => setForecastSym(s.symbol)}
                    title="AI Forecast"
                  >
                    📈 Forecast
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}

      <ForecastModal symbol={forecastSym} onClose={() => setForecastSym(null)} />
      <OrderModal defaults={orderDefaults} onClose={() => setOrderDefaults(null)} />
    </div>
  )
}
