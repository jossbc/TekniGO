import type { User } from 'firebase/auth'

export type ReviewOutcome = { estado_identidad: string; revision_completa: boolean }
export type ReviewDocument = { id: string; tipo: string; estado: string; observaciones: string | null; fecha_emision: string | null; fecha_vencimiento: string | null }
export type DossierRole = 'CLIENTE' | 'TECNICO'
export type DossierList = { total: number; expedientes: { usuario_id: string; nombre_completo: string; estado_identidad: string; roles: DossierRole[] }[] }
export type AdminDossier = {
  usuario_id: string; nombre_completo: string; dni: string; telefono: string; telefono_verificado: boolean
  roles: DossierRole[]; estado_identidad: string; estado_verificacion: string | null; version: number | null; solicitud_id: string | null
  documentos: ReviewDocument[]
  resultados_identidad: { estado: string; motivo: string; proceso_responsable: string }[]
  decisiones: { documento_id: string; administrador_id: string; version: number; estado: string; observaciones: string | null; fecha: string }[]
}

export async function openPrivateDocument(user: User, id: string): Promise<Blob> {
  const url = `${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/admin/identity-documents/${id}/content`
  const request = async (refresh = false) => fetch(url, { headers: { Authorization: `Bearer ${await user.getIdToken(refresh)}` }, cache: 'no-store' })
  let response = await request()
  if (response.status === 401) response = await request(true)
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(typeof body.detail === 'string' ? body.detail : 'No pudimos abrir el documento privado.')
  }
  return response.blob()
}
