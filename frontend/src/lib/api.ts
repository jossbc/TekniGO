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

export function resolveApiUrl(path: string): string {
  const configured = (import.meta.env?.VITE_API_URL || '').trim().replace(/\/$/, '')
  const isLocal = /^https?:\/\/(?:localhost|127\.0\.0\.1)(?::\d+)?$/i.test(configured)
  const base = configured && !isLocal ? configured : ''
  return `${base}${path}`
}

export async function api<T>(path: string, user: User, body?: unknown, method?: string): Promise<T> {
  const httpMethod = method || (body === undefined ? 'GET' : 'POST')
  const readOnly = httpMethod === 'GET' || httpMethod === 'HEAD'
  // Technical document submission also runs identity evaluation after storing the files.
  const timeoutMs = httpMethod === 'POST' && path === '/technicians/me/verification-documents'
    ? 360_000
    : !readOnly && path.endsWith('/identity-verification') ? 240_000 : 30_000
  const controller = new AbortController()
  let timer: ReturnType<typeof setTimeout> | undefined
  const deadline = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      reject(new ApiError(504, readOnly
        ? 'TekniGO tardó demasiado en responder. Intenta nuevamente.'
        : 'TekniGO tardó demasiado en confirmar el cambio. Revisa si se guardó antes de volver a enviarlo.'))
      controller.abort()
    }, timeoutMs)
  })
  try {
    return await Promise.race([request<T>(path, user, body, httpMethod, controller.signal), deadline])
  } finally {
    clearTimeout(timer)
  }
}

async function request<T>(path: string, user: User, body: unknown, httpMethod: string, signal: AbortSignal): Promise<T> {
  const token = await user.getIdToken()
  signal.throwIfAborted()
  const multipart = typeof FormData !== 'undefined' && body instanceof FormData
  const headers: Record<string, string> = { Authorization: `Bearer ${token}` }
  if (!multipart) headers['Content-Type'] = 'application/json'

  const options: RequestInit = {
    method: httpMethod,
    signal,
    cache: 'no-store',
    headers,
  }

  // Asegura que no se envíe un body si el método es GET o HEAD
  if (body !== undefined && httpMethod !== 'GET' && httpMethod !== 'HEAD') {
    options.body = multipart ? body : JSON.stringify(body)
  }

  const url = resolveApiUrl(path)
  let response = await fetch(url, options)
  if (response.status === 401) {
    // Recover an expired token once; a revoked session still requires sign-in.
    const refreshedToken = await user.getIdToken(true)
    signal.throwIfAborted()
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
