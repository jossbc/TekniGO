import { PhoneAuthProvider, updatePhoneNumber, type User, type ApplicationVerifier } from 'firebase/auth'
import { auth } from './firebase'
import { api, errorMessage } from './api'

export type PhoneStatus = { telefono: string; telefono_verificado: boolean }

export function phoneVerificationErrorMessage(error: unknown): string {
  if ((error as { code?: string })?.code === 'auth/operation-not-allowed') {
    return 'La verificación del teléfono no está disponible en este momento. Intenta más tarde.'
  }
  return errorMessage(error)
}

export async function sendPhoneCode(user: User, verifier: ApplicationVerifier): Promise<string> {
  if (!auth || auth.currentUser?.uid !== user.uid) throw new Error('Vuelve a iniciar sesión para verificar tu teléfono.')
  // The destination is always the current registered number, never a form field.
  const status = await api<PhoneStatus>('/users/me/phone-verification', user)
  return new PhoneAuthProvider(auth).verifyPhoneNumber(status.telefono, verifier)
}

export async function confirmPhoneCode(user: User, verificationId: string, code: string): Promise<PhoneStatus> {
  if (!/^\d{6}$/.test(code)) throw new Error('Ingresa el código de 6 dígitos.')
  const credential = PhoneAuthProvider.credential(verificationId, code)
  // Update the existing identity; signing in with the SMS could create another account.
  await updatePhoneNumber(user, credential)
  await user.getIdToken(true)
  return api<PhoneStatus>('/users/me/phone-verification', user, {})
}
