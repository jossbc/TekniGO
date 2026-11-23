import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { build } from 'esbuild'

const require = createRequire(import.meta.url)
const result = await build({
  stdin: { contents: "export * from './src/lib/contractChat'", resolveDir: process.cwd(), loader: 'ts' },
  bundle: true,
  write: false,
  platform: 'node',
  format: 'cjs',
  packages: 'external',
  define: { 'import.meta.env': '{}' },
})
const module = { exports: {} }
new Function('require', 'module', 'exports', result.outputFiles[0].text)(require, module, module.exports)

const {
  CHAT_HEARTBEAT_INTERVAL_MS,
  CHAT_MAX_MESSAGE_LENGTH,
  WS_CLOSE_FORBIDDEN,
  WS_CLOSE_NOT_FOUND,
  WS_CLOSE_STATE_DISABLED,
  createPendingLocalMessage,
  getLatestPersistedTimestamp,
  getReconnectDelayMs,
  isChatClosedState,
  isChatEnabledForState,
  isPermanentChatCloseCode,
  markMessageFailed,
  markMessageSending,
  mergeChatMessages,
  resolveChatWsUrl,
  validateChatDraft,
} = module.exports

test('CA-01 & CA-05: chat is enabled exclusively for ACEPTADA and EN PROCESO states', () => {
  assert.equal(isChatEnabledForState('ACEPTADA'), true)
  assert.equal(isChatEnabledForState('Aceptada'), true)
  assert.equal(isChatEnabledForState('EN PROCESO'), true)
  assert.equal(isChatEnabledForState('EN_PROCESO'), true)

  for (const state of ['PENDIENTE', 'Publicada', 'CON OFERTAS', 'RECHAZADA', 'COMPLETADA', 'CANCELADA', '', null]) {
    assert.equal(isChatEnabledForState(state), false, `Estado inesperado habilitado: ${state}`)
  }

  assert.equal(isChatClosedState('COMPLETADA'), true)
  assert.equal(isChatClosedState('Cancelada'), true)
  assert.equal(isChatClosedState('EN PROCESO'), false)
})

test('CA-03: draft validation rejects empty/whitespace and overly long messages', () => {
  assert.notEqual(validateChatDraft(''), null)
  assert.notEqual(validateChatDraft('   \n\t '), null)
  assert.notEqual(validateChatDraft('a'.repeat(CHAT_MAX_MESSAGE_LENGTH + 1)), null)
  assert.equal(validateChatDraft('Llego en 15 minutos'), null)
})

test('CA-03, CA-04 & CA-06: pending messages only turn sent after persistence and merge without duplicates', () => {
  const pending = createPendingLocalMessage({
    clientMessageId: 'msg-cid-1',
    autorUid: 'cli-1',
    autorNombre: 'Ana',
    rolAutor: 'CLIENTE',
    contenido: '¿Ya vienes en camino?',
    createdAt: '2026-11-23T18:01:00.000Z',
  })
  assert.equal(pending.deliveryState, 'sending')

  // Un mensaje pendiente no cuenta como último timestamp persistido para el cursor after
  assert.equal(getLatestPersistedTimestamp([pending]), null)

  // Falla de red -> pasa a failed sin marcarse como entregado
  const failedList = markMessageFailed([pending], 'msg-cid-1', 'Sin conexión')
  assert.equal(failedList[0].deliveryState, 'failed')
  assert.equal(failedList[0].error, 'Sin conexión')

  // Reintento -> vuelve a sending conservando el mismo client_message_id
  const retryingList = markMessageSending(failedList, 'msg-cid-1')
  assert.equal(retryingList[0].deliveryState, 'sending')
  assert.equal(retryingList[0].client_message_id, 'msg-cid-1')

  // Servidor confirma persistencia + entrega un mensaje anterior de la contraparte
  const merged = mergeChatMessages(retryingList, [
    {
      id: 'srv-2',
      modalidad: 'DIRECTA',
      solicitud_id: 'req-1',
      autor_id: 'u-cli',
      autor_uid: 'cli-1',
      autor_nombre: 'Ana',
      rol_autor: 'CLIENTE',
      contenido: '¿Ya vienes en camino?',
      client_message_id: 'msg-cid-1',
      creado_en: '2026-11-23T18:01:05.000Z',
    },
    {
      id: 'srv-1',
      modalidad: 'DIRECTA',
      solicitud_id: 'req-1',
      autor_id: 'u-tech',
      autor_uid: 'tech-1',
      autor_nombre: 'Carlos',
      rol_autor: 'TECNICO',
      contenido: 'Hola Ana, confirmado',
      client_message_id: null,
      creado_en: '2026-11-23T18:00:10.000Z',
    },
  ])

  assert.equal(merged.length, 2)
  assert.deepEqual(
    merged.map((m) => ({ id: m.id, state: m.deliveryState })),
    [
      { id: 'srv-1', state: 'sent' },
      { id: 'srv-2', state: 'sent' },
    ],
  )
  assert.equal(getLatestPersistedTimestamp(merged), '2026-11-23T18:01:05.000Z')

  // Reconexión reenvía el mismo lote -> cero duplicados
  const afterReconnect = mergeChatMessages(merged, [
    {
      id: 'srv-2',
      modalidad: 'DIRECTA',
      solicitud_id: 'req-1',
      autor_id: 'u-cli',
      autor_uid: 'cli-1',
      autor_nombre: 'Ana',
      rol_autor: 'CLIENTE',
      contenido: '¿Ya vienes en camino?',
      client_message_id: 'msg-cid-1',
      creado_en: '2026-11-23T18:01:05.000Z',
    },
  ])
  assert.equal(afterReconnect.length, 2)
})

test('CA-04: reconnect backoff is bounded and WS URL includes token and optional after cursor', () => {
  assert.equal(getReconnectDelayMs(0), 1000)
  assert.equal(getReconnectDelayMs(1), 2000)
  assert.equal(getReconnectDelayMs(2), 4000)
  assert.equal(getReconnectDelayMs(10), 15000)

  const url = resolveChatWsUrl('ABIERTA', 'req-99', 'tok-abc', '2026-11-23T18:00:00.000Z')
  assert.match(url, /^ws:\/\/.*\/chat\/solicitudes\/ABIERTA\/req-99\/ws\?/)
  assert.ok(url.includes('token=tok-abc'))
  assert.ok(url.includes('after=2026-11-23T18%3A00%3A00.000Z'))
})

test('CA-05: permanent close codes stop automatic reconnection while transient codes allow retry', () => {
  assert.equal(isPermanentChatCloseCode(WS_CLOSE_STATE_DISABLED), true)
  assert.equal(isPermanentChatCloseCode(WS_CLOSE_FORBIDDEN), true)
  assert.equal(isPermanentChatCloseCode(WS_CLOSE_NOT_FOUND), true)
  assert.equal(isPermanentChatCloseCode(1006), false)
  assert.equal(isPermanentChatCloseCode(1011), false)
  assert.equal(isPermanentChatCloseCode(undefined), false)
  assert.ok(CHAT_HEARTBEAT_INTERVAL_MS >= 10_000)
})

test('ST-05: tie-break ordering, repeated retries and simultaneous REST+WS confirmation never duplicate', () => {
  const sameTs = '2026-11-23T19:00:00.000Z'
  const local = createPendingLocalMessage({
    clientMessageId: 'cid-multi-retry',
    autorUid: 'cli-1',
    autorNombre: 'Ana',
    rolAutor: 'CLIENTE',
    contenido: 'Confirmo portón abierto',
    createdAt: sameTs,
  })

  // Dos intentos fallidos consecutivos nunca muestran el mensaje como entregado
  let list = markMessageFailed([local], 'cid-multi-retry', 'Timeout 1')
  assert.equal(list[0].deliveryState, 'failed')
  list = markMessageSending(list, 'cid-multi-retry')
  list = markMessageFailed(list, 'cid-multi-retry', 'Timeout 2')
  assert.equal(list[0].deliveryState, 'failed')
  assert.equal(getLatestPersistedTimestamp(list), null)

  // Tercer intento confirmado por REST y también recibido en vivo por WebSocket con empate de timestamp
  const confirmed = {
    id: '00000000-0000-0000-0000-000000000002',
    modalidad: 'DIRECTA',
    solicitud_id: 'req-1',
    autor_id: 'u-cli',
    autor_uid: 'cli-1',
    autor_nombre: 'Ana',
    rol_autor: 'CLIENTE',
    contenido: 'Confirmo portón abierto',
    client_message_id: 'cid-multi-retry',
    creado_en: sameTs,
  }
  const counterpartSameTs = {
    id: '00000000-0000-0000-0000-000000000001',
    modalidad: 'DIRECTA',
    solicitud_id: 'req-1',
    autor_id: 'u-tech',
    autor_uid: 'tech-1',
    autor_nombre: 'Carlos',
    rol_autor: 'TECNICO',
    contenido: 'Estoy afuera',
    client_message_id: null,
    creado_en: sameTs,
  }

  list = markMessageSending(list, 'cid-multi-retry')
  list = mergeChatMessages(list, [confirmed])
  list = mergeChatMessages(list, [confirmed, counterpartSameTs])

  assert.equal(list.length, 2)
  assert.deepEqual(
    list.map((m) => m.id),
    [
      '00000000-0000-0000-0000-000000000001',
      '00000000-0000-0000-0000-000000000002',
    ],
  )
  assert.equal(list.every((m) => m.deliveryState === 'sent'), true)
})

test('ST-05: online lifecycle transition keeps chat active in ACEPTADA/EN PROCESO and disables on final states', () => {
  const lifecycle = [
    ['PENDIENTE', false],
    ['ACEPTADA', true],
    ['EN PROCESO', true],
    ['COMPLETADA', false],
    ['CANCELADA', false],
  ]
  for (const [state, expected] of lifecycle) {
    assert.equal(isChatEnabledForState(state), expected, `Estado ${state}`)
  }
})
