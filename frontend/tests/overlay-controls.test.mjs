import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { build } from 'esbuild'

const require = createRequire(import.meta.url)
const React = require('react')
let h
const result = await build({
  stdin:{ contents:"export { default as AccountMenu } from './src/components/AccountMenu'; export { default as DossierModal } from './src/components/DossierModal'",resolveDir:process.cwd(),loader:'tsx' },
  bundle:true,write:false,platform:'node',format:'cjs',packages:'external',loader:{ '.png':'dataurl' },define:{ 'import.meta.env':'{}' },
})
const module = { exports:{} }
new Function('require','module','exports',result.outputFiles[0].text)(id => {
  if (id==='react') return { ...React,
    useState(initial) { const i=h.index++; if (!(i in h.state)) h.state[i]=initial; return [h.state[i],value => { h.state[i]=typeof value==='function'?value(h.state[i]):value }] },
    useRef(initial) { const i=h.index++; if (!(i in h.state)) h.state[i]={ current:initial }; return h.state[i] },
    useId() { return 'overlay-test-'+h.index++ },
    useEffect(effect,deps) { const i=h.index++; const previous=h.state[i]; if (!previous || deps.some((dep,index) => dep!==previous[index])) h.effects.push({ i,effect }); h.state[i]=deps },
  }
  return require(id)
},module,module.exports)
const nodes=tree => !tree || typeof tree!=='object'?[]:[tree,...React.Children.toArray(tree.props?.children).flatMap(nodes)]
function setup() { h={ index:0,state:[],effects:[],cleanups:new Map() } }
function render(Component,props) { h.index=0; return nodes(Component(props)) }
function effects() { for (const { i,effect } of h.effects.splice(0)) { h.cleanups.get(i)?.(); h.cleanups.set(i,effect()) } }
function cleanup() { for (const action of h.cleanups.values()) action?.() }
function documentHarness() {
  const previous=Object.getOwnPropertyDescriptor(globalThis,'document')
  const doc=Object.assign(new EventTarget(),{ body:{ style:{ overflow:'auto' } }, activeElement:null })
  Object.defineProperty(globalThis,'document',{ configurable:true,writable:true,value:doc })
  return { doc,restore() { if (previous) Object.defineProperty(globalThis,'document',previous); else delete globalThis.document } }
}

test('avatar menu opens on demand, supports arrows/Escape and closes outside', () => {
  setup()
  const browser=documentHarness()
  let focused=''
  const trigger={ focus() { focused='trigger' } }
  const first={ focus() { focused='first'; browser.doc.activeElement=first } }
  const second={ focus() { focused='second'; browser.doc.activeElement=second } }
  const props={ email:'admin@example.com',accountHref:'/admin',accountLabel:'Administración' }
  function tree() {
    const tree=render(module.exports.AccountMenu,props)
    tree[0].props.ref.current={ contains:() => false }
    tree.find(node => node.type==='button').props.ref.current=trigger
    const panel=tree.find(node => node.props?.role==='menu')
    if (panel) panel.props.ref.current={ querySelector:() => first,querySelectorAll:() => [first,second] }
    effects()
    return tree
  }
  try {
    let state=tree()
    assert.ok(!state.some(node => node.props?.role==='menu'))
    state.find(node => node.type==='button').props.onClick()
    state=tree()
    assert.equal(focused,'first')
    assert.ok(state.some(node => node.props?.variant==='menu'),'logout lives inside the menu')
    state.find(node => node.props?.role==='menu').props.onKeyDown({ key:'ArrowDown',preventDefault() {},currentTarget:{ querySelectorAll:() => [first,second] } })
    assert.equal(focused,'second')
    const escape=new Event('keydown',{ cancelable:true }); Object.defineProperty(escape,'key',{ value:'Escape' })
    browser.doc.dispatchEvent(escape)
    assert.equal(focused,'trigger')
    assert.ok(!tree().some(node => node.props?.role==='menu'))
    tree().find(node => node.type==='button').props.onClick()
    tree()
    browser.doc.dispatchEvent(new Event('pointerdown'))
    assert.ok(!tree().some(node => node.props?.role==='menu'))
  } finally { cleanup(); browser.restore() }
})

test('dossier uses a modal dialog, locks background scroll and restores focus on close', () => {
  setup()
  const browser=documentHarness()
  let restored=false,closed=0
  browser.doc.activeElement={ isConnected:true,focus() { restored=true } }
  const element={ open:false,showModal() { this.open=true },close() { this.open=false } }
  try {
    const tree=render(module.exports.DossierModal,{ children:'Private dossier',onClose() { closed++ } })
    const dialog=tree[0]
    assert.equal(dialog.type,'dialog')
    assert.equal(dialog.props['aria-modal'],'true')
    dialog.props.ref.current=element
    effects()
    assert.equal(element.open,true)
    assert.equal(browser.doc.body.style.overflow,'hidden')
    const target={ getBoundingClientRect:() => ({ left:10,right:100,top:10,bottom:100 }) }
    dialog.props.onClick({ target,currentTarget:target,clientX:30,clientY:30 })
    assert.equal(closed,0,'clicking dialog padding does not discard the dossier')
    dialog.props.onClick({ target,currentTarget:target,clientX:0,clientY:0 })
    assert.equal(closed,1)
    dialog.props.onCancel({ preventDefault() {} })
    assert.equal(closed,2)
    cleanup()
    assert.equal(element.open,false)
    assert.equal(browser.doc.body.style.overflow,'auto')
    assert.equal(restored,true)
  } finally { browser.restore() }
})
