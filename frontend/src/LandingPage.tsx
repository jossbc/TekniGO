import { ArrowDown, ArrowRight, ArrowUpRight, BadgeCheck, Banknote, Check, ChevronDown, Hammer, House, MapPin, PaintRoller, ShieldCheck, Snowflake, Users, Wrench, Zap, Droplets } from 'lucide-react'
import { Link } from 'react-router-dom'
import { Brand } from './components/ui'
import AccountMenu from './components/AccountMenu'
import technicianPhoto from './assets/technician.jpg'
import './landing.css'

const services = [
  { icon: Zap, name: 'Electricidad', text: 'Instalaciones, conexiones y reparaciones.', tone: 'orange' },
  { icon: Droplets, name: 'Fontanería', text: 'Una solución para cada fuga y tubería.', tone: 'blue' },
  { icon: PaintRoller, name: 'Pintura', text: 'Un nuevo aire para tus espacios.', tone: 'orange' },
  { icon: Hammer, name: 'Carpintería', text: 'Detalles, muebles y trabajos en madera.', tone: 'blue' },
  { icon: House, name: 'Albañilería', text: 'Mejoras que hacen de tu casa un hogar.', tone: 'orange' },
  { icon: Snowflake, name: 'Aire acondicionado', text: 'Instalación, mantenimiento y refrigeración.', tone: 'blue' },
]

const questions = [
  ['¿Puedo registrarme como cliente y como técnico?', 'Sí. Puedes comenzar con el rol que necesitas y agregar el otro desde tu cuenta. Así tendrás ambas posibilidades en un mismo perfil.'],
  ['¿Por qué tengo que verificar mi identidad?', 'La verificación ayuda a construir una comunidad de confianza. Durante el registro te pediremos tus documentos y una selfie. Tus documentos de identidad no son públicos.'],
  ['¿Qué necesito para ofrecer mis servicios?', 'Crea tu cuenta como técnico, completa tu perfil y la verificación de identidad. Después registra tus especialidades, tus servicios, sus tarifas y las zonas donde trabajas.'],
  ['¿Dónde puedo indicar mi zona de trabajo?', 'Si eres técnico, puedes configurar tus zonas de cobertura desde tu cuenta, seleccionando los departamentos y municipios donde ofreces tus servicios.'],
]

export default function LandingPage({ authenticated = false }: { authenticated?: boolean }) {
  const clientPath = authenticated ? '/mi-cuenta' : '/registro'
  return <div className="landing">
    <a className="landing-skip" href="#contenido">Saltar al contenido</a>
    <header className="landing-header landing-wrap">
      <nav className="landing-nav" aria-label="Navegación principal">
        <a href="#servicios">Servicios</a><a href="#como-funciona">Cómo funciona</a><a href="#para-tecnicos">Para técnicos</a>
      </nav>
      <Brand />
      <div className="landing-header-actions">
        {!authenticated && <Link className="landing-login" to="/ingresar">Iniciar sesión</Link>}
        {authenticated ? <AccountMenu accountHref="/mi-cuenta" accountLabel="Mi cuenta" /> : <Link className="landing-button landing-button--small" to={clientPath}>{authenticated ? 'Ir a mi espacio' : 'Crear cuenta'}<ArrowUpRight size={16} aria-hidden="true" /></Link>}
      </div>
    </header>

    <main id="contenido">
      <section className="landing-hero landing-wrap" aria-labelledby="landing-title">
        <div className="landing-hero-copy">
          <h1 id="landing-title">Tu hogar necesita <span className="landing-orange">una mano.</span><br /><span className="landing-blue">Aquí está.</span></h1>
          <p className="landing-intro">Desde esa fuga que no espera hasta el proyecto que tienes en mente. Conecta con el talento que sabe cómo resolverlo.</p>
          <div className="landing-hero-actions">
            <Link className="landing-button" to={clientPath} state={{ role: 'CLIENTE' }}>Necesito un técnico<ArrowRight size={18} aria-hidden="true" /></Link>
            <Link className="landing-text-link" to={authenticated ? '/mi-cuenta' : '/registro'} state={{ role: 'TECNICO' }}>Quiero ofrecer mis servicios<ArrowUpRight size={17} aria-hidden="true" /></Link>
          </div>
        </div>
        <div className="landing-hero-visual">
          <div className="landing-photo-frame"><img src={technicianPhoto} alt="Técnico con uniforme azul y una llave de ajuste, listo para trabajar" width="4693" height="3635" fetchPriority="high" /></div>
          <span className="landing-photo-tag"><MapPin size={14} aria-hidden="true" />Hecho para Honduras</span>
          <a className="landing-photo-caption" href="#como-funciona">Conoce cómo funciona<ArrowDown size={15} aria-hidden="true" /></a>
        </div>
      </section>

      <div className="landing-principles landing-wrap" aria-label="Nuestra comunidad">
        <div><ShieldCheck aria-hidden="true" /><span>La confianza primero<small>Verificación de identidad</small></span></div>
        <div><Banknote aria-hidden="true" /><span>Las cosas claras<small>Servicios con tarifas definidas</small></span></div>
        <div><Users aria-hidden="true" /><span>El talento, más cerca<small>Zonas de cobertura en Honduras</small></span></div>
      </div>

      <section className="landing-services landing-wrap landing-section" id="servicios" aria-labelledby="services-title">
        <div className="landing-section-heading"><div><h2 id="services-title">Cada pendiente tiene<br />a alguien que sabe.</h2></div><p>Tu casa está llena de proyectos.<br />Empieza por el que necesitas resolver hoy.</p></div>
        <ul className="landing-service-grid">{services.map(({ icon: Icon, name, text, tone }) => <li className={`landing-service landing-service--${tone}`} key={name}><span className="landing-service-icon"><Icon size={26} strokeWidth={1.6} aria-hidden="true" /></span><h3>{name}</h3><p>{text}</p></li>)}</ul>
        <div className="landing-services-bottom"><p>¿Tu talento está en uno de estos oficios?</p><Link className="landing-text-link" to={authenticated ? '/mi-cuenta' : '/registro'} state={{ role: 'TECNICO' }}>Crea tu perfil de técnico<ArrowUpRight size={16} aria-hidden="true" /></Link></div>
      </section>

      <section className="landing-how" id="como-funciona" aria-labelledby="how-title"><div className="landing-wrap landing-section">
        <div className="landing-section-heading"><div><h2 id="how-title">Empezar es más sencillo<br />de lo que imaginas.</h2></div><Link className="landing-text-link" to={clientPath}>Vamos a comenzar<ArrowRight size={17} aria-hidden="true" /></Link></div>
        <ol className="landing-steps">
          <li><span className="landing-step-number">01</span><h3>Elige cómo conectar.</h3><p>Crea tu cuenta para buscar ayuda o para ofrecer tu talento. Después puedes agregar el otro rol.</p></li>
          <li><span className="landing-step-number">02</span><h3>Preséntate con confianza.</h3><p>Completa tu perfil y verifica tu identidad. La confianza se construye desde el primer paso.</p></li>
          <li><span className="landing-step-number">03</span><h3>Prepara tu espacio.</h3><p>Entra a tu cuenta. Si eres técnico, agrega tus servicios, tarifas y zonas de trabajo.</p></li>
        </ol>
      </div></section>

      <section className="landing-technicians landing-wrap landing-section" id="para-tecnicos" aria-labelledby="technicians-title">
        <div className="landing-trade-art" aria-hidden="true"><div className="landing-art-grid" /><span className="landing-art-label">LO QUE SABES HACER, VALE.</span><span className="landing-tool landing-tool--wrench"><Wrench size={94} strokeWidth={1.25} /></span><span className="landing-tool landing-tool--hammer"><Hammer size={80} strokeWidth={1.25} /></span><span className="landing-art-note"><BadgeCheck size={21} />Tu talento tiene su lugar.</span></div>
        <div className="landing-technicians-copy"><h2 id="technicians-title">Tu próximo proyecto<br />empieza contigo.</h2><p>Sabes hacer bien tu trabajo. Dale un espacio a esa experiencia con un perfil que cuente lo que ofreces y dónde trabajas.</p><ul><li><Check size={17} aria-hidden="true" />Muestra tus especialidades y servicios.</li><li><Check size={17} aria-hidden="true" />Define tus tarifas y tu disponibilidad.</li><li><Check size={17} aria-hidden="true" />Indica los municipios donde trabajas.</li></ul><Link className="landing-button" to={authenticated ? '/mi-cuenta' : '/registro'} state={{ role: 'TECNICO' }}>Soy técnico, quiero comenzar<ArrowRight size={18} aria-hidden="true" /></Link></div>
      </section>

      <section className="landing-faq landing-wrap landing-section" aria-labelledby="faq-title"><div><p className="landing-eyebrow">ANTES DE EMPEZAR</p><h2 id="faq-title">Las preguntas<br />son bienvenidas.</h2><p>Conocer el proceso también<br />es parte de la confianza.</p></div><div className="landing-faq-list">{questions.map(([question, answer]) => <details key={question}><summary>{question}<ChevronDown size={19} aria-hidden="true" /></summary><p>{answer}</p></details>)}</div></section>

      <section className="landing-final landing-wrap" aria-labelledby="final-title"><div><p className="landing-eyebrow">CONEXIONES QUE RESUELVEN</p><h2 id="final-title">Menos pendientes.<br />Más manos a la obra.</h2></div><div><Link className="landing-button" to={clientPath}>Empieza con tu cuenta<ArrowRight size={18} aria-hidden="true" /></Link><p>Un perfil. Ambas posibilidades.</p></div></section>
    </main>
    <footer className="landing-footer landing-wrap"><Brand /><p>Talento local. Soluciones reales.<br /><span>© {new Date().getFullYear()} TekniGO · Honduras</span></p><nav aria-label="Enlaces de pie de página"><a href="#servicios">Servicios</a><a href="#como-funciona">Cómo funciona</a><Link to={authenticated ? '/mi-cuenta' : '/ingresar'}>{authenticated ? 'Mi cuenta' : 'Iniciar sesión'}</Link></nav></footer>
  </div>
}
