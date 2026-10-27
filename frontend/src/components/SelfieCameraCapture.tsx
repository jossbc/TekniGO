import { useEffect, useRef, useState } from 'react'
import { Camera, CircleAlert, RotateCcw, X } from 'lucide-react'
import { validateIdentityImage } from '../lib/validation'
import { Button } from './ui'

type Props = {
  value: File | null
  error?: string
  onCapture: (file: File | null, error?: string) => void
}

function formatSize(bytes: number): string {
  return bytes >= 1024 * 1024
    ? `${(bytes / (1024 * 1024)).toFixed(2)} MB`
    : `${Math.max(1, Math.round(bytes / 1024))} KB`
}

function cameraErrorMessage(err: unknown): string {
  const name = (err as { name?: string })?.name
  if (name === 'NotAllowedError' || name === 'PermissionDeniedError') {
    return 'Permiso de cámara denegado. Autoriza el uso de la cámara en tu navegador e intenta de nuevo.'
  }
  if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
    return 'No encontramos una cámara conectada en este dispositivo.'
  }
  if (name === 'NotReadableError' || name === 'TrackStartError') {
    return 'La cámara está siendo utilizada por otra aplicación. Ciérrala e intenta de nuevo.'
  }
  return 'No pudimos iniciar la cámara. Revisa los permisos de tu navegador e intenta nuevamente.'
}

export default function SelfieCameraCapture({ value, error, onCapture }: Props) {
  const [cameraOpen, setCameraOpen] = useState(false)
  const [videoReady, setVideoReady] = useState(false)
  const [cameraError, setCameraError] = useState('')
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)

  const videoRef = useRef<HTMLVideoElement | null>(null)
  const streamRef = useRef<MediaStream | null>(null)

  useEffect(() => {
    if (!value) {
      setPreviewUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev)
        return null
      })
      return
    }
    const url = URL.createObjectURL(value)
    setPreviewUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev)
      return url
    })
    return () => URL.revokeObjectURL(url)
  }, [value])

  useEffect(() => {
    return () => stopCamera()
  }, [])

  function stopCamera() {
    if (videoRef.current) {
      const videoStream = videoRef.current.srcObject as MediaStream | null
      if (videoStream) {
        videoStream.getTracks().forEach((track) => track.stop())
      }
      videoRef.current.pause()
      videoRef.current.srcObject = null
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop())
      streamRef.current = null
    }
    setVideoReady(false)
    setCameraOpen(false)
  }

  async function startCamera() {
    stopCamera()
    setCameraError('')
    if (!navigator.mediaDevices?.getUserMedia) {
      setCameraError('Tu navegador no soporta captura de cámara en tiempo real.')
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      })
      streamRef.current = stream
      setCameraOpen(true)
      requestAnimationFrame(() => {
        const video = videoRef.current
        if (video) {
          video.srcObject = stream
          video.play().catch(() => {})
        }
      })
    } catch (err) {
      stopCamera()
      setCameraError(cameraErrorMessage(err))
    }
  }

  function takeSnapshot() {
    const video = videoRef.current
    if (!video || !videoReady || video.videoWidth === 0) {
      setCameraError('La cámara aún se está preparando. Espera un momento e intenta de nuevo.')
      return
    }
    const canvas = document.createElement('canvas')
    canvas.width = video.videoWidth
    canvas.height = video.videoHeight
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    ctx.translate(canvas.width, 0)
    ctx.scale(-1, 1)
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height)

    // Apagar el hardware de la cámara inmediatamente después de capturar el frame
    stopCamera()

    canvas.toBlob(
      (blob) => {
        if (!blob) {
          setCameraError('No pudimos capturar la fotografía. Intenta de nuevo.')
          return
        }
        const captured = new File([blob], `selfie-${Date.now()}.jpg`, { type: 'image/jpeg' })
        const validationError = validateIdentityImage(captured, 'la selfie')
        if (validationError) {
          onCapture(null, validationError)
          return
        }
        onCapture(captured, undefined)
      },
      'image/jpeg',
      0.92,
    )
  }

  function clearSelfie() {
    stopCamera()
    setCameraError('')
    onCapture(null, undefined)
  }

  const hasValue = !!value

  return (
    <div
      style={{
        border: `1.5px ${hasValue ? 'solid #2563eb' : 'dashed #cbd5e1'}`,
        borderRadius: 14,
        padding: '1.25rem 1.4rem',
        background: hasValue ? '#eff6ff' : '#f6f8fc',
        marginBottom: '1.25rem',
        transition: 'border-color 0.2s, background-color 0.2s',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12, marginBottom: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span
            style={{
              width: 38,
              height: 38,
              borderRadius: 10,
              background: hasValue ? '#2563eb' : '#fde8d7',
              color: hasValue ? '#ffffff' : '#ea580c',
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexShrink: 0,
            }}
          >
            <Camera size={19} />
          </span>
          <div>
            <span style={{ fontWeight: 700, color: 'var(--ink)', display: 'block', fontSize: '1.05rem' }}>
              3. Selfie en tiempo real (cámara del dispositivo)
            </span>
            <small style={{ color: 'var(--muted)' }}>Ubica tu rostro dentro del óvalo vertical y con buena iluminación.</small>
          </div>
        </div>

        {!cameraOpen ? (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <Button
              type="button"
              className={hasValue ? 'button-outline' : ''}
              style={{ width: 'auto', padding: '0.55rem 1.1rem', fontSize: '0.95rem' }}
              onClick={startCamera}
            >
              {hasValue ? <RotateCcw size={16} /> : <Camera size={16} />}
              {hasValue ? 'Repetir selfie' : 'Abrir cámara'}
            </Button>
            {hasValue && (
              <Button
                type="button"
                className="button-outline"
                style={{ width: 'auto', padding: '0.55rem 1rem', fontSize: '0.95rem', color: '#b91c1c', borderColor: '#fecaca' }}
                onClick={clearSelfie}
              >
                <X size={16} /> Cancelar
              </Button>
            )}
          </div>
        ) : (
          <div className="responsive-actions">
            <Button
              type="button"
              disabled={!videoReady}
              style={{ width: 'auto', padding: '0.55rem 1.1rem', fontSize: '0.95rem' }}
              onClick={takeSnapshot}
            >
              <Camera size={16} /> {videoReady ? 'Capturar selfie' : 'Iniciando cámara…'}
            </Button>
            <Button
              type="button"
              className="button-outline"
              style={{ width: 'auto', padding: '0.55rem 1rem', fontSize: '0.95rem' }}
              onClick={stopCamera}
            >
              Cancelar
            </Button>
          </div>
        )}
      </div>

      {cameraOpen && (
        <div
          style={{
            position: 'relative',
            width: '100%',
            maxWidth: 420,
            marginTop: 12,
            borderRadius: 14,
            overflow: 'hidden',
            background: '#0f172a',
            border: '2px solid #2563eb',
          }}
        >
          <video
            ref={videoRef}
            autoPlay
            playsInline
            muted
            onLoadedMetadata={() => setVideoReady(true)}
            style={{ width: '100%', display: 'block', transform: 'scaleX(-1)' }}
          />
          <div
            aria-hidden="true"
            style={{
              position: 'absolute',
              top: '50%',
              left: '50%',
              width: 145,
              height: 195,
              maxWidth: '48%',
              maxHeight: '78%',
              transform: 'translate(-50%, -50%)',
              border: '2.5px dashed rgba(255, 255, 255, 0.9)',
              borderRadius: '50% 50% 46% 46%',
              boxShadow: '0 0 0 9999px rgba(15, 23, 42, 0.32)',
              pointerEvents: 'none',
            }}
          />
        </div>
      )}

      {previewUrl && !cameraOpen && value && (
        <div
          style={{
            marginTop: 12,
            padding: '0.75rem 1rem',
            borderRadius: 12,
            background: '#ffffff',
            border: '1px solid #bfdbfe',
            display: 'flex',
            alignItems: 'center',
            gap: 14,
            flexWrap: 'wrap',
          }}
        >
          <img
            src={previewUrl}
            alt="Vista previa de tu selfie capturada"
            style={{ width: 96, height: 96, objectFit: 'cover', borderRadius: 12, border: '2px solid #2563eb' }}
          />
          <div>
            <small style={{ display: 'block', color: '#1d4ed8', fontWeight: 700, fontSize: '0.92rem' }}>
              Selfie capturada en vivo ({formatSize(value.size)})
            </small>
            <small style={{ color: 'var(--muted)' }}>
              Puedes usar «Repetir selfie» para tomar otra o «Cancelar» para descartarla.
            </small>
          </div>
        </div>
      )}

      {cameraError && (
        <p role="alert" style={{ color: '#b91c1c', fontWeight: 600, fontSize: '0.85rem', display: 'flex', alignItems: 'center', gap: 6, margin: '10px 0 0' }}>
          <CircleAlert size={15} /> {cameraError}
        </p>
      )}
      {error && (
        <p role="alert" style={{ color: '#b91c1c', fontWeight: 600, fontSize: '0.85rem', display: 'flex', alignItems: 'center', gap: 6, margin: '10px 0 0' }}>
          <CircleAlert size={15} /> {error}
        </p>
      )}
    </div>
  )
}
