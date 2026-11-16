import type { User } from "firebase/auth";
import { api } from "./api";

export interface ServiceReview {
  id: string;
  tipo_solicitud: "DIRECTA" | "ABIERTA";
  solicitud_id: string;
  evaluador_id: string;
  evaluador_nombre: string;
  evaluado_id: string;
  rol_evaluador: "CLIENTE" | "TECNICO";
  puntuacion: number;
  comentario: string;
  aspectos_destacados: string[];
  estado_publicacion: "PENDIENTE" | "PUBLICADA";
  fecha: string;
  publicada_en: string | null;
}

export interface ReputationSummary {
  usuario_id: string;
  promedio: number | null;
  total_resenas: number;
  sin_calificacion: boolean;
  distribucion_estrellas: Record<string, number>;
}

export interface ServiceReviewsStatus {
  tipo_solicitud: string;
  solicitud_id: string;
  servicio_completado: boolean;
  mi_resena: ServiceReview | null;
  contraparte_resena: ServiceReview | null;
  contraparte_ha_calificado: boolean;
  estado_publicacion_general: "NINGUNA" | "PENDIENTE" | "PUBLICADA";
}

export interface UserPublicReviewsResponse extends ReputationSummary {
  resenas: ServiceReview[];
}

export interface SubmitReviewResponse {
  resena: ServiceReview;
  estado_publicacion: "PENDIENTE" | "PUBLICADA";
  publicada_inmediata: boolean;
  mensaje: string;
}

export const TECHNICIAN_HIGHLIGHT_CHIPS = [
  "Muy rápido",
  "Confiable",
  "Seguro",
  "Puntual",
  "Trabajo impecable",
];

export const CLIENT_HIGHLIGHT_CHIPS = [
  "Trato amable",
  "Pago puntual",
  "Comunicación clara",
  "Ubicación exacta",
];

export async function submitServiceReview(
  user: User,
  data: {
    tipo_solicitud: "DIRECTA" | "ABIERTA";
    solicitud_id: string;
    puntuacion: number;
    comentario: string;
    aspectos_destacados?: string[];
  },
): Promise<SubmitReviewResponse> {
  return api<SubmitReviewResponse>("/reviews", user, data, "POST");
}

export async function getServiceReviewsStatus(
  user: User,
  tipo_solicitud: string,
  solicitud_id: string,
): Promise<ServiceReviewsStatus> {
  return api<ServiceReviewsStatus>(
    `/reviews/service/${encodeURIComponent(tipo_solicitud)}/${encodeURIComponent(solicitud_id)}`,
    user,
  );
}

export async function getUserReputation(
  user: User,
  usuario_id: string,
): Promise<ReputationSummary> {
  return api<ReputationSummary>(
    `/reviews/users/${encodeURIComponent(usuario_id)}/reputation`,
    user,
  );
}

export async function getUserPublicReviews(
  user: User,
  usuario_id: string,
): Promise<UserPublicReviewsResponse> {
  return api<UserPublicReviewsResponse>(
    `/reviews/users/${encodeURIComponent(usuario_id)}`,
    user,
  );
}

export function formatReviewDate(isoString?: string | null): string {
  if (!isoString) return "";
  try {
    const d = new Date(isoString);
    return new Intl.DateTimeFormat("es-HN", {
      day: "numeric",
      month: "short",
      year: "numeric",
      timeZone: "America/Tegucigalpa",
    }).format(d);
  } catch {
    return isoString;
  }
}
