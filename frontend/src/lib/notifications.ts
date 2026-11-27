import type { User } from "firebase/auth";
import { api, resolveApiUrl } from "./api";

export const TEGUCIGALPA_TIME_ZONE = "America/Tegucigalpa";
export const PROPOSAL_LIVE_EVENT_NAME = "teknigo:proposals-live-update";
export const NOTIFICATIONS_LIVE_EVENT_NAME = "teknigo:notifications-live-update";
export const NOTIFICATIONS_UNREAD_COUNT_EVENT_NAME = "teknigo:notifications-unread-count";
export const NOTIFICATION_HEARTBEAT_INTERVAL_MS = 25_000;
export const NOTIFICATION_WS_CLOSE_UNAUTHORIZED = 4001;
export const NOTIFICATION_WS_CLOSE_FORBIDDEN = 4003;

export type NotificationConnectionState =
  | "idle"
  | "connecting"
  | "connected"
  | "reconnecting"
  | "offline";

export type NotificationCategory = "proposal" | "service" | "verification" | "finance" | "alert";

export type NotificationReadStatus = "PENDIENTE" | "LEIDA";
export type NotificationFilter = "TODAS" | "PENDIENTE" | "LEIDA";

export type UserNotification = {
  id: string;
  evento_id: string;
  tipo_evento: string;
  titulo: string;
  resumen: string;
  enlace_ruta: string;
  recurso_tipo: string | null;
  recurso_id: string | null;
  metadatos_seguros: Record<string, unknown>;
  estado_lectura?: NotificationReadStatus;
  leida: boolean;
  leida_en: string | null;
  leida_en_local: string | null;
  creada_en: string;
  creada_en_local: string | null;
  zona_horaria?: string;
};

export type NotificationsListResponse = {
  total: number;
  no_leidas: number;
  pendientes?: number;
  filtro_estado?: NotificationFilter;
  offset: number;
  limit: number;
  zona_horaria: string;
  notificaciones: UserNotification[];
};

export type UnreadNotificationsCountResponse = {
  no_leidas: number;
  pendientes: number;
};

export type NotificationReadResponse = {
  notificacion: UserNotification;
  no_leidas: number;
  pendientes?: number;
  actualizada?: boolean;
  ya_leida?: boolean;
};

export type NotificationReadAllResponse = {
  actualizadas: number;
  no_leidas: number;
  pendientes?: number;
};

export type ProposalLiveUpdateDetail = {
  tipo_evento: string;
  solicitud_id: string | null;
  modalidad: string | null;
  notification_id: string | null;
};

export const PROPOSAL_LIVE_EVENT_TYPES = new Set<string>([
  "NUEVA_OFERTA_ABIERTA",
  "NUEVA_CONTRAOFERTA_ABIERTA",
  "PROPUESTA_RECHAZADA",
  "PROPUESTA_ADJUDICADA",
  "PROPUESTA_DESCARTADA_POR_ADJUDICACION",
]);

const EVENT_TYPE_META: Record<string, { label: string; category: NotificationCategory }> = {
  NUEVA_SOLICITUD_DIRECTA: { label: "Solicitud directa", category: "service" },
  SOLICITUD_DIRECTA_ACEPTADA: { label: "Solicitud aceptada", category: "service" },
  SOLICITUD_DIRECTA_RECHAZADA: { label: "Solicitud rechazada", category: "alert" },
  NUEVA_OFERTA_ABIERTA: { label: "Nueva oferta", category: "proposal" },
  NUEVA_CONTRAOFERTA_ABIERTA: { label: "Nueva contraoferta", category: "proposal" },
  PROPUESTA_RECHAZADA: { label: "Propuesta rechazada", category: "proposal" },
  PROPUESTA_ADJUDICADA: { label: "Oferta adjudicada", category: "proposal" },
  PROPUESTA_DESCARTADA_POR_ADJUDICACION: { label: "Oferta cerrada", category: "proposal" },
  SERVICIO_INICIADO: { label: "Servicio iniciado", category: "service" },
  FINALIZACION_REPORTADA: { label: "Finalización reportada", category: "service" },
  RECEPCION_CONFIRMADA: { label: "Recepción confirmada", category: "service" },
  CIERRE_AUTOMATICO_48H: { label: "Cierre automático 48h", category: "service" },
  SERVICIO_CANCELADO: { label: "Servicio cancelado", category: "alert" },
  INCIDENCIA_ABIERTA: { label: "Incidencia abierta", category: "alert" },
  INCIDENCIA_RESUELTA: { label: "Incidencia resuelta", category: "service" },
  AJUSTE_PRESUPUESTO_SOLICITADO: { label: "Ajuste solicitado", category: "finance" },
  AJUSTE_PRESUPUESTO_APROBADO: { label: "Ajuste aprobado", category: "finance" },
  AJUSTE_PRESUPUESTO_RECHAZADO: { label: "Ajuste rechazado", category: "alert" },
  VERIFICACION_APROBADA: { label: "Verificación aprobada", category: "verification" },
  VERIFICACION_RECHAZADA: { label: "Verificación rechazada", category: "alert" },
  DOCUMENTACION_APROBADA: { label: "Documentos aprobados", category: "verification" },
  CERTIFICACION_APROBADA: { label: "Certificación aprobada", category: "verification" },
  CERTIFICACION_RECHAZADA: { label: "Certificación rechazada", category: "alert" },
  CERTIFICACION_REVOCADA: { label: "Certificación revocada", category: "alert" },
  LIQUIDACION_RECIBIDA: { label: "Liquidación recibida", category: "finance" },
  LIQUIDACION_APROBADA: { label: "Liquidación aprobada", category: "finance" },
  LIQUIDACION_RECHAZADA: { label: "Liquidación rechazada", category: "alert" },
  DESEMBOLSO_SOLICITADO: { label: "Desembolso solicitado", category: "finance" },
  DESEMBOLSO_PAGADO: { label: "Desembolso pagado", category: "finance" },
  DESEMBOLSO_RECHAZADO: { label: "Desembolso rechazado", category: "alert" },
  RESTRICCION_OPERATIVA: { label: "Acción requerida", category: "alert" },
};

const ALLOWED_ROUTE_PREFIXES = [
  "/mis-solicitudes",
  "/mis-trabajos",
  "/tecnico",
  "/verificacion",
  "/perfil",
  "/admin",
  "/mi-cuenta",
  "/solicitudes",
];

export function formatNotificationEventType(tipoEvento: string | null | undefined): {
  label: string;
  category: NotificationCategory;
} {
  const key = (tipoEvento || "").trim().toUpperCase();
  if (key && EVENT_TYPE_META[key]) {
    return EVENT_TYPE_META[key];
  }
  const readable = key ? key.toLowerCase().replace(/_/g, " ") : "aviso";
  return {
    label: readable.charAt(0).toUpperCase() + readable.slice(1),
    category: "service",
  };
}

export function formatNotificationDateTegucigalpa(
  input: Pick<UserNotification, "creada_en" | "creada_en_local"> | string | null | undefined,
): string {
  const raw = typeof input === "string" ? input : input?.creada_en || input?.creada_en_local || "";
  if (!raw) return "Fecha reciente";
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw;
  try {
    return `${new Intl.DateTimeFormat("es-HN", {
      timeZone: TEGUCIGALPA_TIME_ZONE,
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(parsed)} (HN)`;
  } catch {
    return parsed.toISOString();
  }
}

export function getAuthorizedNotificationRoute(rawRoute: string | null | undefined): string | null {
  if (!rawRoute) return null;
  const trimmed = rawRoute.trim();
  if (!trimmed.startsWith("/") || trimmed.startsWith("//")) return null;
  if (/[\r\n\0]/.test(trimmed)) return null;
  const basePath = trimmed.split("?")[0].split("#")[0];
  const matches = ALLOWED_ROUTE_PREFIXES.some(
    (prefix) => basePath === prefix || basePath.startsWith(`${prefix}/`),
  );
  return matches ? trimmed : null;
}

function compareNotificationsDesc(a: UserNotification, b: UserNotification): number {
  const timeA = Date.parse(a.creada_en) || 0;
  const timeB = Date.parse(b.creada_en) || 0;
  if (timeA !== timeB) return timeB - timeA;
  return b.id.localeCompare(a.id);
}

export function mergeNotifications(
  current: UserNotification[],
  incoming: UserNotification[],
): UserNotification[] {
  const byId = new Map<string, UserNotification>();
  for (const item of current) {
    if (item?.id) byId.set(item.id, item);
  }
  for (const item of incoming) {
    if (!item?.id) continue;
    const prev = byId.get(item.id);
    byId.set(item.id, prev ? { ...prev, ...item } : item);
  }
  return Array.from(byId.values()).sort(compareNotificationsDesc);
}

export function getLatestNotificationCursor(items: UserNotification[]): {
  afterId: string | null;
  since: string | null;
} {
  if (!items.length) return { afterId: null, since: null };
  const sorted = [...items].sort(compareNotificationsDesc);
  return {
    afterId: sorted[0].id || null,
    since: sorted[0].creada_en || null,
  };
}

export function getNotificationReadStatus(
  item: Pick<UserNotification, "estado_lectura" | "leida"> | null | undefined,
): NotificationReadStatus {
  if (!item) return "PENDIENTE";
  if (item.estado_lectura === "LEIDA" || item.leida) return "LEIDA";
  return "PENDIENTE";
}

export function isNotificationPending(
  item: Pick<UserNotification, "estado_lectura" | "leida"> | null | undefined,
): boolean {
  return getNotificationReadStatus(item) === "PENDIENTE";
}

export function clampUnreadCount(count: number | null | undefined): number {
  if (typeof count !== "number" || !Number.isFinite(count)) return 0;
  return Math.max(0, Math.floor(count));
}

export type NotificationFilterStrategy = {
  value: NotificationFilter;
  label: string;
  emptyMessage: string;
  predicate: (item: UserNotification) => boolean;
};

export const NOTIFICATION_FILTER_STRATEGIES: Record<NotificationFilter, NotificationFilterStrategy> = {
  TODAS: {
    value: "TODAS",
    label: "Todas",
    emptyMessage: "No tienes notificaciones por el momento.",
    predicate: () => true,
  },
  PENDIENTE: {
    value: "PENDIENTE",
    label: "Pendientes",
    emptyMessage: "No tienes notificaciones pendientes por leer.",
    predicate: (item) => isNotificationPending(item),
  },
  LEIDA: {
    value: "LEIDA",
    label: "Leídas",
    emptyMessage: "Aún no tienes notificaciones leídas.",
    predicate: (item) => !isNotificationPending(item),
  },
};

export const NOTIFICATION_FILTER_ORDER: NotificationFilter[] = ["TODAS", "PENDIENTE", "LEIDA"];

export function filterNotificationsByReadStatus(
  items: UserNotification[],
  filter: NotificationFilter,
): UserNotification[] {
  const strategy = NOTIFICATION_FILTER_STRATEGIES[filter] ?? NOTIFICATION_FILTER_STRATEGIES.TODAS;
  return items.filter(strategy.predicate);
}

export function getEmptyNotificationsMessage(filter: NotificationFilter): string {
  return (NOTIFICATION_FILTER_STRATEGIES[filter] ?? NOTIFICATION_FILTER_STRATEGIES.TODAS).emptyMessage;
}

export function applyNotificationReadLocally(
  items: UserNotification[],
  notificationId: string,
  updated?: Partial<UserNotification>,
): UserNotification[] {
  return items.map((entry) => {
    if (entry.id !== notificationId) return entry;
    return {
      ...entry,
      ...updated,
      estado_lectura: "LEIDA",
      leida: true,
      leida_en: updated?.leida_en ?? entry.leida_en,
      leida_en_local: updated?.leida_en_local ?? entry.leida_en_local,
    };
  });
}

export type NotificationWsFrame = {
  type?: string;
  no_leidas?: number;
  pendientes?: number;
  notificacion?: UserNotification;
  notificaciones?: UserNotification[];
  notification_id?: string;
  estado_lectura?: NotificationReadStatus;
  leida_en?: string | null;
  leida_en_local?: string | null;
};

export type NotificationLiveState = {
  items: UserNotification[];
  unreadCount: number;
};

export type NotificationLiveReducerResult = NotificationLiveState & {
  newlyArrived: UserNotification[];
};

function resolveFrameUnreadCount(payload: NotificationWsFrame): number | null {
  if (typeof payload.no_leidas === "number") return clampUnreadCount(payload.no_leidas);
  if (typeof payload.pendientes === "number") return clampUnreadCount(payload.pendientes);
  return null;
}

export function applyNotificationSocketFrame(
  state: NotificationLiveState,
  payload: NotificationWsFrame | null | undefined,
): NotificationLiveReducerResult {
  if (!payload || typeof payload !== "object") {
    return { ...state, newlyArrived: [] };
  }

  const frameUnread = resolveFrameUnreadCount(payload);

  if (
    payload.type === "connected" ||
    payload.type === "pong" ||
    payload.type === "unread_count"
  ) {
    return {
      items: state.items,
      unreadCount: frameUnread !== null ? frameUnread : clampUnreadCount(state.unreadCount),
      newlyArrived: [],
    };
  }

  if (payload.type === "notification" && payload.notificacion) {
    const incoming = payload.notificacion;
    const alreadyExists = state.items.some((entry) => entry.id === incoming.id);
    const newlyArrived = !alreadyExists && incoming.id ? [incoming] : [];
    const merged = mergeNotifications(state.items, [incoming]);
    const fallbackIncrement = newlyArrived.some((entry) => isNotificationPending(entry)) ? 1 : 0;
    const nextUnread =
      frameUnread !== null
        ? frameUnread
        : clampUnreadCount(
            Math.max(
              state.unreadCount + fallbackIncrement,
              merged.filter((entry) => isNotificationPending(entry)).length,
            ),
          );
    return {
      items: merged,
      unreadCount: nextUnread,
      newlyArrived,
    };
  }

  if (payload.type === "sync" && Array.isArray(payload.notificaciones)) {
    const existingIds = new Set(state.items.map((entry) => entry.id));
    const newlyArrived = payload.notificaciones.filter(
      (entry) => Boolean(entry?.id) && !existingIds.has(entry.id),
    );
    const merged = mergeNotifications(state.items, payload.notificaciones);
    const newlyArrivedUnread = newlyArrived.filter((entry) => isNotificationPending(entry)).length;
    const nextUnread =
      frameUnread !== null
        ? frameUnread
        : clampUnreadCount(
            Math.max(
              state.unreadCount + newlyArrivedUnread,
              merged.filter((entry) => isNotificationPending(entry)).length,
            ),
          );
    return {
      items: merged,
      unreadCount: nextUnread,
      newlyArrived,
    };
  }

  if (payload.type === "read_sync" && payload.notification_id) {
    const updatedItems = applyNotificationReadLocally(state.items, payload.notification_id, {
      estado_lectura: payload.estado_lectura ?? "LEIDA",
      leida: true,
      leida_en: payload.leida_en ?? null,
      leida_en_local: payload.leida_en_local ?? null,
    });
    return {
      items: updatedItems,
      unreadCount: frameUnread !== null ? frameUnread : clampUnreadCount(state.unreadCount),
      newlyArrived: [],
    };
  }

  if (payload.type === "read_all_sync") {
    const updatedItems = state.items.map((entry) =>
      isNotificationPending(entry)
        ? {
            ...entry,
            estado_lectura: "LEIDA" as const,
            leida: true,
            leida_en: payload.leida_en ?? entry.leida_en,
            leida_en_local: payload.leida_en_local ?? entry.leida_en_local,
          }
        : entry,
    );
    return {
      items: updatedItems,
      unreadCount: frameUnread !== null ? frameUnread : 0,
      newlyArrived: [],
    };
  }

  return { ...state, newlyArrived: [] };
}

export function emitUnreadNotificationsCount(count: number): void {
  if (typeof window === "undefined" || typeof window.dispatchEvent !== "function") return;
  try {
    const safeCount = clampUnreadCount(count);
    window.dispatchEvent(
      new CustomEvent<{ no_leidas: number; pendientes: number }>(
        NOTIFICATIONS_UNREAD_COUNT_EVENT_NAME,
        { detail: { no_leidas: safeCount, pendientes: safeCount } },
      ),
    );
  } catch {
    // No bloquea cuando el entorno no implementa CustomEvent.
  }
}

export function subscribeUnreadNotificationsCount(
  listener: (detail: { no_leidas: number; pendientes: number }) => void,
): () => void {
  if (typeof window === "undefined" || typeof window.addEventListener !== "function") {
    return () => {};
  }
  const handler = (event: Event) => {
    const custom = event as CustomEvent<{ no_leidas: number; pendientes: number }>;
    if (custom.detail) listener(custom.detail);
  };
  window.addEventListener(NOTIFICATIONS_UNREAD_COUNT_EVENT_NAME, handler);
  return () => {
    window.removeEventListener(NOTIFICATIONS_UNREAD_COUNT_EVENT_NAME, handler);
  };
}

export function isProposalLiveNotification(
  item: Pick<UserNotification, "tipo_evento" | "metadatos_seguros"> | null | undefined,
): boolean {
  if (!item) return false;
  const key = (item.tipo_evento || "").trim().toUpperCase();
  if (PROPOSAL_LIVE_EVENT_TYPES.has(key)) return true;
  return item.metadatos_seguros?.es_evento_propuesta_vivo === true;
}

export function emitProposalLiveUpdate(detail: ProposalLiveUpdateDetail): void {
  if (typeof window === "undefined" || typeof window.dispatchEvent !== "function") return;
  try {
    window.dispatchEvent(new CustomEvent<ProposalLiveUpdateDetail>(PROPOSAL_LIVE_EVENT_NAME, { detail }));
  } catch {
    // No bloquea cuando el entorno no implementa CustomEvent.
  }
}

export function emitGeneralNotificationLiveUpdate(item: UserNotification): void {
  if (typeof window === "undefined" || typeof window.dispatchEvent !== "function") return;
  try {
    window.dispatchEvent(new CustomEvent<UserNotification>(NOTIFICATIONS_LIVE_EVENT_NAME, { detail: item }));
  } catch {
    // No bloquea cuando el entorno no implementa CustomEvent.
  }
}

export function subscribeProposalLiveUpdate(
  listener: (detail: ProposalLiveUpdateDetail) => void,
): () => void {
  if (typeof window === "undefined" || typeof window.addEventListener !== "function") {
    return () => {};
  }
  const handler = (event: Event) => {
    const custom = event as CustomEvent<ProposalLiveUpdateDetail>;
    listener(custom.detail);
  };
  window.addEventListener(PROPOSAL_LIVE_EVENT_NAME, handler);
  return () => {
    window.removeEventListener(PROPOSAL_LIVE_EVENT_NAME, handler);
  };
}

export function subscribeGeneralNotificationLiveUpdate(
  listener: (item: UserNotification) => void,
): () => void {
  if (typeof window === "undefined" || typeof window.addEventListener !== "function") {
    return () => {};
  }
  const handler = (event: Event) => {
    const custom = event as CustomEvent<UserNotification>;
    if (custom.detail) listener(custom.detail);
  };
  window.addEventListener(NOTIFICATIONS_LIVE_EVENT_NAME, handler);
  return () => {
    window.removeEventListener(NOTIFICATIONS_LIVE_EVENT_NAME, handler);
  };
}

export function getNotificationReconnectDelayMs(attempt: number): number {
  const safeAttempt = Math.max(0, Math.floor(attempt));
  return Math.min(15_000, 1_000 * 2 ** safeAttempt);
}

export function resolveNotificationsWsUrl(
  token: string,
  cursor?: { afterId?: string | null; since?: string | null },
): string {
  const params = new URLSearchParams({ token });
  if (cursor?.afterId) params.set("after_id", cursor.afterId);
  if (cursor?.since) params.set("since", cursor.since);
  const path = `/notifications/ws?${params.toString()}`;
  const apiUrl = resolveApiUrl(path);
  if (/^https?:\/\//i.test(apiUrl)) {
    return apiUrl.replace(/^http/i, "ws");
  }
  if (typeof window !== "undefined" && window.location) {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${window.location.host}${apiUrl}`;
  }
  return `ws://127.0.0.1:8000${apiUrl}`;
}

export function fetchNotifications(
  user: User,
  options?: {
    limit?: number;
    offset?: number;
    afterId?: string | null;
    since?: string | null;
    estado?: NotificationFilter;
    soloNoLeidas?: boolean;
  },
): Promise<NotificationsListResponse> {
  const params = new URLSearchParams();
  if (options?.limit) params.set("limit", String(options.limit));
  if (typeof options?.offset === "number" && options.offset > 0) {
    params.set("offset", String(options.offset));
  }
  if (options?.afterId) params.set("after_id", options.afterId);
  if (options?.since) params.set("since", options.since);
  if (options?.estado && options.estado !== "TODAS") {
    params.set("estado", options.estado);
  }
  if (options?.soloNoLeidas) params.set("solo_no_leidas", "true");
  const qs = params.toString();
  return api<NotificationsListResponse>(`/notifications${qs ? `?${qs}` : ""}`, user, undefined, "GET");
}

export function fetchPendingNotifications(
  user: User,
  options?: { limit?: number; offset?: number },
): Promise<NotificationsListResponse> {
  const params = new URLSearchParams();
  if (options?.limit) params.set("limit", String(options.limit));
  if (typeof options?.offset === "number" && options.offset > 0) {
    params.set("offset", String(options.offset));
  }
  const qs = params.toString();
  return api<NotificationsListResponse>(`/notifications/pending${qs ? `?${qs}` : ""}`, user, undefined, "GET");
}

export function fetchUnreadNotificationsCount(user: User): Promise<UnreadNotificationsCountResponse> {
  return api<UnreadNotificationsCountResponse>("/notifications/unread-count", user, undefined, "GET");
}

export function fetchNotificationDetail(user: User, notificationId: string): Promise<UserNotification> {
  return api<UserNotification>(`/notifications/${encodeURIComponent(notificationId)}`, user, undefined, "GET");
}

export function markNotificationRead(user: User, notificationId: string): Promise<NotificationReadResponse> {
  return api<NotificationReadResponse>(
    `/notifications/${encodeURIComponent(notificationId)}/read`,
    user,
    {},
    "PATCH",
  );
}

export function markAllNotificationsRead(user: User): Promise<NotificationReadAllResponse> {
  return api<NotificationReadAllResponse>("/notifications/read-all", user, {}, "POST");
}