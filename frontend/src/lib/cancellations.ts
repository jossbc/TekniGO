import type { User } from "firebase/auth";
import { api } from "./api";

export type CancellationCause = "VOLUNTARIA" | "INASISTENCIA_CONTRAPARTE" | "AJUSTE_RECHAZADO";
export type FaultStatus = "NO_APLICA" | "PENDIENTE_JUSTIFICACION" | "JUSTIFICADA" | "NO_JUSTIFICADA";
export type Rol = "CLIENTE" | "TECNICO";

export interface UploadedEvidence { referencia: string; nombre: string }

export interface JustificationRecord {
  id: string;
  estado: "PENDIENTE" | "APROBADA" | "RECHAZADA";
  motivo: string;
  enviada_en: string | null;
  resuelta_en: string | null;
  observaciones_admin: string | null;
}

export interface CancellationRecord {
  solicitud_id: string;
  modalidad: string;
  estado: "CANCELADA";
  estado_previo: string;
  autor_rol: Rol;
  causa: CancellationCause;
  motivo: string;
  evidencias: string[];
  regla_aplicada: string;
  genera_incumplimiento: boolean;
  responsable_rol: Rol | null;
  estado_incumplimiento: FaultStatus;
  permite_reapertura: boolean;
  solicitud_reabierta_id?: string | null;
  cancelada_en: string | null;
  justificacion?: JustificationRecord | null;
reembolso?: RefundRecord | null;
  alerta_administrativa?: {
    faltas_no_justificadas_acumuladas: number;
    alerta_administrativa_activa: boolean;
  } | null;
}

export interface AdjustmentRecord {
  id: string;
  solicitud_id: string;
  motivo: string;
  moneda: "HNL";
  monto_base: string;
  nuevo_total: string;
  diferencia: string;
  estado: "PENDIENTE" | "APROBADO" | "RECHAZADO";
  solicitado_en: string | null;
  respondido_en: string | null;
  comentario_respuesta: string | null;
  metodo_pago_base?: "tarjeta" | "efectivo";
  reparto_financiero?: PaymentSplitRecord | null;
}

export interface RefundRecord {
  id: string;
  solicitud_id: string;
  modalidad: string;
  moneda: "HNL";
  monto_base_reembolsado: string;
  monto_ajuste_reembolsado: string;
  monto_reembolso: string;
  porcentaje_reembolsado: number;
  monto_comision: string;
  referencia_pasarela_origen: string;
  referencia_reembolso_pasarela: string;
  estado_pasarela: "EN_PROCESO_PASARELA" | "CONFIRMADO_PASARELA";
  iniciado_en: string | null;
  confirmado_en: string | null;
}

export interface PaymentSplitRecord {
  estrategia: string;
  moneda: "HNL";
  monto_base: string;
  monto_ajuste: string;
  monto_total: string;
  metodo_pago_base: string;
  metodo_pago_ajuste: string | null;
  cobrado_digital: string;
  cobrado_efectivo: string;
  tasa_comision: string;
  monto_comision: string;
  monto_neto_tecnico: string;
  credito_digital_tecnico: string;
  deuda_comision_efectivo: string;
}

const base = (id: string) => `/service-requests/${encodeURIComponent(id)}`;

export async function uploadCancellationEvidenceApi(user: User, solicitudId: string, file: File): Promise<UploadedEvidence> {
  const form = new FormData();
  form.append("file", file);
  const res = await api<{ referencia: string }>(`${base(solicitudId)}/cancellation-evidence`, user, form, "POST");
  return { referencia: res.referencia, nombre: file.name };
}

export const cancelRequestApi = (
  user: User, solicitudId: string,
  payload: { causa: CancellationCause; motivo: string; evidencias: string[] },
) => api<CancellationRecord>(`${base(solicitudId)}/cancel`, user, payload, "POST");

export const getCancellationApi = (user: User, solicitudId: string) =>
  api<CancellationRecord>(`${base(solicitudId)}/cancellation`, user);

export const submitJustificationApi = (
  user: User, solicitudId: string, payload: { motivo: string; evidencias: string[] },
) => api<JustificationRecord>(`${base(solicitudId)}/cancellation/justification`, user, payload, "POST");

export const reopenRequestApi = (
  user: User, solicitudId: string, payload: { fecha_hora: string; metodo_pago?: string; presupuesto?: string },
) => api<{ id: string; mensaje?: string }>(`${base(solicitudId)}/reopen`, user, payload, "POST");

export const getAdjustmentApi = (user: User, solicitudId: string) =>
  api<AdjustmentRecord>(`${base(solicitudId)}/adjustment`, user);

export const requestAdjustmentApi = (
  user: User, solicitudId: string,
  payload: { motivo: string; monto_base: string; nuevo_total: string },
) => api<AdjustmentRecord>(`${base(solicitudId)}/adjustment`, user, payload, "POST");

export const respondAdjustmentApi = (
  user: User, solicitudId: string,
  payload: {
    decision: "APROBAR" | "RECHAZAR";
    comentario?: string;
    metodo_pago_diferencia?: "tarjeta" | "efectivo";
    referencia_pasarela_diferencia?: string;
  },
) => api<AdjustmentRecord>(`${base(solicitudId)}/adjustment/response`, user, payload, "POST");

export const confirmRefundApi = (user: User, solicitudId: string) =>
  api<RefundRecord>(`${base(solicitudId)}/refund/confirm`, user, {}, "POST");

export const normalizeState = (value?: string | null) =>
  (value ?? "").trim().toUpperCase().replace(/_/g, " ");