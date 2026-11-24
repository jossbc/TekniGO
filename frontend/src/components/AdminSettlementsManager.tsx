import { useCallback, useEffect, useState } from 'react'
import type { User } from 'firebase/auth'
import { api, errorMessage } from '../lib/api'
import { certificateFileRequest } from '../lib/certifications'
import {
  adminSettlementsPath, decideSettlement, formatLempiras, settlementStatuses, validateDecisionInput,
  type AdminSettlement, type SettlementDecisionAction, type SettlementStatus,
} from '../lib/settlements'
import { Button, Field } from './ui'

const filters: (SettlementStatus | 'TODAS')[] = ['PENDIENTE', 'EN_CONCILIACION', 'APROBADA', 'RECHAZADA', 'TODAS']

export default function AdminSettlementsManager({ user }: { user: User }) {
  const [estado, setEstado] = useState<SettlementStatus | 'TODAS'>('PENDIENTE')
  const [items, setItems] = useState<AdminSettlement[] | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busyId, setBusyId] = useState('')

  const load = useCallback(async () => {
    setItems(null)
    setError('')
    try {
      const query = estado === 'TODAS' ? '' : `?estado=${estado}`
      setItems((await api<{ liquidaciones: AdminSettlement[] }>(`${adminSettlementsPath}${query}`, user)).liquidaciones)
    } catch (err) { setError(errorMessage(err)) }
  }, [estado, user])

  useEffect(() => { void load() }, [load])

  async function openProof(item: AdminSettlement) {
    setBusyId(item.id)
    setError('')
    try {
      const response = await certificateFileRequest(`${adminSettlementsPath}/${item.id}/proof`, user)
      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      window.open(url, '_blank', 'noopener')
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
    } catch (err) { setError(errorMessage(err)) }
    finally { setBusyId('') }
  }

  return (
    <section aria-labelledby="settlements-title">
      <h2 id="settlements-title">Liquidaciones de comisiones</h2>
      <div className="dossier-filters" role="group" aria-label="Filtrar por estado">
        {filters.map(value => (
          <Button key={value} type="button" className={estado === value ? '' : 'button-outline'} aria-pressed={estado === value} onClick={() => setEstado(value)}>
            {value === 'TODAS' ? 'Todas' : settlementStatuses[value]}
          </Button>
        ))}
        <Button type="button" className="button-outline" onClick={() => void load()}>Actualizar</Button>
      </div>
      {notice && <p role="status">{notice}</p>}
      {error && <p className="alert" role="alert">{error}</p>}
      {items === null && !error && <p>Cargando liquidaciones…</p>}
      {items?.length === 0 && <p>No hay liquidaciones en este estado.</p>}
      {items?.map(item => (
        <article key={item.id} className="account-card" style={{ marginBlock: 16 }}>
          <h3>{item.tecnico_nombre ?? 'Técnico'} · {formatLempiras(item.importe_declarado)}</h3>
          <p>
            Ref. {item.referencia} · {settlementStatuses[item.estado]}
            {item.creado_en && <> · Enviada {new Date(item.creado_en).toLocaleString('es-HN')}</>}
          </p>
          {item.tecnico_telefono && <p>Teléfono: {item.tecnico_telefono}</p>}
          {item.revisado_en && (
            <p>
              Revisada por {item.revisado_por_nombre ?? 'administración'} · {new Date(item.revisado_en).toLocaleString('es-HN')}
              {item.importe_verificado && <> · Verificado {formatLempiras(item.importe_verificado)}</>}
              {item.importe_aplicado !== '0.00' && <> · Aplicado {formatLempiras(item.importe_aplicado)}</>}
              {item.importe_excedente !== '0.00' && <> · Excedente en conciliación {formatLempiras(item.importe_excedente)}</>}
            </p>
          )}
          {item.observacion && <p>Observación: {item.observacion}</p>}
          <Button type="button" className="button-outline" disabled={busyId === item.id} onClick={() => void openProof(item)}>
            {busyId === item.id ? 'Abriendo…' : 'Ver comprobante'}
          </Button>
          {item.estado === 'PENDIENTE' && (
            <SettlementDecisionForm
              item={item}
              user={user}
              onDecided={message => {
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

function SettlementDecisionForm({
  item, user, onDecided,
}: { item: AdminSettlement; user: User; onDecided: (message: string) => void }) {
  const [importeVerificado, setImporteVerificado] = useState(item.importe_declarado)
  const [observacion, setObservacion] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function submitDecision(decision: SettlementDecisionAction) {
    if (busy) return
    const problem = validateDecisionInput({ decision, importe_verificado: importeVerificado, observacion })
    if (problem) {
      setError(problem)
      return
    }
    setBusy(true)
    setError('')
    try {
      const result = await decideSettlement(user, item.id, {
        decision,
        importe_verificado: importeVerificado,
        observacion,
      })
      const ef = result.efecto_financiero
      if (decision === 'RECHAZAR') {
        onDecided(`Liquidación ${item.referencia} rechazada. La deuda (${formatLempiras(ef.deuda_posterior)}) y el bloqueo se conservan.`)
      } else if (ef.accion === 'SOBREPAGO_CONCILIACION') {
        onDecided(`Sobrepago registrado en conciliación: aplicado ${formatLempiras(ef.importe_aplicado)}, excedente ${formatLempiras(ef.importe_excedente)} (sin crédito arbitrario).`)
      } else if (ef.accion === 'APROBACION_TOTAL') {
        onDecided(`Liquidación total aprobada (${formatLempiras(ef.importe_aplicado)}). Deuda en L 0.00 y restricción financiera retirada.`)
      } else {
        onDecided(`Liquidación parcial aprobada (${formatLempiras(ef.importe_aplicado)}). Deuda restante: ${formatLempiras(ef.deuda_posterior)}.`)
      }
    } catch (err) { setError(errorMessage(err)) }
    finally { setBusy(false) }
  }

  return (
    <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--border, #e2e8f0)' }}>
      <Field
        label="Importe verificado (L)"
        inputMode="decimal"
        value={importeVerificado}
        onChange={e => setImporteVerificado(e.target.value)}
        hint="Solo se aplica el importe conciliado contra la deuda vigente."
      />
      <Field
        label="Observación administrativa"
        value={observacion}
        onChange={e => setObservacion(e.target.value)}
        hint="Opcional al aprobar; obligatoria al rechazar."
      />
      {error && <p className="alert" role="alert">{error}</p>}
      <div className="dossier-filters" style={{ marginTop: 8 }}>
        <Button type="button" disabled={busy} onClick={() => void submitDecision('APROBAR')}>
          {busy ? 'Procesando…' : 'Aprobar liquidación'}
        </Button>
        <Button type="button" className="button-outline" disabled={busy} onClick={() => void submitDecision('RECHAZAR')}>
          Rechazar
        </Button>
      </div>
    </div>
  )
}
