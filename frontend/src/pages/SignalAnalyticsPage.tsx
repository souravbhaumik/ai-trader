import React, { useState, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Activity, Target, TrendingUp, AlertCircle, Clock, BarChart2, Download, FileText } from 'lucide-react'
import { apiClient } from '../api/client'

// ─── Types ───────────────────────────────────────────────────────────────────
interface PerformanceMetrics {
  period_days: number
  total_signals: number
  evaluated_signals: number
  hit_target_count: number
  hit_stoploss_count: number
  still_open_count: number
  target_hit_rate: number
  stoploss_hit_rate: number
  avg_return_1d: number | null
  avg_return_3d: number | null
  avg_return_5d: number | null
  avg_max_gain: number | null
  avg_max_drawdown: number | null
  buy_count: number
  sell_count: number
  buy_win_rate: number
  sell_win_rate: number
}

interface OutcomeRow {
  signal_id: string
  symbol: string
  signal_type: string
  signal_ts: string
  entry_price: number
  target_price: number | null
  stop_loss: number | null
  confidence: number
  price_1d: number | null
  price_3d: number | null
  price_5d: number | null
  return_1d_pct: number | null
  return_5d_pct: number | null
  hit_target: boolean
  hit_stoploss: boolean
  is_evaluated: boolean
  outcome: 'WIN' | 'LOSS' | 'PENDING' | 'NEUTRAL'
}

// ─── Helpers ─────────────────────────────────────────────────────────────────
const fmt = (n: number | null, digits = 2) =>
  n == null ? '—' : n.toLocaleString('en-IN', { maximumFractionDigits: digits })

const fmtPct = (n: number | null) =>
  n == null ? '—' : `${n > 0 ? '+' : ''}${n.toFixed(2)}%`

const outcomeStyle = (o: string): React.CSSProperties => {
  switch (o) {
    case 'WIN':     return { color: 'var(--color-up)', fontWeight: 700 }
    case 'LOSS':    return { color: 'var(--color-down)', fontWeight: 700 }
    case 'PENDING': return { color: 'var(--color-muted)', fontWeight: 600 }
    default:        return { color: 'var(--text-secondary)', fontWeight: 600 }
  }
}

// ─── Component ───────────────────────────────────────────────────────────────
export default function SignalAnalyticsPage() {
  const [period, setPeriod] = useState<7 | 30 | 90>(30)
  const [csvLoading, setCsvLoading] = useState(false)
  const printRef = useRef<HTMLDivElement>(null)

  const { data: perf, isLoading: perfLoading } = useQuery<PerformanceMetrics>({
    queryKey: ['signal-analytics-perf', period],
    queryFn: () =>
      apiClient
        .get<PerformanceMetrics>(`/signals/analytics/performance?period_days=${period}`)
        .then(r => r.data),
    refetchInterval: 120_000,
  })

  const { data: outcomesResp, isLoading: outLoading } = useQuery<{ outcomes: OutcomeRow[] }>({
    queryKey: ['signal-analytics-outcomes', period],
    queryFn: () =>
      apiClient
        .get<{ outcomes: OutcomeRow[] }>(`/signals/analytics/outcomes?limit=100`)
        .then(r => r.data),
    refetchInterval: 120_000,
  })

  const outcomes = outcomesResp?.outcomes ?? []
  const hasData  = !perfLoading && perf && perf.total_signals > 0

  // ── Exports ────────────────────────────────────────────────────────────────
  const handleCsvExport = async () => {
    setCsvLoading(true)
    try {
      const resp = await apiClient.get(
        `/signals/analytics/export/csv?period_days=${period}`,
        { responseType: 'blob' },
      )
      const url = window.URL.createObjectURL(new Blob([resp.data as BlobPart]))
      const a   = document.createElement('a')
      a.href    = url
      a.download = `signal_outcomes_${period}d_${new Date().toISOString().slice(0,10)}.csv`
      a.click()
      window.URL.revokeObjectURL(url)
    } finally {
      setCsvLoading(false)
    }
  }

  const handlePdfExport = () => {
    window.print()
  }

  return (
    <div className="signal-page">
      {/* ── Print styles injected at runtime ── */}
      <style>{`
        @media print {
          body * { visibility: hidden; }
          .analytics-print-region, .analytics-print-region * { visibility: visible; }
          .analytics-print-region { position: absolute; inset: 0; padding: 24px; background: #fff; color: #000; }
          .analytics-no-print { display: none !important; }
          table { border-collapse: collapse; width: 100%; font-size: 10px; }
          th, td { border: 1px solid #ccc; padding: 4px 8px; text-align: left; }
          th { background: #f0f0f0; font-weight: 700; }
          .print-header { margin-bottom: 20px; }
          .print-title { font-size: 18px; font-weight: 800; color: #000; }
          .print-sub { font-size: 12px; color: #555; margin-top: 4px; }
          .stat-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 8px; margin-bottom: 16px; }
          .stat-box { border: 1px solid #ccc; padding: 8px; border-radius: 4px; }
          .stat-label { font-size: 9px; color: #666; margin-bottom: 4px; text-transform: uppercase; }
          .stat-value { font-size: 16px; font-weight: 800; }
        }
      `}</style>

      <div ref={printRef} className="analytics-print-region">
      {/* ── Header ── */}
      <div className="page-header analytics-no-print">
        <div className="page-header-left">
          <div className="page-header-title">Signal Analytics</div>
          <div className="page-header-sub">Predicted vs actual price performance for AI-generated signals</div>
        </div>

        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {/* Period selector */}
          {([7, 30, 90] as const).map(d => (
            <button
              key={d}
              onClick={() => setPeriod(d)}
              style={{
                padding: '5px 14px',
                borderRadius: 6,
                border: 'none',
                cursor: 'pointer',
                fontSize: 13,
                fontWeight: 600,
                background: period === d ? 'var(--color-accent)' : 'var(--bg-card)',
                color:      period === d ? '#fff' : 'var(--text-secondary)',
              }}
            >
              {d}D
            </button>
          ))}

          {/* Export buttons */}
          <button
            onClick={handleCsvExport}
            disabled={csvLoading}
            title="Download raw CSV from database"
            style={{
              display: 'flex', alignItems: 'center', gap: 6,
              padding: '5px 14px', borderRadius: 6, border: 'none',
              cursor: csvLoading ? 'wait' : 'pointer', fontSize: 13, fontWeight: 600,
              background: 'var(--bg-card)', color: 'var(--color-up)',
              opacity: csvLoading ? 0.6 : 1,
            }}
          >
            <Download size={14} />
            {csvLoading ? 'Exporting…' : 'CSV'}
          </button>
          <button
            onClick={handlePdfExport}
            title="Print / Save as PDF"
            style={{
              display: 'flex', alignItems: 'center', gap: 6,
              padding: '5px 14px', borderRadius: 6, border: 'none',
              cursor: 'pointer', fontSize: 13, fontWeight: 600,
              background: 'var(--bg-card)', color: 'var(--color-accent)',
            }}
          >
            <FileText size={14} />
            PDF
          </button>
        </div>
      </div>

      {/* ── Print-only report header ── */}
      <div className="print-header" style={{ display: 'none' }}>
        <div className="print-title">Signal Analytics Report — Last {period} days</div>
        <div className="print-sub">Generated: {new Date().toLocaleString('en-IN')}</div>
        {perf && (
          <div className="stat-grid" style={{ marginTop: 12 }}>
            {[
              { label: 'Total Signals',    value: perf.total_signals },
              { label: 'Evaluated',        value: perf.evaluated_signals },
              { label: 'Target Hit Rate',  value: `${perf.target_hit_rate}%` },
              { label: 'SL Hit Rate',      value: `${perf.stoploss_hit_rate}%` },
              { label: 'Avg 1D Return',    value: perf.avg_return_1d != null ? `${perf.avg_return_1d > 0 ? '+' : ''}${perf.avg_return_1d}%` : '—' },
              { label: 'Avg 5D Return',    value: perf.avg_return_5d != null ? `${perf.avg_return_5d > 0 ? '+' : ''}${perf.avg_return_5d}%` : '—' },
              { label: 'BUY Win Rate',     value: `${perf.buy_win_rate}% (${perf.buy_count} signals)` },
              { label: 'SELL Win Rate',    value: `${perf.sell_win_rate}% (${perf.sell_count} signals)` },
              { label: 'Still Open',       value: perf.still_open_count },
              { label: 'Max Avg Gain',     value: perf.avg_max_gain != null ? `${perf.avg_max_gain}%` : '—' },
              { label: 'Max Avg Drawdown', value: perf.avg_max_drawdown != null ? `${perf.avg_max_drawdown}%` : '—' },
            ].map(s => (
              <div key={s.label} className="stat-box">
                <div className="stat-label">{s.label}</div>
                <div className="stat-value">{String(s.value)}</div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── Stat Cards ── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 12, margin: '0 0 20px' }}>
        <StatCard
          icon={<Activity size={16} />}
          label="Total Signals"
          value={perfLoading ? '…' : fmt(perf?.total_signals ?? 0, 0)}
          sub={perfLoading ? '' : `${perf?.evaluated_signals ?? 0} evaluated`}
        />
        <StatCard
          icon={<Target size={16} />}
          label="Target Hit Rate"
          value={perfLoading ? '…' : `${perf?.target_hit_rate ?? 0}%`}
          color={perf && perf.target_hit_rate >= 50 ? 'var(--color-up)' : undefined}
          sub={perfLoading ? '' : `${perf?.hit_target_count ?? 0} hits`}
        />
        <StatCard
          icon={<AlertCircle size={16} />}
          label="SL Hit Rate"
          value={perfLoading ? '…' : `${perf?.stoploss_hit_rate ?? 0}%`}
          color={perf && perf.stoploss_hit_rate > 40 ? 'var(--color-down)' : undefined}
          sub={perfLoading ? '' : `${perf?.hit_stoploss_count ?? 0} stopped`}
        />
        <StatCard
          icon={<TrendingUp size={16} />}
          label="Avg 1D Return"
          value={perfLoading ? '…' : fmtPct(perf?.avg_return_1d ?? null)}
          color={perf?.avg_return_1d != null ? (perf.avg_return_1d >= 0 ? 'var(--color-up)' : 'var(--color-down)') : undefined}
        />
        <StatCard
          icon={<BarChart2 size={16} />}
          label="Avg 5D Return"
          value={perfLoading ? '…' : fmtPct(perf?.avg_return_5d ?? null)}
          color={perf?.avg_return_5d != null ? (perf.avg_return_5d >= 0 ? 'var(--color-up)' : 'var(--color-down)') : undefined}
        />
        <StatCard
          icon={<Clock size={16} />}
          label="Still Open"
          value={perfLoading ? '…' : fmt(perf?.still_open_count ?? 0, 0)}
          sub="pending evaluation"
        />
      </div>

      {/* ── BUY vs SELL breakdown ── */}
      {hasData && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 20 }}>
          <div className="signal-card" style={{ padding: 16 }}>
            <div style={{ color: 'var(--color-up)', fontWeight: 700, fontSize: 13, marginBottom: 8 }}>BUY Signals</div>
            <div style={{ fontSize: 26, fontWeight: 800, color: 'var(--color-up)' }}>{perf!.buy_win_rate}%</div>
            <div style={{ color: 'var(--text-secondary)', fontSize: 12, marginTop: 4 }}>win rate · {perf!.buy_count} signals</div>
          </div>
          <div className="signal-card" style={{ padding: 16 }}>
            <div style={{ color: 'var(--color-down)', fontWeight: 700, fontSize: 13, marginBottom: 8 }}>SELL Signals</div>
            <div style={{ fontSize: 26, fontWeight: 800, color: 'var(--color-down)' }}>{perf!.sell_win_rate}%</div>
            <div style={{ color: 'var(--text-secondary)', fontSize: 12, marginTop: 4 }}>win rate · {perf!.sell_count} signals</div>
          </div>
        </div>
      )}

      {/* ── Outcomes table ── */}
      <div className="signal-card" style={{ padding: 0, overflow: 'hidden' }}>
        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', fontWeight: 700, fontSize: 13 }}>
          Recent Signal Outcomes
        </div>

        {outLoading ? (
          <div style={{ padding: 32, textAlign: 'center', color: 'var(--text-secondary)' }}>Loading outcomes…</div>
        ) : outcomes.length === 0 ? (
          <EmptyState />
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr style={{ background: 'var(--bg-row-header)', color: 'var(--text-secondary)' }}>
                  {['Symbol', 'Type', 'Signal Date', 'Entry ₹', 'Target ₹', 'SL ₹', 'Conf %', 'Actual 1D ₹', 'Actual 5D ₹', 'Ret 1D', 'Ret 5D', 'Outcome'].map(h => (
                    <th key={h} style={{ padding: '8px 12px', textAlign: 'left', fontWeight: 600, whiteSpace: 'nowrap' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {outcomes.map(o => (
                  <tr key={o.signal_id} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '8px 12px', fontWeight: 700 }}>{o.symbol}</td>
                    <td style={{ padding: '8px 12px' }}>
                      <span style={{
                        padding: '2px 8px', borderRadius: 4, fontSize: 11, fontWeight: 700,
                        background: o.signal_type === 'BUY' ? 'rgba(0,200,83,.15)' : 'rgba(255,82,82,.15)',
                        color: o.signal_type === 'BUY' ? 'var(--color-up)' : 'var(--color-down)',
                      }}>
                        {o.signal_type}
                      </span>
                    </td>
                    <td style={{ padding: '8px 12px', whiteSpace: 'nowrap', color: 'var(--text-secondary)' }}>
                      {new Date(o.signal_ts).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: '2-digit' })}
                    </td>
                    <td style={{ padding: '8px 12px' }}>{fmt(o.entry_price)}</td>
                    <td style={{ padding: '8px 12px', color: 'var(--color-up)' }}>{fmt(o.target_price)}</td>
                    <td style={{ padding: '8px 12px', color: 'var(--color-down)' }}>{fmt(o.stop_loss)}</td>
                    <td style={{ padding: '8px 12px' }}>{(o.confidence * 100).toFixed(1)}%</td>
                    <td style={{ padding: '8px 12px' }}>{fmt(o.price_1d)}</td>
                    <td style={{ padding: '8px 12px' }}>{fmt(o.price_5d)}</td>
                    <td style={{ padding: '8px 12px', ...pctStyle(o.return_1d_pct) }}>{fmtPct(o.return_1d_pct)}</td>
                    <td style={{ padding: '8px 12px', ...pctStyle(o.return_5d_pct) }}>{fmtPct(o.return_5d_pct)}</td>
                    <td style={{ padding: '8px 12px' }}>
                      <span style={outcomeStyle(o.outcome)}>{o.outcome}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  </div>
  )
}

// ─── Sub-components ───────────────────────────────────────────────────────────
function StatCard({ icon, label, value, sub, color }: {
  icon: React.ReactNode
  label: string
  value: string
  sub?: string
  color?: string
}) {
  return (
    <div className="signal-card" style={{ padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: 'var(--text-secondary)', fontSize: 12, marginBottom: 8 }}>
        {icon} {label}
      </div>
      <div style={{ fontSize: 24, fontWeight: 800, color: color ?? 'var(--text-primary)' }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 4 }}>{sub}</div>}
    </div>
  )
}

function EmptyState() {
  return (
    <div style={{ padding: '48px 24px', textAlign: 'center' }}>
      <Activity size={40} style={{ color: 'var(--text-secondary)', marginBottom: 12, opacity: 0.4 }} />
      <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 8 }}>No outcomes tracked yet</div>
      <div style={{ color: 'var(--text-secondary)', fontSize: 13, maxWidth: 400, margin: '0 auto', lineHeight: 1.6 }}>
        From now on, every signal generated by the ML pipeline automatically records its predicted entry, target, and stop-loss here.
        Actual prices are filled in each evening (5 PM) and the next morning (8:20 AM) once market data is available.
      </div>
    </div>
  )
}

function pctStyle(n: number | null): React.CSSProperties {
  if (n == null) return {}
  return { color: n >= 0 ? 'var(--color-up)' : 'var(--color-down)', fontWeight: 600 }
}
