import { useState, FormEvent } from 'react'
import { TrendingUp, Search } from 'lucide-react'
import ForecastModal from '../components/ForecastModal'

export default function ForecastPage() {
  const [inputVal,  setInputVal]  = useState('')
  const [symbol,    setSymbol]    = useState<string | null>(null)

  const handleSearch = (e: FormEvent) => {
    e.preventDefault()
    const sym = inputVal.trim().toUpperCase()
    if (sym) setSymbol(sym)
  }

  return (
    <div className="signals-page">
      <div className="section-header">
        <div>
          <h2 className="section-title">AI Forecast &amp; Anomaly</h2>
          <p className="text-muted text-sm" style={{ marginTop: 4 }}>
            TFT 5-day price forecast · LSTM Autoencoder anomaly detection
          </p>
        </div>
      </div>

      {/* Search bar */}
      <form onSubmit={handleSearch} style={{ display: 'flex', gap: 10, marginBottom: 24, maxWidth: 420 }}>
        <input
          value={inputVal}
          onChange={e => setInputVal(e.target.value)}
          placeholder="Symbol (e.g. RELIANCE.NS)"
          style={{ flex: 1, background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px', color: 'var(--text)', fontSize: 13 }}
        />
        <button type="submit" className="btn btn-primary"
          disabled={!inputVal.trim()}
          style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
          <Search size={14} /> Analyse
        </button>
      </form>

      {/* Empty state */}
      <div style={{ padding: '60px 0', textAlign: 'center', color: 'var(--text-muted)' }}>
        <TrendingUp size={40} style={{ opacity: 0.3, marginBottom: 12 }} />
        <p style={{ fontSize: 14 }}>Enter a symbol above to run TFT forecast and LSTM anomaly detection.</p>
        <p style={{ fontSize: 12, marginTop: 6 }}>Examples: RELIANCE.NS · TCS.NS · INFY.NS</p>
        <p style={{ fontSize: 12, marginTop: 4, color: 'var(--text-muted)' }}>
          Tip: Click the <strong>📈 Forecast</strong> button on any signal, opportunity, or screener row for instant analysis.
        </p>
      </div>

      {/* Forecast modal */}
      {symbol && <ForecastModal symbol={symbol} onClose={() => setSymbol(null)} />}
    </div>
  )
}
