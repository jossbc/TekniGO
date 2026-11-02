import { useId, useState, type InputHTMLAttributes, type ButtonHTMLAttributes } from 'react'
import { Eye, EyeOff } from 'lucide-react'
import Logo from './Logo'
import { Link } from 'react-router-dom'
import { normalizeHonduranPhone, PHONE_HINT } from '../lib/validation'

export function Brand() {
  return <Link to="/" className="brand" aria-label="TekniGO, inicio"><Logo /></Link>
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

export function PasswordField({ autoComplete = 'current-password', name = 'password', label = 'Contraseña' }: { autoComplete?: string; name?: string; label?: string }) {
  const [visible, setVisible] = useState(false)
  const id = useId()
  return <div className="field"><label htmlFor={id}>{label}</label><div className="password-input"><input id={id} name={name} type={visible ? 'text' : 'password'} minLength={8} required autoComplete={autoComplete} placeholder={name === 'passwordConfirmation' ? 'Repite tu contraseña' : 'Tu contraseña'}/><button type="button" aria-label={`${visible ? 'Ocultar' : 'Mostrar'} ${label.toLowerCase()}`} aria-pressed={visible} onClick={() => setVisible(!visible)}>{visible ? <EyeOff size={19}/> : <Eye size={19}/>}</button></div>{autoComplete === 'new-password' && name === 'password' && <small>Usa al menos 8 caracteres.</small>}</div>
}
