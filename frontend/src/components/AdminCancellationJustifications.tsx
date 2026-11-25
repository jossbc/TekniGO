import { useEffect, useRef, useState } from "react";
import type { User } from "firebase/auth";
import { FileText, RefreshCw } from "lucide-react";
import { Button } from "./ui";
import ModalShell from "./ModalShell";
import { errorMessage } from "../lib/api";
import {
  fetchAdminPrivateEvidenceBlob,
  listAdminJustificationsApi,
  resolveAdminJustificationApi,
  type AdminCancellationJustification,
} from "../lib/cancellations";

export default function AdminCancellationJustifications({ user }: { user: User }) {
  const [items, setItems] = useState<AdminCancellationJustification[]>([]);
  const [estado, setEstado] = useState("TODOS");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [selected, setSelected] = useState<AdminCancellationJustification | null>(null);
  const [observaciones, setObservaciones] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [modalError, setModalError] = useState("");
  const [preview, setPreview] = useState<{ url: string; type: string; label: string } | null>(null);
  const objectUrlRef = useRef("");

  async function load() {
    setLoading(true);
    setError("");
    try {
      setItems(await listAdminJustificationsApi(user, estado));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [user, estado]);

  useEffect(() => {
    return () => {
      if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
    };
  }, []);

  function closeModal() {
    if (objectUrlRef.current) {
      URL.revokeObjectURL(objectUrlRef.current);
      objectUrlRef.current = "";
    }
    setPreview(null);
    setSelected(null);
    setObservaciones("");
    setModalError("");
  }

  async function openEvidence(path: string, label: string) {
    setModalError("");
    try {
      const blob = await fetchAdminPrivateEvidenceBlob(user, path);
      if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
      objectUrlRef.current = URL.createObjectURL(blob);
      setPreview({ url: objectUrlRef.current, type: blob.type, label });
    } catch (err) {
      setModalError(errorMessage(err));
    }
  }

  async function resolve(decision: "APROBAR" | "RECHAZAR") {
    if (!selected || submitting) return;
    if (observaciones.trim().length < 5) {
      setModalError("Las observaciones deben tener al menos 5 caracteres.");
      return;
    }
    setSubmitting(true);
    setModalError("");
    try {
      await resolveAdminJustificationApi(user, selected.id, {
        decision,
        observaciones: observaciones.trim(),
      });
      setNotice(
        decision === "APROBAR"
          ? "Justificación aprobada: la falta quedó excluida del cómputo y se conservó la auditoría."
          : "Justificación rechazada: el incumplimiento permanece activo en el cómputo.",
      );
      closeModal();
      void load();
    } catch (err) {
      setModalError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="account-card" style={{ marginBlock: 24 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <div>
          <h2>Justificaciones de cancelación</h2>
          <p style={{ margin: 0, color: "#475569", fontSize: "14px" }}>
            Revisa apelaciones por cancelación con evidencia privada. Aprobar excluye la falta del cómputo conservando auditoría.
          </p>
        </div>
        <Button type="button" className="button-outline" onClick={() => void load()} disabled={loading}>
          <RefreshCw size={16} /> Actualizar
        </Button>
      </div>

      {notice && <p className="success-alert" role="status">{notice}</p>}
      {error && <p className="alert" role="alert">{error}</p>}

      <div className="admin-toolbar" style={{ marginBottom: 16 }}>
        <div className="dossier-filters" role="group" aria-label="Filtrar justificaciones por estado">
          {["TODOS", "PENDIENTE", "APROBADA", "RECHAZADA"].map((st) => (
            <Button
              key={st}
              type="button"
              className={estado === st ? "" : "button-outline"}
              onClick={() => setEstado(st)}
            >
              {st === "TODOS" ? "Todas" : st}
            </Button>
          ))}
        </div>
      </div>

      {loading && <p>Cargando justificaciones de cancelación…</p>}
      {!loading && items.length === 0 && (
        <p style={{ padding: "20px", textAlign: "center", color: "#64748b" }}>
          No hay justificaciones de cancelación para el filtro seleccionado.
        </p>
      )}

      {!loading && items.length > 0 && (
        <div className="admin-table-wrap">
          <table className="admin-dossier-table">
            <thead>
              <tr>
                <th>Solicitante</th>
                <th>Servicio</th>
                <th>Causa / Alerta</th>
                <th>Enviada</th>
                <th>Estado</th>
                <th>Revisión</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td>
                    <strong>{item.autor_nombre}</strong> ({item.responsable_rol || "—"})
                  </td>
                  <td>
                    <code>{item.solicitud_id ? `${item.solicitud_id.slice(0, 8)}…` : "—"}</code>
                    {item.modalidad && <div style={{ fontSize: 11, color: "#64748b" }}>{item.modalidad}</div>}
                  </td>
                  <td>
                    <div>{(item.causa_cancelacion || "—").replace(/_/g, " ")}</div>
                    {item.alerta_administrativa?.alerta_administrativa_activa && (
                      <span style={{ display: "inline-block", marginTop: 4, fontSize: 11, fontWeight: 700, padding: "2px 6px", borderRadius: 4, background: "#fef2f2", color: "#b91c1c" }}>
                        Alerta CA-10: {item.alerta_administrativa.faltas_no_justificadas_acumuladas} faltas (evaluar en HU-52)
                      </span>
                    )}
                  </td>
                  <td>{item.enviada_en || "—"}</td>
                  <td><strong>{item.estado}</strong></td>
                  <td>
                    <Button type="button" onClick={() => { setSelected(item); setObservaciones(item.observaciones_admin || ""); setModalError(""); setPreview(null); }}>
                      Revisar
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <ModalShell isOpen={Boolean(selected)} onClose={closeModal} title="Revisión de justificación de cancelación">
        {selected && (
          <div style={{ display: "grid", gap: 12, fontSize: 14 }}>
            {modalError && <p className="alert" role="alert">{modalError}</p>}

            <div style={{ background: "#f8fafc", padding: 12, borderRadius: 8, display: "grid", gap: 6 }}>
              <div><strong>Solicitante:</strong> {selected.autor_nombre} ({selected.responsable_rol})</div>
              <div><strong>Solicitud:</strong> <code>{selected.solicitud_id}</code> ({selected.modalidad}) · Estado previo: {selected.estado_previo}</div>
              <div><strong>Motivo de la cancelación:</strong> {selected.motivo_cancelacion}</div>
              <div><strong>Justificación enviada:</strong> {selected.motivo}</div>
              <div><strong>Estado actual de la falta:</strong> {selected.estado_incumplimiento}</div>
            </div>

            <div>
              <strong>Evidencias privadas ({(selected.evidencias_unificadas ?? []).length}):</strong>
              {!selected.evidencias_unificadas || selected.evidencias_unificadas.length === 0 ? (
                <p style={{ margin: "4px 0", color: "#64748b" }}>No se adjuntaron evidencias.</p>
              ) : (
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 6 }}>
                  {selected.evidencias_unificadas.map((ev, idx) => (
                    <Button
                      key={ev.path}
                      type="button"
                      className="button-outline"
                      onClick={() => void openEvidence(ev.path, `Evidencia #${idx + 1}`)}
                    >
                      <FileText size={14} /> Ver evidencia #{idx + 1}
                    </Button>
                  ))}
                </div>
              )}
            </div>

            {preview && (
              <div style={{ border: "1px solid #cbd5e1", borderRadius: 8, padding: 10, background: "#f8fafc" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                  <span style={{ fontSize: 12, fontWeight: 700 }}>{preview.label} ({preview.type || "archivo"})</span>
                  <a href={preview.url} target="_blank" rel="noopener noreferrer" style={{ fontSize: 12 }}>
                    Abrir en nueva pestaña
                  </a>
                </div>
                {preview.type.startsWith("image/") ? (
                  <img
                    src={preview.url}
                    alt={preview.label}
                    style={{ maxWidth: "100%", maxHeight: 360, minHeight: 40, borderRadius: 6, background: "#e2e8f0", objectFit: "contain" }}
                  />
                ) : (
                  <iframe src={preview.url} title={preview.label} style={{ width: "100%", height: 360, border: "none" }} />
                )}
              </div>
            )}

            {selected.estado === "PENDIENTE" ? (
              <div style={{ borderTop: "1px solid #e2e8f0", paddingTop: 12 }}>
                <label htmlFor="just-obs" style={{ display: "block", fontSize: 13, fontWeight: 700, marginBottom: 6 }}>
                  Observaciones de administración (mínimo 5 caracteres):
                </label>
                <textarea
                  id="just-obs"
                  rows={3}
                  value={observaciones}
                  onChange={(e) => setObservaciones(e.target.value)}
                  placeholder="Indica el fundamento para aprobar o rechazar esta justificación..."
                  style={{ width: "100%", padding: "8px 12px", borderRadius: 6, border: "1px solid #cbd5e1", marginBottom: 10 }}
                />
                <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                  <Button type="button" className="button-outline" disabled={submitting} onClick={() => void resolve("RECHAZAR")}>
                    Rechazar (mantener falta)
                  </Button>
                  <Button type="button" disabled={submitting} onClick={() => void resolve("APROBAR")}>
                    Aprobar (excluir falta)
                  </Button>
                </div>
              </div>
            ) : (
              <div style={{ background: "#f1f5f9", padding: 10, borderRadius: 6 }}>
                <strong>Resolución registrada ({selected.estado}):</strong> {selected.observaciones_admin}
                {selected.resuelta_en && <div style={{ fontSize: 12, color: "#64748b" }}>Resuelta: {selected.resuelta_en}</div>}
              </div>
            )}
          </div>
        )}
      </ModalShell>
    </section>
  );
}