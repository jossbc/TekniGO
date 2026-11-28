import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { build } from 'esbuild'

const require = createRequire(import.meta.url)
const result = await build({
  stdin: {
    contents: "export * from './src/lib/adminSuspensions';",
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
  DIMENSION_LABELS,
  MODERATION_EVENT_LABELS,
  SUSPENSION_CAUSE_LABELS,
  classifyAccountRestrictionBadges,
} = module.exports

test('HU-52 CA-07: distinguishes conduct suspension from financial restriction independently', () => {
  const onlyFinancial = classifyAccountRestrictionBadges({
    cuenta_cerrada: false,
    suspendido_por_conducta: false,
    restriccion_financiera: true,
    alerta_tercera_falta_activa: false,
  })
  assert.deepEqual(
    onlyFinancial.map((b) => b.code),
    ['RESTRICCION_FINANCIERA'],
  )

  const simultaneous = classifyAccountRestrictionBadges({
    cuenta_cerrada: false,
    suspendido_por_conducta: true,
    restriccion_financiera: true,
    alerta_tercera_falta_activa: true,
  })
  assert.deepEqual(
    simultaneous.map((b) => b.code),
    ['SUSPENSION_CONDUCTA', 'RESTRICCION_FINANCIERA', 'ALERTA_TERCERA_FALTA'],
  )
})

test('HU-52 CA-07: disciplinary causes never include financial restriction', () => {
  assert.equal('RESTRICCION_FINANCIERA' in SUSPENSION_CAUSE_LABELS, false)
  assert.ok(SUSPENSION_CAUSE_LABELS.CONDUCTA)
  assert.ok(SUSPENSION_CAUSE_LABELS.REINCIDENCIA_FALTAS)
  assert.ok(SUSPENSION_CAUSE_LABELS.FRAUDE_IDENTIDAD)
})

test('HU-52 CA-05 & CA-08: exposes all independent operational dimensions and closure audit labels', () => {
  for (const key of [
    'CUENTA_ACTIVA',
    'VERIFICACION',
    'DISPONIBILIDAD',
    'SUSPENSION_CONDUCTA',
    'RESTRICCION_FINANCIERA',
  ]) {
    assert.ok(DIMENSION_LABELS[key], `Missing dimension label for ${key}`)
  }

  for (const evt of [
    'SUSPENSION_APLICADA',
    'SUSPENSION_LEVANTADA',
    'SUSPENSION_EXPIRADA',
    'ALERTA_REINCIDENCIA_ATENDIDA',
    'CIERRE_CUENTA_APLICADO',
    'BAJA_LOGICA_DOCUMENTAL',
  ]) {
    assert.ok(MODERATION_EVENT_LABELS[evt], `Missing audit event label for ${evt}`)
  }

  const closed = classifyAccountRestrictionBadges({
    cuenta_cerrada: true,
    suspendido_por_conducta: false,
    restriccion_financiera: false,
  })
  assert.equal(closed[0].code, 'CUENTA_CERRADA')
})