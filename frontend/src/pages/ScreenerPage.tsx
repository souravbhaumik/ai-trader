import { useEffect, useState, useCallback } from 'react'
import { BarChart2, Search, ChevronUp, ChevronDown, ChevronsUpDown, ChevronLeft, ChevronRight } from 'lucide-react'
import { apiClient } from '../api/client'

interface ScreenerRow {
  symbol: string; name: string; sector: string; market_cap: number | null
  price: number | null; change_pct: number | null; volume: number | null
  in_nifty50: boolean; in_nifty500: boolean
}
interface ScreenerResult { total: number; page: number; per_page: number; rows: ScreenerRow[]; broker?: string; is_configured?: boolean; warning?: string }

type SortKey = 'symbol' | 'market_cap' | 'name' | 'sector'
type SortDir = 'asc' | 'desc'

function SortIcon({ field, sortKey, dir }: { field: SortKey; sortKey: SortKey; dir: SortDir }) {
  if (field !== sortKey) return <ChevronsUpDown size={11} className="sort-icon" />
  return dir === 'asc' ? <ChevronUp size={11} className="sort-icon" /> : <ChevronDown size={11} className="sort-icon" />
}

export default function ScreenerPage() {
  const [result, setResult]     = useState<ScreenerResult | null>(null)
  const [loading, setLoading]   = useState(true)
  const [q, setQ]               = useState('')
  const [sector, setSector]     = useState('ALL')
  const [sectors, setSectors]   = useState<string[]>([])
  const [signal, setSignal]     = useState('ALL')
  const [page, setPage]         = useState(1)
  const [sortBy, setSortBy]     = useState<SortKey>('market_cap')
  const [sortDir, setSortDir]   = useState<SortDir>('desc')

  const PER_PAGE = 50

  const fetch = useCallback(() => {
    setLoading(true)
    const params = new URLSearchParams({
      page: String(page), per_page: String(PER_PAGE),
      sort_by: sortBy, sort_dir: sortDir,
    })
    if (q.trim())         params.set('q', q.trim())
    if (sector !== 'ALL') params.set('sector', sector)
    if (signal !== 'ALL') params.set('signal', signal)
    apiClient.get<ScreenerResult>(`/screener?${params}`)
      .then(r => setResult(r.data))
      .catch(() => setResult(null))
      .finally(() => setLoading(false))
  }, [q, sector, signal, page, sortBy, sortDir])

  useEffect(() => {
    apiClient.get<{ sectors: string[] }>('/screener/sectors')
      .then(r => setSectors(r.data.sectors ?? []))
      .catch(() => {})
  }, [])

  useEffect(() => { setPage(1) }, [q, sector, signal, sortBy, sortDir])
  useEffect(() => { fetch() }, [fetch])

  function toggleSort(col: SortKey) {
    if (col === sortBy) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortBy(col); setSortDir('desc') }
  }

  const totalPages = result ? Math.ceil(result.total / PER_PAGE) : 1
  const isEmpty    = !loading && (result?.rows.length ?? 0) === 0

  return (
    <div className="screener-page">
      <div className="section-header">
        <div>
          <h2 className="section-title">Market Screener</h2>
          <p className="text-muted text-sm" style={{ marginTop:4 }}>
            {result ? `${result.total.toLocaleString()} stocks · via ${result.broker ?? 'yfinance'}` : 'Nifty 500 universe'}
          </p>
        </div>
      </div>

      {result?.warning && (
        <div style={{ padding:'8px 14px', background:'var(--yellow-dim)', border:'1px solid var(--yellow)', borderRadius:8, fontSize:12, color:'var(--yellow)', marginBottom:12 }}>
          ⚠ {result.warning}
        </div>
      )}

      {/* ── Filters ──────────────────────────────────────────────────────── */}
      <div className="screener-filters">
        <div className="search-box">
          <Search size={14} />
          <input placeholder="Search symbol or name…" value={q} onChange={e => setQ(e.target.value)} />
        </div>
        <select value={sector} onChange={e => setSector(e.target.value)}>
          <option value="ALL">All Sectors</option>
          {sectors.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        <select value={signal} onChange={e => setSignal(e.target.value)}>
          <option value="ALL">All Signals</option>
          <option value="BUY">BUY</option>
          <option value="SELL">SELL</option>
          <option value="HOLD">HOLD</option>
        </select>
      </div>

      <div className="card card-glow" style={{ padding:0, overflow:'hidden' }}>
        {loading ? (
          <div className="empty-state"><BarChart2 size={28}/><p>Loading…</p></div>
        ) : isEmpty ? (
          <div className="empty-state">
            <BarChart2 size={36}/>
            <p>No stocks match your filters, or the universe hasn't been populated yet.</p>
            <p className="text-sm text-muted" style={{ marginTop:8 }}>Run the populate script: <code>docker exec ai-trader-backend-1 python scripts/populate_universe.py</code></p>
          </div>
        ) : (
          <>
            <table className="data-table">
              <thead>
                <tr>
                  <th onClick={() => toggleSort('symbol')} style={{ cursor:'pointer' }}>
                    Symbol <SortIcon field="symbol" sortKey={sortBy} dir={sortDir}/>
                  </th>
                  <th onClick={() => toggleSort('name')} style={{ cursor:'pointer' }}>
                    Name <SortIcon field="name" sortKey={sortBy} dir={sortDir}/>
                  </th>
                  <th onClick={() => toggleSort('sector')} style={{ cursor:'pointer' }}>
                    Sector <SortIcon field="sector" sortKey={sortBy} dir={sortDir}/>
                  </th>
                  <th>Price</th>
                  <th>Change</th>
                  <th onClick={() => toggleSort('market_cap')} style={{ cursor:'pointer' }}>
                    Mkt Cap <SortIcon field="market_cap" sortKey={sortBy} dir={sortDir}/>
                  </th>
                  <th>Index</th>
                </tr>
              </thead>
              <tbody>
                {result!.rows.map(r => (
                  <tr key={r.symbol}>
                    <td className="text-mono" style={{ fontWeight:600 }}>{r.symbol.replace('.NS','')}</td>
                    <td className="text-sm">{r.name}</td>
                    <td><span className="sector-tag">{r.sector}</span></td>
                    <td className="text-mono text-sm">
                      {r.price != null ? `₹${r.price.toLocaleString('en-IN', { maximumFractionDigits:2 })}` : '—'}
                    </td>
                    <td className={`text-mono text-sm ${(r.change_pct ?? 0) >= 0 ? 'text-green' : 'text-red'}`}>
                      {r.change_pct != null ? `${r.change_pct >= 0 ? '+' : ''}${r.change_pct.toFixed(2)}%` : '—'}
                    </td>
                    <td className="text-mono text-sm">
                      {r.market_cap != null ? `₹${(r.market_cap / 1e7).toFixed(0)}Cr` : '—'}
                    </td>
                    <td className="text-sm text-muted">
                      {r.in_nifty50 ? 'N50 · ' : ''}{r.in_nifty500 ? 'N500' : ''}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            {/* Pagination */}
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
