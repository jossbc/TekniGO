import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { build } from 'esbuild'

const require = createRequire(import.meta.url)
let roles = []
const result = await build({
  stdin: { contents: "export { default as AccountPage } from './src/pages/AccountPage'; export { accountPath } from './src/lib/accountPath'", resolveDir: process.cwd(), loader:'tsx' },
  bundle:true, write:false, platform:'node', format:'cjs', packages:'external',
  loader:{ '.png':'dataurl', '.jpg':'dataurl', '.css':'empty' }, define:{ 'import.meta.env':'{}' },
  plugins:[{ name:'account-state', setup(build) {
    build.onResolve({ filter: /\/useAccount$/ }, () => ({ path:'test:account', external:true }))
  } }],
})
const module = { exports:{} }
new Function('require','module','exports',result.outputFiles[0].text)(id => {
  if (id==='react') return { ...require(id), useState:initial => [initial, () => {}], useEffect() {} }
  if (id==='react-router-dom') return { ...require(id), useNavigate:() => () => {}, useLocation:() => ({ state:null }) }
  if (id==='test:account') return { useAccount:() => ({ state:{ status:'ready', profile:{ roles, nombre_completo:'Admin', estado_identidad:'PENDIENTE' } }, retry() {}, updateProfile() {} }) }
  return require(id)
},module,module.exports)

test('admin-only and mixed-role accounts enter administration directly without identity prerequisites', () => {
  for (const assigned of [['ADMIN'],['ADMIN','CLIENTE'],['TECNICO','ADMIN'],['CLIENTE','TECNICO','ADMIN']]) {
    roles = assigned
    assert.equal(module.exports.accountPath(roles), '/admin')
    for (const space of [undefined,'CLIENTE','TECNICO']) {
      const page = module.exports.AccountPage({ user:{ uid:'admin' }, space })
      assert.equal(page.props.to, '/admin')
      assert.equal(page.props.replace, true)
    }
  }
  assert.equal(module.exports.accountPath(['CLIENTE']), '/cliente')
  assert.equal(module.exports.accountPath(['TECNICO']), '/tecnico')
  assert.equal(module.exports.accountPath(['CLIENTE','TECNICO']), '/mi-cuenta')
})
