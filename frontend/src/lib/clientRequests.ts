import type { User } from "firebase/auth";
import { api } from "./api";

export type ModalidadFiltro = "TODOS" | "DIRECTA" | "ABIERTA";
export type EstadoFiltro =
  | "TODOS"
  | "PENDIENTE"
  | "ACEPTADA"
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
  estado: "PENDIENTE" | "ACEPTADA" | "RECHAZADA" | "CANCELADA" | "COMPLETADA" | string;
  fecha_hora: string | null;
  creada_en: string | null;
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

export type ClientRequestDetail = ClientRequestSummary & {
  propuesta: ClientRequestProposal;
  linea_tiempo: ClientRequestTimelineItem[];
  acciones_permitidas: string[];
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
  { value: "PENDIENTE", label: "Pendiente" },
  { value: "ACEPTADA", label: "Aceptada" },
  { value: "COMPLETADA", label: "Completada" },
  { value: "CANCELADA", label: "Cancelada" },
  { value: "RECHAZADA", label: "Rechazada" },
];

export function formatRequestStateLabel(estado: string): string {
  const map: Record<string, string> = {
    PENDIENTE: "Pendiente",
    ACEPTADA: "Aceptada",
    RECHAZADA: "Rechazada",
    CANCELADA: "Cancelada",
    COMPLETADA: "Completada",
  };
  return map[estado.toUpperCase()] || estado;
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
