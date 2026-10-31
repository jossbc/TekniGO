import type { User } from 'firebase/auth'
import { api, type Profile } from './api'

type IdentityDocuments = {
  estado_identidad: string
  documentos: { tipo: string }[]
}

export async function registrationRequirements(user: User, profile: Profile) {
  const [identity, specialties, zones] = await Promise.all([
    api<IdentityDocuments>('/users/me/identity-documents', user),
    profile.roles.includes('TECNICO') ? api<unknown[]>('/technicians/me/specialties', user) : Promise.resolve([true]),
    profile.roles.includes('TECNICO') ? api<unknown[]>('/technicians/me/coverage-zones', user) : Promise.resolve([true]),
  ])
  const identityReady = identity.estado_identidad === 'VERIFICADO' ||
    (identity.estado_identidad === 'EN_REVISION' &&
      ['DNI_FRONTAL', 'DNI_REVERSO', 'SELFIE'].every((tipo) => identity.documentos.some((doc) => doc.tipo === tipo)))
  return { identityReady, specialtiesReady: specialties.length > 0, zonesReady: zones.length > 0 }
}

export function registrationComplete(requirements: Awaited<ReturnType<typeof registrationRequirements>>) {
  return requirements.identityReady && requirements.specialtiesReady && requirements.zonesReady
}
