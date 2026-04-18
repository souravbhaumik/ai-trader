import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Activity, ChevronUp, ChevronDown, ChevronsUpDown, ChevronLeft, ChevronRight } from 'lucide-react'
import { apiClient } from '../api/client'

interface Signal {
  id: string; symbol: string; ts: string; signal_type: 'BUY' | 'SELL' | 'HOLD'
  confidence: number; entry_price: number | null; target_price: number | null
  stop_loss: number | null; model_version: string; is_active: boolean
}
interface SignalResult { total: number; page: number; per_page: number; signals: Signal[] }

type SortDir = 'asc' | 'desc'
type FilterType = 'ALL' | 'BUY' | 'SELL' | 'HOLD'

export default function SignalLogPage() {
  const [filter, setFilter]   = useState<FilterType>('ALL')
  const [active, setActive]   = useState(false)
  const [page, setPage]       = useState(1)
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  const PER_PAGE = 50

  const { data: result, isFetching: loading } = useQuery({
    queryKey: ['signals', filter, active, page, sortDir],
    queryFn: () => {
      const params = new URLSearchParams({ page: String(page), per_page: String(PER_PAGE) })
      if (filter !== 'ALL') params.set('type', filter)
      if (active)           params.set('active', 'true')
      return apiClient.get<SignalResult>(`/signals?${params}`).then(r => r.data)
    },
    placeholderData: (prev) => prev,
  })

  const totalPages = result ? Math.ceil(result.total / PER_PAGE) : 1
  const isEmpty    = !loading && (result?.signals.length ?? 0) === 0

  return (
    <div className="signal-page">
      <div className="section-header">
        <div>
          <h2 className="section-title">Signal Log</h2>
          <p className="text-muted text-sm" style={{ marginTop:4 }}>
            {result ? `${result.total.toLocaleString()} signals total` : 'Full history of every AI-generated trading signal'}
          </p>
        </div>
        <div style={{ display:'flex', gap:8, alignItems:'center' }}>
          {(['ALL','BUY','SELL','HOLD'] as FilterType[]).map(f => (
            <button key={f} onClick={() => setFilter(f)}
              className={`filter-chip ${filter === f ? 'active' : ''}`}>{f}</button>
          ))}
          <label style={{ display:'flex', alignItems:'center', gap:6, fontSize:12, color:'var(--text-muted)', cursor:'pointer' }}>
            <input type="checkbox" checked={active} onChange={e => setActive(e.target.checked)} />
            Active only
          </label>
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
                  <th>Entry</th><th>Target</th><th>SL</th><th>Model</th><th>Status</th>
                </tr>
              </thead>
              <tbody>
                {result!.signals.map(s => (
                  <tr key={s.id}>
                    <td className="text-muted text-sm">
                      {new Date(s.ts + 'Z').toLocaleString('en-IN', { dateStyle:'short', timeStyle:'short' })}
                    </td>
                    <td className="text-mono" style={{ fontWeight:600 }}>{s.symbol.replace('.NS','')}</td>
                    <td><span className={`signal-badge ${s.signal_type}`}>{s.signal_type}</span></td>
                    <td className="text-mono text-sm">{(s.confidence * 100).toFixed(0)}%</td>
                    <td className="text-mono text-sm">{s.entry_price  != null ? `₹${s.entry_price.toLocaleString('en-IN')}` : '—'}</td>
                    <td className="text-mono text-sm">{s.target_price != null ? `₹${s.target_price.toLocaleString('en-IN')}` : '—'}</td>
                    <td className="text-mono text-sm">{s.stop_loss    != null ? `₹${s.stop_loss.toLocaleString('en-IN')}` : '—'}</td>
                    <td className="text-muted text-sm">{s.model_version}</td>
                    <td><span className={`risk-badge ${s.is_active ? 'low' : 'med'}`}>{s.is_active ? 'OPEN' : 'CLOSED'}</span></td>
                  </tr>
                ))}
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
    </div>
  )
}
