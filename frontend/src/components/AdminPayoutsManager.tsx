import { useCallback, useEffect, useState } from 'react'
import type { User } from 'firebase/auth'
import { api, errorMessage } from '../lib/api'
import {
  adminPayoutsPath, bankAccountTypes, decideAdminPayout, fetchAdminPayoutBankDetails,
  payoutStatuses, validatePayoutDecisionInput,
  type AdminBankDetails, type AdminPayoutOrder, type PayoutDecisionAction, type PayoutStatus,
} from '../lib/payouts'
import { formatLempiras } from '../lib/settlements'
import { Button, Field } from './ui'

const filters: (PayoutStatus | 'TODAS')[] = ['PENDIENTE', 'PAGADA', 'RECHAZADA', 'TODAS']

export default function AdminPayoutsManager({ user }: { user: User }) {
  const [estado, setEstado] = useState<PayoutStatus | 'TODAS'>('PENDIENTE')
  const [items, setItems] = useState<AdminPayoutOrder[] | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [revealed, setRevealed] = useState<Record<string, AdminBankDetails>>({})
  const [busyId, setBusyId] = useState('')

  const load = useCallback(async () => {
    setItems(null)
    setError('')
    try {
      const query = estado === 'TODAS' ? '' : `?estado=${estado}`
      setItems((await api<{ ordenes: AdminPayoutOrder[] }>(`${adminPayoutsPath}${query}`, user)).ordenes)
    } catch (err) { setError(errorMessage(err)) }
  }, [estado, user])

  useEffect(() => { void load() }, [load])

  async function toggleBankDetails(orderId: string) {
    if (revealed[orderId]) {
      setRevealed(prev => {
        const next = { ...prev }
        delete next[orderId]
        return next
      })
      return
    }
    setBusyId(orderId)
    setError('')
    try {
      const details = await fetchAdminPayoutBankDetails(user, orderId)
      setRevealed(prev => ({ ...prev, [orderId]: details }))
    } catch (err) { setError(errorMessage(err)) }
    finally { setBusyId('') }
  }

  return (
    <section aria-labelledby="payouts-admin-title">
      <h2 id="payouts-admin-title">Órdenes de desembolso</h2>
      <div className="dossier-filters" role="group" aria-label="Filtrar desembolsos por estado">
        {filters.map(value => (
          <Button key={value} type="button" className={estado === value ? '' : 'button-outline'} aria-pressed={estado === value} onClick={() => setEstado(value)}>
            {value === 'TODAS' ? 'Todas' : payoutStatuses[value]}
          </Button>
        ))}
        <Button type="button" className="button-outline" onClick={() => void load()}>Actualizar</Button>
      </div>
      {notice && <p role="status">{notice}</p>}
      {error && <p className="alert" role="alert">{error}</p>}
      {items === null && !error && <p>Cargando órdenes de desembolso…</p>}
      {items?.length === 0 && <p>No hay órdenes de desembolso en este estado.</p>}
      {items?.map(order => (
        <article key={order.id} className="account-card" style={{ marginBlock: 16 }}>
          <h3>{order.tecnico_nombre ?? 'Técnico'} · {formatLempiras(order.importe)}</h3>
          <p>
            {order.banco} ({bankAccountTypes[order.tipo_cuenta]}) · {order.cuenta_enmascarada} · {payoutStatuses[order.estado]}
            {order.creado_en && <> · Solicitada {new Date(order.creado_en).toLocaleString('es-HN')}</>}
          </p>
          {order.tecnico_telefono && <p>Teléfono: {order.tecnico_telefono}</p>}
          {order.revisado_en && (
            <p>
              Resuelta por {order.revisado_por_nombre ?? 'administración'} · {new Date(order.revisado_en).toLocaleString('es-HN')}
              {order.referencia_transferencia && <> · Ref. {order.referencia_transferencia}</>}
            </p>
          )}
          {order.motivo_rechazo && <p>Motivo de rechazo: {order.motivo_rechazo}</p>}
          {order.observacion && <p>Observación: {order.observacion}</p>}

          <Button type="button" className="button-outline" disabled={busyId === order.id} onClick={() => void toggleBankDetails(order.id)}>
            {busyId === order.id ? 'Consultando…' : revealed[order.id] ? 'Ocultar cuenta' : 'Ver cuenta para transferencia'}
          </Button>

          {revealed[order.id] && (
            <div style={{ marginTop: 10, padding: 10, border: '1px solid var(--border, #e2e8f0)', borderRadius: 8 }}>
              <p><strong>Trámite autorizado — Datos bancarios</strong></p>
              <p>Banco: {revealed[order.id].banco} ({bankAccountTypes[revealed[order.id].tipo_cuenta]})</p>
              <p>Titular: {revealed[order.id].titular_nombre}</p>
              <p>Número de cuenta: <code>{revealed[order.id].numero_cuenta}</code></p>
            </div>
          )}

          {order.estado === 'PENDIENTE' && (
            <PayoutDecisionForm
              order={order}
              user={user}
              onDecided={message => {
                setRevealed(prev => {
                  const next = { ...prev }
                  delete next[order.id]
                  return next
                })
                setNotice(message)
                void load()
              }}
            />
          )}
        </article>
      ))}
    </section>
  )
}

function PayoutDecisionForm({
  order, user, onDecided,
}: { order: AdminPayoutOrder; user: User; onDecided: (message: string) => void }) {
  const [referencia, setReferencia] = useState('')
  const [motivo, setMotivo] = useState('')
  const [observacion, setObservacion] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function submit(decision: PayoutDecisionAction) {
    if (busy) return
    const problem = validatePayoutDecisionInput({
      decision,
      referencia_transferencia: referencia,
      motivo_rechazo: motivo,
    })
    if (problem) {
      setError(problem)
      return
    }
    setBusy(true)
    setError('')
    try {
      const result = await decideAdminPayout(user, order.id, {
        decision,
        referencia_transferencia: referencia,
        motivo_rechazo: motivo,
        observacion,
      })
      if (decision === 'CONFIRMAR') {
        onDecided(`Desembolso de ${formatLempiras(order.importe)} confirmado con ref. ${result.orden.referencia_transferencia}. Reserva liberada y saldo contable actualizado (${formatLempiras(result.saldos.saldo_contable)}).`)
      } else {
        onDecided(`Desembolso de ${formatLempiras(order.importe)} rechazado. La reserva fue liberada sin debitar fondos (saldo libre: ${formatLempiras(result.saldos.saldo_libre)}).`)
      }
    } catch (err) { setError(errorMessage(err)) }
    finally { setBusy(false) }
  }

  return (
    <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--border, #e2e8f0)' }}>
      <Field
        label="Referencia bancaria de la transferencia"
        value={referencia}
        onChange={e => setReferencia(e.target.value)}
        maxLength={64}
        hint="Obligatoria para confirmar el pago."
      />
      <Field
        label="Motivo de rechazo"
        value={motivo}
        onChange={e => setMotivo(e.target.value)}
        hint="Obligatorio si rechazas la orden (libera la reserva sin debitar fondos)."
      />
      <Field
        label="Observación (opcional)"
        value={observacion}
        onChange={e => setObservacion(e.target.value)}
      />
      {error && <p className="alert" role="alert">{error}</p>}
      <div className="dossier-filters" style={{ marginTop: 8 }}>
        <Button type="button" disabled={busy} onClick={() => void submit('CONFIRMAR')}>
          {busy ? 'Procesando…' : 'Confirmar transferencia'}
        </Button>
        <Button type="button" className="button-outline" disabled={busy} onClick={() => void submit('RECHAZAR')}>
          Rechazar orden
        </Button>
      </div>
    </div>
  )
}
