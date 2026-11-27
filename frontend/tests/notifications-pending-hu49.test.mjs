import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { build } from 'esbuild'

const require = createRequire(import.meta.url)
const result = await build({
  stdin: {
    contents: "export * from './src/lib/notifications';",
    resolveDir: process.cwd(),
    loader: 'tsx',
  },
  bundle: true,
  write: false,
  platform: 'node',
  format: 'cjs',
  packages: 'external',
  define: { 'import.meta.env': '{}' },
  plugins: [
    {
      name: 'mock-firebase-auth',
      setup(buildPlugin) {
        buildPlugin.onResolve({ filter: /(?:^|\/)firebase$/ }, () => ({
          path: 'test:firebase',
          external: true,
        }))
      },
    },
  ],
})

const module = { exports: {} }
new Function('require', 'module', 'exports', result.outputFiles[0].text)(
  (id) => {
    if (id === 'test:firebase') return { auth: { currentUser: null } }
    return require(id)
  },
  module,
  module.exports,
)

const {
  NOTIFICATION_FILTER_ORDER,
  applyNotificationReadLocally,
  applyNotificationSocketFrame,
  clampUnreadCount,
  emitUnreadNotificationsCount,
  filterNotificationsByReadStatus,
  getEmptyNotificationsMessage,
  getNotificationReadStatus,
  isNotificationPending,
  subscribeUnreadNotificationsCount,
} = module.exports

function makeNotification(overrides = {}) {
  return {
    id: '01930000-0000-7000-8000-000000000001',
    evento_id: 'evt-1',
    tipo_evento: 'SERVICIO_INICIADO',
    titulo: 'Servicio iniciado',
    resumen: 'El técnico inició el servicio.',
    enlace_ruta: '/mis-trabajos/sol-1',
    recurso_tipo: 'SOLICITUD_DIRECTA',
    recurso_id: 'sol-1',
    metadatos_seguros: {},
    estado_lectura: 'PENDIENTE',
    leida: false,
    leida_en: null,
    leida_en_local: null,
    creada_en: '2026-11-27T14:00:00+00:00',
    creada_en_local: '2026-11-27T08:00:00-06:00',
    ...overrides,
  }
}

test('HU-49 CA-01 & CA-03: distinguishes pending vs read status and clamps unread counters >= 0', () => {
  const pending = makeNotification()
  const read = makeNotification({
    id: '01930000-0000-7000-8000-000000000002',
    estado_lectura: 'LEIDA',
    leida: true,
    leida_en: '2026-11-27T14:15:00+00:00',
  })

  assert.equal(getNotificationReadStatus(pending), 'PENDIENTE')
  assert.equal(isNotificationPending(pending), true)
  assert.equal(getNotificationReadStatus(read), 'LEIDA')
  assert.equal(isNotificationPending(read), false)

  assert.equal(clampUnreadCount(-5), 0)
  assert.equal(clampUnreadCount(0), 0)
  assert.equal(clampUnreadCount(4.9), 4)
  assert.equal(clampUnreadCount(Number.NaN), 0)
  assert.equal(clampUnreadCount(undefined), 0)
})

test('HU-49 CA-04: filters by TODAS / PENDIENTE / LEIDA and provides specific empty pending message', () => {
  assert.deepEqual(NOTIFICATION_FILTER_ORDER, ['TODAS', 'PENDIENTE', 'LEIDA'])

  const pendingItem = makeNotification({ id: 'notif-p1' })
  const readItem = makeNotification({
    id: 'notif-r1',
    estado_lectura: 'LEIDA',
    leida: true,
    leida_en: '2026-11-27T14:10:00+00:00',
  })

  const all = [pendingItem, readItem]
  assert.equal(filterNotificationsByReadStatus(all, 'TODAS').length, 2)
  assert.deepEqual(
    filterNotificationsByReadStatus(all, 'PENDIENTE').map((n) => n.id),
    ['notif-p1'],
  )
  assert.deepEqual(
    filterNotificationsByReadStatus(all, 'LEIDA').map((n) => n.id),
    ['notif-r1'],
  )
  assert.deepEqual(filterNotificationsByReadStatus([readItem], 'PENDIENTE'), [])

  const emptyPendingMsg = getEmptyNotificationsMessage('PENDIENTE')
  const emptyAllMsg = getEmptyNotificationsMessage('TODAS')
  assert.match(emptyPendingMsg, /pendientes/i)
  assert.notEqual(emptyPendingMsg, emptyAllMsg)
})

test('HU-49 CA-02 & CA-03: applyNotificationReadLocally updates only target item and preserves original leida_en', () => {
  const n1 = makeNotification({ id: 'n-1' })
  const n2 = makeNotification({ id: 'n-2' })

  const afterFirst = applyNotificationReadLocally([n1, n2], 'n-1', {
    leida_en: '2026-11-27T15:00:00+00:00',
    leida_en_local: '2026-11-27T09:00:00-06:00',
  })
  assert.equal(afterFirst[0].estado_lectura, 'LEIDA')
  assert.equal(afterFirst[0].leida, true)
  assert.equal(afterFirst[0].leida_en, '2026-11-27T15:00:00+00:00')
  assert.equal(afterFirst[1].estado_lectura, 'PENDIENTE')
  assert.equal(afterFirst[1].leida, false)

  // Repetir lectura sin fecha nueva conserva la fecha original
  const afterSecond = applyNotificationReadLocally(afterFirst, 'n-1')
  assert.equal(afterSecond[0].leida_en, '2026-11-27T15:00:00+00:00')
})

test('HU-49 CA-05: live WebSocket reducer increments on new notifications and syncs read changes across sessions', () => {
  const n1 = makeNotification({ id: 'n-1', creada_en: '2026-11-27T14:00:00+00:00' })
  const n2 = makeNotification({ id: 'n-2', creada_en: '2026-11-27T14:05:00+00:00' })

  // 1. Conexión inicial
  let state = applyNotificationSocketFrame(
    { items: [n1], unreadCount: 0 },
    { type: 'connected', no_leidas: 1, pendientes: 1 },
  )
  assert.equal(state.unreadCount, 1)

  // 2. Nuevo aviso con contador autoritativo incrementa a 2
  state = applyNotificationSocketFrame(state, {
    type: 'notification',
    notificacion: n2,
    no_leidas: 2,
    pendientes: 2,
  })
  assert.equal(state.unreadCount, 2)
  assert.equal(state.items.length, 2)
  assert.equal(state.newlyArrived.length, 1)

  // 3. Reenvío duplicado del mismo aviso no vuelve a incrementar ni duplica
  const dup = applyNotificationSocketFrame(state, {
    type: 'notification',
    notificacion: n2,
  })
  assert.equal(dup.items.length, 2)
  assert.equal(dup.newlyArrived.length, 0)
  assert.equal(dup.unreadCount, 2)

  // 4. Otra sesión marca n-1 como leída -> read_sync actualiza estado, fecha y contador
  state = applyNotificationSocketFrame(state, {
    type: 'read_sync',
    notification_id: 'n-1',
    estado_lectura: 'LEIDA',
    leida_en: '2026-11-27T14:20:00+00:00',
    leida_en_local: '2026-11-27T08:20:00-06:00',
    no_leidas: 1,
    pendientes: 1,
  })
  assert.equal(state.unreadCount, 1)
  const syncedN1 = state.items.find((entry) => entry.id === 'n-1')
  assert.equal(syncedN1.estado_lectura, 'LEIDA')
  assert.equal(syncedN1.leida, true)
  assert.equal(syncedN1.leida_en, '2026-11-27T14:20:00+00:00')

  // 5. Otra sesión marca todas como leídas -> read_all_sync lleva contador a 0
  state = applyNotificationSocketFrame(state, {
    type: 'read_all_sync',
    leida_en: '2026-11-27T14:25:00+00:00',
    no_leidas: 0,
    pendientes: 0,
  })
  assert.equal(state.unreadCount, 0)
  assert.equal(filterNotificationsByReadStatus(state.items, 'PENDIENTE').length, 0)

  // 6. Trama con valor negativo queda acotada a 0
  state = applyNotificationSocketFrame(state, { type: 'unread_count', no_leidas: -3 })
  assert.equal(state.unreadCount, 0)
})

test('HU-49 CA-05: Observer event emits clamped unread counter updates', () => {
  const listeners = new Map()
  const originalWindow = globalThis.window
  globalThis.window = {
    addEventListener(name, fn) {
      listeners.set(name, fn)
    },
    removeEventListener(name) {
      listeners.delete(name)
    },
    dispatchEvent(event) {
      const fn = listeners.get(event.type)
      if (fn) fn(event)
    },
  }

  try {
    const received = []
    const unsubscribe = subscribeUnreadNotificationsCount((detail) => received.push(detail))
    emitUnreadNotificationsCount(3)
    emitUnreadNotificationsCount(-2)
    unsubscribe()
    emitUnreadNotificationsCount(9)

    assert.deepEqual(received, [
      { no_leidas: 3, pendientes: 3 },
      { no_leidas: 0, pendientes: 0 },
    ])
  } finally {
    globalThis.window = originalWindow
  }
})