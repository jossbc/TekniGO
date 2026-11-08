import { sendEmailVerification, type User } from 'firebase/auth'
import { api } from './api'

export type EmailVerificationStatus = { email: string; email_verified: boolean }
export const EMAIL_RESEND_SECONDS = 60
export const EMAIL_CHECK_INTERVAL_MS = 5000

function storageKey(user: User) {
  return `teknigo:verification-email:${user.uid}:${user.email?.toLowerCase()}`
}

export function resendWaitSeconds(user: User): number {
  try {
    const sentAt = Number(sessionStorage.getItem(storageKey(user)))
    return Number.isFinite(sentAt) ? Math.max(0, Math.ceil((sentAt + EMAIL_RESEND_SECONDS * 1000 - Date.now()) / 1000)) : 0
  } catch { return 0 }
}

export async function requestEmailVerification(user: User): Promise<boolean> {
  await user.reload()
  if (user.emailVerified) return false
  if (!user.email) throw new Error('Tu cuenta no tiene un correo electrónico registrado.')
  const remaining = resendWaitSeconds(user)
  if (remaining) throw new Error(`Espera ${remaining} segundos antes de reenviar el enlace.`)
  // Firebase's hosted action handler validates the link; no custom token handling.
  await sendEmailVerification(user)
  try { sessionStorage.setItem(storageKey(user), String(Date.now())) } catch { /* Firebase also applies quotas. */ }
  return true
}

export async function refreshEmailVerification(user: User): Promise<EmailVerificationStatus> {
  await user.reload()
  await user.getIdToken(true)
  return api<EmailVerificationStatus>('/users/me/email-verification', user)
}

export function watchEmailVerification(
  user: User,
  onStatus: (status: EmailVerificationStatus) => void,
  onError: (error: unknown) => void,
): () => void {
  let active = true
  let checking = false

  async function check() {
    if (!active || checking || document.visibilityState === 'hidden' || !navigator.onLine) return
    checking = true
    try {
      const status = await refreshEmailVerification(user)
      if (!active) return
      if (status.email_verified) stop()
      onStatus(status)
    } catch (error) {
      if (active) onError(error)
    } finally { checking = false }
  }

  const timer = window.setInterval(check, EMAIL_CHECK_INTERVAL_MS)
  window.addEventListener('focus', check)
  window.addEventListener('online', check)
  document.addEventListener('visibilitychange', check)
  function stop() {
    active = false
    window.clearInterval(timer)
    window.removeEventListener('focus', check)
    window.removeEventListener('online', check)
    document.removeEventListener('visibilitychange', check)
  }
  void check()
  return stop
}
