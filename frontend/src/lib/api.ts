import type { User } from 'firebase/auth'

export type Profile = {
  id: string
  nombre_completo: string
  email: string
  dni: string
  telefono: string
  roles: ('CLIENTE' | 'TECNICO' | 'ADMIN')[]
  estado_identidad: string
}

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message)
    this.name = 'ApiError'
  }
}

export async function api<T>(path: string, user: User, body?: unknown, method?: string): Promise<T> {

  const token = await user.getIdToken()
  
  const httpMethod = method || (body === undefined ? 'GET' : 'POST')

  const options: RequestInit = {
    method: httpMethod,
    cache: 'no-store',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
  }

  // Asegura que no se envíe un body si el método es GET o HEAD
  if (body !== undefined && httpMethod !== 'GET' && httpMethod !== 'HEAD') {
    options.body = JSON.stringify(body)
  }

  const url = `${import.meta.env.VITE_API_URL || 'http://localhost:8000'}${path}`
  let response = await fetch(url, options)
  if (response.status === 401) {
    // Recover an expired token once; a revoked session still requires sign-in.
    const refreshedToken = await user.getIdToken(true)
    options.headers = { ...options.headers, Authorization: `Bearer ${refreshedToken}` }
    response = await fetch(url, options)
  }


  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    const detail = typeof data.detail === 'string' ? data.detail :
      Array.isArray(data.detail) ? data.detail.map((item: {msg: string}) => item.msg.replace(/^Value error, /, '')).join('. ') : 'No pudimos completar la solicitud.'
    throw new ApiError(response.status, detail)
  }
  return response.status === 204 ? undefined as T : response.json()
}

export function errorMessage(error: unknown) {
  if (error instanceof TypeError && /fetch|network/i.test(error.message)) {
    return 'No pudimos conectar con TekniGO. Revisa tu conexión e intenta nuevamente.'
  }
  const code = (error as {code?: string})?.code
  const messages: Record<string, string> = {
    'auth/invalid-credential': 'El correo o la contraseña no son correctos.',
    'auth/email-already-in-use': 'Este correo ya tiene una cuenta. Inicia sesión para continuar tu registro o agregar el otro rol.',
    'auth/weak-password': 'La contraseña no cumple la política de seguridad del proyecto.',
    'auth/password-does-not-meet-requirements': 'La contraseña no cumple la política de seguridad del proyecto.',
    'auth/invalid-email': 'Revisa el formato de tu correo.',
    'auth/too-many-requests': 'Se realizaron demasiados intentos. Espera un momento.',
    'auth/network-request-failed': 'No hay conexión con el servicio de acceso. Intenta nuevamente.',
    'auth/operation-not-allowed': 'El método de acceso solicitado no está habilitado en Firebase.',
    'auth/invalid-phone-number': 'Revisa el número de teléfono guardado en tu perfil.',
    'auth/invalid-verification-code': 'El código SMS no es correcto. Revisa los 6 dígitos.',
    'auth/code-expired': 'El código SMS venció. Solicita uno nuevo.',
    'auth/invalid-verification-id': 'La solicitud de SMS venció. Solicita un código nuevo.',
    'auth/credential-already-in-use': 'Ese teléfono está vinculado a otra cuenta. Revisa tu número.',
    'auth/requires-recent-login': 'Por seguridad, cierra sesión y vuelve a ingresar antes de verificar tu teléfono.',
    'auth/captcha-check-failed': 'No pudimos completar la comprobación de seguridad. Intenta nuevamente.',
    'auth/unauthorized-domain': 'Este dominio necesita autorización en Firebase para verificar teléfonos.',
    'auth/quota-exceeded': 'El servicio alcanzó su límite de SMS. Intenta más tarde.',
  }
  return code ? messages[code] || 'No pudimos verificar tu acceso. Intenta nuevamente.' :
    error instanceof Error ? error.message : 'Ocurrió un problema. Intenta nuevamente.'
}
