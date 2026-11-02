import { BadgeCheck, Banknote, ShieldCheck, Users } from 'lucide-react'
import technicianPhoto from '../assets/technician.jpg'

export default function AccessStory() {
  return <section className="access-story" aria-labelledby="story-title">
    <div className="access-story__copy">
      <h1 id="story-title">Contrata técnicos en los que <span>sí puedes confiar.</span></h1>
      <p>Conoce sus servicios y tarifas, y encuentra ayuda para tu hogar en un solo lugar.</p>
    </div>
    <div className="access-story__portrait">
      <img src={technicianPhoto} alt="Técnico con uniforme azul sosteniendo una llave de ajuste" width="4693" height="3635" fetchPriority="high" />
      <div className="access-story__card">
        <span className="access-story__card-icon" aria-hidden="true"><BadgeCheck size={24} /></span>
        <div><strong>Una buena conexión empieza aquí.</strong><span>Encuentra el talento que tu hogar necesita.</span></div>
      </div>
    </div>
    <ul className="access-story__trust" aria-label="Ventajas de TekniGO">
      <li><ShieldCheck size={16} aria-hidden="true" />Identidad verificada</li>
      <li><Users size={16} aria-hidden="true" />Contacto directo</li>
      <li><Banknote size={16} aria-hidden="true" />Tarifas visibles</li>
    </ul>
  </section>
}
