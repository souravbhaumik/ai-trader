import { useRef, useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createChart, LineSeries, IChartApi, ISeriesApi, LineData } from 'lightweight-charts'
import { TrendingUp, AlertTriangle, X, Loader, Info, RefreshCw } from 'lucide-react'
import { apiClient } from '../api/client'

// ── API shapes ────────────────────────────────────────────────────────────────
interface ForecastResponse {
  symbol: string; prices: number[]; returns: number[]
  base_price: number; horizon_days: number; version: string
}
interface AnomalyResponse {
  symbol: string; score: number; mse: number; threshold: number
  is_anomaly: boolean; version: string
}
interface NewsArticle {
  id: string; symbol: string; headline: string; summary: string | null
  source: string; url: string | null; sentiment: string
  score: number; confidence: number; published_at: string
}
interface SignalRefreshResponse {
  id: string; symbol: string; ts: string; signal_type: string
  confidence: number; entry_price: number | null; target_price: number | null
  stop_loss: number | null; model_version: string; is_active: boolean
  sentiment_score: number; refreshed: boolean
}

// ── Chip ──────────────────────────────────────────────────────────────────────
export function Chip({ label, value, color, muted }: { label: string; value: string; color?: string; muted?: boolean }) {
  return (
    <div style={{ background: 'var(--bg-hover)', border: '1px solid var(--border)', borderRadius: 8, padding: '6px 12px', display: 'flex', flexDirection: 'column', gap: 2 }}>
      <span style={{ fontSize: 10, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 0.5 }}>{label}</span>
      <span style={{ fontSize: 13, fontWeight: 600, color: color ?? (muted ? 'var(--text-muted)' : 'var(--text)'), fontFamily: 'monospace' }}>{value}</span>
    </div>
  )
}

// ── Age formatter ─────────────────────────────────────────────────────────────
function _ageLabel(isoStr: string): string {
  const diff = Math.floor((Date.now() - new Date(isoStr).getTime()) / 1000)
  if (diff < 60)  return `${diff}s ago`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  return `${Math.floor(diff / 3600)}h ago`
}

// ── Chart ─────────────────────────────────────────────────────────────────────
// Returns YYYY-MM-DD string for a date offset by `tradingDays` business days from `from`
function addTradingDays(from: Date, tradingDays: number): string {
  const d = new Date(from)
  let added = 0
  while (added < tradingDays) {
    d.setDate(d.getDate() + 1)
    const dow = d.getDay()
    if (dow !== 0 && dow !== 6) added++ // skip Sat/Sun
  }
  return d.toISOString().slice(0, 10) // 'YYYY-MM-DD'
}

export function ForecastChart({ data, basePrice }: { data: number[]; basePrice: number }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef     = useRef<IChartApi | null>(null)
  const seriesRef    = useRef<ISeriesApi<'Line'> | null>(null)

  useEffect(() => {
    if (!containerRef.current) return
    if (chartRef.current) { chartRef.current.remove(); chartRef.current = null; seriesRef.current = null }

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth, height: 240,
      layout: { background: { color: 'transparent' }, textColor: '#94a3b8' },
      grid: { vertLines: { color: 'rgba(255,255,255,0.05)' }, horzLines: { color: 'rgba(255,255,255,0.05)' } },
      timeScale: { borderColor: 'rgba(255,255,255,0.1)', timeVisible: false },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.1)' },
    })
    chartRef.current = chart
    const series = chart.addSeries(LineSeries, { color: '#3b82f6', lineWidth: 2, crosshairMarkerVisible: true, priceLineVisible: true })
    seriesRef.current = series

    // Use real calendar dates so lightweight-charts tooltips show correct dates.
    // D+0 = today (base price), D+1…D+N = next N trading days.
    const today = new Date()
    const lineData: LineData[] = [
      { time: today.toISOString().slice(0, 10) as LineData['time'], value: basePrice },
      ...data.map((price, i) => ({
        time: addTradingDays(today, i + 1) as LineData['time'],
        value: price,
      })),
    ]
    series.setData(lineData)
    chart.timeScale().fitContent()

    const ro = new ResizeObserver(() => {
      if (containerRef.current && chartRef.current)
        chartRef.current.applyOptions({ width: containerRef.current.clientWidth })
    })
    if (containerRef.current) ro.observe(containerRef.current)
    return () => { ro.disconnect(); chart.remove(); chartRef.current = null }
  }, [data, basePrice])

  return <div ref={containerRef} style={{ width: '100%' }} />
}

// ── Anomaly gauge ─────────────────────────────────────────────────────────────
export function AnomalyGauge({ score, threshold, isAnomaly }: { score: number; threshold: number; isAnomaly: boolean }) {
  const pct   = Math.min((score / (threshold * 2)) * 100, 100)
  const color = isAnomaly ? 'var(--red)' : score > threshold * 0.7 ? 'var(--yellow)' : 'var(--green)'
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontSize: 13, fontWeight: 600, color }}>
          {isAnomaly ? '⚠ Anomaly Detected' : '✓ Normal Behaviour'}
        </span>
        <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
          Score: <strong style={{ color }}>{score.toFixed(5)}</strong> / threshold: {threshold.toFixed(5)}
        </span>
      </div>
      <div style={{ background: 'var(--bg-hover)', borderRadius: 8, overflow: 'hidden', height: 12 }}>
        <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 8, transition: 'width 0.6s ease' }} />
      </div>
      <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
        Reconstruction error (MSE) vs learned threshold. Values above threshold indicate unusual price behaviour.
      </div>
    </div>
  )
}

// ── Modal ─────────────────────────────────────────────────────────────────────
interface Props { symbol: string | null; onClose: () => void }

export default function ForecastModal({ symbol, onClose }: Props) {
  const encoded = symbol ? encodeURIComponent(symbol) : ''
  const queryClient = useQueryClient()
  const [refreshing, setRefreshing] = useState(false)
  const [refreshError, setRefreshError] = useState<string | null>(null)
  const [freshSignal, setFreshSignal] = useState<SignalRefreshResponse | null>(null)

  const { data: forecast, isLoading: loadingFc, error: fcErr } = useQuery<ForecastResponse>({
    queryKey: ['forecast', symbol],
    queryFn: () => apiClient.get<ForecastResponse>(`/forecasts/${encoded}`).then(r => r.data),
    enabled: !!symbol,
    retry: false,
  })
  const { data: anomaly, isLoading: loadingAn, error: anErr } = useQuery<AnomalyResponse>({
    queryKey: ['anomaly', symbol],
    queryFn: () => apiClient.get<AnomalyResponse>(`/forecasts/${encoded}/anomaly`).then(r => r.data),
    enabled: !!symbol,
    retry: false,
  })
  const { data: newsFeed, isLoading: loadingNews } = useQuery<NewsArticle[]>({
    queryKey: ['news-feed', symbol],
    queryFn: () => apiClient.get<NewsArticle[]>(`/news/feed?symbol=${encodeURIComponent(symbol!.replace('.NS',''))}&limit=10`).then(r => r.data),
    enabled: !!symbol,
    retry: false,
  })

  // Detect stale news (oldest article in feed > 60 min)
  const newsIsStale = newsFeed && newsFeed.length > 0
    ? (Date.now() - new Date(newsFeed[0].published_at).getTime()) > 60 * 60 * 1000
    : false

  const handleRefresh = async () => {
    if (!symbol || refreshing) return
    const sym = symbol.replace('.NS', '')
    setRefreshing(true)
    setRefreshError(null)
    try {
      const res = await apiClient.post<SignalRefreshResponse>(`/signals/${encodeURIComponent(sym)}/refresh`)
      setFreshSignal(res.data)
      // Invalidate the news feed query so it reloads fresh articles
      queryClient.invalidateQueries({ queryKey: ['news-feed', symbol] })
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string }; status?: number } }
      if (err.response?.status === 429) {
        setRefreshError(err.response?.data?.detail ?? 'Rate limited. Please wait before refreshing again.')
      } else {
        setRefreshError(err.response?.data?.detail ?? 'Refresh failed. Please try again.')
      }
    } finally {
      setRefreshing(false)
    }
  }

  if (!symbol) return null

  const displaySym = symbol.replace('.NS', '')

  return (
    // Backdrop
    <div
      style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(0,0,0,0.7)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      {/* Card */}
      <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 16, width: '100%', maxWidth: 760, maxHeight: '90vh', overflowY: 'auto', display: 'flex', flexDirection: 'column' }}>

        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '18px 24px', borderBottom: '1px solid var(--border)', position: 'sticky', top: 0, background: 'var(--bg-card)', zIndex: 1 }}>
          <div>
            <div style={{ fontWeight: 700, fontSize: 18 }}>{displaySym} — AI Forecast & Anomaly</div>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>TFT 5-day price forecast · LSTM Autoencoder anomaly detection</div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            {/* Refresh button */}
            <button
              onClick={handleRefresh}
              disabled={refreshing}
              title="Refresh signal: fetches latest news + recomputes signal"
              style={{
                display: 'flex', alignItems: 'center', gap: 6,
                background: refreshing ? 'var(--bg-hover)' : 'var(--accent)',
                border: '1px solid var(--border)', borderRadius: 8,
                padding: '6px 12px', cursor: refreshing ? 'not-allowed' : 'pointer',
                color: refreshing ? 'var(--text-muted)' : '#fff',
                fontSize: 12, fontWeight: 600, transition: 'all 0.2s',
              }}
            >
              <RefreshCw size={13} style={{ animation: refreshing ? 'spin 1s linear infinite' : 'none' }} />
              {refreshing ? 'Refreshing…' : '↻ Refresh Signal'}
            </button>
            <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', padding: 4 }}>
              <X size={20} />
            </button>
          </div>
        </div>

        <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 24 }}>

          {/* Refresh error banner */}
          {refreshError && (
            <div style={{ padding: '10px 14px', background: 'rgba(239,68,68,0.1)', border: '1px solid var(--red)', borderRadius: 8, fontSize: 13, color: 'var(--red)' }}>
              {refreshError}
            </div>
          )}

          {/* Stale news warning banner */}
          {newsIsStale && !freshSignal && (
            <div style={{ padding: '10px 14px', background: 'rgba(234,179,8,0.1)', border: '1px solid var(--yellow)', borderRadius: 8, fontSize: 12, color: 'var(--yellow)', display: 'flex', gap: 8, alignItems: 'center' }}>
              <AlertTriangle size={14} style={{ flexShrink: 0 }} />
              <span>News data may be stale (latest article is over 1 hour old). Click <strong>↻ Refresh Signal</strong> to fetch the latest headlines and recompute the signal.</span>
            </div>
          )}

          {/* Freshly refreshed signal banner */}
          {freshSignal && (
            <div style={{ padding: '12px 16px', background: 'rgba(34,197,94,0.1)', border: '1px solid var(--green)', borderRadius: 10, fontSize: 13 }}>
              <div style={{ fontWeight: 700, color: 'var(--green)', marginBottom: 6 }}>✓ Signal refreshed {_ageLabel(freshSignal.ts)}</div>
              <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                <Chip label="Direction" value={freshSignal.signal_type} color={freshSignal.signal_type === 'BUY' ? 'var(--green)' : 'var(--red)'} />
                <Chip label="Confidence" value={`${(freshSignal.confidence * 100).toFixed(1)}%`} />
                {freshSignal.entry_price != null && <Chip label="Entry" value={`₹${freshSignal.entry_price.toFixed(2)}`} />}
                {freshSignal.target_price != null && <Chip label="Target" value={`₹${freshSignal.target_price.toFixed(2)}`} color="var(--green)" />}
                {freshSignal.stop_loss != null && <Chip label="Stop Loss" value={`₹${freshSignal.stop_loss.toFixed(2)}`} color="var(--red)" />}
                <Chip label="Sentiment" value={freshSignal.sentiment_score >= 0.1 ? '▲ Positive' : freshSignal.sentiment_score <= -0.1 ? '▼ Negative' : '● Neutral'} color={freshSignal.sentiment_score >= 0.1 ? 'var(--green)' : freshSignal.sentiment_score <= -0.1 ? 'var(--red)' : 'var(--text-muted)'} />
                <Chip label="Model" value={freshSignal.model_version} muted />
              </div>
            </div>
          )}

          {/* TFT forecast section */}
          <div className="settings-section">
            <div className="settings-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <TrendingUp size={15} /> TFT 5-Day Price Forecast
            </div>
            {loadingFc && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text-muted)', fontSize: 13, padding: '20px 0' }}>
                <Loader size={16} /> Running TFT inference…
              </div>
            )}
            {!loadingFc && fcErr && (
              <div style={{ color: 'var(--red)', fontSize: 13, padding: '12px 0' }}>
                {(fcErr as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Forecast unavailable.'}
              </div>
            )}
            {!loadingFc && forecast && (
              <>
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
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

          {/* LSTM Anomaly section */}
          <div className="settings-section">
            <div className="settings-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <AlertTriangle size={15} /> LSTM Anomaly Score
            </div>
            {loadingAn && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text-muted)', fontSize: 13, padding: '20px 0' }}>
                <Loader size={16} /> Running LSTM inference…
              </div>
            )}
            {!loadingAn && anErr && (
              <div style={{ color: 'var(--red)', fontSize: 13, padding: '12px 0' }}>
                {(anErr as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Anomaly score unavailable.'}
              </div>
            )}
            {!loadingAn && anomaly && (
              <>
                <AnomalyGauge score={anomaly.score} threshold={anomaly.threshold} isAnomaly={anomaly.is_anomaly} />
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 16 }}>
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

          {/* News Sentiment section */}
          <div className="settings-section">
            <div className="settings-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              📰 Recent News & Sentiment
            </div>
            {loadingNews && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text-muted)', fontSize: 13, padding: '20px 0' }}>
                <Loader size={16} /> Loading news…
              </div>
            )}
            {!loadingNews && (!newsFeed || newsFeed.length === 0) && (
              <div style={{ color: 'var(--text-muted)', fontSize: 13, padding: '12px 0' }}>
                No recent news found for {displaySym}. News is ingested every 15 minutes during market hours.
              </div>
            )}
            {!loadingNews && newsFeed && newsFeed.length > 0 && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {newsFeed.map(article => {
                  const sentColor = article.sentiment === 'positive' ? 'var(--green)' : article.sentiment === 'negative' ? 'var(--red)' : 'var(--text-muted)'
                  const sentIcon  = article.sentiment === 'positive' ? '▲' : article.sentiment === 'negative' ? '▼' : '●'
                  const sourceLabel: Record<string, string> = {
                    et_markets: 'ET Markets', moneycontrol: 'Moneycontrol',
                    business_std: 'Business Standard', livemint: 'Livemint',
                    nse_corp: 'NSE', bse_corp: 'BSE', google_news: 'Google News',
                    yahoo_finance: 'Yahoo Finance', hindu_bl: 'Business Line',
                    financial_exp: 'Financial Express', ndtv_profit: 'NDTV Profit',
                    zee_biz: 'Zee Business', reuters_markets: 'Reuters Markets',
                    reuters_world: 'Reuters World', ap_business: 'AP Business',
                    investing_com: 'Investing.com', macro_news: 'Global Macro',
                  }
                  return (
                    <div key={article.id} style={{ background: 'var(--bg-hover)', border: '1px solid var(--border)', borderRadius: 10, padding: '12px 14px' }}>
                      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8 }}>
                        <div style={{ flex: 1 }}>
                          {article.url ? (
                            <a href={article.url} target="_blank" rel="noopener noreferrer"
                              style={{ fontWeight: 600, fontSize: 13, color: 'var(--text)', textDecoration: 'none', lineHeight: 1.4 }}
                              onMouseOver={e => (e.currentTarget.style.textDecoration = 'underline')}
                              onMouseOut={e => (e.currentTarget.style.textDecoration = 'none')}
                            >{article.headline}</a>
                          ) : (
                            <span style={{ fontWeight: 600, fontSize: 13, lineHeight: 1.4 }}>{article.headline}</span>
                          )}
                          {article.summary && (
                            <p style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 5, lineHeight: 1.5 }}>{article.summary}</p>
                          )}
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4, flexShrink: 0 }}>
                          <span style={{ fontSize: 12, fontWeight: 700, color: sentColor }}>{sentIcon} {article.sentiment}</span>
                          <span style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: 'monospace' }}>{(article.score * 100).toFixed(0)}%</span>
                        </div>
                      </div>
                      <div style={{ marginTop: 8, fontSize: 11, color: 'var(--text-muted)', display: 'flex', gap: 12 }}>
                        <span>{sourceLabel[article.source] ?? article.source}</span>
                        <span>{new Date(article.published_at).toLocaleString('en-IN', { dateStyle: 'short', timeStyle: 'short' })}</span>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
