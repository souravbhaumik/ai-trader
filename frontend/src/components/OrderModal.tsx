import { useState, useEffect } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { X } from 'lucide-react'
import toast from 'react-hot-toast'
import { apiClient } from '../api/client'
import { useAuthStore } from '../store/authStore'

// ── Props ─────────────────────────────────────────────────────────────────────
export interface OrderDefaults {
  symbol: string            // e.g. RELIANCE.NS
  direction: 'BUY' | 'SELL'
  entryPrice?: number | null
  targetPrice?: number | null
  stopLoss?: number | null
}

interface Props {
  defaults: OrderDefaults | null
  onClose: () => void
}

// ── Modal ─────────────────────────────────────────────────────────────────────
export default function OrderModal({ defaults, onClose }: Props) {
  const tradingMode = useAuthStore(s => s.tradingMode)
  const qc = useQueryClient()

  const [direction, setDirection]     = useState<'BUY' | 'SELL'>(defaults?.direction ?? 'BUY')
  const [qty, setQty]                 = useState(1)
  const [entryPrice, setEntryPrice]   = useState(String(defaults?.entryPrice ?? ''))
  const [targetPrice, setTargetPrice] = useState(String(defaults?.targetPrice ?? ''))
  const [stopLoss, setStopLoss]       = useState(String(defaults?.stopLoss ?? ''))
  const [orderType, setOrderType]     = useState<'MARKET' | 'LIMIT'>('LIMIT')
  const [productType, setProductType] = useState<'DELIVERY' | 'INTRADAY'>('DELIVERY')
  const [notes, setNotes]             = useState('')
  const [showLiveConfirm, setShowLiveConfirm] = useState(false)

  useEffect(() => {
    if (!defaults) return
    setDirection(defaults.direction ?? 'BUY')
    setEntryPrice(String(defaults.entryPrice ?? ''))
    setTargetPrice(String(defaults.targetPrice ?? ''))
    setStopLoss(String(defaults.stopLoss ?? ''))
  }, [defaults])

  const isPaper   = tradingMode !== 'live'
  const sym       = defaults?.symbol.replace('.NS', '') ?? ''

  const paperMutation = useMutation({
    mutationFn: () =>
      apiClient.post('/portfolio/paper/orders', {
        symbol:       defaults!.symbol,
        direction,
        qty,
        entry_price:  parseFloat(entryPrice),
        target_price: targetPrice ? parseFloat(targetPrice) : null,
        stop_loss:    stopLoss    ? parseFloat(stopLoss)    : null,
        notes:        notes.trim() || null,
      }).then(r => r.data),
    onSuccess: () => {
      toast.success(`Paper ${direction} — ${qty} × ${sym} opened`)
      qc.invalidateQueries({ queryKey: ['paper-summary'] })
      qc.invalidateQueries({ queryKey: ['paper-positions'] })
      onClose()
    },
    onError: (err: any) => toast.error(err?.response?.data?.detail ?? 'Order failed'),
  })

  const liveMutation = useMutation({
    mutationFn: () =>
      apiClient.post('/portfolio/live/orders', {
        symbol:       sym,
        direction,
        qty,
        order_type:   orderType,
        product_type: productType,
        price:        orderType === 'LIMIT' ? parseFloat(entryPrice) : 0,
      }).then(r => r.data),
    onSuccess: () => {
      toast.success(`Live ${direction} order placed — ${qty} × ${sym}`)
      qc.invalidateQueries({ queryKey: ['live-orders'] })
      qc.invalidateQueries({ queryKey: ['live-positions'] })
      onClose()
    },
    onError: (err: any) => toast.error(err?.response?.data?.detail ?? 'Order failed'),
  })

  if (!defaults) return null

  const modeLabel  = isPaper ? '📄 Paper' : '⚡ Live'
  const isPending  = paperMutation.isPending || liveMutation.isPending

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!entryPrice || isNaN(parseFloat(entryPrice)) || parseFloat(entryPrice) <= 0) {
      toast.error('Enter a valid entry price')
      return
    }
    if (isPaper) {
      paperMutation.mutate()
    } else {
      // Show confirmation dialog for live orders
      setShowLiveConfirm(true)
    }
  }

  const handleLiveConfirm = () => {
    setShowLiveConfirm(false)
    liveMutation.mutate()
  }

  return (
    <div
      style={{ position: 'fixed', inset: 0, zIndex: 1001, background: 'rgba(0,0,0,0.75)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 16, width: '100%', maxWidth: 480, position: 'relative', overflow: 'hidden' }}>

        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '16px 20px', borderBottom: '1px solid var(--border)' }}>
          <div>
            <div style={{ fontWeight: 700, fontSize: 17 }}>{sym} — Place Order</div>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>{modeLabel} Trading Mode</div>
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)' }}>
            <X size={20} />
          </button>
        </div>

        <form onSubmit={handleSubmit} style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 14 }}>

          {/* Direction */}
          <div>
            <button
              type="button"
              style={{
                width: '100%',
                padding: '10px 0',
                borderRadius: 8,
                fontWeight: 700,
                fontSize: 14,
                border: `2px solid ${direction === 'BUY' ? 'var(--green)' : 'var(--red)'}`,
                background: direction === 'BUY' ? 'var(--green-10)' : 'var(--red-10)',
                color: direction === 'BUY' ? 'var(--green)' : 'var(--red)',
                cursor: 'default',
              }}
            >
              {direction}
            </button>
          </div>

          {/* Qty + Entry Price */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            <div>
              <label className="form-label">Quantity</label>
              <input className="form-input" type="number" min={1} value={qty}
                onChange={e => setQty(Math.max(1, parseInt(e.target.value) || 1))} />
            </div>
            <div>
              <label className="form-label">Entry Price (₹) *</label>
              <input className="form-input" type="number" min={0.01} step="any"
                value={entryPrice} onChange={e => setEntryPrice(e.target.value)} />
            </div>
          </div>

          {/* Target + SL */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            <div>
              <label className="form-label">Target Price (₹) <span className="text-muted">optional</span></label>
              <input className="form-input" type="number" min={0.01} step="any"
                placeholder="—" value={targetPrice} onChange={e => setTargetPrice(e.target.value)} />
            </div>
            <div>
              <label className="form-label">Stop Loss (₹) <span className="text-muted">optional</span></label>
              <input className="form-input" type="number" min={0.01} step="any"
                placeholder="—" value={stopLoss} onChange={e => setStopLoss(e.target.value)} />
            </div>
          </div>

          {/* Live-only extras */}
          {!isPaper && (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
              <div>
                <label className="form-label">Order Type</label>
                <select className="form-input" value={orderType} onChange={e => setOrderType(e.target.value as 'MARKET' | 'LIMIT')}>
                  <option value="LIMIT">LIMIT</option>
                  <option value="MARKET">MARKET</option>
                </select>
              </div>
              <div>
                <label className="form-label">Product</label>
                <select className="form-input" value={productType} onChange={e => setProductType(e.target.value as 'DELIVERY' | 'INTRADAY')}>
                  <option value="DELIVERY">DELIVERY (CNC)</option>
                  <option value="INTRADAY">INTRADAY (MIS)</option>
                </select>
              </div>
            </div>
          )}

          {/* Notes (paper only) */}
          {isPaper && (
            <div>
              <label className="form-label">Notes <span className="text-muted">optional</span></label>
              <input className="form-input" placeholder="Trade thesis…" value={notes}
                onChange={e => setNotes(e.target.value)} maxLength={200} />
            </div>
          )}

          <button
            type="submit"
            className="btn"
            disabled={isPending}
            style={{
              background: direction === 'BUY' ? 'var(--green)' : 'var(--red)',
              color: '#fff',
              padding: '11px 0',
              fontWeight: 700,
              fontSize: 14,
              marginTop: 4,
            }}
          >
            {isPending
              ? 'Placing…'
              : `${modeLabel} ${direction} — ${qty} × ${sym}`}
          </button>
        </form>

        {/* Live order confirmation overlay */}
        {showLiveConfirm && (
          <div style={{
            position:'absolute', inset:0, background:'rgba(0,0,0,0.85)',
            borderRadius:16, display:'flex', alignItems:'center', justifyContent:'center',
            padding:24,
          }}>
            <div style={{ textAlign:'center', maxWidth:320 }}>
              <div style={{ fontSize:32, marginBottom:12 }}>⚠️</div>
              <div style={{ fontWeight:700, fontSize:16, marginBottom:8, color:'var(--yellow)' }}>
                Confirm Live Order
              </div>
              <div style={{ fontSize:13, color:'var(--text-muted)', marginBottom:6 }}>
                You are about to place a <strong>REAL</strong> order with your broker:
              </div>
              <div style={{
                background:'var(--bg-hover)', borderRadius:8, padding:'12px 16px',
                margin:'12px 0 20px', textAlign:'left', fontSize:13,
                border:'1px solid var(--border)',
              }}>
                <div><strong>{direction}</strong> {qty} × {sym}</div>
                <div>Price: ₹{entryPrice} ({orderType})</div>
                <div>Est. value: ₹{(qty * parseFloat(entryPrice || '0')).toLocaleString('en-IN')}</div>
              </div>
              <div style={{ fontSize:12, color:'var(--red)', marginBottom:18 }}>
                This will use real money. This action cannot be undone.
              </div>
              <div style={{ display:'flex', gap:10, justifyContent:'center' }}>
                <button className="btn btn-outline" onClick={() => setShowLiveConfirm(false)}>
                  Cancel
                </button>
                <button
                  className="btn"
                  onClick={handleLiveConfirm}
                  style={{ background:'var(--red)', color:'#fff', border:'none' }}
                >
                  Confirm Order
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
