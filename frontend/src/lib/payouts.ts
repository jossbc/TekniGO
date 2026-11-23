import type { User } from 'firebase/auth'
import { api } from './api'
import { formatLempiras } from './settlements'

export type PayoutStatus = 'PENDIENTE' | 'PAGADA' | 'RECHAZADA'
export type BankAccountType = 'AHORRO' | 'CHEQUES'

export type MaskedBankAccount = {
  id: string
  banco: string
  tipo_cuenta: BankAccountType
  titular_nombre: string
  ultimos_cuatro: string
  cuenta_enmascarada: string
  actualizado_en: string | null
}

export type PayoutOrder = {
  id: string
  importe: string
  importe_reservado: string
  minimo_aplicado: string
  estado: PayoutStatus
  banco: string
  tipo_cuenta: BankAccountType
  cuenta_enmascarada: string
  referencia_transferencia: string | null
  motivo_rechazo: string | null
  observacion: string | null
  creado_en: string | null
  revisado_en: string | null
}

export type PayoutSummary = {
  moneda: 'HNL'
  saldo_contable: string
  saldo_reservado: string
  saldo_libre: string
  deuda_vigente: string
  minimo_retiro: string
  cuenta_bancaria: MaskedBankAccount | null
  ordenes: PayoutOrder[]
}

export const payoutStatuses: Record<PayoutStatus, string> = {
  PENDIENTE: 'Pendiente de transferencia',
  PAGADA: 'Pagada',
  RECHAZADA: 'Rechazada',
}

export const bankAccountTypes: Record<BankAccountType, string> = {
  AHORRO: 'Ahorro',
  CHEQUES: 'Cheques',
}

export const myPayoutsPath = '/users/me/payouts'
export const myBankAccountPath = '/users/me/bank-account'

export function validateBankAccountInput(input: {
  banco: string
  tipo_cuenta: string
  titular_nombre: string
  numero_cuenta: string
}): string | null {
  const banco = input.banco.trim()
  if (banco.length < 2 || banco.length > 80) {
    return 'Indica el nombre del banco (entre 2 y 80 caracteres).'
  }
  if (input.tipo_cuenta !== 'AHORRO' && input.tipo_cuenta !== 'CHEQUES') {
    return 'Selecciona un tipo de cuenta válido (Ahorro o Cheques).'
  }
  const titular = input.titular_nombre.trim()
  if (titular.length < 3 || titular.length > 150) {
    return 'Indica el nombre completo del titular de la cuenta.'
  }
  const digits = input.numero_cuenta.replace(/[\s-]+/g, '')
  if (!/^\d{8,24}$/.test(digits)) {
    return 'El número de cuenta debe tener entre 8 y 24 dígitos.'
  }
  return null
}

function toCents(value: string): number {
  const [intPart, decPart = ''] = value.trim().split('.')
  return Number(intPart) * 100 + Number(decPart.padEnd(2, '0').slice(0, 2))
}

export function validatePayoutRequestInput(
  importe: string,
  summary: Pick<PayoutSummary, 'cuenta_bancaria' | 'deuda_vigente' | 'minimo_retiro' | 'saldo_libre'>,
): string | null {
  if (!summary.cuenta_bancaria) {
    return 'Registra tu cuenta bancaria personal antes de solicitar un desembolso.'
  }
  if (toCents(summary.deuda_vigente) > 0) {
    return `Tienes comisiones pendientes (${formatLempiras(summary.deuda_vigente)}). Liquida tu deuda a L 0.00 antes de retirar.`
  }
  const clean = importe.trim()
  if (!/^\d+(\.\d{1,2})?$/.test(clean) || toCents(clean) <= 0) {
    return 'Escribe un importe mayor que cero con hasta 2 decimales.'
  }
  const cents = toCents(clean)
  if (cents < toCents(summary.minimo_retiro)) {
    return `El importe mínimo de retiro es ${formatLempiras(summary.minimo_retiro)}.`
  }
  if (cents > toCents(summary.saldo_libre)) {
    return `El importe supera tu saldo libre disponible (${formatLempiras(summary.saldo_libre)}).`
  }
  return null
}

export function saveMyBankAccount(
  user: User,
  payload: { banco: string; tipo_cuenta: BankAccountType; titular_nombre: string; numero_cuenta: string },
) {
  return api<{ cuenta_bancaria: MaskedBankAccount }>(myBankAccountPath, user, payload, 'PUT')
}

export function requestMyPayout(user: User, importe: string) {
  return api<{ orden: PayoutOrder }>(myPayoutsPath, user, { importe: importe.trim() }, 'POST')
}

export const adminPayoutsPath = '/admin/payouts'

export type AdminPayoutOrder = PayoutOrder & {
  tecnico_id: string
  tecnico_nombre: string | null
  tecnico_telefono: string | null
  revisado_por: string | null
  revisado_por_nombre: string | null
}

export type AdminBankDetails = {
  orden_id: string
  banco: string
  tipo_cuenta: BankAccountType
  titular_nombre: string
  numero_cuenta: string
  cuenta_enmascarada: string
}

export type PayoutDecisionAction = 'CONFIRMAR' | 'RECHAZAR'

export type PayoutDecisionResponse = {
  orden: AdminPayoutOrder
  saldos: {
    moneda: 'HNL'
    saldo_contable: string
    saldo_reservado: string
    saldo_libre: string
  }
}

export function validatePayoutDecisionInput(input: {
  decision: PayoutDecisionAction
  referencia_transferencia: string
  motivo_rechazo: string
}): string | null {
  if (input.decision === 'RECHAZAR') {
    if (!input.motivo_rechazo.trim()) {
      return 'Debes indicar el motivo para rechazar el desembolso.'
    }
    return null
  }
  const ref = input.referencia_transferencia.replace(/\s+/g, '')
  if (ref.length < 4 || ref.length > 64 || !/^[A-Za-z0-9][A-Za-z0-9\-_/.]*$/.test(ref)) {
    return 'La referencia bancaria debe tener entre 4 y 64 caracteres (letras, números y - _ / .).'
  }
  return null
}

export function fetchAdminPayoutBankDetails(user: User, orderId: string) {
  return api<AdminBankDetails>(`${adminPayoutsPath}/${orderId}/bank-details`, user)
}

export function decideAdminPayout(
  user: User,
  orderId: string,
  payload: {
    decision: PayoutDecisionAction
    referencia_transferencia?: string
    motivo_rechazo?: string
    observacion?: string
  },
) {
  return api<PayoutDecisionResponse>(
    `${adminPayoutsPath}/${orderId}/decision`,
    user,
    {
      decision: payload.decision,
      referencia_transferencia: payload.decision === 'CONFIRMAR' ? payload.referencia_transferencia?.trim() : null,
      motivo_rechazo: payload.decision === 'RECHAZAR' ? payload.motivo_rechazo?.trim() : null,
      observacion: payload.observacion?.trim() || null,
    },
    'POST',
  )
}
