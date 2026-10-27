import type { User } from 'firebase/auth'
import { api, ApiError, errorMessage, type Profile } from './api'

export type AccountState =
  | { status: 'loading' }
  | { status: 'ready'; profile: Profile }
  | { status: 'missing' }
  | { status: 'unauthorized' }
  | { status: 'error'; message: string }

export async function loadAccount(user: User): Promise<AccountState> {
  try {
    return { status: 'ready', profile: await api<Profile>('/users/me', user) }
  } catch (error) {
    if (error instanceof ApiError && error.status === 404 && error.message === 'Completa tu registro en TekniGO') {
      return { status: 'missing' }
    }
    const code = (error as { code?: string })?.code
    if ((error instanceof ApiError && error.status === 401) ||
        ['auth/user-token-expired', 'auth/invalid-user-token', 'auth/user-disabled'].includes(code || '')) {
      return { status: 'unauthorized' }
    }
    return { status: 'error', message: errorMessage(error) }
  }
}
