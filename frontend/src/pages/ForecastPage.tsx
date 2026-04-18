import { useState, useRef, useEffect, FormEvent } from 'react'
import { createChart, LineSeries, IChartApi, ISeriesApi, LineData } from 'lightweight-charts'
import { TrendingUp, AlertTriangle, Search, Loader, Info } from 'lucide-react'
import toast from 'react-hot-toast'
import { apiClient } from '../api/client'

// ── API response shapes ───────────────────────────────────────────────────────
interface ForecastResponse {
  symbol: string
  prices: number[]
  returns: number[]
  base_price: number
  horizon_days: number
  version: string
}

interface AnomalyResponse {
  symbol: string
  score: number
  mse: number
  threshold: number
  is_anomaly: boolean
  version: string
}

// ── Forecast chart (lightweight-charts v5) ───────────────────────────────────
function ForecastChart({ data, basePrice }: { data: number[]; basePrice: number }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef     = useRef<IChartApi | null>(null)
  const seriesRef    = useRef<ISeriesApi<'Line'> | null>(null)

  useEffect(() => {
    if (!containerRef.current) return

    // Destroy previous chart instance
    if (chartRef.current) {
      chartRef.current.remove()
      chartRef.current = null
      seriesRef.current = null
    }

    const chart = createChart(containerRef.current, {
      width:  containerRef.current.clientWidth,
      height: 260,
      layout: {
        background: { color: 'transparent' },
        textColor:  '#94a3b8',
      },
      grid: {
        vertLines: { color: 'rgba(255,255,255,0.05)' },
        horzLines: { color: 'rgba(255,255,255,0.05)' },
      },
      timeScale: {
        borderColor: 'rgba(255,255,255,0.1)',
        tickMarkFormatter: (time: number) => `D+${time}`,
      },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.1)' },
    })
    chartRef.current = chart

    const series = chart.addSeries(LineSeries, {
      color:     '#3b82f6',
      lineWidth: 2,
      crosshairMarkerVisible: true,
      priceLineVisible: true,
    })
    seriesRef.current = series

    // Build line data: time 0 = base price, days 1..N = forecast
    const lineData: LineData[] = [
      { time: 0 as unknown as LineData['time'], value: basePrice },
      ...data.map((price, i) => ({
        time: (i + 1) as unknown as LineData['time'],
        value: price,
      })),
    ]
    series.setData(lineData)
    chart.timeScale().fitContent()

    const ro = new ResizeObserver(() => {
      if (containerRef.current && chartRef.current) {
        chartRef.current.applyOptions({ width: containerRef.current.clientWidth })
      }
    })
    if (containerRef.current) ro.observe(containerRef.current)

    return () => { ro.disconnect(); chart.remove(); chartRef.current = null }
  }, [data, basePrice])

  return <div ref={containerRef} style={{ width: '100%' }} />
}

// ── Anomaly score gauge ───────────────────────────────────────────────────────
function AnomalyGauge({ score, threshold, isAnomaly }: { score: number; threshold: number; isAnomaly: boolean }) {
  const pct = Math.min((score / (threshold * 2)) * 100, 100)
  const color = isAnomaly ? 'var(--red)' : score > threshold * 0.7 ? 'var(--yellow)' : 'var(--green)'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontSize: 13, fontWeight: 600, color }}>
          {isAnomaly ? '⚠ Anomaly Detected' : '✓ Normal Behaviour'}
        </span>
        <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
          Score: <strong style={{ color }}>{score.toFixed(5)}</strong>
          &nbsp;/ threshold: {threshold.toFixed(5)}
        </span>
      </div>

      {/* Bar */}
      <div style={{ background: 'var(--bg-hover)', borderRadius: 8, overflow: 'hidden', height: 12 }}>
        <div style={{
          width: `${pct}%`, height: '100%',
          background: color, borderRadius: 8,
          transition: 'width 0.6s ease',
        }} />
      </div>

      <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
        Reconstruction error (MSE) vs learned threshold. Values above threshold indicate unusual price behaviour.
      </div>
    </div>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────
export default function ForecastPage() {
  const [symbol,        setSymbol]        = useState('')
  const [inputVal,      setInputVal]      = useState('')
  const [forecast,      setForecast]      = useState<ForecastResponse | null>(null)
  const [anomaly,       setAnomaly]       = useState<AnomalyResponse | null>(null)
  const [loadingFc,     setLoadingFc]     = useState(false)
  const [loadingAn,     setLoadingAn]     = useState(false)
  const [fcError,       setFcError]       = useState<string | null>(null)
  const [anError,       setAnError]       = useState<string | null>(null)

  const handleSearch = async (e: FormEvent) => {
    e.preventDefault()
    const sym = inputVal.trim().toUpperCase()
    if (!sym) return
    setSymbol(sym)
    setForecast(null)
    setAnomaly(null)
    setFcError(null)
    setAnError(null)

    // Fire both requests in parallel
    setLoadingFc(true)
    setLoadingAn(true)

    apiClient.get<ForecastResponse>(`/forecasts/${encodeURIComponent(sym)}`)
      .then(r => setForecast(r.data))
      .catch(err => {
        const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        setFcError(detail ?? 'Forecast unavailable.')
        toast.error(`Forecast: ${detail ?? 'Unavailable'}`)
      })
      .finally(() => setLoadingFc(false))

    apiClient.get<AnomalyResponse>(`/forecasts/${encodeURIComponent(sym)}/anomaly`)
      .then(r => setAnomaly(r.data))
      .catch(err => {
        const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        setAnError(detail ?? 'Anomaly score unavailable.')
        toast.error(`Anomaly: ${detail ?? 'Unavailable'}`)
      })
      .finally(() => setLoadingAn(false))
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
      <form onSubmit={handleSearch}
        style={{ display: 'flex', gap: 10, marginBottom: 24, maxWidth: 420 }}>
        <input
          value={inputVal}
          onChange={e => setInputVal(e.target.value)}
          placeholder="Symbol (e.g. RELIANCE.NS)"
          style={{
            flex: 1,
            background: 'var(--bg-card)',
            border: '1px solid var(--border)',
            borderRadius: 8,
            padding: '8px 12px',
            color: 'var(--text)',
            fontSize: 13,
          }}
        />
        <button type="submit" className="btn btn-primary"
          disabled={loadingFc || loadingAn || !inputVal.trim()}
          style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
          {(loadingFc || loadingAn) ? <Loader size={14}/> : <Search size={14}/>}
          Analyse
        </button>
      </form>

      {/* Results */}
      {symbol && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

          {/* TFT Forecast card */}
          <div className="settings-section">
            <div className="settings-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <TrendingUp size={15}/> TFT 5-Day Price Forecast — {symbol}
            </div>

            {loadingFc && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text-muted)', fontSize: 13, padding: '20px 0' }}>
                <Loader size={16}/> Running TFT inference…
              </div>
            )}

            {!loadingFc && fcError && (
              <div style={{ color: 'var(--red)', fontSize: 13, padding: '12px 0' }}>{fcError}</div>
            )}

            {!loadingFc && forecast && (
              <>
                <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 16 }}>
                  <Chip label="Base Price" value={`₹${forecast.base_price.toFixed(2)}`} />
                  <Chip label="Horizon" value={`${forecast.horizon_days} days`} />
                  <Chip
                    label={`D+${forecast.horizon_days} Target`}
                    value={`₹${forecast.prices[forecast.prices.length - 1]?.toFixed(2) ?? '—'}`}
                    color={(forecast.returns[forecast.returns.length - 1] ?? 0) >= 0 ? 'var(--green)' : 'var(--red)'}
                  />
                  <Chip
                    label="Expected Return"
                    value={`${((forecast.returns[forecast.returns.length - 1] ?? 0) * 100).toFixed(2)}%`}
                    color={(forecast.returns[forecast.returns.length - 1] ?? 0) >= 0 ? 'var(--green)' : 'var(--red)'}
                  />
                  <Chip label="Model" value={forecast.version} muted />
                </div>

                <ForecastChart data={forecast.prices} basePrice={forecast.base_price} />

                {/* Day-by-day table */}
                <div style={{ marginTop: 16, overflowX: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                    <thead>
                      <tr style={{ background: 'var(--bg-hover)' }}>
                        {['Day', 'Price (₹)', 'Return'].map(h => (
                          <th key={h} style={{ padding: '6px 12px', textAlign: 'left', fontWeight: 600, color: 'var(--text-muted)', borderBottom: '1px solid var(--border)' }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      <tr style={{ borderBottom: '1px solid var(--border)' }}>
                        <td style={{ padding: '5px 12px', color: 'var(--text-muted)', fontStyle: 'italic' }}>Base (today)</td>
                        <td style={{ padding: '5px 12px', fontFamily: 'monospace' }}>₹{forecast.base_price.toFixed(2)}</td>
                        <td style={{ padding: '5px 12px', color: 'var(--text-muted)' }}>—</td>
                      </tr>
                      {forecast.prices.map((price, i) => {
                        const ret = forecast.returns[i] ?? 0
                        const color = ret >= 0 ? 'var(--green)' : 'var(--red)'
                        return (
                          <tr key={i} style={{ borderBottom: '1px solid var(--border)', background: i % 2 === 0 ? 'transparent' : 'var(--bg-hover)' }}>
                            <td style={{ padding: '5px 12px' }}>D+{i + 1}</td>
                            <td style={{ padding: '5px 12px', fontFamily: 'monospace' }}>₹{price.toFixed(2)}</td>
                            <td style={{ padding: '5px 12px', color, fontFamily: 'monospace', fontWeight: 600 }}>
                              {ret >= 0 ? '+' : ''}{(ret * 100).toFixed(2)}%
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </div>

          {/* LSTM Anomaly card */}
          <div className="settings-section">
            <div className="settings-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <AlertTriangle size={15}/> LSTM Anomaly Score — {symbol}
            </div>

            {loadingAn && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text-muted)', fontSize: 13, padding: '20px 0' }}>
                <Loader size={16}/> Running LSTM inference…
              </div>
            )}

            {!loadingAn && anError && (
              <div style={{ color: 'var(--red)', fontSize: 13, padding: '12px 0' }}>{anError}</div>
            )}

            {!loadingAn && anomaly && (
              <>
                <AnomalyGauge score={anomaly.score} threshold={anomaly.threshold} isAnomaly={anomaly.is_anomaly} />

                <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginTop: 16 }}>
                  <Chip label="MSE" value={anomaly.mse.toFixed(6)} />
                  <Chip label="Threshold" value={anomaly.threshold.toFixed(6)} />
                  <Chip label="Model" value={anomaly.version} muted />
                </div>

                <div style={{ marginTop: 14, padding: '10px 14px', background: 'var(--bg-hover)', borderRadius: 8, fontSize: 12, color: 'var(--text-muted)', display: 'flex', gap: 8 }}>
                  <Info size={14} style={{ flexShrink: 0, marginTop: 1 }} />
                  <span>
                    When an anomaly is detected the signal generation pipeline applies a <strong>20% score penalty</strong> to reduce position sizing risk. The LSTM Autoencoder was trained on 30-day sliding windows of OHLCV data.
                  </span>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* Empty state */}
      {!symbol && (
        <div style={{ padding: '60px 0', textAlign: 'center', color: 'var(--text-muted)' }}>
          <TrendingUp size={40} style={{ opacity: 0.3, marginBottom: 12 }} />
          <p style={{ fontSize: 14 }}>Enter a symbol above to run TFT forecast and LSTM anomaly detection.</p>
          <p style={{ fontSize: 12, marginTop: 6 }}>Examples: RELIANCE.NS · TCS.NS · INFY.NS</p>
        </div>
      )}
    </div>
  )
}

// ── Small chip label ──────────────────────────────────────────────────────────
function Chip({ label, value, color, muted }: { label: string; value: string; color?: string; muted?: boolean }) {
  return (
    <div style={{
      background: 'var(--bg-hover)',
      border: '1px solid var(--border)',
      borderRadius: 8,
      padding: '6px 12px',
      display: 'flex',
      flexDirection: 'column',
      gap: 2,
    }}>
      <span style={{ fontSize: 10, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 0.5 }}>{label}</span>
      <span style={{ fontSize: 13, fontWeight: 600, color: color ?? (muted ? 'var(--text-muted)' : 'var(--text)'), fontFamily: 'monospace' }}>{value}</span>
    </div>
  )
}
