import { useEffect, useState, type FormEvent } from 'react'
import type { User } from 'firebase/auth'
import { api, errorMessage } from '../lib/api'
import { adminCertificatesPath, certificateFileRequest, certificateStatuses, type Certificate, type CertificateStatus } from '../lib/certifications'
import { Button } from './ui'

type AdminCertificate = Certificate & { tecnico: string }
type Page = { certificaciones: AdminCertificate[]; hay_mas: boolean; tiene_certificaciones: boolean }

export default function AdminCertificates({ user, userId, onSaved }: { user: User; userId: string; onSaved: () => void }) {
  const [status, setStatus] = useState<CertificateStatus>('PENDIENTE')
  const [offset, setOffset] = useState(0)
  const [page, setPage] = useState<Page | null>(null)
  const [hasCertificates, setHasCertificates] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    let active = true
    setPage(null)
    setError('')
    api<Page>(`${adminCertificatesPath}?usuario_id=${userId}&estado=${status}&offset=${offset}`, user)
      .then(result => {
        if (!active) return
        setHasCertificates(result.tiene_certificaciones)
        if (!result.certificaciones.length && offset) setOffset(value => value - 20)
        else setPage(result)
      })
      .catch(err => { if (active) setError(errorMessage(err)) })
    return () => { active = false }
  }, [user, userId, status, offset, refresh])

  if (!hasCertificates) return error ? <p className="alert" role="alert">{error}</p> : null

  return <section className="account-card" aria-label="Revisión de certificaciones" style={{ marginBlock: 24 }}>
    <h3>Certificaciones de oficio</h3>
    <label className="field">
      Estado de certificación
      <select value={status} onChange={event => { setStatus(event.target.value as CertificateStatus); setOffset(0) }}>
        {Object.entries(certificateStatuses).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select>
    </label>
    {error && <p className="alert" role="alert">{error}</p>}
    {notice && <p className="success-alert" role="status">{notice}</p>}
    {!page && !error && <p role="status">Cargando certificaciones…</p>}
    {page?.certificaciones.length === 0 && <p>No hay certificaciones con este estado.</p>}
    {page?.certificaciones.map(doc => <CertificateReview key={doc.id} doc={doc} user={user} onSaved={() => {
      setNotice('La decisión quedó registrada.')
      setRefresh(value => value + 1)
      onSaved()
    }} />)}
    <div className="admin-toolbar admin-pagination">
      <Button className="button-outline" disabled={!offset || !page} onClick={() => setOffset(value => value - 20)}>Anterior</Button>
      <Button className="button-outline" disabled={!page?.hay_mas} onClick={() => setOffset(value => value + 20)}>Siguiente</Button>
      <Button className="button-outline" onClick={() => setRefresh(value => value + 1)}>Actualizar</Button>
    </div>
  </section>
}

function CertificateReview({ doc, user, onSaved }: { doc: AdminCertificate; user: User; onSaved: () => void }) {
  const [decision, setDecision] = useState(doc.estado === 'APROBADO' ? 'REVOCADO' : 'APROBADO')
  const [observations, setObservations] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const path = `${adminCertificatesPath}/${doc.id}`

  async function download() {
    setError('')
    setBusy(true)
    try {
      const response = await certificateFileRequest(`${path}/content`, user)
      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      const extension = { 'application/pdf': 'pdf', 'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp' }[blob.type]
      link.href = url
      link.download = `certificacion.${extension ?? 'bin'}`
      link.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (err) { setError(errorMessage(err)) }
    finally { setBusy(false) }
  }

  async function review(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    setError('')
    setBusy(true)
    try {
      await api(path, user, { estado: decision, observaciones: observations }, 'PATCH')
      onSaved()
    } catch (err) { setError(errorMessage(err)) }
    finally { setBusy(false) }
  }

  return <article className="account-card" style={{ marginBlock: 16 }}>
    <h3>{doc.titulo}</h3>
    <p>{doc.tecnico} · {certificateStatuses[doc.estado]}</p>
    {doc.observaciones && <p>{doc.observaciones}</p>}
    {error && <p className="alert" role="alert">{error}</p>}
    <Button className="button-outline" disabled={busy} onClick={download}>Descargar archivo</Button>
    {(doc.estado === 'PENDIENTE' || doc.estado === 'APROBADO') && <form onSubmit={review} style={{ marginTop: 16 }}>
      <label className="field">
        Decisión
        <select value={decision} onChange={event => setDecision(event.target.value)}>
          {doc.estado === 'APROBADO' ? <option value="REVOCADO">Revocar aprobación</option> : <>
            <option value="APROBADO">Aprobar</option><option value="RECHAZADO">Rechazar</option>
          </>}
        </select>
      </label>
      <label className="field">
        Observación {decision !== 'APROBADO' && '(obligatoria)'}
        <input value={observations} onChange={event => setObservations(event.target.value)} maxLength={2000} required={decision !== 'APROBADO'} />
      </label>
      <Button type="submit" disabled={busy}>Guardar decisión</Button>
    </form>}
  </article>
}
