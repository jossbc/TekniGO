import { useEffect, useRef, useState, type FormEvent } from 'react'
import type { User } from 'firebase/auth'
import { FileText } from 'lucide-react'
import { api, errorMessage } from '../lib/api'
import { openPrivateDocument, type ReviewDocument, type ReviewOutcome } from '../lib/adminVerification'
import { Button, Field } from './ui'

export default function DocumentReviewCard({ user, userId, doc, label, identity, machineRejected = false, onSaved }: {
  user: User; userId: string; doc: ReviewDocument; label: string; identity: boolean; machineRejected?: boolean; onSaved: (result: ReviewOutcome) => void
}) {
  const [decision, setDecision] = useState('APROBADO')
  const [observation, setObservation] = useState('')
  const [legible, setLegible] = useState(false)
  const [matches, setMatches] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [preview, setPreview] = useState<{ url: string; type: string } | null>(null)
  const objectUrl = useRef('')
  const active = useRef(true)
  useEffect(() => {
    active.current = true
    return () => { active.current = false; if (objectUrl.current) URL.revokeObjectURL(objectUrl.current) }
  }, [])
  async function open() {
    setBusy(true); setError('')
    try {
      const blob = await openPrivateDocument(user, doc.id)
      if (!active.current) return
      if (objectUrl.current) URL.revokeObjectURL(objectUrl.current)
      objectUrl.current = URL.createObjectURL(blob)
      setPreview({ url: objectUrl.current, type: blob.type })
    } catch (err) { if (active.current) setError(errorMessage(err)) }
    finally { if (active.current) setBusy(false) }
  }
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    setBusy(true); setError('')
    try {
      const result = await api<ReviewOutcome>(`/admin/dossiers/${userId}/verification-review`, user, { documentos: [{
        documento_id: doc.id, estado: decision, observaciones: observation,
        legible, identidad_coincide: identity ? matches : null,
      }] }, 'PATCH')
      if (active.current) onSaved(result)
    } catch (err) { if (active.current) setError(errorMessage(err)) }
    finally { if (active.current) setBusy(false) }
  }
  const requiresResolution = identity && machineRejected && decision === 'APROBADO'
  return <section className="account-card" style={{ marginBottom: 24 }} aria-label={label}>
    <h3>{label}</h3><p>Estado: {doc.estado}</p>
    {doc.observaciones && <p>{doc.observaciones}</p>}
    {doc.fecha_emision && <p>Emisión: {doc.fecha_emision} · Vencimiento: {doc.fecha_vencimiento || 'Sin fecha'}</p>}
    <div className="admin-toolbar">
      <Button type="button" disabled={busy} onClick={open}><FileText size={16} />Abrir documento</Button>
    </div>
    {preview && <div style={{ marginBlock: 16 }}>
      {preview.type.startsWith('image/') ? <img src={preview.url} alt={label} style={{ maxWidth: '100%', maxHeight: 500, objectFit: 'contain' }} /> :
        <iframe src={preview.url} title={label} sandbox="allow-same-origin" style={{ width: '100%', height: 500, border: 0 }} />}
    </div>}
    <form className="admin-document-form" onSubmit={submit}>
      <div className="field"><label htmlFor={`decision-${doc.id}`}>Decisión</label>
      <select id={`decision-${doc.id}`} value={decision} onChange={event => setDecision(event.target.value)}>
        <option value="APROBADO">Aprobar</option><option value="RECHAZADO">Rechazar</option>
      </select></div>
      {decision === 'APROBADO' && <>
        <label style={{ display: 'block', marginBlock: 12 }}><input type="checkbox" required checked={legible} onChange={event => setLegible(event.target.checked)} /> Revisé el archivo y es legible.</label>
        {identity && <label style={{ display: 'block', marginBlock: 12 }}><input type="checkbox" required checked={matches} onChange={event => setMatches(event.target.checked)} /> Contrasté el DNI, los datos registrados y la selfie; no hay discrepancias sin resolver.</label>}
      </>}
      {requiresResolution && <p>La evaluación automática detectó una discrepancia. Explica qué comprobaste y por qué el resultado automático no corresponde. Si la discrepancia persiste, solicita documentos corregidos.</p>}
      <Field label={requiresResolution ? 'Justificación de la aprobación manual (obligatoria)' : decision === 'RECHAZADO' ? 'Motivo del rechazo (obligatorio)' : 'Observaciones'} required={decision === 'RECHAZADO' || requiresResolution} maxLength={2000} value={observation} onChange={event => setObservation(event.target.value)} />
      <Button type="submit" disabled={busy || (decision === 'APROBADO' && !preview)}>{busy ? 'Guardando…' : 'Guardar decisión'}</Button>
    </form>
    {error && <p className="alert" role="alert">{error}</p>}
  </section>
}
