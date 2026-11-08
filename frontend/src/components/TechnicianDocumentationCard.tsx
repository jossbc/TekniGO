import { useEffect, useState } from 'react'
import type { User } from 'firebase/auth'
import { Link } from 'react-router-dom'
import { ArrowUpRight, CheckCircle2, Clock3, ShieldCheck } from 'lucide-react'
import { api, errorMessage } from '../lib/api'

type DocumentationStatus = { estado_verificacion: string; requisitos_pendientes: { nombre: string; motivo?: string | null }[] }

export default function TechnicianDocumentationCard({ user }: { user: User }) {
  const [data, setData] = useState<DocumentationStatus | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    let pending = false
    let timer: ReturnType<typeof setTimeout>
    async function refresh() {
      clearTimeout(timer)
      if (!active || pending || document.visibilityState === 'hidden' || navigator.onLine === false) return
      pending = true
      try {
        const result = await api<DocumentationStatus>('/technicians/me/activation-status', user)
        if (active) { setData(result); setError('') }
      } catch (err) { if (active) setError(errorMessage(err)) }
      finally {
        pending = false
        if (active) timer = setTimeout(refresh, 15000)
      }
    }
    void refresh()
    window.addEventListener('focus', refresh)
    window.addEventListener('online', refresh)
    document.addEventListener('visibilitychange', refresh)
    return () => {
      active = false
      clearTimeout(timer)
      window.removeEventListener('focus', refresh)
      window.removeEventListener('online', refresh)
      document.removeEventListener('visibilitychange', refresh)
    }
  }, [user])
  const verified = data?.estado_verificacion === 'VERIFICADO'
  const labels: Record<string, string> = { VERIFICADO: 'Verificado', EN_REVISION: 'En revisión', RECHAZADO: 'Requiere correcciones', RESTRINGIDO: 'Requiere actualización', PENDIENTE: 'Documentos pendientes', NO_VERIFICADO: 'Documentos pendientes' }
  return <section className="account-card">
    <span className="step-icon"><ShieldCheck size={22} /></span>
    <h2>Documentación del técnico</h2>
    {data ? <>
      <span className={`status-pill${verified ? ' status-pill-verified' : ''}`} role="status">
        {verified ? <CheckCircle2 size={15} /> : <Clock3 size={15} />} {labels[data.estado_verificacion] || 'Pendiente de verificación'}
      </span>
      <p>{verified ? 'Tu identidad, antecedentes policiales y penales están aprobados y tu teléfono está verificado.' : data.estado_verificacion === 'EN_REVISION' ? 'Tus documentos están pendientes de revisión administrativa.' : 'Completa o corrige los requisitos pendientes de tu perfil técnico.'}</p>
      {!verified && data.requisitos_pendientes?.length > 0 && <ul>{data.requisitos_pendientes.map(item => <li key={item.nombre}>{item.motivo || item.nombre}</li>)}</ul>}
    </> : !error && <p role="status">Consultando documentación.</p>}
    {error && <p className="alert" role="alert">No pudimos actualizar el estado: {error}</p>}
    <Link className={verified ? 'link' : 'button'} style={{ width: 'auto' }} to="/verificacion">{verified ? 'Ver documentos aprobados' : 'Revisar documentos y antecedentes'} <ArrowUpRight size={15} /></Link>
  </section>
}
