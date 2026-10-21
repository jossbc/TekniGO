import { useId, useState, type InputHTMLAttributes, type ButtonHTMLAttributes } from 'react'
import { Eye, EyeOff, Wrench } from 'lucide-react'
import { Link } from 'react-router-dom'
import { normalizeHonduranPhone, PHONE_HINT } from '../lib/validation'

export function Brand() {
  return <Link to="/" className="brand" aria-label="TekniGO, inicio"><span className="brand-symbol"><Wrench size={23}/></span><span>Tekni<span className="brand-go">GO</span><small>CONEXIONES QUE RESUELVEN</small></span></Link>
}

export function Button({ className = '', ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button className={`button ${className}`} {...props}/>
}

export function Field({ label, hint, ...props }: InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string }) {
  const generated = useId()
  const id = props.id || generated
  return <div className="field"><label htmlFor={id}>{label}</label><input {...props} id={id} aria-describedby={hint ? `${id}-hint` : undefined}/>{hint && <small id={`${id}-hint`}>{hint}</small>}</div>
}

export function PhoneField() {
  return <Field label="Teléfono celular" name="phone" type="tel" autoComplete="tel"
    placeholder="9819-7602" hint={PHONE_HINT} required
    onChange={event => {
      const input = event.currentTarget
      input.setCustomValidity('')
      if (!input.value) return
      try { normalizeHonduranPhone(input.value) }
      catch (error) { input.setCustomValidity((error as Error).message) }
    }}/>
}

export function PasswordField({ autoComplete = 'current-password' }: { autoComplete?: string }) {
  const [visible, setVisible] = useState(false)
  const id = useId()
  return <div className="field"><label htmlFor={id}>Contraseña</label><div className="password-input"><input id={id} name="password" type={visible ? 'text' : 'password'} minLength={8} required autoComplete={autoComplete} placeholder="Tu contraseña"/><button type="button" aria-label={visible ? 'Ocultar contraseña' : 'Mostrar contraseña'} aria-pressed={visible} onClick={() => setVisible(!visible)}>{visible ? <EyeOff size={19}/> : <Eye size={19}/>}</button></div>{autoComplete === 'new-password' && <small>Usa al menos 6 caracteres. Se aplica la política de seguridad de la cuenta.</small>}</div>
}
