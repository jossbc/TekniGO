import type { User } from 'firebase/auth'
import { api, resolveApiUrl } from './api'

export type ChatModalidad = 'DIRECTA' | 'ABIERTA'
export type ChatRole = 'CLIENTE' | 'TECNICO'
export type ChatDeliveryState = 'sending' | 'sent' | 'failed'
export type ChatConnectionState =
  | 'idle'
  | 'connecting'
  | 'connected'
  | 'reconnecting'
  | 'offline'
  | 'closed'

export type ChatMessage = {
  id: string
  modalidad: ChatModalidad
  solicitud_id: string
  autor_id: string
  autor_uid: string
  autor_nombre: string | null
  rol_autor: ChatRole
  contenido: string
  client_message_id: string | null
  creado_en: string
}

export type ChatLocalMessage = {
  id: string
  client_message_id: string | null
  autor_uid: string
  autor_nombre: string | null
  rol_autor: ChatRole
  contenido: string
  creado_en: string
  deliveryState: ChatDeliveryState
  error?: string | null
}

export type ChatHistoryResponse = {
  modalidad: ChatModalidad
  solicitud_id: string
  estado: string
  rol_usuario: ChatRole
  habilitado: boolean
  mensajes: ChatMessage[]
}

export type ChatSendResponse = {
  modalidad: ChatModalidad
  solicitud_id: string
  estado: string
  duplicado: boolean
  mensaje: ChatMessage
}

export const CHAT_MAX_MESSAGE_LENGTH = 2000
export const CHAT_HEARTBEAT_INTERVAL_MS = 25_000
export const WS_CLOSE_UNAUTHORIZED = 4001
export const WS_CLOSE_FORBIDDEN = 4003
export const WS_CLOSE_NOT_FOUND = 4004
export const WS_CLOSE_STATE_DISABLED = 4009

export function isPermanentChatCloseCode(code?: number | null): boolean {
  return (
    code === WS_CLOSE_STATE_DISABLED ||
    code === WS_CLOSE_FORBIDDEN ||
    code === WS_CLOSE_NOT_FOUND
  )
}

export function normalizeRequestState(estado?: string | null): string {
  return (estado || '').trim().toUpperCase().replace(/_/g, ' ').replace(/\s+/g, ' ')
}

export function isChatEnabledForState(estado?: string | null): boolean {
  const key = normalizeRequestState(estado)
  return key === 'ACEPTADA' || key === 'EN PROCESO'
}

export function isChatClosedState(estado?: string | null): boolean {
  const key = normalizeRequestState(estado)
  return key === 'COMPLETADA' || key === 'CANCELADA'
}

export function validateChatDraft(raw: string): string | null {
  const trimmed = raw.trim()
  if (!trimmed) return 'Escribe un mensaje antes de enviar.'
  if (trimmed.length > CHAT_MAX_MESSAGE_LENGTH) {
    return `El mensaje no puede superar los ${CHAT_MAX_MESSAGE_LENGTH} caracteres.`
  }
  return null
}

export function createClientMessageId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `msg-${crypto.randomUUID()}`
  }
  return `msg-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

export function toLocalMessage(msg: ChatMessage): ChatLocalMessage {
  return {
    id: msg.id,
    client_message_id: msg.client_message_id,
    autor_uid: msg.autor_uid,
    autor_nombre: msg.autor_nombre,
    rol_autor: msg.rol_autor,
    contenido: msg.contenido,
    creado_en: msg.creado_en,
    deliveryState: 'sent',
    error: null,
  }
}

export function createPendingLocalMessage(params: {
  clientMessageId: string
  autorUid: string
  autorNombre: string | null
  rolAutor: ChatRole
  contenido: string
  createdAt?: string
}): ChatLocalMessage {
  return {
    id: params.clientMessageId,
    client_message_id: params.clientMessageId,
    autor_uid: params.autorUid,
    autor_nombre: params.autorNombre,
    rol_autor: params.rolAutor,
    contenido: params.contenido.trim(),
    creado_en: params.createdAt || new Date().toISOString(),
    deliveryState: 'sending',
    error: null,
  }
}

function compareMessages(a: ChatLocalMessage, b: ChatLocalMessage): number {
  const timeA = Date.parse(a.creado_en) || 0
  const timeB = Date.parse(b.creado_en) || 0
  if (timeA !== timeB) return timeA - timeB
  if (a.deliveryState === 'sent' && b.deliveryState !== 'sent') return -1
  if (a.deliveryState !== 'sent' && b.deliveryState === 'sent') return 1
  return a.id.localeCompare(b.id)
}

/**
 * Fusiona mensajes persistidos en orden cronológico sin duplicar por `id` ni por `client_message_id` (CA-03 / CA-04).
 * Un mensaje local en estado `sending` o `failed` pasa a `sent` únicamente cuando el servidor confirma su persistencia (CA-06).
 */
export function mergeChatMessages(
  current: ChatLocalMessage[],
  incoming: ChatMessage[],
): ChatLocalMessage[] {
  const next = [...current]

  for (const raw of incoming) {
    const persisted = toLocalMessage(raw)
    const matchIndex = next.findIndex(
      (item) =>
        item.id === persisted.id ||
        (persisted.client_message_id !== null &&
          item.client_message_id !== null &&
          item.client_message_id === persisted.client_message_id),
    )
    if (matchIndex >= 0) {
      next[matchIndex] = persisted
    } else {
      next.push(persisted)
    }
  }

  return next.sort(compareMessages)
}

export function markMessageFailed(
  current: ChatLocalMessage[],
  clientMessageId: string,
  error: string,
): ChatLocalMessage[] {
  return current.map((item) =>
    item.client_message_id === clientMessageId && item.deliveryState !== 'sent'
      ? { ...item, deliveryState: 'failed', error }
      : item,
  )
}

export function markMessageSending(
  current: ChatLocalMessage[],
  clientMessageId: string,
): ChatLocalMessage[] {
  return current.map((item) =>
    item.client_message_id === clientMessageId && item.deliveryState !== 'sent'
      ? { ...item, deliveryState: 'sending', error: null }
      : item,
  )
}

export function getLatestPersistedTimestamp(messages: ChatLocalMessage[]): string | null {
  let latest: string | null = null
  let latestMs = -1
  for (const item of messages) {
    if (item.deliveryState !== 'sent') continue
    const ms = Date.parse(item.creado_en)
    if (!Number.isNaN(ms) && ms >= latestMs) {
      latestMs = ms
      latest = item.creado_en
    }
  }
  return latest
}

export function getReconnectDelayMs(attempt: number): number {
  const safeAttempt = Math.max(0, Math.floor(attempt))
  return Math.min(15_000, 1_000 * 2 ** safeAttempt)
}

export function resolveChatWsUrl(
  modalidad: ChatModalidad,
  solicitudId: string,
  token: string,
  after?: string | null,
): string {
  const params = new URLSearchParams({ token })
  if (after) params.set('after', after)
  const path = `/chat/solicitudes/${encodeURIComponent(modalidad)}/${encodeURIComponent(solicitudId)}/ws?${params.toString()}`
  const apiUrl = resolveApiUrl(path)
  if (/^https?:\/\//i.test(apiUrl)) {
    return apiUrl.replace(/^http/i, 'ws')
  }
  if (typeof window !== 'undefined' && window.location) {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${proto}//${window.location.host}${apiUrl}`
  }
  return `ws://127.0.0.1:8000${apiUrl}`
}

export function fetchChatMessages(
  user: User,
  modalidad: ChatModalidad,
  solicitudId: string,
  after?: string | null,
): Promise<ChatHistoryResponse> {
  const query = after ? `?after=${encodeURIComponent(after)}` : ''
  return api<ChatHistoryResponse>(
    `/chat/solicitudes/${encodeURIComponent(modalidad)}/${encodeURIComponent(solicitudId)}/mensajes${query}`,
    user,
    undefined,
    'GET',
  )
}

export function sendChatMessage(
  user: User,
  modalidad: ChatModalidad,
  solicitudId: string,
  contenido: string,
  clientMessageId: string,
): Promise<ChatSendResponse> {
  return api<ChatSendResponse>(
    `/chat/solicitudes/${encodeURIComponent(modalidad)}/${encodeURIComponent(solicitudId)}/mensajes`,
    user,
    {
      contenido,
      client_message_id: clientMessageId,
    },
    'POST',
  )
}
