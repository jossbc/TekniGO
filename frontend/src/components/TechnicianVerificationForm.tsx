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

type Props = {
  user: User
  initialStatus?: string
  onStatusChange?: (status: string) => void
}

type VerificationDocsResponse = {
  estado_verificacion: string
  observaciones?: string | null
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
  const [observacionesGenerales, setObservacionesGenerales] = useState<string | null>(null)
  const [hasUploadedDocs, setHasUploadedDocs] = useState(false)

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

  useEffect(() => {
    let active = true
    api<VerificationDocsResponse>('/technicians/me/verification-documents', user)
      .then((res) => {
        if (!active) return
        const uploaded = Array.isArray(res.documentos) && res.documentos.length > 0
        setHasUploadedDocs(uploaded)
        setStatus(res.estado_verificacion || 'NO_VERIFICADO')
        setObservacionesGenerales(res.observaciones ?? null)
      })
      .catch(() => {
        if (!active) return
        setHasUploadedDocs(false)
        setStatus(initialStatus)
      })
    return () => { active = false }
  }, [user, initialStatus])

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
    setFormError('')
    setNotice('')

    // HU-16 CA-04: Validar documentos obligatorios
    const errors: FieldErrors = {}
    if (!dniFrontal) errors.dniFrontal = 'El DNI frontal es obligatorio.'
    if (!dniReverso) errors.dniReverso = 'El DNI reverso es obligatorio.'
    if (!selfie) errors.selfie = 'La selfie en vivo es obligatoria.'
    if (!antecedentesPoliciales) errors.antecedentesPoliciales = 'La constancia de antecedentes policiales es obligatoria.'
    if (!antecedentesPenales) errors.antecedentesPenales = 'La constancia de antecedentes penales es obligatoria.'
    if (!fechaPoliciales) errors.fechaPoliciales = 'Indica la fecha de emisión de los antecedentes policiales.'
    if (!fechaPenales) errors.fechaPenales = 'Indica la fecha de emisión de los antecedentes penales.'

    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors)
      setFormError('Completa todos los documentos y fechas obligatorias antes de enviar la solicitud.')
      return
    }

    setBusy(true)
    try {
      const formData = new FormData()
      formData.append('dni_frontal', dniFrontal!)
      formData.append('dni_reverso', dniReverso!)
      formData.append('selfie', selfie!)
      formData.append('antecedentes_policiales', antecedentesPoliciales!)
      formData.append('antecedentes_penales', antecedentesPenales!)
      formData.append('fecha_policiales', fechaPoliciales)
      formData.append('fecha_penales', fechaPenales)

      const token = await user.getIdToken()
      const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'
      const response = await fetch(`${API_URL}/technicians/me/verification-documents`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: formData,
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(errorData.detail || 'Error al enviar la solicitud de verificación.')
      }

      const data = await response.json()
      setStatus(data.estado_verificacion || 'EN_REVISION')
      setHasUploadedDocs(true)
      onStatusChange?.(data.estado_verificacion || 'EN_REVISION')
      setNotice('Documentos enviados correctamente. Tu solicitud ha pasado a estado En revisión.')
    } catch (err) {
      setFormError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
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
          {status === 'NO_VERIFICADO' && <CreditCard size={26} color="#6b7280" />}

          <div>
            <div style={{ fontWeight: 700, fontSize: '1.1rem' }}>
              Estado: {status === 'VERIFICADO' ? 'Verificado' : status === 'EN_REVISION' ? 'En revisión administrativa' : status === 'RECHAZADO' ? 'Rechazado (requiere reenvío)' : 'Documentación pendiente'}
            </div>
            <p style={{ margin: '4px 0 0', color: 'var(--muted)', fontSize: '0.9rem' }}>
              {status === 'VERIFICADO' && '¡Felicidades! Tu cuenta está verificada y puedes recibir contrataciones activas.'}
              {status === 'EN_REVISION' && 'Tus documentos personales y antecedentes están siendo revisados por administración.'}
              {status === 'RECHAZADO' && (observacionesGenerales || 'Uno o más documentos fueron rechazados. Por favor súbelos nuevamente con las correcciones.')}
              {status === 'NO_VERIFICADO' && 'Para proteger la confianza de los clientes, los técnicos deben presentar DNI, selfie y constancias de antecedentes.'}
            </p>
          </div>
        </div>
      </div>

      {/* Si está en revisión o verificado, mostramos aviso informativo */}
      {status === 'EN_REVISION' && (
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

      {/* Formulario activo (si está pendiente, no verificado o rechazado) */}
      {(status === 'NO_VERIFICADO' || status === 'PENDIENTE' || status === 'RECHAZADO') && (
        <form onSubmit={handleSubmit} noValidate>
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
                <input
                  ref={frontalInputRef}
                  type="file"
                  accept={ACCEPTED_FORMATS}
                  style={{ display: 'none' }}
                  onChange={(e) => handleFileChange('dniFrontal', 'El DNI frontal', e)}
                />
                {!dniFrontal ? (
                  <Button type="button" className="button-outline" onClick={() => frontalInputRef.current?.click()} style={{ width: '100%' }}>
                    <Upload size={16} /> Seleccionar archivo
                  </Button>
                ) : (
                  <div>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                      <span style={{ fontSize: '0.85rem', fontWeight: 600, overflowWrap: 'anywhere' }}>{dniFrontal.name} ({formatSize(dniFrontal.size)})</span>
                      <button type="button" onClick={() => setDniFrontal(null)} style={{ border: 'none', background: 'none', cursor: 'pointer', color: '#dc2626' }}>
                        <X size={16} />
                      </button>
                    </div>
                    {frontalPreview && (
                      <img src={frontalPreview} alt="DNI Frontal" style={{ width: '100%', maxHeight: 150, objectFit: 'contain', borderRadius: 8, border: '1px solid #e5e7eb' }} />
                    )}
                  </div>
                )}
                {fieldErrors.dniFrontal && <p style={{ color: '#dc2626', fontSize: '0.8rem', margin: '6px 0 0' }}>{fieldErrors.dniFrontal}</p>}
              </div>

              {/* DNI Reverso */}
              <div style={{ border: '1px solid #dce2eb', borderRadius: 12, padding: '1rem', background: '#fff' }}>
                <span style={{ fontWeight: 600, display: 'block', marginBottom: 8 }}>DNI (Reverso / Atrás) *</span>
                <input
                  ref={reversoInputRef}
                  type="file"
                  accept={ACCEPTED_FORMATS}
                  style={{ display: 'none' }}
                  onChange={(e) => handleFileChange('dniReverso', 'El DNI reverso', e)}
                />
                {!dniReverso ? (
                  <Button type="button" className="button-outline" onClick={() => reversoInputRef.current?.click()} style={{ width: '100%' }}>
                    <Upload size={16} /> Seleccionar archivo
                  </Button>
                ) : (
                  <div>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                      <span style={{ fontSize: '0.85rem', fontWeight: 600, overflowWrap: 'anywhere' }}>{dniReverso.name} ({formatSize(dniReverso.size)})</span>
                      <button type="button" onClick={() => setDniReverso(null)} style={{ border: 'none', background: 'none', cursor: 'pointer', color: '#dc2626' }}>
                        <X size={16} />
                      </button>
                    </div>
                    {reversoPreview && (
                      <img src={reversoPreview} alt="DNI Reverso" style={{ width: '100%', maxHeight: 150, objectFit: 'contain', borderRadius: 8, border: '1px solid #e5e7eb' }} />
                    )}
                  </div>
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
            <SelfieCameraCapture
              value={selfie}
              error={fieldErrors.selfie}
              onCapture={(file, err) => {
                setSelfie(file)
                setFieldErrors((prev) => ({ ...prev, selfie: err }))
              }}
            />
          </div>

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
                <input
                  ref={policialesInputRef}
                  type="file"
                  accept={ACCEPTED_FORMATS}
                  style={{ display: 'none' }}
                  onChange={(e) => handleFileChange('antecedentesPoliciales', 'Los antecedentes policiales', e)}
                />
                {!antecedentesPoliciales ? (
                  <Button type="button" className="button-outline" onClick={() => policialesInputRef.current?.click()} style={{ width: '100%', marginBottom: 10 }}>
                    <Upload size={16} /> Adjuntar constancia policial
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

                {/* Fecha declarada */}
                <label style={{ display: 'block', fontSize: '0.8rem', fontWeight: 600, color: 'var(--muted)', marginBottom: 4 }}>
                  Fecha de emisión declarada *
                </label>
                <input
                  type="date"
                  value={fechaPoliciales}
                  onChange={(e) => { setFechaPoliciales(e.target.value); setFieldErrors((prev) => ({ ...prev, fechaPoliciales: undefined })) }}
                  style={{ width: '100%', minHeight: 42, padding: '6px 10px', borderRadius: 8, border: '1px solid #dce2eb', fontSize: '0.9rem' }}
                  required
                />
                {fieldErrors.fechaPoliciales && <p style={{ color: '#dc2626', fontSize: '0.8rem', margin: '4px 0 0' }}>{fieldErrors.fechaPoliciales}</p>}
              </div>

              {/* Antecedentes Penales */}
              <div style={{ border: '1px solid #dce2eb', borderRadius: 12, padding: '1rem', background: '#fff' }}>
                <span style={{ fontWeight: 600, display: 'block', marginBottom: 6 }}>Antecedentes Penales (Poder Judicial) *</span>
                <input
                  ref={penalesInputRef}
                  type="file"
                  accept={ACCEPTED_FORMATS}
                  style={{ display: 'none' }}
                  onChange={(e) => handleFileChange('antecedentesPenales', 'Los antecedentes penales', e)}
                />
                {!antecedentesPenales ? (
                  <Button type="button" className="button-outline" onClick={() => penalesInputRef.current?.click()} style={{ width: '100%', marginBottom: 10 }}>
                    <Upload size={16} /> Adjuntar constancia penal
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

                {/* Fecha declarada */}
                <label style={{ display: 'block', fontSize: '0.8rem', fontWeight: 600, color: 'var(--muted)', marginBottom: 4 }}>
                  Fecha de emisión declarada *
                </label>
                <input
                  type="date"
                  value={fechaPenales}
                  onChange={(e) => { setFechaPenales(e.target.value); setFieldErrors((prev) => ({ ...prev, fechaPenales: undefined })) }}
                  style={{ width: '100%', minHeight: 42, padding: '6px 10px', borderRadius: 8, border: '1px solid #dce2eb', fontSize: '0.9rem' }}
                  required
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