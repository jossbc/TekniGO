import { useEffect, useId, useRef, useState } from "react";
import { onIdTokenChanged, type User } from "firebase/auth";
import { Bell, Check, CheckCheck, ExternalLink, Wifi, WifiOff } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { errorMessage } from "../lib/api";
import { auth } from "../lib/firebase";
import {
  NOTIFICATION_FILTER_ORDER,
  NOTIFICATION_FILTER_STRATEGIES,
  NOTIFICATION_HEARTBEAT_INTERVAL_MS,
  NOTIFICATION_WS_CLOSE_FORBIDDEN,
  NOTIFICATION_WS_CLOSE_UNAUTHORIZED,
  applyNotificationReadLocally,
  applyNotificationSocketFrame,
  clampUnreadCount,
  emitGeneralNotificationLiveUpdate,
  emitUnreadNotificationsCount,
  emitProposalLiveUpdate,
  fetchNotifications,
  filterNotificationsByReadStatus,
  formatNotificationDateTegucigalpa,
  formatNotificationEventType,
  getAuthorizedNotificationRoute,
  getEmptyNotificationsMessage,
  getLatestNotificationCursor,
  getNotificationReconnectDelayMs,
  isNotificationPending,
  isProposalLiveNotification,
  markAllNotificationsRead,
  markNotificationRead,
  mergeNotifications,
  resolveNotificationsWsUrl,
  type NotificationConnectionState,
  type NotificationFilter,
  type UserNotification,
} from "../lib/notifications";

type NotificationTrayProps = {
  user?: User | null;
};

type NotificationFilterBarProps = {
  activeFilter: NotificationFilter;
  unreadCount: number;
  onSelect: (filter: NotificationFilter) => void;
};

function NotificationFilterBar({
  activeFilter,
  unreadCount,
  onSelect,
}: NotificationFilterBarProps) {
  return (
    <div className="notification-tray-filters" role="tablist" aria-label="Filtrar notificaciones">
      {NOTIFICATION_FILTER_ORDER.map((filterKey) => {
        const strategy = NOTIFICATION_FILTER_STRATEGIES[filterKey];
        const isActive = activeFilter === filterKey;
        return (
          <button
            key={filterKey}
            type="button"
            role="tab"
            aria-selected={isActive}
            className={`notification-filter-tab${isActive ? " notification-filter-tab--active" : ""}`}
            onClick={() => onSelect(filterKey)}
          >
            <span>{strategy.label}</span>
            {filterKey === "PENDIENTE" && (
              <span
                className="notification-filter-count"
                aria-label={`${unreadCount} pendientes`}
              >
                {unreadCount > 99 ? "99+" : unreadCount}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

type NotificationTrayItemProps = {
  item: UserNotification;
  isBusy: boolean;
  onMarkRead: (item: UserNotification) => void;
  onOpenDetail: (item: UserNotification) => void;
};

function NotificationTrayItem({
  item,
  isBusy,
  onMarkRead,
  onOpenDetail,
}: NotificationTrayItemProps) {
  const meta = formatNotificationEventType(item.tipo_evento);
  const route = getAuthorizedNotificationRoute(item.enlace_ruta);
  const dateLabel = formatNotificationDateTegucigalpa(item);
  const pending = isNotificationPending(item);
  const readDateLabel = !pending && item.leida_en ? formatNotificationDateTegucigalpa(item.leida_en) : null;

  return (
    <li>
      <article
        className={`notification-item${pending ? " notification-item--unread" : " notification-item--read"}`}
        data-read-status={pending ? "PENDIENTE" : "LEIDA"}
      >
        <div className="notification-item-meta">
          <div className="notification-item-badges">
            <span className={`notification-badge notification-badge--${meta.category}`}>
              {meta.label}
            </span>
            <span
              className={`notification-status-pill ${
                pending ? "notification-status-pill--pending" : "notification-status-pill--read"
              }`}
            >
              <span className="notification-status-dot" aria-hidden="true" />
              {pending ? "Pendiente" : "Leída"}
            </span>
          </div>
          <time dateTime={item.creada_en} className="notification-item-date">
            {dateLabel}
          </time>
        </div>

        <strong className="notification-item-title">{item.titulo}</strong>
        <p className="notification-item-summary">{item.resumen}</p>

        <div className="notification-item-actions">
          {route ? (
            <button
              type="button"
              className="notification-item-link-btn"
              onClick={() => onOpenDetail(item)}
              disabled={isBusy}
            >
              Ver detalle <ExternalLink size={12} aria-hidden="true" />
            </button>
          ) : (
            <span />
          )}

          {pending ? (
            <button
              type="button"
              className="notification-mark-read-btn"
              onClick={() => onMarkRead(item)}
              disabled={isBusy}
              aria-label={`Marcar como leída: ${item.titulo}`}
            >
              <Check size={13} aria-hidden="true" />
              {isBusy ? "Guardando..." : "Marcar como leída"}
            </button>
          ) : (
            <span className="notification-item-read-at">
              <CheckCheck size={12} aria-hidden="true" />
              {readDateLabel ? `Leída · ${readDateLabel}` : "Leída"}
            </span>
          )}
        </div>
      </article>
    </li>
  );
}

export default function NotificationTray({ user }: NotificationTrayProps) {
  const navigate = useNavigate();
  const [resolvedUser, setResolvedUser] = useState<User | null>(() => user ?? auth?.currentUser ?? null);
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<UserNotification[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [connectionState, setConnectionState] = useState<NotificationConnectionState>("idle");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [markingAll, setMarkingAll] = useState(false);
  const [activeFilter, setActiveFilter] = useState<NotificationFilter>("TODAS");
  const [filterLoading, setFilterLoading] = useState(false);

    const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const itemsRef = useRef<UserNotification[]>([]);
  const unreadCountRef = useRef<number>(0);
  const activeFilterRef = useRef<NotificationFilter>("TODAS");
  const panelId = useId();

  useEffect(() => {
    itemsRef.current = items;
  }, [items]);

  useEffect(() => {
    const safeCount = clampUnreadCount(unreadCount);
    unreadCountRef.current = safeCount;
    emitUnreadNotificationsCount(safeCount);
  }, [unreadCount]);

  useEffect(() => {
    activeFilterRef.current = activeFilter;
  }, [activeFilter]);

  useEffect(() => {
    if (user !== undefined) {
      setResolvedUser(user ?? null);
      return;
    }
    setResolvedUser(auth?.currentUser ?? null);
    if (!auth) return;
    return onIdTokenChanged(auth, (nextUser) => {
      setResolvedUser(nextUser);
    });
  }, [user]);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent) {
      if (!containerRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false);
        triggerRef.current?.focus();
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

    function emitLiveUpdatesForNewItems(newlyArrived: UserNotification[]) {
    for (const item of newlyArrived) {
      emitGeneralNotificationLiveUpdate(item);
      if (isProposalLiveNotification(item)) {
        emitProposalLiveUpdate({
          tipo_evento: item.tipo_evento,
          solicitud_id:
            typeof item.metadatos_seguros?.solicitud_id === "string"
              ? item.metadatos_seguros.solicitud_id
              : item.recurso_id,
          modalidad:
            typeof item.metadatos_seguros?.modalidad === "string"
              ? item.metadatos_seguros.modalidad
              : "ABIERTA",
          notification_id: item.id,
        });
      }
    }
  }

  function processIncomingBatch(incoming: UserNotification[], nextUnread?: number) {
    if (!incoming.length) {
      if (typeof nextUnread === "number") setUnreadCount(clampUnreadCount(nextUnread));
      return;
    }
    const existingIds = new Set(itemsRef.current.map((entry) => entry.id));
    const newlyArrived = incoming.filter((entry) => entry?.id && !existingIds.has(entry.id));
    const newlyArrivedUnread = newlyArrived.filter((entry) => isNotificationPending(entry)).length;

    setItems((prev) => {
      const merged = mergeNotifications(prev, incoming);
      itemsRef.current = merged;
      if (typeof nextUnread === "number") {
        setUnreadCount(clampUnreadCount(nextUnread));
      } else if (newlyArrivedUnread > 0) {
        setUnreadCount((prevCount) =>
          clampUnreadCount(
            Math.max(
              prevCount + newlyArrivedUnread,
              merged.filter((entry) => isNotificationPending(entry)).length,
            ),
          ),
        );
      }
      return merged;
    });

    emitLiveUpdatesForNewItems(newlyArrived);
  }

  useEffect(() => {
    const activeUser = resolvedUser;
    if (!activeUser) {
      setItems([]);
      setUnreadCount(0);
      setConnectionState("idle");
      return;
    }

    let cancelled = false;
    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    let heartbeatTimer: ReturnType<typeof setInterval> | undefined;
    let attempt = 0;

    async function loadSnapshot(isBackgroundRefresh: boolean) {
      if (!activeUser || cancelled) return;
      if (!isBackgroundRefresh && itemsRef.current.length === 0) {
        setLoading(true);
      }
      try {
        // Sin afterId/since para sincronizar también cambios de lectura hechos desde otras sesiones (CA-05)
        const data = await fetchNotifications(activeUser, {
          limit: 40,
          estado: "TODAS",
        });
        if (cancelled) return;
        setLoadError("");
        processIncomingBatch(data.notificaciones || [], data.no_leidas);
      } catch (err) {
        if (!cancelled && !isBackgroundRefresh) {
          setLoadError(errorMessage(err));
        }
      } finally {
        if (!cancelled && !isBackgroundRefresh) {
          setLoading(false);
        }
      }
    }

    function stopHeartbeat() {
      if (heartbeatTimer !== undefined) {
        clearInterval(heartbeatTimer);
        heartbeatTimer = undefined;
      }
    }

    async function connectWs(forceTokenRefresh = false) {
      if (cancelled || !activeUser) return;
      if (typeof WebSocket === "undefined") {
        setConnectionState("offline");
        return;
      }
      if (typeof navigator !== "undefined" && navigator.onLine === false) {
        setConnectionState("offline");
        return;
      }

      setConnectionState(attempt === 0 ? "connecting" : "reconnecting");
      let token = "";
      try {
        token = await activeUser.getIdToken(forceTokenRefresh);
      } catch {
        if (cancelled) return;
        scheduleReconnect(false);
        return;
      }
      if (cancelled) return;

      const cursor = getLatestNotificationCursor(itemsRef.current);
      const url = resolveNotificationsWsUrl(token, cursor);
      const socket = new WebSocket(url);
      ws = socket;

      socket.onopen = () => {
        if (cancelled) {
          socket.close();
          return;
        }
        attempt = 0;
        setConnectionState("connected");
        stopHeartbeat();
        heartbeatTimer = setInterval(() => {
          if (socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ type: "ping" }));
          }
        }, NOTIFICATION_HEARTBEAT_INTERVAL_MS);
      };

      socket.onmessage = (event) => {
        if (cancelled) return;
        try {
          const payload = JSON.parse(String(event.data || "{}"));
          const next = applyNotificationSocketFrame(
            { items: itemsRef.current, unreadCount: unreadCountRef.current },
            payload,
          );
          if (next.items !== itemsRef.current) {
            itemsRef.current = next.items;
            setItems(next.items);
          }
          setUnreadCount(next.unreadCount);
          if (next.newlyArrived.length > 0) {
            emitLiveUpdatesForNewItems(next.newlyArrived);
          }
        } catch {
          // Ignora tramas malformadas sin romper la sesión en vivo.
        }
      };

      socket.onerror = () => {
        if (cancelled) return;
        setConnectionState("reconnecting");
      };

      socket.onclose = (event) => {
        stopHeartbeat();
        if (cancelled) return;
        if (event.code === NOTIFICATION_WS_CLOSE_FORBIDDEN) {
          setConnectionState("offline");
          return;
        }
        const needsRefresh = event.code === NOTIFICATION_WS_CLOSE_UNAUTHORIZED;
        scheduleReconnect(needsRefresh);
      };
    }

    function scheduleReconnect(forceTokenRefresh: boolean) {
      if (cancelled) return;
      if (reconnectTimer !== undefined) {
        clearTimeout(reconnectTimer);
      }
      if (typeof navigator !== "undefined" && navigator.onLine === false) {
        setConnectionState("offline");
        return;
      }
      const delay = getNotificationReconnectDelayMs(attempt);
      attempt += 1;
      setConnectionState("reconnecting");
      reconnectTimer = setTimeout(() => {
        void loadSnapshot(true);
        void connectWs(forceTokenRefresh);
      }, delay);
    }

    function handleOnlineOrFocus() {
      if (cancelled) return;
      void void loadSnapshot(true);
      if (!ws || ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
        attempt = 0;
        void connectWs(false);
      } else if (ws.readyState === WebSocket.OPEN) {
        const cursor = getLatestNotificationCursor(itemsRef.current);
        ws.send(JSON.stringify({ type: "sync", after_id: cursor.afterId, since: cursor.since }));
      }
    }

    void loadSnapshot(false).then(() => {
      if (!cancelled) {
        void connectWs(false);
      }
    });

    if (typeof window !== "undefined") {
      window.addEventListener("online", handleOnlineOrFocus);
      window.addEventListener("focus", handleOnlineOrFocus);
    }

    return () => {
      cancelled = true;
      stopHeartbeat();
      if (reconnectTimer !== undefined) clearTimeout(reconnectTimer);
      if (typeof window !== "undefined") {
        window.removeEventListener("online", handleOnlineOrFocus);
        window.removeEventListener("focus", handleOnlineOrFocus);
      }
      if (ws) {
        ws.onclose = null;
        ws.close();
      }
    };
  }, [resolvedUser]);

    function handleToggleOpen() {
    const nextOpen = !open;
    setOpen(nextOpen);
    if (nextOpen && resolvedUser) {
      void fetchNotifications(resolvedUser, {
        limit: 40,
        estado: activeFilterRef.current,
      })
        .then((data) => {
          setLoadError("");
          processIncomingBatch(data.notificaciones || [], data.no_leidas);
        })
        .catch(() => {
          // Si falla el refresco silencioso, mantiene el estado actual sin cerrar la bandeja.
        });
    }
  }

    async function handleSelectFilter(nextFilter: NotificationFilter) {
    setActiveFilter(nextFilter);
    if (!resolvedUser) return;
    setFilterLoading(true);
    setLoadError("");
    try {
      const data = await fetchNotifications(resolvedUser, {
        limit: 40,
        estado: nextFilter,
      });
      setItems((prev) => mergeNotifications(prev, data.notificaciones || []));
      setUnreadCount(clampUnreadCount(data.no_leidas));
    } catch (err) {
      setLoadError(errorMessage(err));
    } finally {
      setFilterLoading(false);
    }
  }

  async function handleMarkAsReadOnly(item: UserNotification) {
    if (!resolvedUser || busyId || !isNotificationPending(item)) return;
    setBusyId(item.id);
    setLoadError("");
    try {
      const result = await markNotificationRead(resolvedUser, item.id);
      setItems((prev) =>
        result.notificacion
          ? mergeNotifications(prev, [result.notificacion])
          : applyNotificationReadLocally(prev, item.id),
      );
      setUnreadCount(clampUnreadCount(result.no_leidas));
    } catch (err) {
      setLoadError(errorMessage(err));
    } finally {
      setBusyId(null);
    }
  }

  async function handleOpenNotification(item: UserNotification) {
    if (!resolvedUser || busyId) return;
    setBusyId(item.id);
    setLoadError("");
    try {
      if (isNotificationPending(item)) {
        const result = await markNotificationRead(resolvedUser, item.id);
        setItems((prev) =>
          result.notificacion
            ? mergeNotifications(prev, [result.notificacion])
            : applyNotificationReadLocally(prev, item.id),
        );
        setUnreadCount(clampUnreadCount(result.no_leidas));
      }
      const safeRoute = getAuthorizedNotificationRoute(item.enlace_ruta);
      setOpen(false);
      if (safeRoute) {
        navigate(safeRoute);
      }
    } catch (err) {
      setLoadError(errorMessage(err));
    } finally {
      setBusyId(null);
    }
  }

  async function handleMarkAllRead() {
    if (!resolvedUser || markingAll || unreadCount === 0) return;
    setMarkingAll(true);
    setLoadError("");
    try {
      const res = await markAllNotificationsRead(resolvedUser);
      const readAt = new Date().toISOString();
      setItems((prev) =>
        prev.map((entry) =>
          isNotificationPending(entry)
            ? { ...entry, estado_lectura: "LEIDA", leida: true, leida_en: entry.leida_en ?? readAt }
            : entry,
        ),
      );
      setUnreadCount(clampUnreadCount(res.no_leidas));
    } catch (err) {
      setLoadError(errorMessage(err));
    } finally {
      setMarkingAll(false);
    }
  }

  const safeUnreadCount = clampUnreadCount(unreadCount);
  const visibleItems = filterNotificationsByReadStatus(items, activeFilter);
  const emptyMessage = getEmptyNotificationsMessage(activeFilter);
  const triggerLabel =
    safeUnreadCount > 0 ? `Notificaciones (${safeUnreadCount} sin leer)` : "Notificaciones";

  return (
    <div className="notification-tray" ref={containerRef}>
      <button
        type="button"
        ref={triggerRef}
        className="notification-tray-trigger"
        aria-label={triggerLabel}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={handleToggleOpen}
      >
        <Bell size={18} aria-hidden="true" />
        {safeUnreadCount > 0 && (
          <span className="notification-tray-badge" aria-hidden="true">
            {safeUnreadCount > 99 ? "99+" : safeUnreadCount}
          </span>
        )}
      </button>

      {open && (
        <div
          id={panelId}
          className="notification-tray-panel"
          role="dialog"
          aria-label="Bandeja de notificaciones"
        >
          <div className="notification-tray-header">
            <div className="notification-tray-title">
              <strong>Notificaciones</strong>
              <span
                className={`notification-live-status notification-live-status--${connectionState}`}
                title="Estado de conexión en tiempo real"
              >
                {connectionState === "connected" ? (
                  <>
                    <Wifi size={12} aria-hidden="true" /> En vivo
                  </>
                ) : connectionState === "reconnecting" || connectionState === "connecting" ? (
                  "Sincronizando..."
                ) : (
                  <>
                    <WifiOff size={12} aria-hidden="true" /> Offline
                  </>
                )}
              </span>
            </div>
            {safeUnreadCount > 0 && (
              <button
                type="button"
                className="notification-mark-all"
                onClick={() => void handleMarkAllRead()}
                disabled={markingAll}
              >
                <CheckCheck size={14} aria-hidden="true" />
                {markingAll ? "Marcando..." : "Marcar todas"}
              </button>
            )}
          </div>

          <NotificationFilterBar
            activeFilter={activeFilter}
            unreadCount={safeUnreadCount}
            onSelect={(nextFilter) => void handleSelectFilter(nextFilter)}
          />

          {loadError && (
            <p className="alert notification-tray-error" role="alert">
              {loadError}
            </p>
          )}

          {(loading || filterLoading) && visibleItems.length === 0 ? (
            <div className="notification-tray-empty">Cargando notificaciones...</div>
          ) : visibleItems.length === 0 ? (
            <div className="notification-tray-empty" role="status">
              {emptyMessage}
            </div>
          ) : (
            <ul className="notification-tray-list" aria-label="Lista de notificaciones">
              {visibleItems.map((item) => (
                <NotificationTrayItem
                  key={item.id}
                  item={item}
                  isBusy={busyId === item.id}
                  onMarkRead={(entry) => void handleMarkAsReadOnly(entry)}
                  onOpenDetail={(entry) => void handleOpenNotification(entry)}
                />
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
