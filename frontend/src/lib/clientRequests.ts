import type { User } from "firebase/auth";
import { api } from "./api";

export type ModalidadFiltro = "TODOS" | "DIRECTA" | "ABIERTA";
export type EstadoFiltro =
  | "TODOS"
  | "PUBLICADA"
  | "CON OFERTAS"
  | "PENDIENTE"
  | "ACEPTADA"
  | "EN_PROCESO"
  | "RECHAZADA"
  | "CANCELADA"
  | "COMPLETADA";

export type ClientRequestServiceInfo = {
  id: string;
  nombre: string | null;
  especialidad_id: string | null;
};

export type ClientRequestTechnicianInfo = {
  usuario_id: string | null;
  firebase_uid: string;
  nombre_completo: string | null;
  estado_verificacion?: string | null;
  calificacion_promedio?: number | null;
};

export type ClientRequestSummary = {
  id: string;
  modalidad: "DIRECTA" | "ABIERTA";
  estado: "PENDIENTE" | "ACEPTADA" | "EN_PROCESO" | "EN PROCESO" | "RECHAZADA" | "CANCELADA" | "COMPLETADA" | string;
  fecha_hora: string | null;
  creada_en: string | null;
  finalizacion_reportada_en?: string | null;
  vencimiento_confirmacion_en?: string | null;
  finalizacion_reportada_local?: string | null;
  vencimiento_confirmacion_local?: string | null;
  pendiente_recepcion?: boolean;
  presupuesto: number | null;
  monto_base: number | null;
  cantidad: number | null;
  tarifa_aplicada: number | null;
  unidad_cobro: string | null;
  metodo_pago: string | null;
  municipio: string | null;
  sector: string | null;
  direccion_exacta: string | null;
  descripcion: string | null;
  servicio: ClientRequestServiceInfo | null;
  tecnico: ClientRequestTechnicianInfo | null;
};

export type ClientRequestProposal = {
  servicio_id: string | null;
  servicio_nombre: string | null;
  cantidad: number | null;
  tarifa_aplicada: number | null;
  unidad_cobro: string | null;
  monto_pactado: number | null;
  metodo_pago: string | null;
};

export type ClientRequestTimelineItem = {
  estado: string;
  descripcion: string;
  fecha: string | null;
};

export type ReceivedOpenRequestProposal = {
  id: string;
  propuesta_id: string;
  solicitud_id: string;
  usuario_id: string;
  tecnico_id: string;
  tipo: string;
  monto: number;
  justificacion: string | null;
  estado: string;
  activa: boolean;
  elegible_para_adjudicar?: boolean;
  creada_en: string | null;
  tecnico: {
    usuario_id: string;
    firebase_uid: string;
    nombre_completo: string | null;
    estado_verificacion: string | null;
    verificado?: boolean;
    certificado?: boolean;
    certificaciones_aprobadas_count?: number;
    disponible?: boolean;
    habilitado_operativamente?: boolean;
    calificacion_promedio: number | null;
  };
};

export type ClientRequestDetail = ClientRequestSummary & {
  propuesta: ClientRequestProposal;
  linea_tiempo: ClientRequestTimelineItem[];
  acciones_permitidas: string[];
  propuestas_recibidas?: ReceivedOpenRequestProposal[];
  eventos?: {
    evento_id: string;
    tipo: string;
    mensaje?: string;
    finalizacion_reportada_en?: string;
    vencimiento_confirmacion_en?: string;
    finalizacion_reportada_local?: string;
    vencimiento_confirmacion_local?: string;
  }[];
};

export type ClientRequestsPageResponse = {
  total: number;
  offset: number;
  limit: number;
  modalidad: ModalidadFiltro;
  estado: EstadoFiltro;
  solicitudes: ClientRequestSummary[];
};

export const MODALIDAD_OPTIONS: { value: ModalidadFiltro; label: string }[] = [
  { value: "TODOS", label: "Todas" },
  { value: "DIRECTA", label: "Directas" },
  { value: "ABIERTA", label: "Abiertas" },
];

export const ESTADO_OPTIONS: { value: EstadoFiltro; label: string }[] = [
  { value: "TODOS", label: "Todos los estados" },
  { value: "PUBLICADA", label: "Publicada" },
  { value: "CON OFERTAS", label: "Con ofertas" },
  { value: "PENDIENTE", label: "Pendiente" },
  { value: "ACEPTADA", label: "Aceptada" },
  { value: "EN_PROCESO", label: "En proceso" },
  { value: "COMPLETADA", label: "Completada" },
  { value: "CANCELADA", label: "Cancelada" },
  { value: "RECHAZADA", label: "Rechazada" },
];

export function formatRequestStateLabel(estado: string, pendienteRecepcion?: boolean): string {
  const normalized = (estado || "").trim().toUpperCase();
  if ((normalized === "EN_PROCESO" || normalized === "EN PROCESO") && pendienteRecepcion) {
    return "En proceso · Pendiente de recepción";
  }
  const map: Record<string, string> = {
    PUBLICADA: "Publicada",
    "CON OFERTAS": "Con ofertas",
    CON_OFERTAS: "Con ofertas",
    PENDIENTE: "Pendiente",
    ACEPTADA: "Aceptada",
    EN_PROCESO: "En proceso",
    "EN PROCESO": "En proceso",
    RECHAZADA: "Rechazada",
    CANCELADA: "Cancelada",
    COMPLETADA: "Completada",
  };
  return map[normalized] || estado;
}

export function formatRequestModalityLabel(modalidad: string): string {
  return modalidad.toUpperCase() === "ABIERTA" ? "Abierta" : "Directa";
}

export function formatLempiras(amount: number | null | undefined): string {
  if (amount === null || amount === undefined || Number.isNaN(Number(amount))) {
    return "Por definir";
  }
  return `L ${Number(amount).toLocaleString("es-HN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export function formatUnitLabel(unidad: string | null | undefined): string {
  if (!unidad) return "";
  const map: Record<string, string> = {
    POR_HORA: "por hora",
    POR_SERVICIO: "por servicio",
    POR_DIA: "por día",
    POR_METRO: "por metro",
  };
  return map[unidad.toUpperCase()] || unidad.replaceAll("_", " ").toLowerCase();
}

export async function listMyRequests(
  user: User,
  params: {
    modalidad?: ModalidadFiltro;
    estado?: EstadoFiltro;
    offset?: number;
    limit?: number;
  } = {},
): Promise<ClientRequestsPageResponse> {
  const search = new URLSearchParams();
  search.set("modalidad", params.modalidad || "TODOS");
  search.set("estado", params.estado || "TODOS");
  search.set("offset", String(params.offset ?? 0));
  search.set("limit", String(params.limit ?? 10));
  return api<ClientRequestsPageResponse>(`/users/me/requests?${search.toString()}`, user);
}

export async function getMyRequestDetail(
  user: User,
  solicitudId: string,
): Promise<ClientRequestDetail> {
  return api<ClientRequestDetail>(
    `/users/me/requests/${encodeURIComponent(solicitudId)}`,
    user,
  );
}

export async function executeMyRequestAction(
  user: User,
  solicitudId: string,
  accion: string,
): Promise<ClientRequestDetail> {
  return api<ClientRequestDetail>(
    `/users/me/requests/${encodeURIComponent(solicitudId)}/actions`,
    user,
    { accion },
    "POST",
  );
}

export {
  TEGUCIGALPA_TIME_ZONE,
  formatConfirmationWindowStatus,
  formatTegucigalpaDateTime,
} from "./directRequests";

export async function rejectProposalApi(
  user: User,
  solicitudId: string,
  proposalId: string,
  motivo?: string,
): Promise<{
  mensaje: string;
  solicitud_id: string;
  estado_solicitud: string;
  propuesta: ReceivedOpenRequestProposal;
}> {
  return api(
    `/open-requests/${encodeURIComponent(solicitudId)}/proposals/${encodeURIComponent(proposalId)}/reject`,
    user,
    { motivo: motivo || null },
    "POST",
  );
}

export async function awardProposalApi(
  user: User,
  solicitudId: string,
  proposalId: string,
  payload: {
    metodo_pago?: "efectivo" | "tarjeta";
    referencia_pasarela?: string | null;
    idempotency_key?: string | null;
  } = {},
): Promise<{
  mensaje: string;
  reintento_idempotente: boolean;
  solicitud_id: string;
  estado_solicitud: string;
  tecnico_adjudicado_id: string;
  monto_pactado: number;
  metodo_pago: string;
  referencia_pasarela: string | null;
  propuesta_adjudicada: ReceivedOpenRequestProposal;
  propuestas_descartadas: ReceivedOpenRequestProposal[];
  cantidad_propuestas_descartadas: number;
}> {
  return api(
    `/open-requests/${encodeURIComponent(solicitudId)}/proposals/${encodeURIComponent(proposalId)}/award`,
    user,
    payload,
    "POST",
  );
}
