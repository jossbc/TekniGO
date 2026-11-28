import type { User } from "firebase/auth";
import { api } from "./api";

export type SuspensionType = "TEMPORAL" | "DEFINITIVA";

export type SuspensionCause =
  | "CONDUCTA"
  | "REINCIDENCIA_FALTAS"
  | "INCUMPLIMIENTO_REGLAS"
  | "FRAUDE_IDENTIDAD"
  | "INCIDENCIA_GRAVE";

export type SuspensionStatus = "ACTIVA" | "LEVANTADA" | "EXPIRADA";

export type AccountModerationFilter =
  | "TODOS"
  | "SUSPENDIDOS"
  | "RESTRICCION_FINANCIERA"
  | "CON_ALERTA_FALTAS"
  | "CERRADAS";

export type OperationalDimensionItem = {
  dimension:
    | "CUENTA_ACTIVA"
    | "VERIFICACION"
    | "DISPONIBILIDAD"
    | "SUSPENSION_CONDUCTA"
    | "RESTRICCION_FINANCIERA";
  habilitada: boolean;
  causa_codigo: string | null;
  motivo: string | null;
  es_restriccion_financiera: boolean;
  es_suspension_conducta: boolean;
};

export type SuspensionRecord = {
  id: string;
  usuario_id: string;
  usuario_uid: string;
  tipo: SuspensionType;
  causa: SuspensionCause;
  motivo: string;
  estado: SuspensionStatus;
  responsable_admin_id: string;
  responsable_admin_uid: string;
  responsable_admin_nombre: string | null;
  iniciada_en: string | null;
  vence_en: string | null;
  levantada_en: string | null;
  levantada_por_admin_id: string | null;
  levantada_por_admin_nombre: string | null;
  motivo_levantamiento: string | null;
  expirada_en: string | null;
  alerta_reincidencia_id: string | null;
  actualizado_en: string | null;
};

export type SuspensionAuditItem = {
  id: string;
  usuario_id: string;
  suspension_id: string | null;
  evento: string;
  tipo_suspension: string | null;
  causa: string | null;
  estado_anterior: string | null;
  estado_nuevo: string | null;
  motivo: string;
  actor_admin_id: string | null;
  actor_admin_nombre: string | null;
  proceso_responsable: string;
  metadatos: Record<string, unknown>;
  creado_en: string | null;
};

export type RecidivismAlertItem = {
  id: string;
  tipo_evento: string;
  usuario_uid: string;
  rol_infractor: string | null;
  umbral_faltas: number;
  total_faltas_computadas: number;
  faltas_ids: string[];
  estado: string;
  requiere_decision_hu52: boolean;
  suspension_automatica_aplicada: boolean;
  generado_en: string | null;
  actualizado_en: string | null;
};

export type AccountModerationRow = {
  usuario_id: string;
  firebase_uid: string;
  nombre_completo: string;
  correo: string;
  roles: string[];
  estado_identidad: string;
  estado_verificacion: string | null;
  disponible: boolean | null;
  cuenta_cerrada: boolean;
  suspendido_por_conducta: boolean;
  restriccion_financiera: boolean;
  habilitado_operativamente: boolean;
  suspension_activa: SuspensionRecord | null;
  faltas_no_justificadas: number;
  alerta_tercera_falta_activa: boolean;
  evento_reincidencia_activo: RecidivismAlertItem | null;
};

export type AccountModerationListResponse = {
  total: number;
  offset: number;
  limit: number;
  filtro: string;
  cuentas: AccountModerationRow[];
};

export type AccountOperationalState = {
  usuario_id: string;
  roles: string[];
  cuenta_cerrada: boolean;
  suspendido_por_conducta: boolean;
  restriccion_financiera: boolean;
  verificacion_vigente: boolean;
  disponible: boolean | null;
  puede_contratar_como_cliente: boolean;
  puede_operar_como_tecnico: boolean;
  habilitado_operativamente: boolean;
  motivos_bloqueo: string[];
  dimensiones: OperationalDimensionItem[];
  suspension_activa: SuspensionRecord | null;
};

export type AccountModerationDetail = {
  usuario: {
    usuario_id: string;
    firebase_uid: string;
    nombre_completo: string;
    correo: string;
    dni: string;
    telefono: string;
    telefono_verificado: boolean;
    estado_identidad: string;
    estado_verificacion: string | null;
    disponible: boolean | null;
    cierre_cuenta_en: string | null;
    motivo_cierre_cuenta: string | null;
    roles: string[];
  };
  estado_operativo: AccountOperationalState;
  resumen_faltas: {
    umbral_reincidencia: number;
    total_faltas_no_justificadas: number;
    total_faltas_excluidas_justificadas: number;
    alerta_tercera_reincidencia_activa: boolean;
    suspension_automatica_aplicada: boolean;
    evento_reincidencia_activo: RecidivismAlertItem | null;
    faltas_computables: Array<{
      registro_id: string;
      solicitud_id: string;
      causa: string;
      estado_incumplimiento: string;
      ocurrida_en: string | null;
    }>;
    faltas_excluidas: Array<{
      registro_id: string;
      solicitud_id: string;
      causa: string;
      estado_incumplimiento: string;
      ocurrida_en: string | null;
    }>;
  };
  historial_suspensiones: SuspensionRecord[];
  auditoria: SuspensionAuditItem[];
};

export const SUSPENSION_CAUSE_LABELS: Record<SuspensionCause, string> = {
  CONDUCTA: "Conducta inapropiada",
  REINCIDENCIA_FALTAS: "Reincidencia de incumplimientos",
  INCUMPLIMIENTO_REGLAS: "Incumplimiento de reglas",
  FRAUDE_IDENTIDAD: "Fraude o suplantación de identidad",
  INCIDENCIA_GRAVE: "Incidencia grave confirmada",
};

export const SUSPENSION_STATUS_LABELS: Record<SuspensionStatus, string> = {
  ACTIVA: "Activa",
  LEVANTADA: "Levantada",
  EXPIRADA: "Expirada",
};

export const DIMENSION_LABELS: Record<OperationalDimensionItem["dimension"], string> = {
  CUENTA_ACTIVA: "Estado de cuenta",
  VERIFICACION: "Verificación documental y teléfono",
  DISPONIBILIDAD: "Disponibilidad operativa",
  SUSPENSION_CONDUCTA: "Suspensión por conducta",
  RESTRICCION_FINANCIERA: "Restricción financiera (saldo)",
};

export const MODERATION_EVENT_LABELS: Record<string, string> = {
  SUSPENSION_APLICADA: "Suspensión aplicada",
  SUSPENSION_LEVANTADA: "Suspensión temporal levantada",
  SUSPENSION_EXPIRADA: "Suspensión temporal expirada",
  ALERTA_REINCIDENCIA_ATENDIDA: "Alerta de reincidencia atendida",
  CIERRE_CUENTA_APLICADO: "Cierre de cuenta aplicado",
  BAJA_LOGICA_DOCUMENTAL: "Baja lógica documental aplicada",
};

export function formatModerationDate(iso: string | null | undefined): string {
  if (!iso) return "Sin fecha";
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return iso;
  return dt.toLocaleString("es-HN", {
    timeZone: "America/Tegucigalpa",
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export function classifyAccountRestrictionBadges(row: {
  cuenta_cerrada: boolean;
  suspendido_por_conducta: boolean;
  restriccion_financiera: boolean;
  alerta_tercera_falta_activa?: boolean;
}): Array<{ code: string; label: string; tone: "danger" | "warning" | "neutral" | "success" }> {
  const badges: Array<{ code: string; label: string; tone: "danger" | "warning" | "neutral" | "success" }> = [];
  if (row.cuenta_cerrada) {
    badges.push({ code: "CUENTA_CERRADA", label: "Cuenta cerrada", tone: "neutral" });
  }
  if (row.suspendido_por_conducta) {
    badges.push({ code: "SUSPENSION_CONDUCTA", label: "Suspendida por conducta", tone: "danger" });
  }
  if (row.restriccion_financiera) {
    badges.push({ code: "RESTRICCION_FINANCIERA", label: "Restricción financiera (saldo)", tone: "warning" });
  }
  if (row.alerta_tercera_falta_activa) {
    badges.push({ code: "ALERTA_TERCERA_FALTA", label: "Alerta 3.ª falta (revisión manual)", tone: "warning" });
  }
  if (badges.length === 0) {
    badges.push({ code: "SIN_RESTRICCIONES", label: "Sin sanciones activas", tone: "success" });
  }
  return badges;
}

export async function listAccountsModeration(
  user: User,
  params: { search?: string; filtro?: AccountModerationFilter; offset?: number; limit?: number } = {},
): Promise<AccountModerationListResponse> {
  const qs = new URLSearchParams();
  if (params.search?.trim()) qs.set("search", params.search.trim());
  qs.set("filtro", params.filtro || "TODOS");
  qs.set("offset", String(params.offset ?? 0));
  qs.set("limit", String(params.limit ?? 25));
  return api<AccountModerationListResponse>(`/admin/accounts/moderation?${qs.toString()}`, user);
}

export async function getAccountModerationDetail(
  user: User,
  userId: string,
): Promise<AccountModerationDetail> {
  return api<AccountModerationDetail>(`/admin/accounts/${encodeURIComponent(userId)}/moderation`, user);
}

export async function applyAccountSuspension(
  user: User,
  userId: string,
  payload: {
    tipo: SuspensionType;
    causa: SuspensionCause;
    motivo: string;
    vence_en?: string | null;
    alerta_reincidencia_id?: string | null;
  },
): Promise<{ suspension: SuspensionRecord; estado_operativo: AccountOperationalState }> {
  return api(`/admin/accounts/${encodeURIComponent(userId)}/suspensions`, user, payload, "POST");
}

export async function resolveRecidivismAlertWithoutSuspension(
  user: User,
  alertId: string,
  observaciones: string,
): Promise<{ alerta_id: string; estado: string; suspension_aplicada: boolean }> {
  return api(
    `/admin/recidivism-alerts/${encodeURIComponent(alertId)}/resolve`,
    user,
    { observaciones: observaciones.trim() },
    "POST",
  );
}

export async function liftTemporalSuspension(
  user: User,
  suspensionId: string,
  motivoLevantamiento: string,
): Promise<{
  suspension: SuspensionRecord;
  reintento_idempotente: boolean;
  operaciones_restituidas: boolean;
  estado_operativo: AccountOperationalState;
}> {
  return api(
    `/admin/suspensions/${encodeURIComponent(suspensionId)}/lift`,
    user,
    { motivo_levantamiento: motivoLevantamiento.trim() },
    "POST",
  );
}

export async function expireOverdueSuspensions(
  user: User,
): Promise<{ evaluado_en: string; total_expiradas: number }> {
  return api("/admin/suspensions/expire-overdue", user, {}, "POST");
}

export async function closeAccountByAdmin(
  user: User,
  userId: string,
  motivo: string,
): Promise<{
  usuario_id: string;
  estado_cuenta: string;
  total_documentos_baja_logica: number;
  acceso_operativo_revocado: boolean;
}> {
  return api(
    `/admin/accounts/${encodeURIComponent(userId)}/close`,
    user,
    { motivo: motivo.trim() },
    "POST",
  );
}