import { useCallback, useEffect, useState, type FormEvent } from 'react'
import type { User } from 'firebase/auth'
import { Landmark } from 'lucide-react'
import { api, errorMessage } from '../lib/api'
import {
  formatLempiras, mySettlementsPath, settlementStatuses, submitSettlement, validateSettlementInput,
  type SettlementSummary,
} from '../lib/settlements'
import { Button, Field } from './ui'

export default function TechnicianSettlementCard({ user }: { user: User }) {
  const [data, setData] = useState<SettlementSummary | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [importe, setImporte] = useState('')
  const [referencia, setReferencia] = useState('')
  const [file, setFile] = useState<File | null>(null)

  const load = useCallback(async () => {
    try {
      setData(await api<SettlementSummary>(mySettlementsPath, user))
      setError('')
    } catch (err) { setError(errorMessage(err)) }
  }, [user])

  useEffect(() => { void load() }, [load])

  if (!data) return error ? <p className="alert" role="alert">{error}</p> : null
  const hasDebt = Number(data.deuda_vigente) > 0
  if (!hasDebt && !data.restriccion_financiera && !data.liquidaciones.length) return null

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    if (busy || !data) return
    setNotice('')
    const problem = validateSettlementInput({ importe, referencia, file }, data)
    if (problem) { setError(problem); return }
    setBusy(true)
    setError('')
    try {
      await submitSettlement(user, importe, referencia, file as File)
      setImporte(''); setReferencia(''); setFile(null)
      form?.reset()
      setNotice('Enviamos tu comprobante. Tu deuda y tu restricción no cambian hasta que administración lo revise.')
      await load()
    } catch (err) { setError(errorMessage(err)) }
    finally { setBusy(false) }
  }

  return (
    <section className="account-card" aria-labelledby="settlement-title">
      <span className="step-icon"><Landmark size={22} /></span>
      <h2 id="settlement-title">Liquidar comisiones</h2>
      <p>
        Deuda vigente: <strong>{formatLempiras(data.deuda_vigente)}</strong>
        {Number(data.importe_en_revision) > 0 && <> · En revisión: {formatLempiras(data.importe_en_revision)}</>}
        {data.servicios_efectivo_sin_liquidar > 0 && <> · Servicios en efectivo sin liquidar: {data.servicios_efectivo_sin_liquidar}</>}
      </p>
      {data.restriccion_financiera && (
        <p className="alert" role="alert">
          Tu cuenta tiene una restricción financiera activa. Solo se retirará cuando administración apruebe la liquidación total de tu deuda.
        </p>
      )}
      {!hasDebt && !data.restriccion_financiera && data.liquidaciones.length > 0 && (
        <p role="status">
          Tu deuda de comisiones está regularizada (L 0.00) y no tienes restricción financiera activa.
          {data.suspendido
            ? ' Tu cuenta mantiene una suspensión administrativa independiente.'
            : data.estado_verificacion && data.estado_verificacion !== 'VERIFICADO'
              ? ' Tu verificación técnica sigue pendiente de forma independiente.'
              : ''}
        </p>
      )}
      {hasDebt && (
        <form onSubmit={submit} noValidate>
          <Field label="Importe pagado (L)" inputMode="decimal" value={importe} onChange={e => setImporte(e.target.value)} />
          <Field label="Número de referencia bancaria" value={referencia} onChange={e => setReferencia(e.target.value)} maxLength={64} />
          <Field
            label="Fotografía o captura del comprobante"
            type="file"
            accept={data.formatos_permitidos.join(',')}
            onChange={e => setFile(e.target.files?.[0] ?? null)}
            hint={`Formatos: ${data.formatos_texto}. Tamaño máximo: ${Math.round(data.tamano_maximo_bytes / 1024 / 1024)} MB. Se guarda cifrado y solo administración puede verlo.`}
          />
          <Button type="submit" disabled={busy}>{busy ? 'Enviando…' : 'Enviar comprobante'}</Button>
        </form>
      )}
      {notice && <p role="status">{notice}</p>}
      {error && <p className="alert" role="alert">{error}</p>}
      {data.liquidaciones.length > 0 && (
        <ul aria-label="Mis liquidaciones">
          {data.liquidaciones.map(item => (
            <li key={item.id}>
              <strong>{formatLempiras(item.importe_declarado)}</strong> · Ref. {item.referencia} · {settlementStatuses[item.estado]}
              {item.importe_aplicado !== '0.00' && <> · Aplicado {formatLempiras(item.importe_aplicado)}</>}
              {item.observacion && <><br /><small>Observación: {item.observacion}</small></>}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
