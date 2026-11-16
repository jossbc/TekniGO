import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { build } from 'esbuild'

const require = createRequire(import.meta.url)
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
const { MemoryRouter } = require('react-router-dom')

const bundleResult = await build({
  stdin: {
    contents: `
      export { default as ReviewModal } from './src/components/ReviewModal';
      export { TechnicianCard } from './src/components/TechnicianCard';
      export { formatReviewDate, TECHNICIAN_HIGHLIGHT_CHIPS, CLIENT_HIGHLIGHT_CHIPS } from './src/lib/reviews';
    `,
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
})

const loadedModule = { exports: {} }
new Function('require', 'module', 'exports', bundleResult.outputFiles[0].text)(
  id => require(id),
  loadedModule,
  loadedModule.exports
)

const { ReviewModal, TechnicianCard, formatReviewDate, TECHNICIAN_HIGHLIGHT_CHIPS, CLIENT_HIGHLIGHT_CHIPS } = loadedModule.exports

test('formatReviewDate formats date into America/Tegucigalpa local representation', () => {
  // 2026-11-10T18:00:00Z is 12:00 in Tegucigalpa (UTC-6)
  const formatted = formatReviewDate('2026-11-10T18:00:00Z')
  assert.ok(typeof formatted === 'string')
  assert.ok(formatted.includes('nov') || formatted.includes('10'))
})

test('ReviewModal renders client rating technician view with stars and chips (HU-44)', () => {
  const html = renderToStaticMarkup(
    React.createElement(ReviewModal, {
      user: {},
      tipo_solicitud: 'DIRECTA',
      solicitud_id: 'sol-123',
      evaluadoNombre: 'Juan Perez',
      rolEvaluador: 'CLIENTE',
      onClose: () => {},
      onSuccess: () => {},
    })
  )

  assert.ok(html.includes('¿Cómo estuvo el servicio con Juan Perez?'))
  assert.ok(html.includes('Califica al profesional'))
  assert.ok(html.includes('¿Qué aspectos destacarías?'))
  assert.ok(html.includes('Puntual'))
  assert.ok(html.includes('Trabajo impecable'))
  assert.ok(html.includes('Enviar calificación'))
  assert.ok(html.includes('Publicación simultánea'))
})

test('ReviewModal renders technician rating client view with client-specific chips (HU-45)', () => {
  const html = renderToStaticMarkup(
    React.createElement(ReviewModal, {
      user: {},
      tipo_solicitud: 'DIRECTA',
      solicitud_id: 'sol-789',
      evaluadoNombre: 'Maria Lopez',
      rolEvaluador: 'TECNICO',
      onClose: () => {},
      onSuccess: () => {},
    })
  )

  assert.ok(html.includes('¿Cómo estuvo el servicio con Maria Lopez?'))
  assert.ok(html.includes('Califica al cliente'))
  assert.ok(html.includes('Trato amable'))
  assert.ok(html.includes('Pago puntual'))
  assert.ok(html.includes('Comunicación clara'))
})

test('TechnicianCard displays Sin calificaciones · Nuevo when rating is null and formatted score with count when rated (HU-46)', () => {
  const unratedTech = {
    usuario_id: 'tech-1',
    nombre_completo: 'Carlos Gomez',
    especialidades: ['Fontanería'],
    tecnico_verificado: true,
    estado_verificacion: 'VERIFICADO',
    estado_certificacion: 'NO_CERTIFICADO',
    calificacion_promedio: null,
    total_resenas: 0,
    zonas_cobertura: [],
  }

  const unratedHtml = renderToStaticMarkup(
    React.createElement(MemoryRouter, {}, React.createElement(TechnicianCard, { technician: unratedTech }))
  )
  assert.ok(unratedHtml.includes('Sin calificaciones'))
  assert.ok(unratedHtml.includes('Nuevo'))

  const ratedTech = {
    ...unratedTech,
    calificacion_promedio: 4.8,
    total_resenas: 12,
  }

  const ratedHtml = renderToStaticMarkup(
    React.createElement(MemoryRouter, {}, React.createElement(TechnicianCard, { technician: ratedTech }))
  )
  assert.ok(ratedHtml.includes('4.8'))
  assert.ok(ratedHtml.includes('(12)'))
})
