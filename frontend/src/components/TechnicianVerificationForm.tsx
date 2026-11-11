import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from 'react'
import type { User } from 'firebase/auth'
import {
  Calendar,
  CheckCircle2,
  CircleAlert,
  Clock3,
  CreditCard,
  FileCheck,
  FileText,
  ShieldAlert,
  ShieldCheck,
  Upload,
  X,
} from 'lucide-react'
import { api, errorMessage } from '../lib/api'
import { ALLOWED_IMAGE_TYPES, IMAGE_RULES_HINT, validateIdentityImage } from '../lib/validation'
import SelfieCameraCapture from './SelfieCameraCapture'
import { Button } from './ui'
import LoadingState from './LoadingState'

type Props = {
  user: User
  initialStatus?: string
  onStatusChange?: (status: string) => void
}

type RequirementItem = {
  codigo: string
  nombre: string
  aprobado: boolean
  vigente: boolean
  estado: string
  fecha_emision?: string | null
  fecha_vencimiento?: string | null
  motivo?: string | null
}

type VerificationDocsResponse = {
  estado_identidad?: string
  solicitud_id?: string | null
  procesamiento?: { estado: string; observacion: string; fecha: string; proceso_responsable?: string } | null
  estado_verificacion: string
  observaciones?: string | null
  motivo_restriccion?: string | null
  requisitos_pendientes?: RequirementItem[]
  documentos: { id: string; tipo: string; estado: string; observaciones?: string | null; creado_en?: string }[]
}

type FieldErrors = {
  dniFrontal?: string
  dniReverso?: string
  selfie?: string
  antecedentesPoliciales?: string
  antecedentesPenales?: string
  fechaPoliciales?: string
  fechaPenales?: string
}

const MAX_BYTES = 5 * 1024 * 1024
const ACCEPTED_FORMATS = [...ALLOWED_IMAGE_TYPES, 'application/pdf'].join(',')

function formatSize(bytes: number): string {
  return bytes >= 1024 * 1024
    ? `${(bytes / (1024 * 1024)).toFixed(2)} MB`
    : `${Math.max(1, Math.round(bytes / 1024))} KB`
}

function validateFile(file: File, label: string): string | null {
  if (file.size > MAX_BYTES) {
    return `${label} supera el tamaño máximo permitido de 5 MB.`
  }
  const isImage = (ALLOWED_IMAGE_TYPES as readonly string[]).includes(file.type)
  const isPdf = file.type === 'application/pdf'
  if (!isImage && !isPdf) {
    return `${label} debe ser una imagen válida (JPG, PNG, WEBP) o documento PDF.`
  }
  return null
}

export default function TechnicianVerificationForm({ user, initialStatus = 'NO_VERIFICADO', onStatusChange }: Props) {
  const [status, setStatus] = useState<string>(initialStatus)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [requestId, setRequestId] = useState<string | null>(null)
  const [processing, setProcessing] = useState<VerificationDocsResponse['procesamiento']>(null)
  const [observacionesGenerales, setObservacionesGenerales] = useState<string | null>(null)
  const [motivoRestriccion, setMotivoRestriccion] = useState<string | null>(null)
  const [requisitosPendientes, setRequisitosPendientes] = useState<RequirementItem[]>([])
  const [hasUploadedDocs, setHasUploadedDocs] = useState(false)
  const [documents, setDocuments] = useState<VerificationDocsResponse['documentos']>([])

  // Conjunto de tipos de documentos ya guardados en el backend (ej: DNI_FRONTAL, DNI_REVERSO, SELFIE)
  const [existingDocTypes, setExistingDocTypes] = useState<Set<string>>(new Set())

  // Archivos obligatorios
  const [dniFrontal, setDniFrontal] = useState<File | null>(null)
  const [dniReverso, setDniReverso] = useState<File | null>(null)
  const [selfie, setSelfie] = useState<File | null>(null)
  const [antecedentesPoliciales, setAntecedentesPoliciales] = useState<File | null>(null)
  const [antecedentesPenales, setAntecedentesPenales] = useState<File | null>(null)

  // Fechas de vigencia / emisión declaradas
  const [fechaPoliciales, setFechaPoliciales] = useState<string>('')
  const [fechaPenales, setFechaPenales] = useState<string>('')

  // Previews
  const [frontalPreview, setFrontalPreview] = useState<string | null>(null)
  const [reversoPreview, setReversoPreview] = useState<string | null>(null)

  // Control de interfaz
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})
  const [formError, setFormError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)

  const frontalInputRef = useRef<HTMLInputElement | null>(null)
  const reversoInputRef = useRef<HTMLInputElement | null>(null)
  const policialesInputRef = useRef<HTMLInputElement | null>(null)
  const penalesInputRef = useRef<HTMLInputElement | null>(null)

  // Flags para verificar cuáles ya existen en el backend
  const hasExistingDniFrontal = existingDocTypes.has('DNI_FRONTAL')
  const hasExistingDniReverso = existingDocTypes.has('DNI_REVERSO')
  const hasExistingSelfie = existingDocTypes.has('SELFIE')
  const hasExistingPoliciales = existingDocTypes.has('ANTECEDENTES_POLICIALES')
  const hasExistingPenales = existingDocTypes.has('ANTECEDENTES_PENALES')

  function applyResponse(res: VerificationDocsResponse, notify = false) {
    const docs = Array.isArray(res.documentos) ? res.documentos : []
    setLoadError('')
    setDocuments(docs)
    setHasUploadedDocs(docs.length > 0)
    setExistingDocTypes(new Set(docs.filter(doc => !['RECHAZADO', 'VENCIDO', 'INVALIDADO'].includes(doc.estado)).map(doc => doc.tipo)))
    setStatus(res.estado_verificacion || 'NO_VERIFICADO')
    setRequestId(res.solicitud_id ?? null)
    setProcessing(res.procesamiento ?? null)
    setObservacionesGenerales(res.observaciones ?? null)
    setMotivoRestriccion(res.motivo_restriccion ?? null)
    setRequisitosPendientes(Array.isArray(res.requisitos_pendientes) ? res.requisitos_pendientes : [])
    if (notify) onStatusChange?.(res.estado_identidad || 'EN_REVISION')
  }

  useEffect(() => {
    let active = true
    setLoading(true)
    api<VerificationDocsResponse>('/technicians/me/verification-documents', user)
      .then(res => { if (active) applyResponse(res) })
      .catch(err => { if (active) setLoadError(errorMessage(err)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [user, initialStatus, attempt])

  async function retryIdentity() {
    if (busy || !requestId) return
    setBusy(true)
    setFormError('')
    try {
      const result = await api<VerificationDocsResponse>('/technicians/me/identity-verification', user, { solicitud_id: requestId })
      applyResponse(result, true)
    } catch (err) { setFormError(errorMessage(err)) }
    finally { setBusy(false) }
  }

  // Manejo de previews para imágenes de DNI
  useEffect(() => {
    if (!dniFrontal || dniFrontal.type === 'application/pdf') {
      setFrontalPreview(null)
      return
    }
    const url = URL.createObjectURL(dniFrontal)
    setFrontalPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [dniFrontal])

  useEffect(() => {
    if (!dniReverso || dniReverso.type === 'application/pdf') {
      setReversoPreview(null)
      return
    }
    const url = URL.createObjectURL(dniReverso)
    setReversoPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [dniReverso])

  function handleFileChange(
    field: 'dniFrontal' | 'dniReverso' | 'antecedentesPoliciales' | 'antecedentesPenales',
    label: string,
    e: ChangeEvent<HTMLInputElement>
  ) {
    const file = e.target.files?.[0] ?? null
    setFormError('')
    if (!file) return

    const err = validateFile(file, label)
    if (err) {
      setFieldErrors((prev) => ({ ...prev, [field]: err }))
      return
    }

    setFieldErrors((prev) => ({ ...prev, [field]: undefined }))
    if (field === 'dniFrontal') setDniFrontal(file)
    if (field === 'dniReverso') setDniReverso(file)
    if (field === 'antecedentesPoliciales') setAntecedentesPoliciales(file)
    if (field === 'antecedentesPenales') setAntecedentesPenales(file)
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (busy) return
    setFormError('')
    setNotice('')

    // HU-16 CA-04: Validar documentos obligatorios
    const errors: FieldErrors = {}
    if (!hasExistingDniFrontal && !dniFrontal) errors.dniFrontal = 'El DNI frontal es obligatorio.'
    if (!hasExistingDniReverso && !dniReverso) errors.dniReverso = 'El DNI reverso es obligatorio.'
    if (!hasExistingSelfie && !selfie) errors.selfie = 'La selfie en vivo es obligatoria.'
    if (!hasExistingPoliciales && !antecedentesPoliciales) errors.antecedentesPoliciales = 'La constancia de antecedentes policiales es obligatoria.'
    if (!hasExistingPenales && !antecedentesPenales) errors.antecedentesPenales = 'La constancia de antecedentes penales es obligatoria.'
   // Validar fechas obligatorias para antecedentes si se están subiendo nuevos antecedentes
    if ((antecedentesPoliciales || !hasExistingPoliciales) && !fechaPoliciales) {
      errors.fechaPoliciales = 'Indica la fecha de emisión de los antecedentes policiales.'
    }
    if ((antecedentesPenales || !hasExistingPenales) && !fechaPenales) {
      errors.fechaPenales = 'Indica la fecha de emisión de los antecedentes penales.'
    }

    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors)
      setFormError('Completa todos los documentos y fechas obligatorias antes de enviar la solicitud.')
      return
    }

    setBusy(true)
    try {
      const formData = new FormData()
      if (dniFrontal) formData.append('dni_frontal', dniFrontal)
      if (dniReverso) formData.append('dni_reverso', dniReverso)
      if (selfie) formData.append('selfie', selfie)
      if (antecedentesPoliciales) formData.append('antecedentes_policiales', antecedentesPoliciales)
      if (antecedentesPenales) formData.append('antecedentes_penales', antecedentesPenales)
      if (fechaPoliciales) formData.append('fecha_policiales', fechaPoliciales)
      if (fechaPenales) formData.append('fecha_penales', fechaPenales)  

      const data = await api<VerificationDocsResponse>('/technicians/me/verification-documents', user, formData)
      applyResponse(data, true)
      setDniFrontal(null); setDniReverso(null); setSelfie(null)
      setAntecedentesPoliciales(null); setAntecedentesPenales(null)
      setFechaPoliciales(''); setFechaPenales(''); setFieldErrors({})
      setNotice('Documentos enviados correctamente. Consulta el resultado de identidad y las observaciones de tu expediente.')
    } catch (err) {
      setFormError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  if (loading) return <LoadingState variant="inline" message="Consultando tu expediente técnico." />

  if (loadError) return <div><p className="alert" role="alert">No pudimos consultar tus documentos: {loadError}</p><Button type="button" className="button-outline" style={{ width: 'auto' }} onClick={() => setAttempt(value => value + 1)}>Reintentar carga del expediente</Button></div>
  const identityApproved = ['DNI_FRONTAL', 'DNI_REVERSO', 'SELFIE'].every(type => documents.some(doc => doc.tipo === type && doc.estado === 'APROBADO'))
  const incompleteDossier = ['DNI_FRONTAL', 'DNI_REVERSO', 'SELFIE', 'ANTECEDENTES_POLICIALES', 'ANTECEDENTES_PENALES'].some(type => !documents.some(doc => doc.tipo === type))
  const awaitingReview = status === 'EN_REVISION' && !incompleteDossier
  return (
    <div>
      <Button type="button" className="button-outline" style={{ width: 'auto', marginBottom: 16 }} disabled={busy} onClick={() => setAttempt(value => value + 1)}>Actualizar estado de documentos</Button>
      {/* Alerta de éxito */}
      {notice && (
        <div className="success-alert" role="status" style={{ marginBottom: '1.5rem' }}>
          <CheckCircle2 size={20} />
          <span>{notice}</span>
        </div>
      )}

      {/* Alerta de error global */}
      {formError && (
        <div className="alert" role="alert" style={{ marginBottom: '1.5rem' }}>
          <CircleAlert size={18} style={{ display: 'inline', marginRight: 6 }} />
          {formError}
        </div>
      )}

      {documents.length > 0 && <section className="account-card" style={{ marginBottom: 24 }} aria-label="Resultado de identidad">
        <h3>Evaluación de DNI y selfie</h3>
        <p>{processing ? `${processing.estado}: ${processing.observacion}` : 'Todavía no hay un resultado de evaluación de identidad para esta versión.'}</p>
        <p>{processing?.proceso_responsable === 'REVISION_ADMINISTRATIVA_IDENTIDAD' ? 'Este es el resultado de la revisión administrativa de tu identidad.' : processing?.proceso_responsable === 'IDENTIDAD_REUTILIZADA' ? 'Tu identidad aprobada se conserva al completar los antecedentes del perfil técnico.' : 'La evaluación automática acompaña la revisión administrativa de tus documentos y antecedentes.'}</p>
        {requestId && (!processing || ['ERROR', 'INCIERTO'].includes(processing.estado)) && documents.every(doc => doc.estado === 'PENDIENTE') &&
          <Button type="button" disabled={busy} onClick={retryIdentity} style={{ width: 'auto' }}>{busy ? 'Evaluando identidad…' : 'Reintentar evaluación'}</Button>}
      </section>}

      {/* Tarjeta de estado de revisión */}
      <div style={{
        padding: '1.25rem',
        borderRadius: 12,
        background: status === 'VERIFICADO' ? '#f0fdf4' : status === 'EN_REVISION' ? '#eff6ff' : status === 'RECHAZADO' ? '#fef2f2' : '#f9fafb',
        border: `1px solid ${status === 'VERIFICADO' ? '#86efac' : status === 'EN_REVISION' ? '#93c5fd' : status === 'RECHAZADO' ? '#fca5a5' : '#e5e7eb'}`,
        marginBottom: '2rem'
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          {status === 'VERIFICADO' && <ShieldCheck size={26} color="#16a34a" />}
          {status === 'EN_REVISION' && <Clock3 size={26} color="#2563eb" />}
          {status === 'RECHAZADO' && <ShieldAlert size={26} color="#dc2626" />}
          {status === 'RESTRINGIDO' && <ShieldAlert size={26} color="#dc2626" />}
          {status === 'NO_VERIFICADO' && <CreditCard size={26} color="#6b7280" />}

          <div>
            <div style={{ fontWeight: 700, fontSize: '1.1rem' }}>
              Estado: {status === 'VERIFICADO' ? 'Verificado' : status === 'EN_REVISION' ? 'En revisión administrativa' : status === 'RECHAZADO' ? 'Rechazado (requiere reenvío)' : 'Documentación pendiente'}
            </div>
            <p style={{ margin: '4px 0 0', color: 'var(--muted)', fontSize: '0.9rem' }}>
              {status === 'VERIFICADO' && 'Tu documentación y teléfono están verificados. Recibir contrataciones también depende de tu disponibilidad y de las restricciones de tu cuenta.'}
              {status === 'EN_REVISION' && 'Tus documentos personales y antecedentes están siendo revisados por administración.'}
              {status === 'RECHAZADO' && (observacionesGenerales || 'Uno o más documentos fueron rechazados. Por favor súbelos nuevamente con las correcciones.')}
              {status === 'RESTRINGIDO' && (motivoRestriccion || 'Se retiró temporalmente tu habilitación para nuevas contrataciones porque un requisito obligatorio venció o fue invalidado.')}
              {status === 'NO_VERIFICADO' && 'Para proteger la confianza de los clientes, los técnicos deben presentar DNI, selfie y constancias de antecedentes.'}
            </p>
          </div>
        </div>

        {/* ST-04: Mostrar requisitos pendientes cuando aplica */}
        {status !== 'VERIFICADO' && requisitosPendientes.length > 0 && (
          <div style={{ marginTop: '1rem', paddingTop: '0.85rem', borderTop: '1px solid #e5e7eb' }}>
            <div style={{ fontWeight: 600, fontSize: '0.88rem', marginBottom: 6 }}>
              Requisitos pendientes para obtener o recuperar el estado Técnico Verificado:
            </div>
            <ul style={{ margin: 0, paddingLeft: '1.25rem', fontSize: '0.86rem', color: '#374151' }}>
              {requisitosPendientes.map((req) => (
                <li key={req.codigo} style={{ marginBottom: 3 }}>
                  <strong>{req.nombre}</strong> ({req.estado}){req.motivo ? `: ${req.motivo}` : ''}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {documents.length > 0 && <section aria-label="Estados documentales" style={{ marginBottom: 24 }}>
        <h3>Estado de tus documentos</h3>
        <ul>{documents.map(doc => <li key={doc.id}>
          <strong>{doc.tipo.replaceAll('_', ' ')}</strong>: {doc.estado}
          {doc.observaciones && <p>{doc.observaciones}</p>}
        </li>)}</ul>
      </section>}

      {/* Si está en revisión o verificado, mostramos aviso informativo */}
      {awaitingReview && (
        <div style={{ textAlign: 'center', padding: '2rem 1rem', border: '2px dashed #bfdbfe', borderRadius: 12, background: '#f8fafc' }}>
          <Clock3 size={38} color="#2563eb" style={{ margin: '0 auto 12px' }} />
          <h3 style={{ margin: '0 0 8px' }}>Solicitud en proceso de revisión</h3>
          <p style={{ color: 'var(--muted)', maxWidth: 500, margin: '0 auto' }}>
            Ya recibimos tu DNI, selfie y antecedentes policiales/penales. Te notificaremos en cuanto el equipo administrativo valide tu perfil.
          </p>
        </div>
      )}

      {status === 'VERIFICADO' && (
        <div style={{ textAlign: 'center', padding: '2rem 1rem', border: '2px solid #86efac', borderRadius: 12, background: '#f0fdf4' }}>
          <CheckCircle2 size={40} color="#16a34a" style={{ margin: '0 auto 12px' }} />
          <h3 style={{ margin: '0 0 8px', color: '#166534' }}>Técnico Verificado</h3>
          <p style={{ color: '#15803d', maxWidth: 480, margin: '0 auto' }}>
            Tus documentos han sido aprobados satisfactoriamente. Tu perfil cuenta con insignia de seguridad para los clientes.
          </p>
        </div>
      )}

      {status === 'RESTRINGIDO' && (
        <div style={{ marginBottom: '1.5rem' }}>
          <Button type="button" onClick={() => setStatus('PENDIENTE')} style={{ width: 'auto' }}>
            Actualizar documentos vencidos o invalidados
          </Button>
        </div>
      )}

      {/* Formulario activo (si está pendiente, no verificado o rechazado) */}
      {(status === 'NO_VERIFICADO' || status === 'PENDIENTE' || status === 'RECHAZADO' || (status === 'EN_REVISION' && incompleteDossier)) && (
        <form onSubmit={handleSubmit} noValidate>
          {identityApproved && <p className="success-alert" role="status">Tu DNI y selfie ya están aprobados. Se conservarán; completa únicamente los antecedentes pendientes.</p>}
          {!identityApproved && <>
          {/* SECCIÓN 1: DNI (CA-01) */}
          <div style={{ marginBottom: '2rem' }}>
            <h3 style={{ fontSize: '1.2rem', margin: '0 0 0.5rem', display: 'flex', alignItems: 'center', gap: 8 }}>
              <CreditCard size={20} color="var(--blue)" /> 1. Documento Nacional de Identificación (DNI)
            </h3>
            <p style={{ color: 'var(--muted)', fontSize: '0.9rem', margin: '0 0 1rem' }}>
              Sube fotos claras o archivos PDF de tu DNI por ambos lados. Máximo 5 MB por archivo.
            </p>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '1.25rem' }}>
              {/* DNI Frontal */}
              <div style={{ border: '1px solid #dce2eb', borderRadius: 12, padding: '1rem', background: '#fff' }}>
                <span style={{ fontWeight: 600, display: 'block', marginBottom: 8 }}>DNI (Frente / Frontal) *</span>
                {hasExistingDniFrontal && !dniFrontal ? (
                  <div style={{ padding: '8px 10px', background: '#f0fdf4', borderRadius: 8, border: '1px solid #86efac', color: '#166534', fontSize: '0.85rem', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}><CheckCircle2 size={16} /> Documento cargado previamente</span>
                    <button type="button" onClick={() => frontalInputRef.current?.click()} style={{ background: 'none', border: 'none', color: '#2563eb', cursor: 'pointer', fontSize: '0.8rem', textDecoration: 'underline' }}>Cambiar</button>
                  </div>
                ) : null}

                <input
                  ref={frontalInputRef}
                  type="file"
                  accept={ACCEPTED_FORMATS}
                  style={{ display: hasExistingDniFrontal && !dniFrontal ? 'none' : 'block', width: '100%' }}
                  onChange={(e) => handleFileChange('dniFrontal', 'El DNI frontal', e)}
                />
                {frontalPreview && (
                  <img src={frontalPreview} alt="DNI Frontal" style={{ width: '100%', maxHeight: 150, objectFit: 'contain', borderRadius: 8, marginTop: 8, border: '1px solid #e5e7eb' }} />
                )}
                {fieldErrors.dniFrontal && <p style={{ color: '#dc2626', fontSize: '0.8rem', margin: '6px 0 0' }}>{fieldErrors.dniFrontal}</p>}
              </div>

              {/* DNI Reverso */}
              <div style={{ border: '1px solid #dce2eb', borderRadius: 12, padding: '1rem', background: '#fff' }}>
                <span style={{ fontWeight: 600, display: 'block', marginBottom: 8 }}>DNI (Reverso / Atrás) *</span>
                {hasExistingDniReverso && !dniReverso ? (
                  <div style={{ padding: '8px 10px', background: '#f0fdf4', borderRadius: 8, border: '1px solid #86efac', color: '#166534', fontSize: '0.85rem', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}><CheckCircle2 size={16} /> Documento cargado previamente</span>
                    <button type="button" onClick={() => reversoInputRef.current?.click()} style={{ background: 'none', border: 'none', color: '#2563eb', cursor: 'pointer', fontSize: '0.8rem', textDecoration: 'underline' }}>Cambiar</button>
                  </div>
                ) : null}

                <input
                  ref={reversoInputRef}
                  type="file"
                  accept={ACCEPTED_FORMATS}
                  style={{ display: hasExistingDniReverso && !dniReverso ? 'none' : 'block', width: '100%' }}
                  onChange={(e) => handleFileChange('dniReverso', 'El DNI reverso', e)}
                />
                {reversoPreview && (
                  <img src={reversoPreview} alt="DNI Reverso" style={{ width: '100%', maxHeight: 150, objectFit: 'contain', borderRadius: 8, marginTop: 8, border: '1px solid #e5e7eb' }} />
                )}
                {fieldErrors.dniReverso && <p style={{ color: '#dc2626', fontSize: '0.8rem', margin: '6px 0 0' }}>{fieldErrors.dniReverso}</p>}
              </div>
            </div>
          </div>

          {/* SECCIÓN 2: BIOMETRÍA / SELFIE (CA-02) */}
          <div style={{ marginBottom: '2rem' }}>
            <h3 style={{ fontSize: '1.2rem', margin: '0 0 0.5rem', display: 'flex', alignItems: 'center', gap: 8 }}>
              <ShieldCheck size={20} color="var(--blue)" /> 2. Foto selfie en vivo
            </h3>
            <p style={{ color: 'var(--muted)', fontSize: '0.9rem', margin: '0 0 1rem' }}>
              Tómate una foto en vivo desde tu cámara para comparar con tu DNI. No se admiten fotos prediseñadas o de archivo.
            </p>
            {hasExistingSelfie && !selfie ? (
              <div style={{ padding: '12px 16px', background: '#f0fdf4', borderRadius: 8, border: '1px solid #86efac', color: '#166534', fontSize: '0.9rem', display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1rem' }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}><CheckCircle2 size={18} /> Selfie verificada/cargada previamente</span>
              </div>
            ) : (
              <SelfieCameraCapture
                value={selfie}
                error={fieldErrors.selfie}
                onCapture={(file, err) => {
                  setSelfie(file)
                  setFieldErrors((prev) => ({ ...prev, selfie: err }))
                }}
              />
            )}
          </div>

          </>}
          {/* SECCIÓN 3: ANTECEDENTES POLICIALES Y PENALES (CA-03, ST-04) */}
          <div style={{ marginBottom: '2.5rem' }}>
            <h3 style={{ fontSize: '1.2rem', margin: '0 0 0.5rem', display: 'flex', alignItems: 'center', gap: 8 }}>
              <FileCheck size={20} color="var(--blue)" /> 3. Antecedentes Policiales y Penales
            </h3>
            <p style={{ color: 'var(--muted)', fontSize: '0.9rem', margin: '0 0 1rem' }}>
              Adjunta las constancias emitidas por la DPI y el Poder Judicial de Honduras, e indica su fecha de emisión para validar su vigencia.
            </p>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '1.25rem' }}>
              {/* Antecedentes Policiales */}
              <div style={{ border: '1px solid #dce2eb', borderRadius: 12, padding: '1rem', background: '#fff' }}>
                <span style={{ fontWeight: 600, display: 'block', marginBottom: 6 }}>Antecedentes Policiales (DPI) *</span>
                {hasExistingPoliciales && !antecedentesPoliciales ? (
                  <div style={{ padding: '8px 10px', background: '#f0fdf4', borderRadius: 8, border: '1px solid #86efac', color: '#166534', fontSize: '0.85rem', marginBottom: 10 }}>
                    <CheckCircle2 size={16} style={{ display: 'inline', marginRight: 4 }} /> Constancia policial cargada previa
                  </div>
                ) : null}

                <input
                  ref={policialesInputRef}
                  type="file"
                  accept={ACCEPTED_FORMATS}
                  style={{ display: 'none' }}
                  onChange={(e) => handleFileChange('antecedentesPoliciales', 'Los antecedentes policiales', e)}
                />
                {!antecedentesPoliciales ? (
                  <Button type="button" className="button-outline" onClick={() => policialesInputRef.current?.click()} style={{ width: '100%', marginBottom: 10 }}>
                    <Upload size={16} /> {hasExistingPoliciales ? 'Reemplazar constancia policial' : 'Adjuntar constancia policial'}
                  </Button>
                ) : (
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 10px', background: '#f8fafc', borderRadius: 8, marginBottom: 10 }}>
                    <span style={{ fontSize: '0.85rem', fontWeight: 600, overflowWrap: 'anywhere' }}>{antecedentesPoliciales.name} ({formatSize(antecedentesPoliciales.size)})</span>
                    <button type="button" onClick={() => setAntecedentesPoliciales(null)} style={{ border: 'none', background: 'none', cursor: 'pointer', color: '#dc2626' }}>
                      <X size={16} />
                    </button>
                  </div>
                )}
                {fieldErrors.antecedentesPoliciales && <p style={{ color: '#dc2626', fontSize: '0.8rem', margin: '0 0 8px' }}>{fieldErrors.antecedentesPoliciales}</p>}

                <label style={{ display: 'block', fontSize: '0.8rem', fontWeight: 600, color: 'var(--muted)', marginBottom: 4 }}>
                  Fecha de emisión declarada *
                </label>
                <input
                  type="date"
                  value={fechaPoliciales}
                  onChange={(e) => { setFechaPoliciales(e.target.value); setFieldErrors((prev) => ({ ...prev, fechaPoliciales: undefined })) }}
                  style={{ width: '100%', minHeight: 42, padding: '6px 10px', borderRadius: 8, border: '1px solid #dce2eb', fontSize: '0.9rem' }}
                />
                {fieldErrors.fechaPoliciales && <p style={{ color: '#dc2626', fontSize: '0.8rem', margin: '4px 0 0' }}>{fieldErrors.fechaPoliciales}</p>}
              </div>

              {/* Antecedentes Penales */}
              <div style={{ border: '1px solid #dce2eb', borderRadius: 12, padding: '1rem', background: '#fff' }}>
                <span style={{ fontWeight: 600, display: 'block', marginBottom: 6 }}>Antecedentes Penales (Poder Judicial) *</span>
                {hasExistingPenales && !antecedentesPenales ? (
                  <div style={{ padding: '8px 10px', background: '#f0fdf4', borderRadius: 8, border: '1px solid #86efac', color: '#166534', fontSize: '0.85rem', marginBottom: 10 }}>
                    <CheckCircle2 size={16} style={{ display: 'inline', marginRight: 4 }} /> Constancia penal cargada previa
                  </div>
                ) : null}

                <input
                  ref={penalesInputRef}
                  type="file"
                  accept={ACCEPTED_FORMATS}
                  style={{ display: 'none' }}
                  onChange={(e) => handleFileChange('antecedentesPenales', 'Los antecedentes penales', e)}
                />
                {!antecedentesPenales ? (
                  <Button type="button" className="button-outline" onClick={() => penalesInputRef.current?.click()} style={{ width: '100%', marginBottom: 10 }}>
                    <Upload size={16} /> {hasExistingPenales ? 'Reemplazar constancia penal' : 'Adjuntar constancia penal'}
                  </Button>
                ) : (
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 10px', background: '#f8fafc', borderRadius: 8, marginBottom: 10 }}>
                    <span style={{ fontSize: '0.85rem', fontWeight: 600, overflowWrap: 'anywhere' }}>{antecedentesPenales.name} ({formatSize(antecedentesPenales.size)})</span>
                    <button type="button" onClick={() => setAntecedentesPenales(null)} style={{ border: 'none', background: 'none', cursor: 'pointer', color: '#dc2626' }}>
                      <X size={16} />
                    </button>
                  </div>
                )}
                {fieldErrors.antecedentesPenales && <p style={{ color: '#dc2626', fontSize: '0.8rem', margin: '0 0 8px' }}>{fieldErrors.antecedentesPenales}</p>}

                <label style={{ display: 'block', fontSize: '0.8rem', fontWeight: 600, color: 'var(--muted)', marginBottom: 4 }}>
                  Fecha de emisión declarada *
                </label>
                <input
                  type="date"
                  value={fechaPenales}
                  onChange={(e) => { setFechaPenales(e.target.value); setFieldErrors((prev) => ({ ...prev, fechaPenales: undefined })) }}
                  style={{ width: '100%', minHeight: 42, padding: '6px 10px', borderRadius: 8, border: '1px solid #dce2eb', fontSize: '0.9rem' }}
                />
                {fieldErrors.fechaPenales && <p style={{ color: '#dc2626', fontSize: '0.8rem', margin: '4px 0 0' }}>{fieldErrors.fechaPenales}</p>}
              </div>
            </div>
          </div>

          {/* Botón de envío */}
          <div style={{ display: 'flex', justifyContent: 'flex-start' }}>
            <Button type="submit" disabled={busy} style={{ width: 'auto', minWidth: 260 }}>
              {busy ? 'Enviando solicitud…' : 'Enviar solicitud de verificación'}
            </Button>
          </div>
        </form>
      )}
    </div>
  )
}
