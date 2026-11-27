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

const ownerPath = '/users/me/cash-collections'
const localDate = new Intl.DateTimeFormat('es-HN', {
  dateStyle: 'medium', timeStyle: 'short', timeZone: 'America/Tegucigalpa',
})

export default function CashCollectionsCard({ user, admin = false }: { user: User; admin?: boolean }) {
  const [collections, setCollections] = useState<Collection[] | null>(null)
  const [confirming, setConfirming] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    api<{ cobros: Collection[] }>(admin ? '/admin/cash-collections' : ownerPath, user)
      .then(data => { if (active) setCollections(data.cobros) })
      .catch(err => { if (active) setError(errorMessage(err)) })
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
    } catch (err) { setError(errorMessage(err)) }
    finally { setBusy(false) }
  }

  return <section className="account-card">
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
