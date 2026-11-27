import { useRef, useState } from 'react'
import type { User } from 'firebase/auth'
import { api, errorMessage } from '../lib/api'
import { formatLempiras } from '../lib/settlements'
import { Button } from './ui'

type Receipt = {
  solicitud_id: string
  fecha_cierre: string | null
  monto_base: string
  monto_ajuste: string
  monto_total: string
  cobrado_digital: string
  cobrado_efectivo: string
  monto_comision: string
  monto_neto_tecnico: string
}

export default function ServiceReceipt({ user, requestId }: { user: User; requestId: string }) {
  const printable = useRef<HTMLElement>(null)
  const [receipt, setReceipt] = useState<Receipt | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function openReceipt() {
    setLoading(true)
    setError('')
    try {
      setReceipt(await api<Receipt>(`/users/me/service-receipts/${encodeURIComponent(requestId)}`, user))
    } catch (err) { setError(errorMessage(err)) }
    finally { setLoading(false) }
  }

  function printReceipt() {
    const section = printable.current
    if (!section) return
    section.classList.add('service-receipt-print')
    try { window.print() }
    finally { section.classList.remove('service-receipt-print') }
  }

  if (!receipt) return <>
    <Button type="button" className="button-outline" disabled={loading} onClick={() => void openReceipt()}>
      {loading ? 'Cargando comprobante…' : 'Ver comprobante'}
    </Button>
    {error && <p role="alert">{error}</p>}
  </>

  const amounts = [
    ['Base', receipt.monto_base], ['Ajustes aprobados', receipt.monto_ajuste], ['Total', receipt.monto_total],
    ['Tarjeta', receipt.cobrado_digital], ['Efectivo', receipt.cobrado_efectivo],
    ['Comisión', receipt.monto_comision], ['Neto del técnico', receipt.monto_neto_tecnico],
  ]
  return <section ref={printable} className="account-card service-receipt">
    <h3>Comprobante de servicio</h3>
    <p>{receipt.solicitud_id}</p>
    {receipt.fecha_cierre && <p>{new Intl.DateTimeFormat('es-HN', {
      dateStyle: 'long', timeZone: 'America/Tegucigalpa',
    }).format(new Date(receipt.fecha_cierre))}</p>}
    <dl>
      {amounts.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{formatLempiras(value)}</dd></div>)}
    </dl>
    <div className="responsive-actions service-receipt-actions">
      <Button type="button" className="button-outline" onClick={() => setReceipt(null)}>Cerrar comprobante</Button>
      <Button type="button" onClick={printReceipt}>Imprimir comprobante</Button>
    </div>
  </section>
}
