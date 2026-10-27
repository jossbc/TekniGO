import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from 'react'
import type { User } from 'firebase/auth'
import { CheckCircle2, CircleAlert, Clock3, CreditCard, ShieldCheck, Upload, X } from 'lucide-react'
import { api, errorMessage } from '../lib/api'
import { ALLOWED_IMAGE_TYPES, IMAGE_RULES_HINT, validateIdentityImage } from '../lib/validation'
import SelfieCameraCapture from './SelfieCameraCapture'
import { Button } from './ui'

type Props = {
  user: User
  initialStatus?: string
  onStatusChange?: (status: string) => void
}

type IdentityDocsResponse = {
  estado_identidad: string
  documentos: { id: string; tipo: string; estado: string; observaciones?: string | null }[]
}

type FieldErrors = {
  dniFrontal?: string
  dniReverso?: string
  selfie?: string
}

const ACCEPT_ATTR = ALLOWED_IMAGE_TYPES.join(',')

function formatSize(bytes: number): string {
  return bytes >= 1024 * 1024
    ? `${(bytes / (1024 * 1024)).toFixed(2)} MB`
    : `${Math.max(1, Math.round(bytes / 1024))} KB`
}

export default function ClientIdentityVerification({ user, initialStatus = 'PENDIENTE', onStatusChange }: Props) {
  const [status, setStatus] = useState('PENDIENTE')
  const [hasUploadedDocs, setHasUploadedDocs] = useState(false)
  const [dniFrontal, setDniFrontal] = useState<File | null>(null)
  const [dniReverso, setDniReverso] = useState<File | null>(null)
  const [frontalPreview, setFrontalPreview] = useState<string | null>(null)
  const [reversoPreview, setReversoPreview] = useState<string | null>(null)
  const [selfie, setSelfie] = useState<File | null>(null)
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})
  const [formError, setFormError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)

  const frontalInputRef = useRef<HTMLInputElement | null>(null)
  const reversoInputRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    let active = true
    api<IdentityDocsResponse>('/users/me/identity-documents', user)
      .then((res) => {
        if (!active) return
        const uploaded = Array.isArray(res.documentos) && res.documentos.length > 0
        setHasUploadedDocs(uploaded)
        setStatus(uploaded ? res.estado_identidad : 'PENDIENTE')
      })
      .catch(() => {
        if (!active) return
        setHasUploadedDocs(false)
        setStatus(initialStatus === 'EN_REVISION' ? 'EN_REVISION' : 'PENDIENTE')
      })
    return () => {
      active = false
    }
  }, [user, initialStatus])

  useEffect(() => {
    if (!dniFrontal) {
      setFrontalPreview((prev) => {
        if (prev) URL.revokeObjectURL(prev)
        return null
      })
      return
    }
    const url = URL.createObjectURL(dniFrontal)
    setFrontalPreview((prev) => {
      if (prev) URL.revokeObjectURL(prev)
      return url
    })
    return () => URL.revokeObjectURL(url)
  }, [dniFrontal])

  useEffect(() => {
    if (!dniReverso) {
      setReversoPreview((prev) => {
        if (prev) URL.revokeObjectURL(prev)
        return null
      })
      return
    }
    const url = URL.createObjectURL(dniReverso)
    setReversoPreview((prev) => {
      if (prev) URL.revokeObjectURL(prev)
      return url
    })
    return () => URL.revokeObjectURL(url)
  }, [dniReverso])

  function handleFileChange(
    kind: 'dniFrontal' | 'dniReverso',
    label: string,
    event: ChangeEvent<HTMLInputElement>,
  ) {
    const file = event.currentTarget.files?.[0] ?? null
    setNotice('')
    setFormError('')
    const error = file ? validateIdentityImage(file, label) : `Debes adjuntar ${label}.`
    setFieldErrors((prev) => ({ ...prev, [kind]: error || undefined }))

    if (kind === 'dniFrontal') setDniFrontal(error ? null : file)
    if (kind === 'dniReverso') setDniReverso(error ? null : file)
  }

  function clearDniFile(kind: 'dniFrontal' | 'dniReverso') {
    setNotice('')
    setFormError('')
    setFieldErrors((prev) => ({ ...prev, [kind]: undefined }))
    if (kind === 'dniFrontal') {
      setDniFrontal(null)
      if (frontalInputRef.current) frontalInputRef.current.value = ''
    } else {
      setDniReverso(null)
      if (reversoInputRef.current) reversoInputRef.current.value = ''
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setNotice('')
    setFormError('')

    const nextErrors: FieldErrors = {
      dniFrontal: validateIdentityImage(dniFrontal, 'el DNI (frente)') || undefined,
      dniReverso: validateIdentityImage(dniReverso, 'el DNI (reverso)') || undefined,
      selfie: validateIdentityImage(selfie, 'la selfie') || undefined,
    }
    setFieldErrors(nextErrors)
    if (nextErrors.dniFrontal || nextErrors.dniReverso || nextErrors.selfie) return

    setBusy(true)
    try {
      const token = await user.getIdToken()
      const formData = new FormData()
      formData.append('dni_frontal', dniFrontal!)
      formData.append('dni_reverso', dniReverso!)
      formData.append('selfie', selfie!)

      const response = await fetch(
        `${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/users/me/identity-documents`,
        {
          method: 'POST',
          headers: { Authorization: `Bearer ${token}` },
          body: formData,
        },
      )

      if (!response.ok) {
        const data = await response.json().catch(() => ({}))
        const detail = typeof data.detail === 'string' ? data.detail : 'No pudimos enviar tus documentos.'
        throw new Error(detail)
      }

      setHasUploadedDocs(true)
      setStatus('EN_REVISION')
      onStatusChange?.('EN_REVISION')
      setNotice('Tus documentos fueron enviados y quedaron pendientes de revisión por un administrador.')
    } catch (err) {
      setFormError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  if (hasUploadedDocs && status === 'VERIFICADO') {
    return (
      <div className="success-alert" role="status">
        <CheckCircle2 size={20} />
        <span>Tu identidad ya fue verificada.</span>
      </div>
    )
  }

  if (hasUploadedDocs && status === 'EN_REVISION') {
    return (
      <div>
        {notice && (
          <div className="success-alert" role="status" style={{ marginBottom: '1rem' }}>
            <CheckCircle2 size={20} />
            <span>{notice}</span>
          </div>
        )}
        <span className="status-pill" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <Clock3 size={15} /> En revisión administrativa
        </span>
        <p style={{ marginTop: '0.75rem', marginBottom: 0 }}>
          Ya recibimos tu DNI por ambos lados y tu selfie. Un administrador revisará tus documentos de forma privada.
        </p>
      </div>
    )
  }

  const renderDniCard = (
    kind: 'dniFrontal' | 'dniReverso',
    id: string,
    title: string,
    subtitle: string,
    file: File | null,
    preview: string | null,
    inputRef: React.RefObject<HTMLInputElement | null>,
    errorMsg?: string,
  ) => {
    const selected = !!file
    return (
      <div
        style={{
          border: `1.5px ${selected ? 'solid #2563eb' : 'dashed #cbd5e1'}`,
          borderRadius: 14,
          padding: '1.25rem 1.35rem',
          background: selected ? '#eff6ff' : '#f6f8fc',
          transition: 'border-color 0.2s, background-color 0.2s',
        }}
      >
        <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'flex-start', gap: 10, marginBottom: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span
              style={{
                width: 36,
                height: 36,
                borderRadius: 10,
                background: selected ? '#2563eb' : '#fde8d7',
                color: selected ? '#ffffff' : '#ea580c',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                flexShrink: 0,
              }}
            >
              <CreditCard size={18} />
            </span>
            <div>
              <label htmlFor={id} style={{ display: 'block', fontWeight: 700, color: 'var(--ink)', cursor: 'pointer' }}>
                {title}
              </label>
              <small style={{ color: 'var(--muted)' }}>{subtitle}</small>
            </div>
          </div>

          {selected && (
            <Button
              type="button"
              className="button-outline"
              style={{ width: 'auto', padding: '0.35rem 0.75rem', fontSize: '0.85rem', color: '#b91c1c', borderColor: '#fecaca' }}
              onClick={() => clearDniFile(kind)}
            >
              <X size={14} /> Cancelar
            </Button>
          )}
        </div>

        <input
          ref={inputRef}
          id={id}
          type="file"
          accept={ACCEPT_ATTR}
          onChange={(e) => handleFileChange(kind, kind === 'dniFrontal' ? 'el DNI (frente)' : 'el DNI (reverso)', e)}
          style={{ width: '100%', fontSize: '0.92rem' }}
        />

        {file && preview && (
          <div
            style={{
              marginTop: 10,
              padding: '0.6rem 0.85rem',
              borderRadius: 10,
              background: '#ffffff',
              border: '1px solid #bfdbfe',
              display: 'flex',
              alignItems: 'center',
              gap: 10,
            }}
          >
            <img
              src={preview}
              alt={title}
              style={{ width: 64, height: 44, objectFit: 'cover', borderRadius: 8, border: '1px solid #93c5fd', flexShrink: 0 }}
            />
            <small style={{ color: '#1d4ed8', fontWeight: 600, overflowWrap: 'anywhere' }}>
              <Upload size={13} style={{ verticalAlign: 'middle' }} /> {file.name} ({formatSize(file.size)})
            </small>
          </div>
        )}

        {errorMsg && (
          <p role="alert" style={{ color: '#b91c1c', fontWeight: 600, fontSize: '0.85rem', display: 'flex', alignItems: 'center', gap: 6, margin: '8px 0 0' }}>
            <CircleAlert size={15} /> {errorMsg}
          </p>
        )}
      </div>
    )
  }

  return (
    <form onSubmit={handleSubmit} noValidate>
      <p style={{ margin: '0 0 0.9rem' }}>
        Adjunta una fotografía clara de tu DNI por ambos lados y tómate una selfie en tiempo real para verificar tu identidad.
      </p>

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          padding: '0.75rem 1rem',
          borderRadius: 12,
          background: '#eff6ff',
          border: '1px solid #bfdbfe',
          fontSize: '0.92rem',
          color: '#1e3a8a',
          marginBottom: '1.35rem',
        }}
      >
        <ShieldCheck size={18} color="#2563eb" style={{ flexShrink: 0 }} />
        <span>{IMAGE_RULES_HINT} Tus archivos se almacenan cifrados y privados; solo administración puede revisarlos.</span>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 280px), 1fr))', gap: '1.25rem', marginBottom: '1.25rem' }}>
        {renderDniCard(
          'dniFrontal',
          'dni-frontal',
          '1. Fotografía del DNI (frente)',
          'Lado donde aparece tu foto y nombre.',
          dniFrontal,
          frontalPreview,
          frontalInputRef,
          fieldErrors.dniFrontal,
        )}
        {renderDniCard(
          'dniReverso',
          'dni-reverso',
          '2. Fotografía del DNI (reverso)',
          'Lado posterior legible y sin reflejos.',
          dniReverso,
          reversoPreview,
          reversoInputRef,
          fieldErrors.dniReverso,
        )}
      </div>

      <SelfieCameraCapture
        value={selfie}
        error={fieldErrors.selfie}
        onCapture={(captured, error) => {
          setNotice('')
          setFormError('')
          setSelfie(captured)
          setFieldErrors((prev) => ({ ...prev, selfie: error }))
        }}
      />

      {formError && (
        <div className="alert" role="alert" style={{ marginBottom: '1rem' }}>
          {formError}
        </div>
      )}

      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <Button type="submit" disabled={busy} style={{ width: 'auto', padding: '0.8rem 1.6rem', fontSize: '1rem' }}>
          {busy ? 'Enviando documentos…' : 'Enviar para verificación'}
        </Button>
      </div>
    </form>
  )
}
