import { useEffect, useState, type FormEvent } from 'react'
import type { User } from 'firebase/auth'
import { api, errorMessage } from '../lib/api'
import { certificateStatuses, ownerCertificatesPath, type Certificate } from '../lib/certifications'
import { Button } from './ui'

export default function TechnicianCertificates({ user }: { user: User }) {
  const [certificates, setCertificates] = useState<Certificate[] | null>(null)
  const [refresh, setRefresh] = useState(0)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let active = true
    api<Certificate[]>(ownerCertificatesPath, user)
      .then(result => { if (active) setCertificates(result) })
      .catch(err => { if (active) setError(errorMessage(err)) })
    return () => { active = false }
  }, [user, refresh])

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    const form = event.currentTarget
    const data = new FormData(form)
    const file = data.get('archivo')
    setError('')
    setNotice('')
    if (!(file instanceof File) || !file.size || file.size > 5 * 1024 * 1024 ||
      !['image/jpeg', 'image/png', 'image/webp', 'application/pdf'].includes(file.type)) {
      setError('Selecciona un archivo JPG, PNG, WEBP o PDF de hasta 5 MB.')
      return
    }
    setBusy(true)
    try {
      await api<Certificate>(ownerCertificatesPath, user, data)
      form.reset()
      setNotice('Certificación enviada para revisión.')
      setRefresh(value => value + 1)
    } catch (err) { setError(errorMessage(err)) }
    finally { setBusy(false) }
  }

  return <section className="account-card profile-card" aria-label="Certificaciones de oficio">
    <h2>Certificaciones de oficio</h2>
    <p>Adjunta diplomas o certificaciones. Son opcionales y no afectan tu verificación de seguridad.</p>
    {error && <p className="alert" role="alert">{error}</p>}
    {notice && <p className="success-alert" role="status">{notice}</p>}
    <form onSubmit={upload}>
      <label className="field">
        Título de la certificación
        <input name="titulo" required maxLength={150} placeholder="Ej. Diploma de instalaciones eléctricas" />
      </label>
      <label className="field">
        Archivo de la certificación
        <small>JPG, PNG, WEBP o PDF. Máximo 5 MB por archivo. Solo administración puede abrirlo.</small>
        <input name="archivo" type="file" accept="image/jpeg,image/png,image/webp,application/pdf" required />
      </label>
      <Button type="submit" disabled={busy} style={{ width: 'auto' }}>{busy ? 'Enviando…' : 'Enviar certificación'}</Button>
    </form>
    <h3 style={{ marginTop: 24 }}>Mis certificaciones</h3>
    {certificates === null && !error && <p role="status">Cargando certificaciones…</p>}
    {certificates?.length === 0 && <p>Aún no has enviado certificaciones.</p>}
    {certificates && certificates.length > 0 && <ul>{certificates.map(doc => <li key={doc.id}>
      <strong>{doc.titulo}</strong> · {certificateStatuses[doc.estado]}
      {doc.observaciones && <p>{doc.observaciones}</p>}
    </li>)}</ul>}
    <Button type="button" className="button-outline" style={{ width: 'auto' }} onClick={() => { setError(''); setRefresh(value => value + 1) }}>Actualizar estados</Button>
  </section>
}
