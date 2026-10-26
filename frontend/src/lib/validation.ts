export const PHONE_HINT = 'Ejemplo: 9819-7602 o +504 9819-7602.'

export function normalizeHonduranPhone(value: string): string {
  const normalized = value.replace(/[\s()-]/g, '')
  if (!/^(?:\+504)?[389][0-9]{7}$/.test(normalized)) {
    throw new Error('Ingresa un celular hondureño de 8 dígitos que comience con 9, 8 o 3. Puedes usar guion y el prefijo +504.')
  }
  return normalized.startsWith('+504') ? normalized : `+504${normalized}`
}

export function normalizeEmail(value: string): string {
  const normalized = value.trim().toLowerCase()
  if (!normalized) throw new Error('Ingresa tu correo')
             if (normalized.length > 254 || !/^[^@\s]+@(?:[a-z0-9-]+\.)+(?:com|net|org|edu|gov|info|biz|io|dev|app|hn)$/.test(normalized)) {
    throw new Error('Ingresa un correo válido')
  }
  return normalized
}


export const ALLOWED_IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/webp'] as const
export const MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024
export const IMAGE_RULES_HINT = 'Formatos permitidos: JPG, PNG o WEBP. Tamaño máximo: 5 MB por archivo.'

export function validateIdentityImage(file: File | null | undefined, label: string): string {
  if (!file) return `Debes adjuntar ${label}.`
  if (file.size <= 0) return `El archivo de ${label} está vacío.`
  if (!ALLOWED_IMAGE_TYPES.includes(file.type as (typeof ALLOWED_IMAGE_TYPES)[number])) {
    return `El formato de ${label} no es válido. Usa JPG, PNG o WEBP.`
  }
  if (file.size > MAX_IMAGE_SIZE_BYTES) {
    return `El archivo de ${label} supera el tamaño máximo de 5 MB.`
  }
  return ''
}
