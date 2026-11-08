import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { build } from 'esbuild'

const require = createRequire(import.meta.url)
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
const { MemoryRouter } = require('react-router-dom')
let profile
const result = await build({
  stdin: {
    contents: "export { TechnicianCard } from './src/components/TechnicianCard'; export { default as PublicProfile } from './src/TechnicianPublicProfilePage'",
    resolveDir: process.cwd(), loader: 'tsx',
  },
  bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external',
  loader: { '.png': 'dataurl' }, define: { 'import.meta.env': '{}' },
})
const module = { exports: {} }
new Function('require', 'module', 'exports', result.outputFiles[0].text)(id => {
  if (id === 'react') return {
    ...React,
    useState: initial => [initial === null ? profile : initial === true ? false : initial, () => {}],
  }
  return require(id)
}, module, module.exports)

for (const rating of [null, 0, 4.5]) {
  test(`catalog card and loaded public profile render API data with rating ${rating} and no profile tariff`, () => {
    profile = {
      usuario_id: 'test-technician', nombre_completo: 'Técnico de prueba',
      especialidades: ['Electricidad'], tecnico_verificado: true,
      estado_verificacion: 'VERIFICADO', estado_certificacion: 'NO_CERTIFICADO',
      calificacion_promedio: rating,
      zonas_cobertura: rating === null ? [] : [
        { departamento: 'Francisco Morazán', municipio: 'Distrito Central', sector: 'Kennedy' },
      ],
      servicios: [{ id: 'service-1', nombre: 'Instalación eléctrica', descripcion: 'Instalación de tomacorrientes.' }],
    }
    for (const component of [
      React.createElement(module.exports.TechnicianCard, { technician: profile }),
      React.createElement(module.exports.PublicProfile, { user: {} }),
    ]) {
      const html = renderToStaticMarkup(React.createElement(MemoryRouter, {}, component))
      assert.ok(html.includes('Técnico de prueba'))
      assert.ok(html.includes(rating === null ? 'Sin calificaciones' : rating.toFixed(1)))
      assert.ok(html.includes('Tarifas según el servicio'))
      assert.ok(!html.includes('undefined'))
      if (component.type === module.exports.PublicProfile) {
        assert.ok(html.includes('Zonas de cobertura'))
        if (rating === null) {
          assert.ok(html.includes('No hay zonas de cobertura publicadas.'))
        } else {
          assert.ok(html.includes('Distrito Central, Francisco Morazán'))
          assert.ok(html.includes('Kennedy'))
        }
      }
    }
  })
}
