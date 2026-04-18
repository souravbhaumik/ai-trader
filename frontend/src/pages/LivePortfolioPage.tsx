import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Zap, TrendingUp, TrendingDown, PlusCircle, XCircle, RefreshCw, AlertTriangle } from 'lucide-react'
import toast from 'react-hot-toast'
import { apiClient } from '../api/client'
import { useAuthStore } from '../store/authStore'

interface LivePosition {
  symbol: string
  exchange: string
  product_type: string
  direction: string
  qty: number
  avg_buy_price: number
  ltp: number
  pnl: number
  pnl_pct: number
}

interface LiveOrder {
  id: string
  broker_order_id: string | null
  symbol: string
  direction: string
  qty: number
  order_type: string
  product_type: string
  price: number
  status: string
  message: string | null
  placed_at: string
}

// ── Order form ─────────────────────────────────────────────────────────────────
function OrderForm({ onSuccess }: { onSuccess: () => void }) {
  const [symbol, setSymbol] = useState('')
  const [direction, setDirection] = useState<'BUY' | 'SELL'>('BUY')
  const [qty, setQty] = useState(1)
  const [orderType, setOrderType] = useState<'MARKET' | 'LIMIT'>('MARKET')
  const [productType, setProductType] = useState<'DELIVERY' | 'INTRADAY'>('DELIVERY')
  const [price, setPrice] = useState(0)

  const qc = useQueryClient()
  const mutation = useMutation({
    mutationFn: () =>
      apiClient.post('/portfolio/live/orders', {
        symbol: symbol.toUpperCase(),
        direction,
        qty,
        order_type: orderType,
        product_type: productType,
        price: orderType === 'LIMIT' ? price : 0,
      }).then(r => r.data),
    onSuccess: () => {
      toast.success(`${direction} order placed for ${symbol.toUpperCase()}`)
      qc.invalidateQueries({ queryKey: ['live-orders'] })
      qc.invalidateQueries({ queryKey: ['live-positions'] })
      setSymbol('')
      setQty(1)
      setPrice(0)
      onSuccess()
    },
    onError: (err: any) => {
      const msg = err?.response?.data?.detail ?? 'Order failed'
      toast.error(msg)
    },
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!symbol.trim()) { toast.error('Enter a symbol'); return }
    mutation.mutate()
  }

  return (
    <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <div>
          <label className="form-label">Symbol</label>
          <input
            className="form-input"
            placeholder="e.g. RELIANCE"
            value={symbol}
            onChange={e => setSymbol(e.target.value.toUpperCase())}
            style={{ textTransform: 'uppercase' }}
          />
        </div>
        <div>
          <label className="form-label">Qty</label>
          <input
            className="form-input"
            type="number"
            min={1}
            value={qty}
            onChange={e => setQty(Math.max(1, parseInt(e.target.value) || 1))}
          />
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <div>
          <label className="form-label">Direction</label>
          <select className="form-input" value={direction} onChange={e => setDirection(e.target.value as 'BUY' | 'SELL')}>
            <option value="BUY">BUY</option>
            <option value="SELL">SELL</option>
          </select>
        </div>
        <div>
          <label className="form-label">Order Type</label>
          <select className="form-input" value={orderType} onChange={e => setOrderType(e.target.value as 'MARKET' | 'LIMIT')}>
            <option value="MARKET">MARKET</option>
            <option value="LIMIT">LIMIT</option>
          </select>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <div>
          <label className="form-label">Product Type</label>
          <select className="form-input" value={productType} onChange={e => setProductType(e.target.value as 'DELIVERY' | 'INTRADAY')}>
            <option value="DELIVERY">DELIVERY (CNC)</option>
            <option value="INTRADAY">INTRADAY (MIS)</option>
          </select>
        </div>
        {orderType === 'LIMIT' && (
          <div>
            <label className="form-label">Price (₹)</label>
            <input
              className="form-input"
              type="number"
              min={0}
              step={0.05}
              value={price}
              onChange={e => setPrice(parseFloat(e.target.value) || 0)}
            />
          </div>
        )}
      </div>

      <button
        type="submit"
        className="btn"
        style={{
          background: direction === 'BUY' ? 'var(--green)' : 'var(--red)',
          color: '#fff',
          padding: '10px 20px',
          fontWeight: 600,
        }}
        disabled={mutation.isPending}
      >
        {mutation.isPending ? 'Placing…' : `Place ${direction} Order`}
      </button>
    </form>
  )
}

// ── Position row ───────────────────────────────────────────────────────────────
function PositionRow({ pos }: { pos: LivePosition }) {
  const up = pos.pnl >= 0
  return (
    <tr>
      <td className="text-mono">{pos.symbol}</td>
      <td><span style={{ fontSize: 11, padding: '2px 6px', borderRadius: 4, background: pos.direction === 'BUY' ? 'var(--green-10)' : 'var(--red-10)', color: pos.direction === 'BUY' ? 'var(--green)' : 'var(--red)' }}>{pos.direction}</span></td>
      <td className="text-mono">{pos.qty}</td>
      <td className="text-mono">₹{pos.avg_buy_price.toLocaleString('en-IN', { maximumFractionDigits: 2 })}</td>
      <td className="text-mono">₹{pos.ltp.toLocaleString('en-IN', { maximumFractionDigits: 2 })}</td>
      <td className="text-mono" style={{ color: up ? 'var(--green)' : 'var(--red)' }}>
        {up ? '+' : ''}₹{pos.pnl.toFixed(2)} ({pos.pnl_pct.toFixed(2)}%)
      </td>
      <td style={{ fontSize: 11, color: 'var(--text-muted)' }}>{pos.product_type}</td>
    </tr>
  )
}

// ── Order status badge ─────────────────────────────────────────────────────────
function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    PENDING: '#f39c12', OPEN: '#3498db', COMPLETE: 'var(--green)',
    CANCELLED: 'var(--text-muted)', REJECTED: 'var(--red)',
  }
  return (
    <span style={{ fontSize: 11, padding: '2px 6px', borderRadius: 4, background: `${colors[status] ?? '#888'}22`, color: colors[status] ?? '#888', fontWeight: 600 }}>
      {status}
    </span>
  )
}

// ── Main page ──────────────────────────────────────────────────────────────────
export default function LivePortfolioPage() {
  const tradingMode = useAuthStore(s => s.tradingMode)
  const [showForm, setShowForm] = useState(false)
  const qc = useQueryClient()

  const { data: positions = [], isLoading: posLoading, refetch: refetchPos } = useQuery<LivePosition[]>({
    queryKey: ['live-positions'],
    queryFn: () => apiClient.get('/portfolio/live/positions').then(r => r.data),
    enabled: tradingMode === 'live',
    retry: false,
  })

  const { data: holdings = [], isLoading: holdLoading, refetch: refetchHold } = useQuery<LivePosition[]>({
    queryKey: ['live-holdings'],
    queryFn: () => apiClient.get('/portfolio/live/holdings').then(r => r.data),
    enabled: tradingMode === 'live',
    retry: false,
  })

  const { data: orders = [], isLoading: ordLoading, refetch: refetchOrd } = useQuery<LiveOrder[]>({
    queryKey: ['live-orders'],
    queryFn: () => apiClient.get('/portfolio/live/orders?limit=50').then(r => r.data),
    enabled: tradingMode === 'live',
    retry: false,
  })

  const cancelMutation = useMutation({
    mutationFn: (orderId: string) => apiClient.delete(`/portfolio/live/orders/${orderId}`).then(r => r.data),
    onSuccess: () => {
      toast.success('Order cancelled')
      qc.invalidateQueries({ queryKey: ['live-orders'] })
    },
    onError: (err: any) => toast.error(err?.response?.data?.detail ?? 'Cancel failed'),
  })

  const totalPnl = positions.reduce((s, p) => s + p.pnl, 0)
  const holdingPnl = holdings.reduce((s, h) => s + h.pnl, 0)

  if (tradingMode !== 'live') {
    return (
      <div style={{ padding: 32 }}>
        <div className="card" style={{ maxWidth: 500, margin: '0 auto', textAlign: 'center', padding: 40 }}>
          <AlertTriangle size={40} style={{ color: 'var(--text-muted)', margin: '0 auto 16px' }} />
          <h3 style={{ marginBottom: 8 }}>Live Mode Not Active</h3>
          <p className="text-muted" style={{ marginBottom: 20 }}>
            Switch to <strong>Live Trading</strong> mode in Settings and add your Angel One credentials to use this page.
          </p>
          <a href="/settings" className="btn" style={{ display: 'inline-flex' }}>Go to Settings</a>
        </div>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20, padding: '0 0 32px' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div>
          <h2 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
            <Zap size={20} style={{ color: 'var(--accent)' }} /> Live Portfolio
          </h2>
          <p className="text-muted" style={{ margin: '4px 0 0', fontSize: 13 }}>
            Real positions and orders via Angel One
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            className="btn-outline btn"
            onClick={() => { refetchPos(); refetchHold(); refetchOrd() }}
            style={{ display: 'flex', alignItems: 'center', gap: 6 }}
          >
            <RefreshCw size={14} /> Refresh
          </button>
          <button
            className="btn"
            onClick={() => setShowForm(f => !f)}
            style={{ display: 'flex', alignItems: 'center', gap: 6, background: showForm ? 'var(--red)' : 'var(--accent)' }}
          >
            {showForm ? <XCircle size={14} /> : <PlusCircle size={14} />}
            {showForm ? 'Cancel' : 'New Order'}
          </button>
        </div>
      </div>

      {/* Order form */}
      {showForm && (
        <div className="card card-glow">
          <div className="card-header"><span className="card-title">Place Live Order</span></div>
          <div style={{ padding: '0 0 4px' }}>
            <OrderForm onSuccess={() => setShowForm(false)} />
          </div>
        </div>
      )}

      {/* P&L summary */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16 }}>
        {[
          {
            label: 'Intraday P&L', value: totalPnl,
            sub: `${positions.length} open position${positions.length !== 1 ? 's' : ''}`,
          },
          {
            label: 'Holdings P&L', value: holdingPnl,
            sub: `${holdings.length} holding${holdings.length !== 1 ? 's' : ''}`,
          },
          {
            label: 'Total P&L', value: totalPnl + holdingPnl,
            sub: 'Intraday + Holdings',
          },
        ].map(({ label, value, sub }) => (
          <div key={label} className="stat-card">
            <div className="stat-card-inner">
              <div>
                <div className="stat-icon">{value >= 0 ? <TrendingUp size={18} /> : <TrendingDown size={18} />}</div>
                <div className="stat-label">{label}</div>
                <div className="stat-value" style={{ color: value >= 0 ? 'var(--green)' : 'var(--red)' }}>
                  {value >= 0 ? '+' : ''}₹{value.toFixed(2)}
                </div>
                <div className="stat-sub text-muted">{sub}</div>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Intraday positions */}
      <div className="card card-glow">
        <div className="card-header"><span className="card-title">Intraday Positions</span></div>
        {posLoading ? (
          <div className="empty-state"><RefreshCw size={24} style={{ animation: 'spin 1s linear infinite' }} /><p>Loading…</p></div>
        ) : positions.length === 0 ? (
          <div className="empty-state"><Zap size={28} /><p>No open intraday positions</p></div>
        ) : (
          <table className="data-table">
            <thead><tr>
              <th>Symbol</th><th>Dir</th><th>Qty</th><th>Avg Price</th><th>LTP</th><th>P&L</th><th>Type</th>
            </tr></thead>
            <tbody>
              {positions.map(p => <PositionRow key={p.symbol + p.direction} pos={p} />)}
            </tbody>
          </table>
        )}
      </div>

      {/* Holdings */}
      <div className="card card-glow">
        <div className="card-header"><span className="card-title">Delivery Holdings</span></div>
        {holdLoading ? (
          <div className="empty-state"><RefreshCw size={24} /><p>Loading…</p></div>
        ) : holdings.length === 0 ? (
          <div className="empty-state"><Zap size={28} /><p>No delivery holdings</p></div>
        ) : (
          <table className="data-table">
            <thead><tr>
              <th>Symbol</th><th>Dir</th><th>Qty</th><th>Avg Price</th><th>LTP</th><th>P&L</th><th>Type</th>
            </tr></thead>
            <tbody>
              {holdings.map(h => <PositionRow key={h.symbol} pos={h} />)}
            </tbody>
          </table>
        )}
      </div>

      {/* Order history */}
      <div className="card card-glow">
        <div className="card-header"><span className="card-title">Recent Orders</span></div>
        {ordLoading ? (
          <div className="empty-state"><RefreshCw size={24} /><p>Loading…</p></div>
        ) : orders.length === 0 ? (
          <div className="empty-state"><Zap size={28} /><p>No orders placed yet</p></div>
        ) : (
          <table className="data-table">
            <thead><tr>
              <th>Symbol</th><th>Dir</th><th>Qty</th><th>Type</th><th>Price</th><th>Status</th><th>Time</th><th></th>
            </tr></thead>
            <tbody>
              {orders.map(o => (
                <tr key={o.id}>
                  <td className="text-mono">{o.symbol}</td>
                  <td>
                    <span style={{ fontSize: 11, padding: '2px 6px', borderRadius: 4, background: o.direction === 'BUY' ? 'var(--green-10)' : 'var(--red-10)', color: o.direction === 'BUY' ? 'var(--green)' : 'var(--red)' }}>
                      {o.direction}
                    </span>
                  </td>
                  <td className="text-mono">{o.qty}</td>
                  <td className="text-mono text-sm">{o.order_type}</td>
                  <td className="text-mono">{o.price > 0 ? `₹${o.price.toLocaleString('en-IN')}` : 'MKT'}</td>
                  <td><StatusBadge status={o.status} /></td>
                  <td className="text-muted text-sm">
                    {new Date(o.placed_at + 'Z').toLocaleString('en-IN', { dateStyle: 'short', timeStyle: 'short' })}
                  </td>
                  <td>
                    {(o.status === 'PENDING' || o.status === 'OPEN') && (
                      <button
                        className="btn-outline btn"
                        style={{ padding: '2px 8px', fontSize: 12 }}
                        onClick={() => cancelMutation.mutate(o.id)}
                        disabled={cancelMutation.isPending}
                      >
                        Cancel
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
