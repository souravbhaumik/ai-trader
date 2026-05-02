import React, { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Activity, ChevronUp, ChevronDown, ChevronLeft, ChevronRight, Radio, Zap, MessageSquare } from 'lucide-react'
import { apiClient } from '../api/client'
import { useSignalStream } from '../hooks/useSignalStream'
import ForecastModal from '../components/ForecastModal'
import OrderModal, { OrderDefaults } from '../components/OrderModal'
import TickerLogo from '../components/TickerLogo'

interface Signal {
  id: string; symbol: string; ts: string; signal_type: 'BUY' | 'SELL' | 'HOLD'
  confidence: number; entry_price: number | null; target_price: number | null
  stop_loss: number | null; model_version: string; is_active: boolean
  explanation: string | null
  // Phase 12: institutional alpha metrics
  delivery_pct?: number | null; pcr_ratio?: number | null;
}
interface SignalResult { total: number; page: number; per_page: number; signals: Signal[] }

type SortDir = 'asc' | 'desc'
type FilterType = 'ALL' | 'BUY' | 'SELL' | 'HOLD'
type ViewMode = 'opportunities' | 'history'

export default function SignalLogPage() {
  const [view, setView]     = useState<ViewMode>('opportunities')
  const [filter, setFilter]   = useState<FilterType>('ALL')
  const [active, setActive]   = useState(false)
  const [page, setPage]       = useState(1)
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [forecastSym, setForecastSym]     = useState<string | null>(null)
  const [orderDefaults, setOrderDefaults] = useState<OrderDefaults | null>(null)
  const [expandedId, setExpandedId]       = useState<string | null>(null)

  const PER_PAGE = 50

  // Live WebSocket stream — new signals arrive in real-time on page 1
  const { signals: liveSignals, connected: wsConnected } = useSignalStream(page === 1)

  const { data: result, isFetching: loading } = useQuery({
    queryKey: ['signals', view, filter, active, page, sortDir],
    queryFn: () => {
      const params = new URLSearchParams({ page: String(page), per_page: String(PER_PAGE) })
      if (view === 'opportunities') {
        params.set('active', 'true')
        if (filter === 'ALL') params.set('type', 'BUY,SELL')
        else if (filter !== 'HOLD') params.set('type', filter)
        // HOLD is excluded from Opportunities — no type param means BUY,SELL already set
      } else {
        if (filter !== 'ALL') params.set('type', filter)
        if (active) params.set('active', 'true')
        // no active param → backend returns all (active and closed)
      }
      return apiClient.get<SignalResult>(`/signals?${params}`).then(r => r.data)
    },
    placeholderData: (prev) => prev,
  })

  // On page 1, prepend live WS signals (deduped) in front of the REST results
  const displaySignals = useMemo<Signal[]>(() => {
    const restSignals = result?.signals ?? []
    if (page !== 1 || liveSignals.length === 0) return restSignals
    const restIds = new Set(restSignals.map(s => s.id))
    const freshLive = liveSignals.filter(s =>
      !restIds.has(s.id) &&
      (filter === 'ALL' || s.signal_type === filter) &&
      (!active || s.is_active)
    ) as Signal[]
    const merged = [...freshLive, ...restSignals]
    return sortDir === 'asc' ? [...merged].reverse() : merged
  }, [liveSignals, result, page, filter, active, sortDir])

  const totalPages = result ? Math.ceil(result.total / PER_PAGE) : 1
  const isEmpty    = !loading && displaySignals.length === 0

  return (
    <div className="signal-page">
      {/* Page Header */}
      <div className="page-header">
        <div className="page-header-left">
          <div className="page-header-title">Signals</div>
          <div className="page-header-sub">AI-generated trade signals from the ML pipeline</div>
        </div>
      </div>

      {/* View toggle */}
      <div style={{ display:'flex', gap:4, marginBottom:16, borderBottom:'1px solid var(--border)', paddingBottom:12 }}>
        <button
          onClick={() => { setView('opportunities'); setPage(1) }}
          className={`filter-chip ${view === 'opportunities' ? 'active' : ''}`}
          style={{ display:'flex', alignItems:'center', gap:6 }}
        ><Zap size={13}/> Opportunities</button>
        <button
          onClick={() => { setView('history'); setPage(1) }}
          className={`filter-chip ${view === 'history' ? 'active' : ''}`}
          style={{ display:'flex', alignItems:'center', gap:6 }}
        ><Activity size={13}/> History</button>
      </div>

      <div className="section-header">
        <div>
          <h2 className="section-title">
            {view === 'opportunities' ? 'AI Opportunities' : 'Signal History'}
          </h2>
          <p className="text-muted text-sm" style={{ marginTop:4 }}>
            {view === 'opportunities'
              ? 'Active BUY/SELL signals — ranked by model confidence'
              : result ? `${result.total.toLocaleString()} signals total` : 'Full history of every AI-generated trading signal'
            }
          </p>
        </div>
        <div style={{ display:'flex', gap:8, alignItems:'center' }}>
          {/* Live indicator always visible */}
          <span style={{ display:'flex', alignItems:'center', gap:4, fontSize:11,
                         color: wsConnected ? 'var(--accent-green)' : 'var(--text-muted)' }}>
            <Radio size={12} />
            {wsConnected ? 'LIVE' : 'connecting…'}
          </span>
          {/* Filters: show type filter in both views; active toggle only in history */}
          {(view === 'opportunities'
            ? (['ALL','BUY','SELL'] as FilterType[])
            : (['ALL','BUY','SELL','HOLD'] as FilterType[])
          ).map(f => (
            <button key={f} onClick={() => setFilter(f)}
              className={`filter-chip ${filter === f ? 'active' : ''}`}>{f}</button>
          ))}
          {view === 'history' && (
            <label style={{ display:'flex', alignItems:'center', gap:6, fontSize:12, color:'var(--text-muted)', cursor:'pointer' }}>
              <input type="checkbox" checked={active} onChange={e => setActive(e.target.checked)} />
              Active only
            </label>
          )}
        </div>
      </div>

      <div className="card card-glow" style={{ padding:0, overflow:'hidden' }}>
        {loading ? (
          <div className="empty-state"><Activity size={28}/><p>Loading…</p></div>
        ) : isEmpty ? (
          <div className="empty-state">
            <Activity size={36}/>
            <p>No signals logged yet. History builds as the pipeline runs.</p>
          </div>
        ) : (
          <>
            <table className="data-table">
              <thead>
                <tr>
                  <th onClick={() => setSortDir(d => d === 'asc' ? 'desc' : 'asc')} style={{ cursor:'pointer' }}>
                    Time {sortDir === 'asc' ? <ChevronUp size={11}/> : <ChevronDown size={11}/>}
                  </th>
                  <th>Symbol</th><th>Signal</th><th>Confidence</th>
                  <th>Deliv %</th><th>PCR</th>
                  <th>Entry</th><th>Target</th><th>SL</th><th>Model</th><th>Status</th><th>Forecast</th><th></th>
                </tr>
              </thead>
              <tbody>
                {displaySignals.map(s => {
                  const isExpanded = expandedId === s.id
                  return (
                    <React.Fragment key={s.id}>
                      <tr style={liveSignals.some(l => l.id === s.id && page === 1)
                          ? { animation: 'fadeInRow 0.4s ease' } : undefined}>
                        <td className="text-muted text-sm">
                          {new Date(s.ts + 'Z').toLocaleString('en-IN', { dateStyle:'short', timeStyle:'short' })}
                        </td>
                        <td>
                          <div style={{ display:'flex', alignItems:'center', gap:8 }}>
                            <TickerLogo symbol={s.symbol} size={28} />
                            <span className="text-mono" style={{ fontWeight:600 }}>{s.symbol.replace('.NS','')}</span>
                          </div>
                        </td>
                        <td>
                          <span
                            className={`signal-badge ${s.signal_type}`}
                            style={{ cursor: s.signal_type !== 'HOLD' ? 'pointer' : undefined }}
                            title={s.signal_type !== 'HOLD' ? `Click to ${s.signal_type}` : undefined}
                            onClick={() => s.signal_type !== 'HOLD' && setOrderDefaults({ symbol: s.symbol, direction: s.signal_type === 'SELL' ? 'SELL' : 'BUY', entryPrice: s.entry_price, targetPrice: s.target_price, stopLoss: s.stop_loss })}
                          >{s.signal_type}</span>
                        </td>
                        <td className="text-mono text-sm">{(s.confidence * 100).toFixed(0)}%</td>
                        {/* Phase 12: delivery % green when >40%, dashes until DB has data */}
                        <td style={{ color: s.delivery_pct && s.delivery_pct > 0.4 ? 'var(--green)' : 'inherit' }} className="text-mono text-sm">
                          {s.delivery_pct != null ? `${(s.delivery_pct * 100).toFixed(0)}%` : '—'}
                        </td>
                        <td className="text-mono text-sm">{s.pcr_ratio != null ? s.pcr_ratio.toFixed(2) : '—'}</td>
                        <td className="text-mono text-sm">{s.entry_price  != null ? `₹${s.entry_price.toLocaleString('en-IN')}` : '—'}</td>
                        <td className="text-mono text-sm">{s.target_price != null ? `₹${s.target_price.toLocaleString('en-IN')}` : '—'}</td>
                        <td className="text-mono text-sm">{s.stop_loss    != null ? `₹${s.stop_loss.toLocaleString('en-IN')}` : '—'}</td>
                        <td className="text-muted text-sm">{s.model_version}</td>
                        <td><span className={`risk-badge ${s.is_active ? 'low' : 'med'}`}>{s.is_active ? 'OPEN' : 'CLOSED'}</span></td>
                        <td>
                          <button
                            className="btn-outline btn"
                            style={{ padding:'3px 7px', fontSize:11 }}
                            onClick={() => setForecastSym(s.symbol)}
                            title="AI Forecast"
                          >📈</button>
                        </td>
                        <td>
                          {s.explanation && (
                            <button
                              className="btn-outline btn"
                              style={{ padding:'3px 7px', fontSize:11, color: isExpanded ? 'var(--accent-blue)' : undefined }}
                              onClick={() => setExpandedId(isExpanded ? null : s.id)}
                              title="Why this signal?"
                            ><MessageSquare size={12}/></button>
                          )}
                        </td>
                      </tr>
                      {isExpanded && s.explanation && (
                        <tr>
                        <td colSpan={13} style={{ padding:'10px 16px', background:'var(--surface-raised, rgba(255,255,255,0.03))', borderTop:'none' }}>
                            <div style={{ display:'flex', gap:8, alignItems:'flex-start' }}>
                              <MessageSquare size={14} style={{ marginTop:2, flexShrink:0, color:'var(--accent-blue)' }}/>
                              <p style={{ margin:0, fontSize:13, lineHeight:1.6, color:'var(--text-secondary)', fontStyle:'italic' }}>
                                {s.explanation}
                              </p>
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  )
                })}
              </tbody>
            </table>
            <div className="pagination">
              <button disabled={page <= 1} onClick={() => setPage(p => p - 1)}><ChevronLeft size={14}/></button>
              <span className="text-sm text-muted">Page {page} of {totalPages}</span>
              <button disabled={page >= totalPages} onClick={() => setPage(p => p + 1)}><ChevronRight size={14}/></button>
            </div>
          </>
        )}
      </div>

      <ForecastModal symbol={forecastSym} onClose={() => setForecastSym(null)} />
      <OrderModal defaults={orderDefaults} onClose={() => setOrderDefaults(null)} />
    </div>
  )
}

