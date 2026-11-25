import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { build } from 'esbuild'

const require = createRequire(import.meta.url)
const result = await build({
  stdin: {
    contents: [
      "export * from './src/lib/notifications';",
      "export { default as NotificationTray } from './src/components/NotificationTray';",
      "export { default as Header } from './src/components/Header';",
    ].join('\n'),
    resolveDir: process.cwd(),
    loader: 'tsx',
  },
  bundle: true,
  write: false,
  platform: 'node',
  format: 'cjs',
  packages: 'external',
  loader: { '.png': 'dataurl' },
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

const authMock = { currentUser: null }
const module = { exports: {} }
new Function('require', 'module', 'exports', result.outputFiles[0].text)(
  (id) => {
    if (id === 'test:firebase') return { auth: authMock }
    return require(id)
  },
  module,
  module.exports,
)

const {
  Header,
  NotificationTray,
  formatNotificationDateTegucigalpa,
  formatNotificationEventType,
  getAuthorizedNotificationRoute,
  getLatestNotificationCursor,
  getNotificationReconnectDelayMs,
  isProposalLiveNotification,
  mergeNotifications,
  resolveNotificationsWsUrl,
} = module.exports

test('CA-01 & CA-05: formats dates in America/Tegucigalpa and maps event categories cleanly', () => {
  const formatted = formatNotificationDateTegucigalpa({
    creada_en: '2026-11-25T18:30:00+00:00',
    creada_en_local: '2026-11-25T12:30:00-06:00',
  })
  assert.match(formatted, /12:30/)
  assert.match(formatted, /\(HN\)$/)

  assert.deepEqual(formatNotificationEventType('NUEVA_OFERTA_ABIERTA'), {
    label: 'Nueva oferta',
    category: 'proposal',
  })
  assert.deepEqual(formatNotificationEventType('RESTRICCION_OPERATIVA'), {
    label: 'Acción requerida',
    category: 'alert',
  })
})

test('CA-07: strict route sanitizer accepts only authorized internal app routes', () => {
  assert.equal(getAuthorizedNotificationRoute('/mis-solicitudes/sol-1'), '/mis-solicitudes/sol-1')
  assert.equal(getAuthorizedNotificationRoute('/tecnico/oportunidades'), '/tecnico/oportunidades')
  assert.equal(getAuthorizedNotificationRoute('/admin?tab=settlements'), '/admin?tab=settlements')

  for (const unsafe of [
    '',
    null,
    'https://evil.example/redirect',
    'http://localhost/hack',
    '//evil.example/protocol-relative',
    'javascript:alert(1)',
    '/open-requests/123',
    '/ruta-desconocida/99',
  ]) {
    assert.equal(getAuthorizedNotificationRoute(unsafe), null, `Unsafe route allowed: ${unsafe}`)
  }
})

test('CA-04 & CA-05: merges incoming notifications without duplicates and builds recovery cursor', () => {
  const n1 = {
    id: '01930000-0000-7000-8000-000000000001',
    evento_id: 'evt-1',
    tipo_evento: 'NUEVA_OFERTA_ABIERTA',
    titulo: 'Nueva oferta',
    resumen: 'Recibiste una propuesta',
    enlace_ruta: '/mis-solicitudes/open-1',
    recurso_tipo: 'PROPUESTA_ABIERTA',
    recurso_id: 'open-1',
    metadatos_seguros: { es_evento_propuesta_vivo: true },
    leida: false,
    leida_en: null,
    leida_en_local: null,
    creada_en: '2026-11-25T14:00:00+00:00',
    creada_en_local: '2026-11-25T08:00:00-06:00',
  }
  const n2 = {
    ...n1,
    id: '01930000-0000-7000-8000-000000000002',
    evento_id: 'evt-2',
    tipo_evento: 'PROPUESTA_ADJUDICADA',
    creada_en: '2026-11-25T15:00:00+00:00',
    creada_en_local: '2026-11-25T09:00:00-06:00',
  }

  const merged = mergeNotifications([n1], [n2, { ...n1, leida: true }])
  assert.equal(merged.length, 2)
  assert.equal(merged[0].id, n2.id)
  assert.equal(merged[1].leida, true)

  const cursor = getLatestNotificationCursor(merged)
  assert.deepEqual(cursor, {
    afterId: n2.id,
    since: n2.creada_en,
  })

  assert.equal(isProposalLiveNotification(n1), true)
  assert.equal(
    isProposalLiveNotification({ tipo_evento: 'DESEMBOLSO_PAGADO', metadatos_seguros: {} }),
    false,
  )
})

test('CA-05: WebSocket reconnect backoff and recovery URL query params', () => {
  assert.equal(getNotificationReconnectDelayMs(0), 1000)
  assert.equal(getNotificationReconnectDelayMs(1), 2000)
  assert.equal(getNotificationReconnectDelayMs(2), 4000)
  assert.equal(getNotificationReconnectDelayMs(8), 15000)

  const wsUrl = resolveNotificationsWsUrl('jwt-token-1', {
    afterId: 'notif-99',
    since: '2026-11-25T15:00:00+00:00',
  })
  assert.match(wsUrl, /^ws:\/\/127\.0\.0\.1:8000\/notifications\/ws\?/)
  assert.match(wsUrl, /token=jwt-token-1/)
  assert.match(wsUrl, /after_id=notif-99/)
  assert.match(wsUrl, /since=2026-11-25T15%3A00%3A00%2B00%3A00/)
})

test('CA-01: Header includes NotificationTray only for authenticated users without breaking AccountMenu', () => {
  const { createElement } = require('react')
  const { renderToStaticMarkup } = require('react-dom/server')
  const { MemoryRouter } = require('react-router-dom')

  const unauthHtml = renderToStaticMarkup(
    createElement(MemoryRouter, {}, createElement(Header, { authenticated: false })),
  )
  assert.doesNotMatch(unauthHtml, /aria-label="Notificaciones"/)

  const authHtml = renderToStaticMarkup(
    createElement(MemoryRouter, {}, createElement(Header, { authenticated: true })),
  )
  assert.match(authHtml, /aria-label="Notificaciones"/)
  assert.match(authHtml, /aria-label="Abrir menú de cuenta"/)

  const trayOnly = renderToStaticMarkup(
    createElement(MemoryRouter, {}, createElement(NotificationTray, { user: null })),
  )
  assert.match(trayOnly, /notification-tray-trigger/)
})
