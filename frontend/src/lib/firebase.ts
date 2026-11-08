import { initializeApp } from 'firebase/app'
import { getAuth } from 'firebase/auth'

const config = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
  appId: import.meta.env.VITE_FIREBASE_APP_ID,
}
export const auth = Object.values(config).every(Boolean) ? getAuth(initializeApp(config)) : null
export const phoneTestMode = import.meta.env.DEV === true && import.meta.env.VITE_FIREBASE_PHONE_TEST_MODE === 'true'
if (auth) {
  auth.languageCode = 'es'
  // Firebase still validates the number/code configured in its console.
  // Production builds always retain the real app verification flow.
  auth.settings.appVerificationDisabledForTesting = phoneTestMode
}
