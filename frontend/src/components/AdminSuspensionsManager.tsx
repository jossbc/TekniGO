import { useEffect, useState } from "react";
import type { User } from "firebase/auth";
import { AlertTriangle, CheckCircle2, RefreshCw, ShieldBan, UserCheck } from "lucide-react";
import { errorMessage } from "../lib/api";
import {
  DIMENSION_LABELS,
  MODERATION_EVENT_LABELS,
  SUSPENSION_CAUSE_LABELS,
  SUSPENSION_STATUS_LABELS,
  applyAccountSuspension,
  classifyAccountRestrictionBadges,
  closeAccountByAdmin,
  expireOverdueSuspensions,
  formatModerationDate,
  getAccountModerationDetail,
  listAccountsModeration,
  liftTemporalSuspension,
  resolveRecidivismAlertWithoutSuspension,
  type AccountModerationDetail,
  type AccountModerationFilter,
  type AccountModerationListResponse,
  type SuspensionCause,
  type SuspensionType,
} from "../lib/adminSuspensions";
import DossierModal from "./DossierModal";
import { Button } from "./ui";

const FILTER_OPTIONS: Array<[AccountModerationFilter, string]> = [
  ["TODOS", "Todas las cuentas"],
  ["SUSPENDIDOS", "Suspendidas por conducta"],
  ["RESTRICCION_FINANCIERA", "Restricción financiera"],
  ["CON_ALERTA_FALTAS", "Con alerta de 3.ª falta"],
  ["CERRADAS", "Cerradas"],
];

const BADGE_STYLES: Record<"danger" | "warning" | "neutral" | "success", { bg: string; color: string; border: string }> = {
  danger: { bg: "#fef2f2", color: "#b91c1c", border: "#fecaca" },
  warning: { bg: "#fffbeb", color: "#b45309", border: "#fde68a" },
  neutral: { bg: "#f1f5f9", color: "#475569", border: "#cbd5e1" },
  success: { bg: "#f0fdf4", color: "#15803d", border: "#bbf7d0" },
};

export default function AdminSuspensionsManager({ user }: { user: User }) {
  const [filter, setFilter] = useState<AccountModerationFilter>("TODOS");
  const [search, setSearch] = useState("");
  const [appliedSearch, setAppliedSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [list, setList] = useState<AccountModerationListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refreshTick, setRefreshTick] = useState(0);

  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<AccountModerationDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [actionNotice, setActionNotice] = useState("");

  // Formulario de aplicación de suspensión (CA-01)
  const [tipo, setTipo] = useState<SuspensionType>("TEMPORAL");
  const [causa, setCausa] = useState<SuspensionCause>("CONDUCTA");
  const [motivo, setMotivo] = useState("");
  const [venceLocal, setVenceLocal] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [alertObs, setAlertObs] = useState("");
  const [resolvingAlert, setResolvingAlert] = useState(false);
  const [liftReason, setLiftReason] = useState("");
  const [lifting, setLifting] = useState(false);
  const [expiringOverdue, setExpiringOverdue] = useState(false);
  const [closeReason, setCloseReason] = useState("");
  const [closingAccount, setClosingAccount] = useState(false);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    listAccountsModeration(user, { search: appliedSearch, filtro: filter, offset, limit: 25 })
      .then((res) => {
        if (active) setList(res);
      })
      .catch((err) => {
        if (active) setError(errorMessage(err));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [user, appliedSearch, filter, offset, refreshTick]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      setDetailError("");
      return;
    }
    let active = true;
    setDetailLoading(true);
    setDetailError("");
    getAccountModerationDetail(user, selectedId)
      .then((res) => {
        if (active) setDetail(res);
      })
      .catch((err) => {
        if (active) setDetailError(errorMessage(err));
      })
      .finally(() => {
        if (active) setDetailLoading(false);
      });
    return () => {
      active = false;
    };
  }, [user, selectedId, refreshTick]);

  async function handleApplySuspension(event: React.FormEvent) {
    event.preventDefault();
    if (!detail || submitting) return;
    setDetailError("");
    setActionNotice("");

    if (motivo.trim().length < 5) {
      setDetailError("Ingresa un motivo detallado (mínimo 5 caracteres).");
      return;
    }
    if (tipo === "TEMPORAL" && !venceLocal) {
      setDetailError("Selecciona la fecha y hora de vencimiento para una suspensión temporal.");
      return;
    }

    setSubmitting(true);
    try {
      const venceIso = tipo === "TEMPORAL" && venceLocal ? new Date(venceLocal).toISOString() : null;
      const alertId = detail.resumen_faltas.evento_reincidencia_activo?.id || null;
      await applyAccountSuspension(user, detail.usuario.usuario_id, {
        tipo,
        causa,
        motivo: motivo.trim(),
        vence_en: venceIso,
        alerta_reincidencia_id: causa === "REINCIDENCIA_FALTAS" ? alertId : null,
      });
      setMotivo("");
      setVenceLocal("");
      setActionNotice(
        tipo === "TEMPORAL"
          ? "Suspensión temporal aplicada y registrada en auditoría."
          : "Suspensión definitiva aplicada y registrada en auditoría.",
      );
      setRefreshTick((v) => v + 1);
    } catch (err) {
      setDetailError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

    async function handleDismissRecidivismAlert() {
    const activeAlert = detail?.resumen_faltas.evento_reincidencia_activo;
    if (!activeAlert || resolvingAlert) return;
    if (alertObs.trim().length < 5) {
      setDetailError("Ingresa observaciones (mínimo 5 caracteres) para registrar tu decisión sobre la alerta.");
      return;
    }
    setResolvingAlert(true);
    setDetailError("");
    setActionNotice("");
    try {
      await resolveRecidivismAlertWithoutSuspension(user, activeAlert.id, alertObs);
      setAlertObs("");
      setActionNotice("Alerta de reincidencia atendida sin aplicar suspensión. Decisión registrada en auditoría.");
      setRefreshTick((v) => v + 1);
    } catch (err) {
      setDetailError(errorMessage(err));
    } finally {
      setResolvingAlert(false);
    }
  }

    async function handleLiftSuspension() {
    const activeSusp = detail?.estado_operativo.suspension_activa;
    if (!activeSusp || lifting) return;
    if (liftReason.trim().length < 5) {
      setDetailError("Ingresa un motivo de levantamiento (mínimo 5 caracteres).");
      return;
    }
    setLifting(true);
    setDetailError("");
    setActionNotice("");
    try {
      const res = await liftTemporalSuspension(user, activeSusp.id, liftReason);
      setLiftReason("");
      setActionNotice(
        res.operaciones_restituidas
          ? "Suspensión temporal levantada y operaciones restituidas."
          : "Suspensión temporal levantada; persisten otras restricciones independientes (verificación, disponibilidad o saldo).",
      );
      setRefreshTick((v) => v + 1);
    } catch (err) {
      setDetailError(errorMessage(err));
    } finally {
      setLifting(false);
    }
  }

  async function handleExpireOverdue() {
    if (expiringOverdue) return;
    setExpiringOverdue(true);
    setError("");
    try {
      await expireOverdueSuspensions(user);
      setRefreshTick((v) => v + 1);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setExpiringOverdue(false);
    }
  }

    async function handleCloseAccount() {
    if (!detail || closingAccount) return;
    if (closeReason.trim().length < 5) {
      setDetailError("Ingresa el motivo de cierre de cuenta (mínimo 5 caracteres).");
      return;
    }
    setClosingAccount(true);
    setDetailError("");
    setActionNotice("");
    try {
      const res = await closeAccountByAdmin(user, detail.usuario.usuario_id, closeReason);
      setCloseReason("");
      setActionNotice(
        `Cierre de cuenta tramitado: acceso operativo revocado, identidad bloqueada y baja lógica aplicada a ${res.total_documentos_baja_logica} documento(s).`,
      );
      setRefreshTick((v) => v + 1);
    } catch (err) {
      setDetailError(errorMessage(err));
    } finally {
      setClosingAccount(false);
    }
  }

  return (
    <section aria-label="Moderación y suspensiones de cuentas">
      <p style={{ marginTop: 0, color: "#475569" }}>
        Administra suspensiones temporales o definitivas por conducta. La restricción financiera por saldo,
        la verificación documental y la disponibilidad se gestionan como dimensiones independientes.
      </p>

      <div className="admin-toolbar" style={{ flexWrap: "wrap", gap: 10 }}>
        <div className="dossier-filters" role="group" aria-label="Filtrar cuentas">
          {FILTER_OPTIONS.map(([val, label]) => (
            <Button
              key={val}
              type="button"
              className={filter === val ? "" : "button-outline"}
              aria-pressed={filter === val}
              onClick={() => {
                setFilter(val);
                setOffset(0);
              }}
            >
              {label}
            </Button>
          ))}
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            setOffset(0);
            setAppliedSearch(search);
          }}
          style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}
        >
          <input
            type="search"
            aria-label="Buscar cuenta por nombre, correo, DNI o teléfono"
            placeholder="Nombre, correo, DNI o teléfono…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ padding: "8px 12px", borderRadius: 8, border: "1px solid #cbd5e1", minWidth: 230 }}
          />
          <Button type="submit" className="button-outline" style={{ width: "auto" }}>
            Buscar
          </Button>
          <Button
            type="button"
            className="button-outline"
            style={{ width: "auto" }}
            disabled={expiringOverdue}
            onClick={() => void handleExpireOverdue()}
          >
            {expiringOverdue ? "Revisando…" : "Procesar vencimientos"}
          </Button>
          <Button
            type="button"
            className="button-outline"
            style={{ width: "auto" }}
            onClick={() => setRefreshTick((v) => v + 1)}
          >
            <RefreshCw size={15} /> Actualizar
          </Button>
        </form>
      </div>

      {error && (
        <p className="alert" role="alert">
          <AlertTriangle size={16} /> {error}
        </p>
      )}

      {loading && !list ? (
        <p role="status">Cargando cuentas…</p>
      ) : list ? (
        <section className="account-card" style={{ marginBlock: 20 }}>
          <h2>Cuentas registradas ({list.total})</h2>
          {list.cuentas.length === 0 ? (
            <p>No se encontraron cuentas para este filtro.</p>
          ) : (
            <div className="admin-table-wrap">
              <table className="admin-dossier-table">
                <thead>
                  <tr>
                    <th>Usuario</th>
                    <th>Roles</th>
                    <th>Estado operativo y dimensiones</th>
                    <th>Faltas</th>
                    <th>Acciones</th>
                  </tr>
                </thead>
                <tbody>
                  {list.cuentas.map((row) => {
                    const badges = classifyAccountRestrictionBadges(row);
                    return (
                      <tr key={row.usuario_id}>
                        <td>
                          <strong>{row.nombre_completo}</strong>
                          <div style={{ fontSize: 12, color: "#64748b" }}>{row.correo}</div>
                        </td>
                        <td>{row.roles.join(", ") || "Sin roles"}</td>
                        <td>
                          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                            {badges.map((b) => {
                              const st = BADGE_STYLES[b.tone];
                              return (
                                <span
                                  key={b.code}
                                  style={{
                                    fontSize: 11.5,
                                    fontWeight: 700,
                                    padding: "2px 8px",
                                    borderRadius: 999,
                                    background: st.bg,
                                    color: st.color,
                                    border: `1px solid ${st.border}`,
                                  }}
                                >
                                  {b.label}
                                </span>
                              );
                            })}
                          </div>
                        </td>
                        <td>
                          <strong>{row.faltas_no_justificadas}</strong> / 3
                        </td>
                        <td>
                          <Button
                            type="button"
                            className="button-outline"
                            style={{ width: "auto" }}
                            onClick={() => {
                              setActionNotice("");
                              setDetailError("");
                              setSelectedId(row.usuario_id);
                            }}
                          >
                            <ShieldBan size={15} /> Gestionar
                          </Button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          <div className="admin-toolbar admin-pagination">
            <Button
              type="button"
              className="button-outline"
              disabled={offset === 0}
              onClick={() => setOffset((v) => Math.max(0, v - 25))}
            >
              Anterior
            </Button>
            <Button
              type="button"
              className="button-outline"
              disabled={offset + 25 >= list.total}
              onClick={() => setOffset((v) => v + 25)}
            >
              Siguiente
            </Button>
          </div>
        </section>
      ) : null}

      {selectedId && (
        <DossierModal onClose={() => setSelectedId("")}>
          {actionNotice && (
            <p className="success-alert" role="status">
              <CheckCircle2 size={16} /> {actionNotice}
            </p>
          )}
          {detailError && (
            <p className="alert" role="alert">
              <AlertTriangle size={16} /> {detailError}
            </p>
          )}

          {detailLoading && !detail ? (
            <p role="status">Cargando detalle de moderación…</p>
          ) : detail ? (
            <section aria-label="Detalle de moderación de cuenta" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
              <div>
                <h2 style={{ marginBottom: 4 }}>{detail.usuario.nombre_completo}</h2>
                <p style={{ margin: 0, fontSize: 13.5, color: "#475569" }}>
                  {detail.usuario.correo} · Roles: <strong>{detail.usuario.roles.join(", ") || "Ninguno"}</strong>
                </p>
                <p style={{ margin: "4px 0 0", fontSize: 13, color: "#64748b" }}>
                  DNI: <code>{detail.usuario.dni}</code> · Teléfono: <code>{detail.usuario.telefono}</code>
                </p>
              </div>

              {/* Dimensiones independientes (CA-05, CA-07) */}
              <div style={{ padding: 12, borderRadius: 10, background: "#f8fafc", border: "1px solid #e2e8f0" }}>
                <h3 style={{ marginTop: 0, marginBottom: 8 }}>Dimensiones operativas independientes</h3>
                <ul style={{ margin: 0, paddingLeft: 18, display: "flex", flexDirection: "column", gap: 6 }}>
                  {detail.estado_operativo.dimensiones.map((dim) => (
                    <li key={dim.dimension} style={{ fontSize: 13.5 }}>
                      <strong>{DIMENSION_LABELS[dim.dimension]}:</strong>{" "}
                      <span style={{ color: dim.habilitada ? "#15803d" : "#b91c1c", fontWeight: 700 }}>
                        {dim.habilitada ? "Habilitada" : "Restringida"}
                      </span>
                      {dim.motivo ? ` — ${dim.motivo}` : ""}
                    </li>
                  ))}
                </ul>
              </div>

              {/* Alerta de tercera falta (CA-03 / CA-04) */}
              {detail.resumen_faltas.alerta_tercera_reincidencia_activa && (
                <div
                  role="status"
                  style={{ padding: 12, borderRadius: 10, background: "#fffbeb", border: "1px solid #fde68a" }}
                >
                  <strong style={{ color: "#92400e", display: "block", marginBottom: 4 }}>
                    Alerta activa por acumulación de faltas ({detail.resumen_faltas.total_faltas_no_justificadas} no justificadas)
                  </strong>
                  <p style={{ margin: 0, fontSize: 13, color: "#78350f" }}>
                    Esta alerta requiere decisión manual de administración; nunca aplica suspensión automática.
                  </p>
                </div>
              )}

              {/* Suspensión activa o formulario para aplicar */}
              {detail.resumen_faltas.alerta_tercera_reincidencia_activa && (
                <div
                  role="status"
                  style={{ padding: 12, borderRadius: 10, background: "#fffbeb", border: "1px solid #fde68a" }}
                >
                  <strong style={{ color: "#92400e", display: "block", marginBottom: 4 }}>
                    Alerta activa por acumulación de faltas ({detail.resumen_faltas.total_faltas_no_justificadas} no justificadas ·{" "}
                    {detail.resumen_faltas.total_faltas_excluidas_justificadas} justificadas)
                  </strong>
                  <p style={{ margin: "0 0 8px", fontSize: 13, color: "#78350f" }}>
                    Esta alerta requiere decisión manual de administración; nunca aplica suspensión automática.
                  </p>
                  {detail.resumen_faltas.evento_reincidencia_activo && (
                    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                      <input
                        type="text"
                        aria-label="Observaciones para atender la alerta sin suspender"
                        placeholder="Observaciones administrativas para desestimar sin suspender…"
                        value={alertObs}
                        onChange={(e) => setAlertObs(e.target.value)}
                        style={{ padding: "7px 10px", borderRadius: 6, border: "1px solid #fcd34d", background: "#fff" }}
                      />
                      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                        <Button
                          type="button"
                          className="button-outline"
                          style={{ width: "auto" }}
                          disabled={resolvingAlert}
                          onClick={() => void handleDismissRecidivismAlert()}
                        >
                          {resolvingAlert ? "Registrando…" : "Atender sin suspender"}
                        </Button>
                        <Button
                          type="button"
                          className="button-outline"
                          style={{ width: "auto" }}
                          onClick={() => setCausa("REINCIDENCIA_FALTAS")}
                        >
                          Preparar suspensión por reincidencia
                        </Button>
                      </div>
                    </div>
                  )}
                  
                </div>
              )}
              {/* Suspensión activa (con levantamiento temporal) o formulario para aplicar */}
              {detail.estado_operativo.suspension_activa ? (
                <div style={{ padding: 12, borderRadius: 10, background: "#fef2f2", border: "1px solid #fecaca" }}>
                  <h3 style={{ marginTop: 0, color: "#b91c1c" }}>Suspensión activa</h3>
                  <p style={{ margin: "4px 0", fontSize: 13.5 }}>
                    <strong>Tipo:</strong> {detail.estado_operativo.suspension_activa.tipo} ·{" "}
                    <strong>Causa:</strong>{" "}
                    {SUSPENSION_CAUSE_LABELS[detail.estado_operativo.suspension_activa.causa] ||
                      detail.estado_operativo.suspension_activa.causa}
                  </p>
                  <p style={{ margin: "4px 0", fontSize: 13.5 }}>
                    <strong>Motivo:</strong> {detail.estado_operativo.suspension_activa.motivo}
                  </p>
                  <p style={{ margin: "4px 0", fontSize: 12.5, color: "#64748b" }}>
                    Aplicada por {detail.estado_operativo.suspension_activa.responsable_admin_nombre || "Administración"} el{" "}
                    {formatModerationDate(detail.estado_operativo.suspension_activa.iniciada_en)}
                    {detail.estado_operativo.suspension_activa.vence_en
                      ? ` · Vence: ${formatModerationDate(detail.estado_operativo.suspension_activa.vence_en)}`
                      : " · Sin fecha de vencimiento"}
                  </p>
                  {detail.estado_operativo.suspension_activa.tipo === "TEMPORAL" && (
                    <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 8 }}>
                      <input
                        type="text"
                        aria-label="Motivo de levantamiento de suspensión temporal"
                        placeholder="Motivo del levantamiento anticipado (mínimo 5 caracteres)…"
                        value={liftReason}
                        onChange={(e) => setLiftReason(e.target.value)}
                        style={{ padding: "7px 10px", borderRadius: 6, border: "1px solid #fca5a5", background: "#fff" }}
                      />
                      <Button
                        type="button"
                        className="button-outline"
                        style={{ width: "auto" }}
                        disabled={lifting}
                        onClick={() => void handleLiftSuspension()}
                      >
                        {lifting ? "Levantando…" : "Levantar suspensión temporal"}
                      </Button>
                    </div>
                  )}
                </div>
              ) : !detail.estado_operativo.cuenta_cerrada ? (
                <form
                  onSubmit={handleApplySuspension}
                  style={{ padding: 14, borderRadius: 10, border: "1px solid #e2e8f0", background: "#fff" }}
                >
                  <h3 style={{ marginTop: 0 }}>Aplicar suspensión por conducta</h3>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 10 }}>
                    <label style={{ fontSize: 13, fontWeight: 600 }}>
                      Tipo de suspensión
                      <select
                        value={tipo}
                        onChange={(e) => setTipo(e.target.value as SuspensionType)}
                        style={{ display: "block", width: "100%", marginTop: 4, padding: 8, borderRadius: 6, border: "1px solid #cbd5e1" }}
                      >
                        <option value="TEMPORAL">Temporal (con vencimiento)</option>
                        <option value="DEFINITIVA">Definitiva</option>
                      </select>
                    </label>
                    <label style={{ fontSize: 13, fontWeight: 600 }}>
                      Causa disciplinaria
                      <select
                        value={causa}
                        onChange={(e) => setCausa(e.target.value as SuspensionCause)}
                        style={{ display: "block", width: "100%", marginTop: 4, padding: 8, borderRadius: 6, border: "1px solid #cbd5e1" }}
                      >
                        {Object.entries(SUSPENSION_CAUSE_LABELS).map(([k, label]) => (
                          <option key={k} value={k}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>

                  {tipo === "TEMPORAL" && (
                    <label style={{ display: "block", fontSize: 13, fontWeight: 600, marginBottom: 10 }}>
                      Fecha y hora de vencimiento
                      <input
                        type="datetime-local"
                        value={venceLocal}
                        onChange={(e) => setVenceLocal(e.target.value)}
                        required
                        style={{ display: "block", width: "100%", marginTop: 4, padding: 8, borderRadius: 6, border: "1px solid #cbd5e1" }}
                      />
                    </label>
                  )}

                  <label style={{ display: "block", fontSize: 13, fontWeight: 600, marginBottom: 10 }}>
                    Motivo detallado (auditoría obligatoria)
                    <textarea
                      rows={3}
                      maxLength={1500}
                      value={motivo}
                      onChange={(e) => setMotivo(e.target.value)}
                      placeholder="Describe el incumplimiento verificado y el respaldo de la decisión…"
                      required
                      style={{ display: "block", width: "100%", marginTop: 4, padding: 8, borderRadius: 6, border: "1px solid #cbd5e1" }}
                    />
                  </label>

                  <Button type="submit" disabled={submitting} style={{ width: "auto", background: "#dc2626", color: "#fff" }}>
                    <ShieldBan size={15} /> {submitting ? "Aplicando…" : "Aplicar suspensión"}
                  </Button>
                </form>
              ) : (
                <p style={{ margin: 0, color: "#475569" }}>
                  <UserCheck size={15} /> Esta cuenta se encuentra cerrada con baja lógica documental aplicada.
                </p>
              )}
                            {!detail.estado_operativo.cuenta_cerrada && (
                <details style={{ padding: 12, borderRadius: 10, border: "1px solid #cbd5e1", background: "#f8fafc" }}>
                  <summary style={{ cursor: "pointer", fontWeight: 700, fontSize: 13.5, color: "#334155" }}>
                    Tramitar cierre de cuenta y baja lógica documental
                  </summary>
                  <p style={{ margin: "8px 0", fontSize: 12.5, color: "#475569" }}>
                    Revoca el acceso operativo de la cuenta, aplica baja lógica a documentos y certificaciones
                    conforme a la política de conservación e impide reutilizar el DNI o teléfono, sin eliminar
                    historial ni deudas previas.
                  </p>
                  <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    <input
                      type="text"
                      aria-label="Motivo de cierre de cuenta"
                      placeholder="Motivo del cierre de cuenta (mínimo 5 caracteres)…"
                      value={closeReason}
                      onChange={(e) => setCloseReason(e.target.value)}
                      style={{ padding: "7px 10px", borderRadius: 6, border: "1px solid #cbd5e1", background: "#fff" }}
                    />
                    <Button
                      type="button"
                      className="button-outline"
                      style={{ width: "auto", color: "#b91c1c", borderColor: "#fca5a5" }}
                      disabled={closingAccount}
                      onClick={() => void handleCloseAccount()}
                    >
                      {closingAccount ? "Tramitando cierre…" : "Confirmar cierre de cuenta"}
                    </Button>
                  </div>
                </details>
              )}
              {/* Historial de suspensiones y auditoría */}
              <div>
                <h3>Historial de suspensiones ({detail.historial_suspensiones.length})</h3>
                {detail.historial_suspensiones.length === 0 ? (
                  <p style={{ fontSize: 13, color: "#64748b" }}>Esta cuenta no registra suspensiones previas.</p>
                ) : (
                  <ul style={{ paddingLeft: 18, fontSize: 13 }}>
                    {detail.historial_suspensiones.map((s) => (
                      <li key={s.id} style={{ marginBottom: 6 }}>
                        <strong>{s.tipo}</strong> · {SUSPENSION_STATUS_LABELS[s.estado]} ·{" "}
                        {SUSPENSION_CAUSE_LABELS[s.causa] || s.causa} — {s.motivo} ({formatModerationDate(s.iniciada_en)})
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div>
                <h3>Auditoría inmutable ({detail.auditoria.length})</h3>
                {detail.auditoria.length === 0 ? (
                  <p style={{ fontSize: 13, color: "#64748b" }}>Aún no hay eventos de moderación registrados.</p>
                ) : (
                  <ul style={{ paddingLeft: 18, fontSize: 12.5, color: "#334155" }}>
                    {detail.auditoria.map((a) => (
                      <li key={a.id} style={{ marginBottom: 5 }}>
                        <strong>{MODERATION_EVENT_LABELS[a.evento] || a.evento}</strong> ·{" "}
                        {formatModerationDate(a.creado_en)} · {a.actor_admin_nombre || a.proceso_responsable}: {a.motivo}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </section>
          ) : null}
        </DossierModal>
      )}
    </section>
  );
}