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

export type OpportunityStateFilter = "TODOS" | "PUBLICADA" | "CON_OFERTAS";

export type TechnicianCoverageZoneItem = {
  departamento: string;
  municipio: string;
  sector: string;
};

export type TechnicianProposalSummary = {
  id: string;
  tipo: string;
  monto: number;
  estado: "PENDIENTE" | "ACEPTADA" | "RECHAZADA" | "CANCELADA" | "RETIRADA" | string;
  creada_en: string | null;
};

export type TechnicianOpportunityItem = {
  id: string;
  solicitud_id: string;
  modalidad: "ABIERTA";
  estado: string;
  especialidad_id: string;
  especialidad: { id: string; nombre: string | null };
  descripcion: string;
  municipio: string;
  sector: string;
  direccion_exacta?: string;
  fecha_hora: string | null;
  metodo_pago: string;
  presupuesto: number;
  monto_base: number;
  creada_en: string | null;
  cliente: {
    nombre_completo: string | null;
    telefono?: string | null;
  };
  ubicacion_exacta_protegida: boolean;
  mi_propuesta?: TechnicianProposalSummary | null;
};

export type AvailableOpportunitiesQuery = {
  especialidad_id?: string;
  municipio?: string;
  sector?: string;
  estado?: OpportunityStateFilter;
  offset?: number;
  limit?: number;
};

export type AvailableOpportunitiesResponse = {
  total: number;
  offset: number;
  limit: number;
  filtros_aplicados?: {
    especialidad_id: string | null;
    municipio: string | null;
    sector: string | null;
    estado: string;
  };
  diagnostico_cobertura?: {
    tiene_especialidades: boolean;
    cantidad_especialidades: number;
    tiene_zonas_cobertura: boolean;
    cantidad_zonas_cobertura: number;
  };
  mensaje?: string | null;
  solicitudes: TechnicianOpportunityItem[];
};

export async function fetchCoverageZonesCatalog(
  user: User,
  params?: { especialidad_id?: string; municipio?: string },
): Promise<TechnicianCoverageZoneItem[]> {
  try {
    const query = new URLSearchParams();
    if (params?.especialidad_id) query.set("especialidad_id", params.especialidad_id);
    if (params?.municipio) query.set("municipio", params.municipio);
    const qs = query.toString();
    return await api<TechnicianCoverageZoneItem[]>(
      `/open-requests/coverage-zones${qs ? `?${qs}` : ""}`,
      user,
    );
  } catch {
    return [];
  }
}

export type DiscardOpportunityResponse = {
  solicitud_id: string;
  descartada: boolean;
  descartada_en: string | null;
  estado_solicitud: string;
  mensaje: string;
};

export async function fetchMyTechnicianSpecialties(user: User): Promise<SpecialtyOption[]> {
  return api<SpecialtyOption[]>("/technicians/me/specialties", user);
}

export async function fetchMyTechnicianCoverageZones(user: User): Promise<TechnicianCoverageZoneItem[]> {
  return api<TechnicianCoverageZoneItem[]>("/technicians/me/coverage-zones", user);
}

export async function listAvailableOpportunitiesApi(
  user: User,
  query: AvailableOpportunitiesQuery = {},
): Promise<AvailableOpportunitiesResponse> {
  const params = new URLSearchParams();
  if (query.especialidad_id) params.set("especialidad_id", query.especialidad_id);
  if (query.municipio) params.set("municipio", query.municipio);
  if (query.sector) params.set("sector", query.sector);
  if (query.estado) params.set("estado", query.estado);
  params.set("offset", String(query.offset ?? 0));
  params.set("limit", String(query.limit ?? 10));
  return api<AvailableOpportunitiesResponse>(`/open-requests/available?${params.toString()}`, user);
}

export async function getOpportunityDetailApi(
  user: User,
  solicitudId: string,
): Promise<TechnicianOpportunityItem> {
  return api<TechnicianOpportunityItem>(`/open-requests/${encodeURIComponent(solicitudId)}`, user);
}

export async function discardOpportunityApi(
  user: User,
  solicitudId: string,
): Promise<DiscardOpportunityResponse> {
  return api<DiscardOpportunityResponse>(
    `/open-requests/${encodeURIComponent(solicitudId)}/discard`,
    user,
    {},
    "POST",
  );
}

export type AcceptPublishedBudgetResponse = {
  propuesta: {
    id: string;
    propuesta_id: string;
    solicitud_id: string;
    usuario_id: string;
    tecnico_id: string;
    tipo: string;
    monto: number;
    estado: string;
    activa: boolean;
    creada_en: string | null;
  };
  solicitud: TechnicianOpportunityItem;
  estado_solicitud: string;
  reintento_idempotente: boolean;
  mensaje: string;
};

export async function acceptPublishedBudgetApi(
  user: User,
  solicitudId: string,
): Promise<AcceptPublishedBudgetResponse> {
  return api<AcceptPublishedBudgetResponse>(
    `/open-requests/${encodeURIComponent(solicitudId)}/accept-budget`,
    user,
    {},
    "POST",
  );
}
