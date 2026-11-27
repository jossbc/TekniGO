import { useEffect, useState } from 'react'
import type { User } from 'firebase/auth'
import { api, errorMessage } from '../lib/api'
import { formatLempiras } from '../lib/settlements'
import { Button } from './ui'

type Collection = {
  solicitud_id: string
  modalidad: string
  efectivo: string
  digital: string
  comision: string
  efectivo_ajuste?: string
  registrado_en: string | null
  tecnico_uid?: string
}

type Wallet = {
  saldo_disponible: string
  saldo_reservado: string
  efectivo_recibido: string
  comisiones_pendientes: string
  restriccion_financiera: boolean
  servicios_efectivo_sin_liquidar: number
  limite_deuda: string | null
  limite_sin_liquidar: number
  movimientos: {
    id: string
    fecha: string
    servicio: string | null
    concepto: string
    debito: string
    credito: string
    saldo: string | null
    deuda_generada: string
    deuda_liquidada: string
  }[]
}

const ownerPath = '/users/me/cash-collections'
const walletPath = '/users/me/wallet'
const localDate = new Intl.DateTimeFormat('es-HN', {
  dateStyle: 'medium', timeStyle: 'short', timeZone: 'America/Tegucigalpa',
})

export default function CashCollectionsCard({ user, admin = false }: { user: User; admin?: boolean }) {
  const [collections, setCollections] = useState<Collection[] | null>(null)
  const [confirming, setConfirming] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [wallet, setWallet] = useState<Wallet | null>(null)
  const [filter, setFilter] = useState('TODOS')

  useEffect(() => {
    let active = true
    api<{ cobros: Collection[] }>(admin ? '/admin/cash-collections' : ownerPath, user)
      .then(data => { if (active) setCollections(data.cobros) })
      .catch(err => { if (active) setError(errorMessage(err)) })
    if (!admin) {
      api<Wallet>(walletPath, user)
        .then(data => { if (active) setWallet(data) })
        .catch(err => { if (active) setError(errorMessage(err)) })
    }
    return () => { active = false }
  }, [admin, user])

  async function register(collection: Collection) {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      const saved = await api<Collection>(`${ownerPath}/${encodeURIComponent(collection.solicitud_id)}`, user, undefined, 'POST')
      setCollections(rows => rows?.map(row =>
        row.solicitud_id === saved.solicitud_id && row.modalidad === saved.modalidad ? saved : row
      ) ?? null)
      setConfirming(null)
      setWallet(await api<Wallet>(walletPath, user))
    } catch (err) { setError(errorMessage(err)) }
    finally { setBusy(false) }
  }

  async function inspectWallet(uid: string) {
    setError('')
    try {
      setWallet(await api<Wallet>(`/admin/technicians/${encodeURIComponent(uid)}/wallet`, user))
      setFilter('TODOS')
    } catch (err) { setError(errorMessage(err)) }
  }

  return <section className="account-card">
    {wallet && <>
      <h2>Billetera</h2>
      <dl>
        <div><dt>Saldo disponible</dt><dd>{formatLempiras(wallet.saldo_disponible)}</dd></div>
        <div><dt>Reservado para retiros</dt><dd>{formatLempiras(wallet.saldo_reservado)}</dd></div>
        <div><dt>Efectivo recibido</dt><dd>{formatLempiras(wallet.efectivo_recibido)}</dd></div>
        <div><dt>Comisiones pendientes</dt><dd>{formatLempiras(wallet.comisiones_pendientes)}</dd></div>
      </dl>
      <p>{wallet.restriccion_financiera ? 'Restringido por saldo' : 'Sin restricción por saldo'}</p>
      <p>Servicios en efectivo sin liquidar {wallet.servicios_efectivo_sin_liquidar} de {wallet.limite_sin_liquidar}
        {wallet.limite_deuda && <> · Límite de deuda {formatLempiras(wallet.limite_deuda)}</>}
      </p>
      <h3>Movimientos</h3>
      <label className="field">
        Concepto
        <select value={filter} onChange={event => setFilter(event.currentTarget.value)}>
          <option value="TODOS">Todos</option>
          {[...new Set(wallet.movimientos.map(row => row.concepto))].map(concept => (
            <option key={concept} value={concept}>{concept}</option>
          ))}
        </select>
      </label>
      {wallet.movimientos.length === 0 && <p>Sin movimientos registrados.</p>}
      <ul>
        {wallet.movimientos.filter(row => filter === 'TODOS' || row.concepto === filter).map(row => (
          <li key={row.id}>
            <strong>{row.concepto}</strong> · {localDate.format(new Date(row.fecha))}
            {row.servicio && <> · Servicio {row.servicio.slice(0, 8)}</>}
            <div>Débito {formatLempiras(row.debito)} · Crédito {formatLempiras(row.credito)}
              {row.saldo !== null && <> · Saldo {formatLempiras(row.saldo)}</>}
              {Number(row.deuda_generada) > 0 && <> · Deuda generada {formatLempiras(row.deuda_generada)}</>}
              {Number(row.deuda_liquidada) > 0 && <> · Deuda liquidada {formatLempiras(row.deuda_liquidada)}</>}
            </div>
          </li>
        ))}
      </ul>
    </>}
    <h2>Cobros en efectivo</h2>
    <p>El efectivo se recibe fuera de la plataforma.</p>
    {error && <p className="alert" role="alert">{error}</p>}
    {collections?.length === 0 && <p>Sin cobros en efectivo para mostrar.</p>}
    {collections === null && !error && <p role="status">Cargando cobros…</p>}
    {collections?.map(collection => {
      const key = `${collection.modalidad}:${collection.solicitud_id}`
      return <article key={key} className="account-card">
        <h3>Servicio {collection.solicitud_id.slice(0, 8)}</h3>
        <p>Efectivo {formatLempiras(collection.efectivo)} · Digital {formatLempiras(collection.digital)} · Comisión total {formatLempiras(collection.comision)}</p>
        {Number(collection.efectivo_ajuste) > 0 && <p>Ajuste en efectivo {formatLempiras(collection.efectivo_ajuste!)}</p>}
        {collection.registrado_en ? (
          <>
            <p>Registrado el {localDate.format(new Date(collection.registrado_en))}</p>
            {admin && collection.tecnico_uid && (
              <Button type="button" className="button-outline" onClick={() => void inspectWallet(collection.tecnico_uid!)}>
                Consultar billetera del técnico
              </Button>
            )}
          </>
        ) : confirming === key ? (
          <>
            <p>Confirma que recibiste {formatLempiras(collection.efectivo)} en efectivo.</p>
            <div className="responsive-actions">
              <Button type="button" className="button-outline" disabled={busy} onClick={() => setConfirming(null)}>Cancelar</Button>
              <Button type="button" disabled={busy} onClick={() => void register(collection)}>Confirmar cobro</Button>
            </div>
          </>
        ) : !admin && (
          <Button type="button" className="button-outline" disabled={busy} onClick={() => setConfirming(key)}>Registrar cobro</Button>
        )}
      </article>
    })}
  </section>
}
