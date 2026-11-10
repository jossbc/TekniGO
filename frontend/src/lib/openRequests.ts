import type { User } from "firebase/auth";
import { api } from "./api";

export type SpecialtyOption = {
  id: string;
  nombre: string;
};

export type SuggestedRangeUnitItem = {
  unidad_cobro: string;
  tarifa_promedio: number;
  tarifa_minima: number;
  tarifa_maxima: number;
  rango_sugerido_min: number;
  rango_sugerido_max: number;
  cantidad_servicios: number;
};

export type SuggestedRangeResponse = {
  especialidad_id: string;
  tiene_referencia: boolean;
  unidad_cobro: string | null;
  tarifa_minima: number | null;
  tarifa_maxima: number | null;
  tarifa_promedio: number | null;
  rango_sugerido_min: number | null;
  rango_sugerido_max: number | null;
  servicios_comparables: number;
  mensaje: string;
  unidades?: SuggestedRangeUnitItem[];
};

export type CreateOpenRequestPayload = {
  especialidad_id: string;
  descripcion: string;
  municipio: string;
  sector: string;
  direccion_exacta: string;
  fecha_hora: string;
  metodo_pago: "efectivo" | "tarjeta";
  presupuesto: number;
};

export type PublishedOpenRequestResponse = {
  id: string;
  solicitud_id: string;
  modalidad: "ABIERTA";
  estado: "PUBLICADA" | string;
  cliente_id: string;
  tecnico_id: string | null;
  especialidad_id: string;
  especialidad: { id: string; nombre: string | null };
  descripcion: string;
  municipio: string;
  sector: string;
  direccion_exacta: string;
  fecha_hora: string | null;
  metodo_pago: string;
  presupuesto: number;
  monto_base: number;
  creada_en: string | null;
  mensaje?: string;
};

export async function fetchSpecialtiesForOpenRequest(user: User): Promise<SpecialtyOption[]> {
  return api<SpecialtyOption[]>("/specialties", user);
}

export async function publishOpenRequestApi(
  user: User,
  payload: CreateOpenRequestPayload,
): Promise<PublishedOpenRequestResponse> {
  return api<PublishedOpenRequestResponse>("/open-requests", user, payload, "POST");
}

export async function fetchSuggestedRangeForSpecialty(
  user: User,
  especialidadId: string,
): Promise<SuggestedRangeResponse | null> {
  try {
    return await api<SuggestedRangeResponse>(
      `/open-requests/suggested-range?especialidad_id=${encodeURIComponent(especialidadId)}`,
      user,
    );
  } catch {
    return null;
  }
}
