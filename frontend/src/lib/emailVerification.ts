import { sendEmailVerification, type User } from 'firebase/auth'
import { api } from './api'

export type EmailVerificationStatus = { email: string; email_verified: boolean }
export const EMAIL_RESEND_SECONDS = 60

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
