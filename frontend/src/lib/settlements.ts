import type { User } from 'firebase/auth'
import { api } from './api'

export type SettlementStatus = 'PENDIENTE' | 'APROBADA' | 'RECHAZADA' | 'EN_CONCILIACION'

export type Settlement = {
  id: string
  importe_declarado: string
  referencia: string
  estado: SettlementStatus
  importe_verificado: string | null
  importe_aplicado: string
  importe_excedente: string
  observacion: string | null
  creado_en: string | null
  revisado_en: string | null
}

export type AdminSettlement = Settlement & {
  tecnico_id: string
  tecnico_nombre: string | null
  tecnico_telefono: string | null
  content_type: string
  tamano_bytes: number
  revisado_por: string | null
  revisado_por_nombre: string | null
}

export type SettlementSummary = {
  moneda: 'HNL'
  deuda_vigente: string
  importe_en_revision: string
  restriccion_financiera: boolean
  servicios_efectivo_sin_liquidar: number
  suspendido: boolean
  estado_verificacion: string | null
  habilitado_operativamente: boolean
  formatos_permitidos: string[]
  formatos_texto: string
  tamano_maximo_bytes: number
  liquidaciones: Settlement[]
}

export const settlementStatuses: Record<SettlementStatus, string> = {
  PENDIENTE: 'Pendiente de revisión',
  APROBADA: 'Aprobada',
  RECHAZADA: 'Rechazada',
  EN_CONCILIACION: 'En conciliación',
}

export const mySettlementsPath = '/users/me/settlements'
export const adminSettlementsPath = '/admin/settlements'

export function formatLempiras(value: string | number) {
  return `L ${Number(value).toLocaleString('es-HN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

/** Validación previa en el navegador; el servidor valida de nuevo el contenido real. */
export function validateSettlementInput(
  input: { importe: string; referencia: string; file: { type: string; size: number } | null },
  rules: { formatos_permitidos: string[]; tamano_maximo_bytes: number; formatos_texto: string },
): string | null {
  if (!/^\d+(\.\d{1,2})?$/.test(input.importe.trim()) || Number(input.importe) <= 0) {
    return 'Escribe un importe mayor que cero con hasta 2 decimales.'
  }
  const reference = input.referencia.replace(/\s+/g, '')
  if (reference.length < 4 || reference.length > 64 || !/^[A-Za-z0-9][A-Za-z0-9\-_/.]*$/.test(reference)) {
    return 'La referencia debe tener entre 4 y 64 caracteres (letras, números y - _ / .).'
  }
  if (!input.file) return 'Adjunta la fotografía o captura del comprobante.'
  if (!rules.formatos_permitidos.includes(input.file.type)) return `El comprobante debe ser ${rules.formatos_texto}.`
  if (input.file.size <= 0) return 'El archivo está vacío.'
  if (input.file.size > rules.tamano_maximo_bytes) {
    return `El comprobante supera el máximo de ${Math.round(rules.tamano_maximo_bytes / 1024 / 1024)} MB.`
  }
  return null
}

export function submitSettlement(user: User, importe: string, referencia: string, file: File) {
  const form = new FormData()
  form.append('importe', importe.trim())
  form.append('referencia', referencia.trim())
  form.append('comprobante', file)
  return api<Settlement>(mySettlementsPath, user, form)
}

export type SettlementDecisionAction = 'APROBAR' | 'RECHAZAR'

export type SettlementDecisionResponse = {
  liquidacion: AdminSettlement
  efecto_financiero: {
    accion: 'APROBACION_TOTAL' | 'APROBACION_PARCIAL' | 'SOBREPAGO_CONCILIACION' | 'RECHAZO'
    moneda: 'HNL'
    deuda_anterior: string
    deuda_posterior: string
    importe_aplicado: string
    importe_excedente: string
    servicios_efectivo_sin_liquidar: number
    restriccion_financiera: boolean
    suspendido: boolean
    estado_verificacion: string | null
  }
}

export function validateDecisionInput(input: {
  decision: SettlementDecisionAction
  importe_verificado: string
  observacion: string
}): string | null {
  if (input.decision === 'RECHAZAR') {
    if (!input.observacion.trim()) {
      return 'Debes escribir una observación para rechazar la liquidación.'
    }
    return null
  }
  const amount = input.importe_verificado.trim()
  if (!/^\d+(\.\d{1,2})?$/.test(amount) || Number(amount) <= 0) {
    return 'Escribe un importe verificado mayor que cero con hasta 2 decimales.'
  }
  return null
}

export function decideSettlement(
  user: User,
  settlementId: string,
  payload: { decision: SettlementDecisionAction; importe_verificado?: string; observacion?: string },
) {
  return api<SettlementDecisionResponse>(
    `${adminSettlementsPath}/${settlementId}/decision`,
    user,
    {
      decision: payload.decision,
      importe_verificado: payload.decision === 'APROBAR' ? payload.importe_verificado?.trim() : null,
      observacion: payload.observacion?.trim() || null,
    },
    'POST',
  )
}
