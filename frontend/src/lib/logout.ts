import { signOut } from 'firebase/auth'
import { auth } from './firebase'
import { api, ApiError } from './api'

export async function logoutCurrentSession(): Promise<void> {
  if (auth?.currentUser) {
    try { await api('/auth/logout', auth.currentUser, {}) }
    catch (error) {
      // A revoked/expired session can still be cleared from this browser.
      const code = (error as { code?: string })?.code
      const expired = error instanceof ApiError && error.status === 401
      const invalidIdentity = ['auth/user-token-expired', 'auth/user-disabled', 'auth/user-not-found', 'auth/invalid-user-token'].includes(code || '')
      if (!expired && !invalidIdentity) throw error
    }
  }
  if (auth) await signOut(auth)
}
