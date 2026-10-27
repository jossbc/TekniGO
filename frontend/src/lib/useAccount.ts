import { useEffect, useState } from 'react'
import type { User } from 'firebase/auth'
import { loadAccount, type AccountState } from './account'
import type { Profile } from './api'

export function useAccount(user: User) {
  const [result, setResult] = useState<{ user: User; attempt: number; state: AccountState }>()
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let active = true
    loadAccount(user).then((state) => {
      if (!active) return
      setResult({ user, attempt, state })
    })
    return () => { active = false }
  }, [user, attempt])
  const state: AccountState = result?.user === user && result.attempt === attempt
    ? result.state : { status: 'loading' }
  return {
    state,
    retry: () => setAttempt((value) => value + 1),
    updateProfile: (profile: Profile) => setResult({ user, attempt, state: { status: 'ready', profile } }),
  }
}
