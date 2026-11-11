import type { User } from 'firebase/auth'

export type CertificateStatus = 'PENDIENTE' | 'APROBADO' | 'RECHAZADO' | 'REVOCADO'
export type Certificate = {
  id: string
  titulo: string
  estado: CertificateStatus
  observaciones: string | null
  creado_en: string
}

export const certificateStatuses: Record<CertificateStatus, string> = {
  PENDIENTE: 'Pendiente',
  APROBADO: 'Aprobada',
  RECHAZADO: 'Rechazada',
  REVOCADO: 'Revocada',
}

export const ownerCertificatesPath = '/technicians/me/certifications'
export const adminCertificatesPath = '/admin/certifications'

export async function certificateFileRequest(path: string, user: User, body?: FormData) {
  const url = `${import.meta.env.VITE_API_URL || 'http://localhost:8000'}${path}`
  const request = async (refresh = false) => fetch(url, {
    method: body ? 'POST' : 'GET',
    body,
    cache: 'no-store',
    headers: { Authorization: `Bearer ${await user.getIdToken(refresh)}` },
  })
  let response = await request()
  if (response.status === 401) response = await request(true)
  if (!response.ok) {
    const result = await response.json().catch(() => ({}))
    throw new Error(typeof result.detail === 'string' ? result.detail : 'No pudimos procesar el archivo.')
  }
  return response
}
