import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { build } from 'esbuild'

const require = createRequire(import.meta.url)
const result = await build({
  stdin: {
    contents: "export * from './src/lib/digitalPayments';",
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
  formatGatewayEnvironmentBadge,
  formatRetentionLabel,
  getPaymentResultPresentation,
  getRefundStatusPresentation,
} = module.exports

test('HU-38 CA-02: rejected, pending, and abandoned states present recoverable outcomes', () => {
  for (const status of ['PENDIENTE', 'RECHAZADO', 'ABANDONADO', 'EXPIRADO']) {
    const ui = getPaymentResultPresentation(status)
    assert.equal(ui.recoverable, true, `${status} must be recoverable`)
  }
  const confirmed = getPaymentResultPresentation('CONFIRMADO')
  assert.equal(confirmed.recoverable, false)
  assert.equal(confirmed.tone, 'success')
})

test('HU-38 CA-03: retention labels clearly distinguish retained vs released funds', () => {
  assert.match(formatRetentionLabel('RETENIDO'), /retenido/i)
  assert.match(formatRetentionLabel('LIBERADO'), /liberado/i)
  assert.match(formatRetentionLabel('REEMBOLSADO'), /devuelto/i)
})

test('HU-38 CA-06 & CA-07: refund only shows Reembolsado after external confirmation', () => {
  const pending = getRefundStatusPresentation({ estado_pasarela: 'EN_PROCESO_PASARELA' })
  assert.notEqual(pending.label, 'Reembolsado')
  assert.equal(pending.canRetry, true)

  const failed = getRefundStatusPresentation({ estado_pasarela: 'PENDIENTE_CONCILIACION' })
  assert.match(failed.label, /conciliación|reintento/i)
  assert.equal(failed.canRetry, true)

  const done = getRefundStatusPresentation({ estado_pasarela: 'CONFIRMADO_PASARELA' })
  assert.equal(done.label, 'Reembolsado')
  assert.equal(done.canRetry, false)
})

test('HU-38 CA-08: clearly distinguishes real Sandbox from local Dummy simulator', () => {
  const sandbox = formatGatewayEnvironmentBadge({
    proveedor: 'stripe',
    entorno: 'SANDBOX',
    es_sandbox: true,
    es_simulacion_local: false,
    etiqueta: 'Stripe Sandbox (Modo pruebas)',
    moneda: 'HNL',
  })
  assert.equal(sandbox.tone, 'sandbox')
  assert.match(sandbox.label, /sandbox/i)

  const dummy = formatGatewayEnvironmentBadge({
    proveedor: 'dummy',
    entorno: 'SIMULADOR_LOCAL',
    es_sandbox: false,
    es_simulacion_local: true,
    etiqueta: 'Simulador local',
    moneda: 'HNL',
  })
  assert.equal(dummy.tone, 'simulator')
  assert.match(dummy.notice, /no corresponde a una integración real/i)
})