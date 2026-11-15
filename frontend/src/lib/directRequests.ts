import { getAuth, type User } from "firebase/auth";
import { api, resolveApiUrl } from "./api";

export interface TechnicianDirectRequest {
  id: string;
  cliente_nombre: string;
  servicio_nombre: string;
  fecha_hora: string;
  descripcion: string;
  estado: "PENDIENTE" | "ACEPTADA" | "EN_PROCESO" | "EN PROCESO" | "RECHAZADA" | "COMPLETADA" | string;
  total_estimado: number | string;
  pendiente_recepcion?: boolean;
  finalizacion_reportada_en?: string | null;
  vencimiento_confirmacion_en?: string | null;
  finalizacion_reportada_local?: string | null;
  vencimiento_confirmacion_local?: string | null;
  tiene_ajuste_pendiente?: boolean;
  motivo_ajuste_pendiente?: string | null;
}

export interface ReportServiceCompletionResponse {
  id: string;
  solicitud_id: string;
  modalidad: "DIRECTA" | "ABIERTA";
  estado: string;
  estado_etiqueta: string;
  pendiente_recepcion: boolean;
  completada: boolean;
  fondos_liberados: boolean;
  estado_fondos: string;
  plazo_confirmacion_horas: number;
  finalizacion_reportada_en: string;
  vencimiento_confirmacion_en: string;
  finalizacion_reportada_local: string;
  vencimiento_confirmacion_local: string;
  zona_horaria_usuario: string;
  reintento_idempotente: boolean;
  mensaje: string;
}

export const TEGUCIGALPA_TIME_ZONE = "America/Tegucigalpa";

export function formatTegucigalpaDateTime(iso: string | null | undefined): string {
  if (!iso) return "Pendiente de registro";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return `${date.toLocaleString("es-HN", {
    timeZone: TEGUCIGALPA_TIME_ZONE,
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  })} (America/Tegucigalpa)`;
}

export function formatConfirmationWindowStatus(
  vencimientoIso: string | null | undefined,
  referenceNow?: Date | string | number,
): { vencido: boolean; horasRestantes: number; etiqueta: string } {
  if (!vencimientoIso) {
    return {
      vencido: false,
      horasRestantes: 48,
      etiqueta: "Plazo máximo de 48 horas desde el reporte",
    };
  }
  const expiresAt = new Date(vencimientoIso).getTime();
  const nowMs =
    referenceNow !== undefined ? new Date(referenceNow).getTime() : Date.now();
  if (Number.isNaN(expiresAt) || Number.isNaN(nowMs)) {
    return {
      vencido: false,
      horasRestantes: 48,
      etiqueta: "Plazo máximo de 48 horas para confirmar o abrir incidencia",
    };
  }
  const diffMs = expiresAt - nowMs;
  if (diffMs <= 0) {
    return {
      vencido: true,
      horasRestantes: 0,
      etiqueta: "Plazo de 48 horas vencido (elegible para revisión de cierre automático)",
    };
  }
  const totalMinutes = Math.ceil(diffMs / 60000);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return {
    vencido: false,
    horasRestantes: hours,
    etiqueta:
      hours > 0
        ? `Restan ${hours} h ${minutes > 0 ? `${minutes} min ` : ""}para confirmar o reportar incidencia (máx. 48 h)`
        : `Restan ${minutes} min para confirmar o reportar incidencia (máx. 48 h)`,
  };
}

export const TECHNICIAN_DIRECT_REQUESTS_PATH = "/technicians/me/direct-requests";

async function authorizedJsonPost(path: string, body: unknown, fallbackError: string) {
  const currentUser = getAuth().currentUser;
  if (!currentUser) {
    throw new Error("Debes iniciar sesión para continuar.");
  }

  let token = await currentUser.getIdToken();
  const sendRequest = (bearer: string) =>
    fetch(resolveApiUrl(path), {
      method: "POST",
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${bearer}`,
      },
      body: JSON.stringify(body),
    });

  let response = await sendRequest(token);
  if (response.status === 401) {
    token = await currentUser.getIdToken(true);
    response = await sendRequest(token);
  }

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    const detail =
      typeof errorData.detail === "string"
        ? errorData.detail
        : Array.isArray(errorData.detail)
          ? errorData.detail
              .map((item: { msg?: string }) => (item.msg || "").replace(/^Value error, /, ""))
              .filter(Boolean)
              .join(". ")
          : "";
    throw new Error(detail || fallbackError);
  }

  return response.json();
}

// HU-21: Petición para calcular el presupuesto
export const estimateBudget = async (servicio_id: string, cantidad: number) =>
  authorizedJsonPost(
    "/direct-requests/estimate",
    { servicio_id, cantidad },
    "Error al calcular el presupuesto",
  );

// HU-20: Petición para guardar la solicitud
export const createDirectRequest = async (requestData: {
  tecnico_id: string;
  servicio_id: string;
  municipio: string;
  sector: string;
  direccion_exacta: string;
  fecha_hora: string;
  descripcion: string;
  cantidad: number;
  metodo_pago: string;
}) =>
  authorizedJsonPost(
    "/direct-requests/",
    requestData,
    "Error al crear la solicitud",
  );

// HU-22, HU-23, HU-24: Bandeja, detalle y respuesta del técnico autenticado
export const listTechnicianDirectRequests = (user: User) =>
  api<TechnicianDirectRequest[]>(TECHNICIAN_DIRECT_REQUESTS_PATH, user);

export const getTechnicianDirectRequest = (user: User, solicitudId: string) =>
  api<TechnicianDirectRequest>(`${TECHNICIAN_DIRECT_REQUESTS_PATH}/${encodeURIComponent(solicitudId)}`, user);

export const acceptTechnicianDirectRequest = (user: User, solicitudId: string) =>
  api<TechnicianDirectRequest>(`${TECHNICIAN_DIRECT_REQUESTS_PATH}/${encodeURIComponent(solicitudId)}/accept`, user, undefined, "PATCH");

export const rejectTechnicianDirectRequest = (user: User, solicitudId: string) =>
  api<TechnicianDirectRequest>(`${TECHNICIAN_DIRECT_REQUESTS_PATH}/${encodeURIComponent(solicitudId)}/reject`, user, undefined, "PATCH");

// HU-34: Reportar trabajo finalizado (idempotente, inicia plazo de 48h en En proceso)
export const reportTechnicianRequestCompletion = (
  user: User,
  solicitudId: string,
  payload?: { comentario?: string; now?: string },
) =>
  api<ReportServiceCompletionResponse>(
    `${TECHNICIAN_DIRECT_REQUESTS_PATH}/${encodeURIComponent(solicitudId)}/report-completion`,
    user,
    payload,
    "PATCH",
  );
