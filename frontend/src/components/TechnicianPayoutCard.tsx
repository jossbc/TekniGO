import { useCallback, useEffect, useState, type FormEvent } from 'react'
import type { User } from 'firebase/auth'
import { Wallet } from 'lucide-react'
import { api, errorMessage } from '../lib/api'
import {
  bankAccountTypes, myPayoutsPath, payoutStatuses, requestMyPayout, saveMyBankAccount,
  validateBankAccountInput, validatePayoutRequestInput,
  type BankAccountType, type PayoutSummary,
} from '../lib/payouts'
import { formatLempiras } from '../lib/settlements'
import { Button, Field } from './ui'

export default function TechnicianPayoutCard({ user }: { user: User }) {
  const [data, setData] = useState<PayoutSummary | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)

  // Cuenta bancaria privada (CA-01, CA-07)
  const [editingAccount, setEditingAccount] = useState(false)
  const [banco, setBanco] = useState('')
  const [tipoCuenta, setTipoCuenta] = useState<BankAccountType>('AHORRO')
  const [titular, setTitular] = useState('')
  const [numeroCuenta, setNumeroCuenta] = useState('')

  // Solicitud de desembolso (CA-01, CA-02, CA-03)
  const [importe, setImporte] = useState('')

  const load = useCallback(async () => {
    try {
      setData(await api<PayoutSummary>(myPayoutsPath, user))
      setError('')
    } catch (err) { setError(errorMessage(err)) }
  }, [user])

  useEffect(() => { void load() }, [load])

  if (!data) return error ? <p className="alert" role="alert">{error}</p> : null

  const hasDebt = Number(data.deuda_vigente) > 0
  const showAccountForm = !data.cuenta_bancaria || editingAccount

  async function submitAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    setNotice('')
    const problem = validateBankAccountInput({
      banco, tipo_cuenta: tipoCuenta, titular_nombre: titular, numero_cuenta: numeroCuenta,
    })
    if (problem) { setError(problem); return }
    setBusy(true)
    setError('')
    try {
      await saveMyBankAccount(user, {
        banco: banco.trim(),
        tipo_cuenta: tipoCuenta,
        titular_nombre: titular.trim(),
        numero_cuenta: numeroCuenta.trim(),
      })
      setNumeroCuenta('')
      setEditingAccount(false)
      setNotice('Cuenta bancaria guardada de forma cifrada. Solo se mostrará enmascarada.')
      await load()
    } catch (err) { setError(errorMessage(err)) }
    finally { setBusy(false) }
  }

  async function submitPayout(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || !data) return
    setNotice('')
    const problem = validatePayoutRequestInput(importe, data)
    if (problem) { setError(problem); return }
    setBusy(true)
    setError('')
    try {
      const res = await requestMyPayout(user, importe)
      setImporte('')
      setNotice(
        `Solicitud registrada por ${formatLempiras(res.orden.importe)}. El importe quedó reservado en estado pendiente hasta que administración confirme la transferencia.`,
      )
      await load()
    } catch (err) { setError(errorMessage(err)) }
    finally { setBusy(false) }
  }

  return (
    <section className="account-card" aria-labelledby="payout-title">
      <span className="step-icon"><Wallet size={22} /></span>
      <h2 id="payout-title">Retirar ganancias</h2>
      <p>
        Saldo libre: <strong>{formatLempiras(data.saldo_libre)}</strong>
        {' · '}Reservado: {formatLempiras(data.saldo_reservado)}
        {' · '}Saldo contable: {formatLempiras(data.saldo_contable)}
      </p>
      <p>
        Mínimo de retiro: <strong>{formatLempiras(data.minimo_retiro)}</strong>
        {' · '}Deuda vigente: <strong>{formatLempiras(data.deuda_vigente)}</strong>
      </p>

      {data.cuenta_bancaria && !editingAccount && (
        <div style={{ marginBlock: 12 }}>
          <p>
            Cuenta registrada: <strong>{data.cuenta_bancaria.banco}</strong> ({bankAccountTypes[data.cuenta_bancaria.tipo_cuenta]})
            {' · '}{data.cuenta_bancaria.cuenta_enmascarada}
            {' · '}Titular: {data.cuenta_bancaria.titular_nombre}
          </p>
          <Button
            type="button"
            className="button-outline"
            onClick={() => {
              setBanco(data.cuenta_bancaria?.banco ?? '')
              setTipoCuenta(data.cuenta_bancaria?.tipo_cuenta ?? 'AHORRO')
              setTitular(data.cuenta_bancaria?.titular_nombre ?? '')
              setNumeroCuenta('')
              setEditingAccount(true)
            }}
          >
            Actualizar cuenta bancaria
          </Button>
        </div>
      )}

      {showAccountForm && (
        <form onSubmit={submitAccount} noValidate style={{ marginBlock: 12 }}>
          <Field label="Banco" value={banco} onChange={e => setBanco(e.target.value)} maxLength={80} />
          <div className="field">
            <label htmlFor="payout-account-type">Tipo de cuenta</label>
            <select
              id="payout-account-type"
              value={tipoCuenta}
              onChange={e => setTipoCuenta(e.target.value as BankAccountType)}
            >
              <option value="AHORRO">Ahorro</option>
              <option value="CHEQUES">Cheques</option>
            </select>
          </div>
          <Field label="Nombre completo del titular" value={titular} onChange={e => setTitular(e.target.value)} maxLength={150} />
          <Field
            label="Número de cuenta bancaria"
            inputMode="numeric"
            autoComplete="off"
            value={numeroCuenta}
            onChange={e => setNumeroCuenta(e.target.value)}
            hint="Dato privado: se guarda cifrado y solo se muestra enmascarado fuera del trámite de transferencia."
          />
          <div className="dossier-filters">
            <Button type="submit" disabled={busy}>{busy ? 'Guardando…' : 'Guardar cuenta bancaria'}</Button>
            {data.cuenta_bancaria && (
              <Button type="button" className="button-outline" onClick={() => setEditingAccount(false)}>
                Cancelar
              </Button>
            )}
          </div>
        </form>
      )}

      {hasDebt && (
        <p className="alert" role="alert">
          No puedes solicitar un desembolso mientras tengas comisiones pendientes ({formatLempiras(data.deuda_vigente)}).
        </p>
      )}

      {data.cuenta_bancaria && !hasDebt && (
        <form onSubmit={submitPayout} noValidate style={{ marginBlock: 12 }}>
          <Field
            label="Importe a retirar (L)"
            inputMode="decimal"
            value={importe}
            onChange={e => setImporte(e.target.value)}
            hint={`Mínimo ${formatLempiras(data.minimo_retiro)} · Disponible ${formatLempiras(data.saldo_libre)}.`}
          />
          <Button type="submit" disabled={busy || Number(data.saldo_libre) < Number(data.minimo_retiro)}>
            {busy ? 'Solicitando…' : 'Solicitar desembolso'}
          </Button>
        </form>
      )}

      {notice && <p role="status">{notice}</p>}
      {error && <p className="alert" role="alert">{error}</p>}

      {data.ordenes.length > 0 && (
        <ul aria-label="Mis solicitudes de desembolso">
          {data.ordenes.map(order => (
            <li key={order.id}>
              <strong>{formatLempiras(order.importe)}</strong> · {order.banco} ({order.cuenta_enmascarada}) · {payoutStatuses[order.estado]}
              {order.referencia_transferencia && <> · Ref. {order.referencia_transferencia}</>}
              {order.motivo_rechazo && <><br /><small>Motivo de rechazo: {order.motivo_rechazo}</small></>}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
