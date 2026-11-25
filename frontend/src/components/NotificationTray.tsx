import { useEffect, useId, useRef, useState } from "react";
import { onIdTokenChanged, type User } from "firebase/auth";
import { Bell, CheckCheck, ExternalLink, Wifi, WifiOff } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { errorMessage } from "../lib/api";
import { auth } from "../lib/firebase";
import {
  NOTIFICATION_HEARTBEAT_INTERVAL_MS,
  NOTIFICATION_WS_CLOSE_FORBIDDEN,
  NOTIFICATION_WS_CLOSE_UNAUTHORIZED,
  emitGeneralNotificationLiveUpdate,
  emitProposalLiveUpdate,
  fetchNotifications,
  formatNotificationDateTegucigalpa,
  formatNotificationEventType,
  getAuthorizedNotificationRoute,
  getLatestNotificationCursor,
  getNotificationReconnectDelayMs,
  isProposalLiveNotification,
  markAllNotificationsRead,
  markNotificationRead,
  mergeNotifications,
  resolveNotificationsWsUrl,
  type NotificationConnectionState,
  type UserNotification,
} from "../lib/notifications";

type NotificationTrayProps = {
  user?: User | null;
};

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

  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const itemsRef = useRef<UserNotification[]>([]);
  const panelId = useId();

  useEffect(() => {
    itemsRef.current = items;
  }, [items]);

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

  function processIncomingBatch(incoming: UserNotification[], nextUnread?: number) {
    if (!incoming.length) {
      if (typeof nextUnread === "number") setUnreadCount(nextUnread);
      return;
    }
    const existingIds = new Set(itemsRef.current.map((entry) => entry.id));
    const newlyArrived = incoming.filter((entry) => entry?.id && !existingIds.has(entry.id));
    const newlyArrivedUnread = newlyArrived.filter((entry) => !entry.leida).length;

    setItems((prev) => {
      const merged = mergeNotifications(prev, incoming);
      itemsRef.current = merged;
      if (typeof nextUnread === "number") {
        setUnreadCount(nextUnread);
      } else if (newlyArrivedUnread > 0) {
        setUnreadCount((prevCount) => Math.max(prevCount + newlyArrivedUnread, merged.filter((entry) => !entry.leida).length));
      }
      return merged;
    });

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

    async function loadInitialOrCatchup(isCatchup: boolean) {
      if (!activeUser || cancelled) return;
      if (!isCatchup && itemsRef.current.length === 0) {
        setLoading(true);
      }
      try {
        const cursor = isCatchup ? getLatestNotificationCursor(itemsRef.current) : undefined;
        const data = await fetchNotifications(activeUser, {
          limit: 40,
          afterId: cursor?.afterId,
          since: cursor?.since,
        });
        if (cancelled) return;
        setLoadError("");
        processIncomingBatch(data.notificaciones || [], data.no_leidas);
      } catch (err) {
        if (!cancelled && !isCatchup) {
          setLoadError(errorMessage(err));
        }
      } finally {
        if (!cancelled && !isCatchup) {
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
          if (payload.type === "connected" || payload.type === "pong") {
            if (typeof payload.no_leidas === "number") {
              setUnreadCount(payload.no_leidas);
            }
          } else if (payload.type === "notification" && payload.notificacion) {
            processIncomingBatch([payload.notificacion], payload.no_leidas);
          } else if (payload.type === "sync" && Array.isArray(payload.notificaciones)) {
            processIncomingBatch(payload.notificaciones, payload.no_leidas);
          } else if (payload.type === "read_sync" && payload.notification_id) {
            setItems((prev) =>
              prev.map((item) =>
                item.id === payload.notification_id ? { ...item, leida: true } : item,
              ),
            );
            if (typeof payload.no_leidas === "number") {
              setUnreadCount(payload.no_leidas);
            }
          } else if (payload.type === "read_all_sync") {
            setItems((prev) => prev.map((item) => ({ ...item, leida: true })));
            setUnreadCount(typeof payload.no_leidas === "number" ? payload.no_leidas : 0);
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
        void loadInitialOrCatchup(true);
        void connectWs(forceTokenRefresh);
      }, delay);
    }

    function handleOnlineOrFocus() {
      if (cancelled) return;
      void loadInitialOrCatchup(true);
      if (!ws || ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
        attempt = 0;
        void connectWs(false);
      } else if (ws.readyState === WebSocket.OPEN) {
        const cursor = getLatestNotificationCursor(itemsRef.current);
        ws.send(JSON.stringify({ type: "sync", after_id: cursor.afterId, since: cursor.since }));
      }
    }

    void loadInitialOrCatchup(false).then(() => {
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

  async function handleOpenNotification(item: UserNotification) {
    if (!resolvedUser || busyId) return;
    setBusyId(item.id);
    setLoadError("");
    try {
      if (!item.leida) {
        const result = await markNotificationRead(resolvedUser, item.id);
        setItems((prev) =>
          mergeNotifications(prev, result.notificacion ? [result.notificacion] : [{ ...item, leida: true }]),
        );
        setUnreadCount(result.no_leidas);
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
      setItems((prev) => prev.map((entry) => ({ ...entry, leida: true })));
      setUnreadCount(res.no_leidas);
    } catch (err) {
      setLoadError(errorMessage(err));
    } finally {
      setMarkingAll(false);
    }
  }

  const triggerLabel =
    unreadCount > 0 ? `Notificaciones (${unreadCount} sin leer)` : "Notificaciones";

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
        onClick={() => setOpen((value) => !value)}
      >
        <Bell size={18} aria-hidden="true" />
        {unreadCount > 0 && (
          <span className="notification-tray-badge" aria-hidden="true">
            {unreadCount > 99 ? "99+" : unreadCount}
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
            {unreadCount > 0 && (
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

          {loadError && (
            <p className="alert notification-tray-error" role="alert">
              {loadError}
            </p>
          )}

          {loading && items.length === 0 ? (
            <div className="notification-tray-empty">Cargando notificaciones...</div>
          ) : items.length === 0 ? (
            <div className="notification-tray-empty">
              No tienes notificaciones por el momento.
            </div>
          ) : (
            <ul className="notification-tray-list" aria-label="Lista de notificaciones">
              {items.map((item) => {
                const meta = formatNotificationEventType(item.tipo_evento);
                const route = getAuthorizedNotificationRoute(item.enlace_ruta);
                const dateLabel = formatNotificationDateTegucigalpa(item);
                const isBusy = busyId === item.id;
                return (
                  <li key={item.id}>
                    <button
                      type="button"
                      className={`notification-item${item.leida ? "" : " notification-item--unread"}`}
                      onClick={() => void handleOpenNotification(item)}
                      disabled={isBusy}
                    >
                      <div className="notification-item-meta">
                        <span className={`notification-badge notification-badge--${meta.category}`}>
                          {meta.label}
                        </span>
                        <time dateTime={item.creada_en} className="notification-item-date">
                          {dateLabel}
                        </time>
                      </div>
                      <strong className="notification-item-title">{item.titulo}</strong>
                      <p className="notification-item-summary">{item.resumen}</p>
                      {route && (
                        <span className="notification-item-link">
                          Ver detalle <ExternalLink size={12} aria-hidden="true" />
                        </span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
