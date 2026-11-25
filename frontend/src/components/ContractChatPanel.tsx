import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import type { User } from 'firebase/auth'
import {
  AlertCircle,
  Check,
  Clock,
  Lock,
  MessageSquare,
  RefreshCw,
  Send,
  Wifi,
  WifiOff,
} from 'lucide-react'
import { ApiError } from '../lib/api'
import {
  CHAT_HEARTBEAT_INTERVAL_MS,
  CHAT_MAX_MESSAGE_LENGTH,
  createClientMessageId,
  createPendingLocalMessage,
  fetchChatMessages,
  getLatestPersistedTimestamp,
  getReconnectDelayMs,
  isChatClosedState,
  isChatEnabledForState,
  isPermanentChatCloseCode,
  markMessageFailed,
  markMessageSending,
  mergeChatMessages,
  resolveChatWsUrl,
  sendChatMessage,
  validateChatDraft,
} from '../lib/contractChat'
import type {
  ChatConnectionState,
  ChatLocalMessage,
  ChatMessage,
  ChatModalidad,
  ChatRole,
} from '../lib/contractChat'

type Props = {
  user: User
  modalidad: ChatModalidad
  solicitudId: string
  estadoSolicitud: string
  currentRole: ChatRole
  counterpartName?: string | null
  onChatClosed?: (estado?: string) => void
}

function formatMessageTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleTimeString('es-HN', {
    hour: '2-digit',
    minute: '2-digit',
  })
}

const CONNECTION_BADGES: Record<
  ChatConnectionState,
  { label: string; bg: string; color: string; border: string }
> = {
  idle: {
    label: 'Preparando chat...',
    bg: '#f1f5f9',
    color: '#475569',
    border: '#cbd5e1',
  },
  connecting: {
    label: 'Conectando en vivo...',
    bg: '#eff6ff',
    color: '#1d4ed8',
    border: '#bfdbfe',
  },
  connected: {
    label: 'Conectado en vivo',
    bg: '#f0fdf4',
    color: '#166534',
    border: '#bbf7d0',
  },
  reconnecting: {
    label: 'Reconectando...',
    bg: '#fefce8',
    color: '#a16207',
    border: '#fde047',
  },
  offline: {
    label: 'Sin conexión',
    bg: '#fef2f2',
    color: '#b91c1c',
    border: '#fecaca',
  },
  closed: {
    label: 'Chat deshabilitado',
    bg: '#f1f5f9',
    color: '#475569',
    border: '#cbd5e1',
  },
}

export default function ContractChatPanel({
  user,
  modalidad,
  solicitudId,
  estadoSolicitud,
  currentRole,
  counterpartName,
  onChatClosed,
}: Props) {
  const enabledByProp = isChatEnabledForState(estadoSolicitud)
  const [messages, setMessages] = useState<ChatLocalMessage[]>([])
  const [draft, setDraft] = useState('')
  const [draftError, setDraftError] = useState<string | null>(null)
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [connectionState, setConnectionState] = useState<ChatConnectionState>(
    enabledByProp ? 'connecting' : 'closed',
  )
  const [closedReason, setClosedReason] = useState<string | null>(
    enabledByProp
      ? null
      : isChatClosedState(estadoSolicitud)
        ? 'El chat operativo se deshabilitó porque la solicitud fue completada o cancelada.'
        : 'El chat operativo se habilita únicamente cuando la solicitud está Aceptada o En proceso.',
  )
  const [syncError, setSyncError] = useState<string | null>(null)

  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const heartbeatTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const reconnectAttemptRef = useRef(0)
  const messagesRef = useRef<ChatLocalMessage[]>([])
  const unmountedRef = useRef(false)
  const closedPermanentlyRef = useRef(!enabledByProp)
  const listEndRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    messagesRef.current = messages
  }, [messages])

  useEffect(() => {
    listEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [messages.length])

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimerRef.current !== null) {
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }
  }, [])

  const clearHeartbeatTimer = useCallback(() => {
    if (heartbeatTimerRef.current !== null) {
      clearInterval(heartbeatTimerRef.current)
      heartbeatTimerRef.current = null
    }
  }, [])

  const closeActiveSocket = useCallback(() => {
    clearReconnectTimer()
    clearHeartbeatTimer()
    const ws = wsRef.current
    wsRef.current = null
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      try {
        ws.close(1000, 'client_cleanup')
      } catch {
        // Ignorar errores al cerrar socket ya cerrado
      }
    }
  }, [clearHeartbeatTimer, clearReconnectTimer])

  const disableChat = useCallback(
    (reason: string, estado?: string) => {
      closedPermanentlyRef.current = true
      closeActiveSocket()
      setConnectionState('closed')
      setClosedReason(reason)
      setMessages([])
      setDraft('')
      setDraftError(null)
      setSyncError(null)
      onChatClosed?.(estado)
    },
    [closeActiveSocket, onChatClosed],
  )

  const syncPersistedHistory = useCallback(
    async (incremental: boolean) => {
      if (closedPermanentlyRef.current) return false
      if (!incremental) setLoadingHistory(true)
      try {
        const after = incremental ? getLatestPersistedTimestamp(messagesRef.current) : null
        const data = await fetchChatMessages(user, modalidad, solicitudId, after)
        if (unmountedRef.current || closedPermanentlyRef.current) return false
        setMessages((prev) => mergeChatMessages(prev, data.mensajes))
        setSyncError(null)
        return true
      } catch (err) {
        if (unmountedRef.current) return false
        if (err instanceof ApiError && (err.status === 409 || err.status === 403)) {
          disableChat(err.message)
          return false
        }
        setSyncError(
          err instanceof Error
            ? err.message
            : 'No se pudo cargar el historial del chat. Intenta reconectar.',
        )
        return false
      } finally {
        if (!incremental && !unmountedRef.current) {
          setLoadingHistory(false)
        }
      }
    },
    [disableChat, modalidad, solicitudId, user],
  )

  const connectLiveChannel = useCallback(
    async (isReconnect: boolean) => {
      if (unmountedRef.current || closedPermanentlyRef.current) return
      closeActiveSocket()

      if (typeof navigator !== 'undefined' && navigator.onLine === false) {
        setConnectionState('offline')
        return
      }

      setConnectionState(isReconnect ? 'reconnecting' : 'connecting')

      let token: string
      try {
        token = await user.getIdToken()
      } catch {
        if (unmountedRef.current || closedPermanentlyRef.current) return
        setConnectionState('offline')
        setSyncError('No se pudo validar tu sesión para conectar el chat en vivo.')
        return
      }

      if (unmountedRef.current || closedPermanentlyRef.current) return

      const after = isReconnect ? getLatestPersistedTimestamp(messagesRef.current) : null
      const url = resolveChatWsUrl(modalidad, solicitudId, token, after)

      let ws: WebSocket
      try {
        ws = new WebSocket(url)
      } catch {
        setConnectionState('offline')
        return
      }
      wsRef.current = ws

      ws.onopen = () => {
        if (unmountedRef.current || closedPermanentlyRef.current) {
          ws.close(1000)
          return
        }
        reconnectAttemptRef.current = 0
        setConnectionState('connected')
        setSyncError(null)
        clearHeartbeatTimer()
        heartbeatTimerRef.current = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN && !closedPermanentlyRef.current) {
            try {
              ws.send(JSON.stringify({ type: 'ping' }))
            } catch {
              // El manejador onclose gestionará la reconexión si el socket cayó
            }
          }
        }, CHAT_HEARTBEAT_INTERVAL_MS)
      }

      ws.onmessage = (event) => {
        if (unmountedRef.current || closedPermanentlyRef.current) return
        let data: Record<string, unknown>
        try {
          data = JSON.parse(String(event.data))
        } catch {
          return
        }
        const type = String(data.type || '')
        if (type === 'connected') {
          reconnectAttemptRef.current = 0
          setConnectionState('connected')
          setSyncError(null)
          return
        }
        if (type === 'sync' && Array.isArray(data.mensajes)) {
          setMessages((prev) => mergeChatMessages(prev, data.mensajes as ChatMessage[]))
          return
        }
        if (type === 'message' && data.mensaje && typeof data.mensaje === 'object') {
          setMessages((prev) => mergeChatMessages(prev, [data.mensaje as ChatMessage]))
          return
        }
        if (type === 'error') {
          const cid = typeof data.client_message_id === 'string' ? data.client_message_id : null
          const detail = typeof data.detail === 'string' ? data.detail : 'No se pudo entregar el mensaje.'
          if (cid) {
            setMessages((prev) => markMessageFailed(prev, cid, detail))
          } else {
            setSyncError(detail)
          }
          return
        }
        if (type === 'chat_closed') {
          const reason =
            typeof data.motivo === 'string'
              ? data.motivo
              : 'El chat operativo se deshabilitó porque la solicitud cambió de estado.'
          const estado = typeof data.estado === 'string' ? data.estado : undefined
          disableChat(reason, estado)
        }
      }

      ws.onerror = () => {
        if (unmountedRef.current || closedPermanentlyRef.current) return
        setSyncError('Conexión en vivo interrumpida. Intentando reconectar...')
      }

      ws.onclose = (event) => {
        clearHeartbeatTimer()
        if (wsRef.current === ws) {
          wsRef.current = null
        }
        if (unmountedRef.current || closedPermanentlyRef.current) return

        if (isPermanentChatCloseCode(event.code)) {
          disableChat(
            event.reason ||
              'El chat operativo fue deshabilitado porque la solicitud fue completada o cancelada.',
          )
          return
        }

        const attempt = reconnectAttemptRef.current
        reconnectAttemptRef.current += 1
        const delay = getReconnectDelayMs(attempt)
        setConnectionState(
          typeof navigator !== 'undefined' && navigator.onLine === false
            ? 'offline'
            : 'reconnecting',
        )
        clearReconnectTimer()
        reconnectTimerRef.current = setTimeout(() => {
          void (async () => {
            const ok = await syncPersistedHistory(true)
            if (ok) {
              await connectLiveChannel(true)
            }
          })()
        }, delay)
      }
    },
    [
      clearHeartbeatTimer,
      clearReconnectTimer,
      closeActiveSocket,
      disableChat,
      modalidad,
      solicitudId,
      syncPersistedHistory,
      user,
    ],
  )

  // Reaccionar inmediatamente si el estado de la solicitud cambia externamente a Completada/Cancelada (CA-05)
  useEffect(() => {
    if (!enabledByProp) {
      disableChat(
        isChatClosedState(estadoSolicitud)
          ? 'El chat operativo se deshabilitó porque la solicitud fue completada o cancelada.'
          : 'El chat operativo se habilita únicamente cuando la solicitud está Aceptada o En proceso.',
        estadoSolicitud,
      )
      return
    }

    closedPermanentlyRef.current = false
    setClosedReason(null)
    unmountedRef.current = false

    let active = true
    void (async () => {
      const ok = await syncPersistedHistory(false)
      if (active && ok && !closedPermanentlyRef.current) {
        await connectLiveChannel(false)
      }
    })()

    const handleOnline = () => {
      if (closedPermanentlyRef.current) return
      reconnectAttemptRef.current = 0
      void (async () => {
        const ok = await syncPersistedHistory(true)
        if (ok) await connectLiveChannel(true)
      })()
    }
    const handleOffline = () => {
      if (closedPermanentlyRef.current) return
      closeActiveSocket()
      setConnectionState('offline')
    }

    window.addEventListener('online', handleOnline)
    window.addEventListener('offline', handleOffline)

    return () => {
      active = false
      unmountedRef.current = true
      window.removeEventListener('online', handleOnline)
      window.removeEventListener('offline', handleOffline)
      closeActiveSocket()
    }
  }, [
    closeActiveSocket,
    connectLiveChannel,
    disableChat,
    enabledByProp,
    estadoSolicitud,
    modalidad,
    solicitudId,
    syncPersistedHistory,
  ])

  const transmitMessage = useCallback(
    async (contenido: string, clientMessageId: string) => {
      if (closedPermanentlyRef.current) return
      try {
        const res = await sendChatMessage(
          user,
          modalidad,
          solicitudId,
          contenido,
          clientMessageId,
        )
        if (unmountedRef.current || closedPermanentlyRef.current) return
        setMessages((prev) => mergeChatMessages(prev, [res.mensaje]))
        setSyncError(null)
      } catch (err) {
        if (unmountedRef.current) return
        if (err instanceof ApiError && (err.status === 409 || err.status === 403)) {
          disableChat(err.message)
          return
        }
        const reason =
          err instanceof Error
            ? err.message
            : 'Error de conexión. Puedes reintentar sin duplicar el envío.'
        setMessages((prev) => markMessageFailed(prev, clientMessageId, reason))
      }
    },
    [disableChat, modalidad, solicitudId, user],
  )

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (closedPermanentlyRef.current) return

    const validation = validateChatDraft(draft)
    if (validation) {
      setDraftError(validation)
      return
    }

    const content = draft.trim()
    const clientMessageId = createClientMessageId()
    setDraft('')
    setDraftError(null)

    const pending = createPendingLocalMessage({
      clientMessageId,
      autorUid: user.uid,
      autorNombre: user.displayName || 'Tú',
      rolAutor: currentRole,
      contenido: content,
    })
    setMessages((prev) => [...prev, pending])

    await transmitMessage(content, clientMessageId)
  }

  const handleRetry = async (message: ChatLocalMessage) => {
    if (closedPermanentlyRef.current || !message.client_message_id) return
    setMessages((prev) => markMessageSending(prev, message.client_message_id!))
    await transmitMessage(message.contenido, message.client_message_id)
  }

  const handleManualReconnect = () => {
    if (closedPermanentlyRef.current) return
    reconnectAttemptRef.current = 0
    void (async () => {
      const ok = await syncPersistedHistory(true)
      if (ok) await connectLiveChannel(true)
    })()
  }

  const badge = CONNECTION_BADGES[connectionState]
  const isClosed = connectionState === 'closed' || closedPermanentlyRef.current

  return (
    <section
      aria-label="Chat de contratación"
      style={{
        border: '1px solid #e2e8f0',
        borderRadius: '14px',
        background: '#ffffff',
        overflow: 'hidden',
        marginTop: '14px',
      }}
    >
      {/* Cabecera */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          gap: '10px',
          flexWrap: 'wrap',
          padding: '12px 14px',
          background: '#f8fafc',
          borderBottom: '1px solid #e2e8f0',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <MessageSquare size={17} color="#0284c7" />
          <div>
            <div style={{ fontWeight: 700, fontSize: '0.92rem', color: '#0f172a' }}>
              Chat privado de contratación
            </div>
            {counterpartName && (
              <div style={{ fontSize: '0.78rem', color: '#64748b' }}>
                Conversación con {counterpartName}
              </div>
            )}
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '5px',
              padding: '3px 10px',
              borderRadius: '999px',
              fontSize: '0.75rem',
              fontWeight: 600,
              background: badge.bg,
              color: badge.color,
              border: `1px solid ${badge.border}`,
            }}
          >
            {connectionState === 'connected' ? (
              <Wifi size={12} />
            ) : connectionState === 'closed' ? (
              <Lock size={12} />
            ) : (
              <WifiOff size={12} />
            )}
            {badge.label}
          </span>

          {(connectionState === 'reconnecting' || connectionState === 'offline') && (
            <button
              type="button"
              onClick={handleManualReconnect}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '4px',
                padding: '4px 10px',
                borderRadius: '8px',
                border: '1px solid #cbd5e1',
                background: '#fff',
                color: '#0f172a',
                fontSize: '0.75rem',
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              <RefreshCw size={12} />
              Reconectar
            </button>
          )}
        </div>
      </div>

      {/* Estado deshabilitado / cerrado (CA-05) */}
      {isClosed ? (
        <div
          style={{
            padding: '18px 16px',
            display: 'flex',
            alignItems: 'center',
            gap: '10px',
            color: '#475569',
            fontSize: '0.86rem',
            background: '#f8fafc',
          }}
        >
          <Lock size={16} color="#64748b" />
          <span>
            {closedReason ||
              'El chat operativo está deshabilitado para esta solicitud.'}
          </span>
        </div>
      ) : (
        <>
          {syncError && (
            <div
              role="status"
              style={{
                padding: '8px 14px',
                background: '#fefce8',
                borderBottom: '1px solid #fde047',
                color: '#854d0e',
                fontSize: '0.8rem',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: '8px',
              }}
            >
              <span>{syncError}</span>
              <button
                type="button"
                onClick={handleManualReconnect}
                style={{
                  border: 'none',
                  background: 'transparent',
                  color: '#854d0e',
                  fontWeight: 700,
                  cursor: 'pointer',
                  textDecoration: 'underline',
                  fontSize: '0.78rem',
                }}
              >
                Reintentar conexión
              </button>
            </div>
          )}

          {/* Lista de mensajes */}
          <div
            style={{
              maxHeight: '300px',
              minHeight: '140px',
              overflowY: 'auto',
              padding: '12px 14px',
              display: 'flex',
              flexDirection: 'column',
              gap: '10px',
              background: '#f8fafc',
            }}
          >
            {loadingHistory && messages.length === 0 ? (
              <div style={{ textAlign: 'center', color: '#64748b', fontSize: '0.85rem', padding: '24px 0' }}>
                Cargando mensajes...
              </div>
            ) : messages.length === 0 ? (
              <div style={{ textAlign: 'center', color: '#64748b', fontSize: '0.85rem', padding: '24px 0' }}>
                Aún no hay mensajes. Escribe para coordinar el trabajo con tu contraparte.
              </div>
            ) : (
              messages.map((msg) => {
                const isOwn = msg.autor_uid === user.uid
                return (
                  <div
                    key={msg.client_message_id || msg.id}
                    style={{
                      display: 'flex',
                      flexDirection: 'column',
                      alignItems: isOwn ? 'flex-end' : 'flex-start',
                    }}
                  >
                    <div
                      style={{
                        maxWidth: '82%',
                        padding: '9px 12px',
                        borderRadius: '12px',
                        background:
                          msg.deliveryState === 'failed'
                            ? '#fef2f2'
                            : isOwn
                              ? '#0284c7'
                              : '#ffffff',
                        color:
                          msg.deliveryState === 'failed'
                            ? '#991b1b'
                            : isOwn
                              ? '#ffffff'
                              : '#0f172a',
                        border:
                          msg.deliveryState === 'failed'
                            ? '1px solid #fca5a5'
                            : isOwn
                              ? '1px solid #0284c7'
                              : '1px solid #e2e8f0',
                        fontSize: '0.88rem',
                        lineHeight: 1.4,
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-word',
                      }}
                    >
                      {!isOwn && (
                        <div
                          style={{
                            fontSize: '0.73rem',
                            fontWeight: 700,
                            color: '#475569',
                            marginBottom: '2px',
                          }}
                        >
                          {msg.autor_nombre || (msg.rol_autor === 'TECNICO' ? 'Técnico' : 'Cliente')}
                        </div>
                      )}
                      <div>{msg.contenido}</div>
                    </div>

                    {/* Metadatos e indicador de envío (CA-03 / CA-06) */}
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '6px',
                        marginTop: '3px',
                        fontSize: '0.72rem',
                        color: msg.deliveryState === 'failed' ? '#b91c1c' : '#64748b',
                      }}
                    >
                      {msg.deliveryState === 'sent' && (
                        <>
                          <span>{formatMessageTime(msg.creado_en)}</span>
                          {isOwn && (
                            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '2px', color: '#0284c7' }}>
                              <Check size={12} /> Enviado
                            </span>
                          )}
                        </>
                      )}

                      {msg.deliveryState === 'sending' && (
                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: '3px' }}>
                          <Clock size={11} /> Enviando...
                        </span>
                      )}

                      {msg.deliveryState === 'failed' && (
                        <>
                          <span style={{ display: 'inline-flex', alignItems: 'center', gap: '3px' }}>
                            <AlertCircle size={12} /> No enviado
                          </span>
                          <button
                            type="button"
                            onClick={() => void handleRetry(msg)}
                            style={{
                              border: 'none',
                              background: 'transparent',
                              color: '#b91c1c',
                              fontWeight: 700,
                              cursor: 'pointer',
                              textDecoration: 'underline',
                              padding: 0,
                              fontSize: '0.72rem',
                            }}
                          >
                            Reintentar
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                )
              })
            )}
            <div ref={listEndRef} />
          </div>

          {/* Formulario de envío */}
          <form
            onSubmit={handleSubmit}
            style={{
              padding: '10px 12px',
              borderTop: '1px solid #e2e8f0',
              background: '#ffffff',
              display: 'flex',
              flexDirection: 'column',
              gap: '6px',
            }}
          >
            {draftError && (
              <div style={{ fontSize: '0.78rem', color: '#b91c1c' }}>{draftError}</div>
            )}
            <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
              <input
                type="text"
                value={draft}
                onChange={(e) => {
                  setDraft(e.target.value)
                  if (draftError) setDraftError(null)
                }}
                maxLength={CHAT_MAX_MESSAGE_LENGTH}
                placeholder="Escribe un mensaje para coordinar el trabajo..."
                aria-label="Mensaje del chat"
                style={{
                  flex: 1,
                  padding: '9px 12px',
                  borderRadius: '10px',
                  border: '1px solid #cbd5e1',
                  fontSize: '0.88rem',
                  outline: 'none',
                }}
              />
              <button
                type="submit"
                disabled={!draft.trim()}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '6px',
                  padding: '9px 14px',
                  borderRadius: '10px',
                  border: 'none',
                  background: draft.trim() ? '#0284c7' : '#cbd5e1',
                  color: '#ffffff',
                  fontWeight: 600,
                  fontSize: '0.85rem',
                  cursor: draft.trim() ? 'pointer' : 'not-allowed',
                }}
              >
                <Send size={14} />
                Enviar
              </button>
            </div>
          </form>
        </>
      )}
    </section>
  )
}
