import type { User } from "firebase/auth";
import { api } from "./api";

export type TipoIncidencia =
  | "INCUMPLIMIENTO_SERVICIO"
  | "CONDUCTA_INAPROPIADA"
  | "RIESGO_SEGURIDAD"
  | "DESACUERDO_AJUSTE"
  | "FUERZA_MAYOR"
  | "OTRO";

export type PrioridadTicket = "ALTA" | "MEDIA" | "NORMAL";
export type EstadoTicket = "ABIERTO" | "EN_REVISION" | "RESUELTO" | "CERRADO";

export interface IncidentActuation {
  actuacion_id: string;
  instante: string;
  instante_tegucigalpa: string;
  autor_uid: string;
  autor_rol: string;
  accion: string;
  observaciones: string;
  evidencias?: string[];
}

export interface IncidentTicket {
  ticket_id: string;
  solicitud_id: string;
  modalidad: string;
  estado_servicio_al_reportar: string;
  reportante_uid: string;
  reportante_nombre: string;
  rol_reportante: "CLIENTE" | "TECNICO";
  contraparte_uid: string;
  tipo: TipoIncidencia;
  motivo: string;
  prioridad: PrioridadTicket;
  estado: EstadoTicket;
  es_apelacion_justificada: boolean;
  falta_computada: boolean;
  bloquea_cierre_automatico: boolean;
  bloquea_liberacion_fondos: boolean;
  creado_en: string;
  creado_en_tegucigalpa: string;
  actualizado_en: string;
  evidencias_privadas: string[];
  historial_actuaciones: IncidentActuation[];
  resolucion_final?: {
    decision: string;
    observaciones: string;
    administrador_uid: string;
    resuelto_en: string;
    resuelto_en_tegucigalpa: string;
    suspension_vinculada?: boolean;
    conciliacion_reembolso?: boolean;
  } | null;
  es_disputa_cruzada?: boolean;
  reporte_cliente?: IncidentTicket | null;
  reporte_tecnico?: IncidentTicket | null;
  tickets_solicitud?: IncidentTicket[];
}

export async function createIncidentTicketApi(
  user: User,
  payload: {
    solicitud_id: string;
    tipo: TipoIncidencia;
    motivo: string;
    evidencias?: string[];
    es_apelacion_justificada?: boolean;
  },
): Promise<IncidentTicket> {
  return api("/users/me/incident-tickets", user, payload, "POST");
}

export async function getIncidentTicketDetailApi(
  user: User,
  ticketId: string,
): Promise<IncidentTicket> {
  return api(`/admin/incident-tickets/${encodeURIComponent(ticketId)}`, user);
}

export async function listServiceIncidentTicketsApi(
  user: User,
  solicitudId: string,
): Promise<IncidentTicket[]> {
  return api(`/service-requests/${encodeURIComponent(solicitudId)}/incident-tickets`, user);
}

export async function listAdminIncidentInboxApi(
  user: User,
  filters: { estado?: string; prioridad?: string } = {},
): Promise<IncidentTicket[]> {
  const search = new URLSearchParams();
  if (filters.estado) search.set("estado", filters.estado);
  if (filters.prioridad) search.set("prioridad", filters.prioridad);
  return api(`/admin/incident-tickets?${search.toString()}`, user);
}

// Patrón Memento / Audit Trail: agregar actuación intermedia sin borrar el historial
export async function appendTicketActuationApi(
  user: User,
  ticketId: string,
  payload: {
    accion: string;
    observaciones: string;
    evidencias?: string[];
  },
): Promise<IncidentTicket> {
  return api(`/admin/incident-tickets/${encodeURIComponent(ticketId)}/actuations`, user, payload, "POST");
}

export interface AdminResolvePayload {
  decision:
    | "COMPLETAR_SERVICIO"
    | "DESBLOQUEAR_CONFIRMACION"
    | "CANCELAR_SERVICIO"
    | "SUSPENDER_USUARIO"
    | "CONCILIACION_REEMBOLSO";
  observaciones: string;
  aprobar_justificacion_fuerza_mayor?: boolean;
  vincular_suspension?: boolean;
  conciliacion_reembolso?: boolean;
}

// Patrón Strategy (CA-04, CA-05, CA-06, CA-07): resolución administrativa inmutable
export async function resolveIncidentTicketApi(
  user: User,
  ticketId: string,
  payload: AdminResolvePayload,
): Promise<IncidentTicket> {
  return api(`/admin/incident-tickets/${encodeURIComponent(ticketId)}/resolve`, user, payload, "POST");
}

// Patrón Specification (CA-05): consulta del expediente de faltas y justificaciones
export async function getUserIncidentPenaltiesApi(
  user: User,
  userUid: string,
): Promise<{
  usuario_uid: string;
  total_faltas_computables: number;
  faltas_computables: Array<{
    ticket_id: string;
    tipo: string;
    motivo: string;
    resolucion?: string;
    fecha?: string;
  }>;
  total_justificaciones_excluidas: number;
  justificaciones_excluidas: Array<{
    ticket_id: string;
    tipo: string;
    motivo_justificacion: string;
    fecha_aprobacion?: string;
  }>;
}> {
  return api(`/users/${encodeURIComponent(userUid)}/incident-penalties`, user);
}