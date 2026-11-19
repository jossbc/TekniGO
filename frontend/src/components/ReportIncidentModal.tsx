import { useEffect, useId, useRef, useState, type ChangeEvent } from "react";
import type { User } from "firebase/auth";
import { AlertTriangle, FileText, Lock, Paperclip, Trash2, X } from "lucide-react";
import { Button } from "./ui";
import { errorMessage } from "../lib/api";
import { createIncidentTicketApi, type TipoIncidencia } from "../lib/incidents";

interface Props {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: (mensaje: string) => void;
  user: User;
  solicitudId: string;
  rol: "CLIENTE" | "TECNICO";
}

const TIPO_OPTIONS: Array<{ value: TipoIncidencia; label: string; roles: Array<"CLIENTE" | "TECNICO"> }> = [
  { value: "INCUMPLIMIENTO_SERVICIO", label: "Incumplimiento del servicio pactado", roles: ["CLIENTE", "TECNICO"] },
  { value: "CONDUCTA_INAPROPIADA", label: "Conducta inadecuada o falta de respeto", roles: ["CLIENTE", "TECNICO"] },
  { value: "RIESGO_SEGURIDAD", label: "Riesgo de seguridad física o patrimonial", roles: ["CLIENTE", "TECNICO"] },
  { value: "DESACUERDO_AJUSTE", label: "Desacuerdo o rechazo de ajuste de precio", roles: ["CLIENTE", "TECNICO"] },
  { value: "FUERZA_MAYOR", label: "Fuerza mayor o imprevisto justificado (clima, corte, derrumbe)", roles: ["CLIENTE", "TECNICO"] },
  { value: "OTRO", label: "Otro motivo", roles: ["CLIENTE", "TECNICO"] },
];

export default function ReportIncidentModal({
  isOpen,
  onClose,
  onSuccess,
  user,
  solicitudId,
  rol,
}: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  const [tipo, setTipo] = useState<TipoIncidencia>("INCUMPLIMIENTO_SERVICIO");
  const [motivo, setMotivo] = useState("");
  const [esApelacion, setEsApelacion] = useState(false);
  const [archivos, setArchivos] = useState<string[]>([]);
  const [inputUrl, setInputUrl] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!isOpen) {
      setTipo(rol === "TECNICO" ? "FUERZA_MAYOR" : "INCUMPLIMIENTO_SERVICIO");
      setMotivo("");
      setEsApelacion(rol === "TECNICO");
      setArchivos([]);
      setInputUrl("");
      setError("");
      setSubmitting(false);
      return;
    }
    const el = dialog.current;
    if (el && typeof el.showModal === "function") el.showModal();
    document.body.style.overflow = "hidden";
    return () => {
      if (el) el.close();
      document.body.style.overflow = "";
    };
  }, [isOpen, rol]);

  // Si selecciona fuerza mayor, sugerir apelación justificada (CA-05)
  function handleTipoChange(val: TipoIncidencia) {
    setTipo(val);
    if (val === "FUERZA_MAYOR" || val === "DESACUERDO_AJUSTE") {
      setEsApelacion(true);
    }
  }

    function handleFileSelected(e: ChangeEvent<HTMLInputElement>) {
    if (!e.target.files) return;
    const fileList = Array.from(e.target.files);
    fileList.forEach((file) => {
      if (file.type.startsWith("image/")) {
        const reader = new FileReader();
        reader.onload = () => {
          if (typeof reader.result === "string") {
            setArchivos((prev) => Array.from(new Set([...prev, reader.result as string])));
          }
        };
        reader.readAsDataURL(file);
      } else {
        setArchivos((prev) => Array.from(new Set([...prev, file.name])));
      }
    });
    e.target.value = "";
  }

  function handleAddTextEvidence() {
    const clean = inputUrl.trim();
    if (!clean) return;
    setArchivos((prev) => (prev.includes(clean) ? prev : [...prev, clean]));
    setInputUrl("");
  }

  function handleRemoveEvidence(index: number) {
    setArchivos((prev) => prev.filter((_, idx) => idx !== index));
  }

  async function handleSubmit() {
    if (motivo.trim().length < 10) {
      setError("Por favor describe el motivo de la incidencia con al menos 10 caracteres.");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      // Patrón Facade: envía reporte bilateral con evidencias privadas (CA-01, CA-02)
      await createIncidentTicketApi(user, {
        solicitud_id: solicitudId,
        tipo,
        motivo: motivo.trim(),
        evidencias: archivos,
        es_apelacion_justificada: esApelacion,
      });

      onSuccess(
        "Reporte registrado exitosamente. Se ha generado un ticket prioritario en administración y se bloqueó preventivamente el cierre.",
      );
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  if (!isOpen) return null;

  return (
    <dialog
      ref={dialog}
      className="dossier-modal admin-panel"
      aria-labelledby={titleId}
      aria-modal="true"
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      <header className="dossier-modal-heading">
        <h2 id={titleId} style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <AlertTriangle color="#dc2626" size={20} />
          {rol === "CLIENTE" ? "Reportar problema o disputa" : "Reportar incidencia o justificación técnica"}
        </h2>
        <button
          type="button"
          className="dossier-modal-close"
          aria-label="Cerrar ventana"
          onClick={onClose}
          autoFocus
        >
          <X size={20} />
        </button>
      </header>

      <div className="dossier-modal-body" style={{ padding: "20px" }}>
        {error && <p className="alert" role="alert">{error}</p>}

        <p style={{ fontSize: "14px", color: "#475569", margin: "0 0 16px" }}>
          Al enviar este reporte, se generará un <strong>ticket prioritario</strong> en la bandeja de administración y se pausará temporalmente el cierre automático y la liberación de fondos mientras se resuelve.
        </p>

        {/* Tipo de incidencia */}
        <div style={{ marginBottom: "14px" }}>
          <label style={{ display: "block", fontSize: "13px", fontWeight: 700, marginBottom: "6px" }}>
            Tipo de situación o falta:
          </label>
          <select
            value={tipo}
            onChange={(e) => handleTipoChange(e.target.value as TipoIncidencia)}
            style={{ width: "100%", padding: "8px 12px", borderRadius: "6px", border: "1px solid #cbd5e1" }}
          >
            {TIPO_OPTIONS.filter((opt) => opt.roles.includes(rol)).map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </div>

        {/* Checkbox de apelación justificada (CA-05) */}
        <div style={{ marginBottom: "14px", background: "#f8fafc", padding: "10px", borderRadius: "6px", fontSize: "13px" }}>
          <label style={{ display: "flex", alignItems: "center", gap: "8px", cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={esApelacion}
              onChange={(e) => setEsApelacion(e.target.checked)}
            />
            <span>
              <strong>Apelación por causa justificada:</strong> Solicitar que esta incidencia se evalúe por fuerza mayor o conducta de la contraparte sin computar falta disciplinaria.
            </span>
          </label>
        </div>

               {/* Motivo detallado con indicador de longitud mínima */}
        <div style={{ marginBottom: "14px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" }}>
            <label style={{ fontSize: "13px", fontWeight: 700 }}>
              Descripción detallada del motivo y hechos:
            </label>
            <span
              style={{
                fontSize: "12px",
                fontWeight: 600,
                color: motivo.trim().length >= 10 ? "#16a34a" : "#dc2626",
              }}
            >
              {motivo.trim().length} / 10 caracteres mínimos
            </span>
          </div>
          <textarea
            rows={4}
            value={motivo}
            onChange={(e) => setMotivo(e.target.value)}
            placeholder="Explica detalladamente qué sucedió, fecha, conducta observada o impedimento fortuito (mínimo 10 caracteres)..."
            style={{
              width: "100%",
              padding: "10px 12px",
              borderRadius: "6px",
              border: `1px solid ${motivo.trim().length >= 10 ? "#cbd5e1" : (motivo.length > 0 ? "#fca5a5" : "#cbd5e1")}`,
              fontSize: "14px",
            }}
          />
          {motivo.length > 0 && motivo.trim().length < 10 && (
            <small style={{ color: "#dc2626", fontSize: "11px", display: "block", marginTop: "4px" }}>
              Faltan {10 - motivo.trim().length} caracteres para cumplir con la longitud mínima requerida.
            </small>
          )}
        </div>

        {/* Sección de adjuntar evidencias privadas (CA-01, CA-02) */}
        <div style={{ marginBottom: "18px", border: "1px dashed #cbd5e1", borderRadius: "8px", padding: "14px", background: "#fbfcfd" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "6px", fontWeight: 700, fontSize: "13px", color: "#1e293b", marginBottom: "4px" }}>
            <Paperclip size={16} /> Adjuntar evidencias privadas (fotos, documentos, capturas)
          </div>
          <p style={{ margin: "0 0 10px", fontSize: "12px", color: "#64748b" }}>
            <Lock size={12} style={{ display: "inline", verticalAlign: "middle", marginRight: "4px" }} />
            <strong>Privacidad garantizada:</strong> Solo tú y la administración autorizada podrán ver estas evidencias en el expediente de moderación.
          </p>

          <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", marginBottom: "10px" }}>
            <label style={{ cursor: "pointer" }}>
              <input
                type="file"
                multiple
                accept="image/*,.pdf,.doc,.docx"
                onChange={handleFileSelected}
                style={{ display: "none" }}
              />
              <span className="button button-outline" style={{ display: "inline-flex", alignItems: "center", gap: "6px", padding: "6px 12px", fontSize: "13px" }}>
                <Paperclip size={14} /> Seleccionar archivos
              </span>
            </label>

            <div style={{ display: "flex", gap: "6px", flex: 1, minWidth: "220px" }}>
              <input
                type="text"
                placeholder="O ingresa enlace o nombre de archivo..."
                value={inputUrl}
                onChange={(e) => setInputUrl(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); handleAddTextEvidence(); } }}
                style={{ flex: 1, padding: "6px 10px", borderRadius: "6px", border: "1px solid #cbd5e1", fontSize: "13px" }}
              />
              <Button type="button" className="button-outline" onClick={handleAddTextEvidence}>
                Agregar
              </Button>
            </div>
          </div>

          {archivos.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: "6px", marginTop: "8px" }}>
              {archivos.map((archivo, idx) => (
                <div
                  key={idx}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    background: "#f1f5f9",
                    padding: "6px 10px",
                    borderRadius: "6px",
                    fontSize: "13px",
                  }}
                >
                  <span style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                    <FileText size={14} color="#2563eb" /> {archivo}
                  </span>
                  <button
                    type="button"
                    onClick={() => handleRemoveEvidence(idx)}
                    aria-label={`Eliminar evidencia ${archivo}`}
                    style={{ background: "none", border: "none", cursor: "pointer", color: "#dc2626" }}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Acciones del modal */}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
          <Button type="button" className="button-outline" onClick={onClose} disabled={submitting}>
            Cancelar
          </Button>
          <Button
            type="button"
            onClick={handleSubmit}
            disabled={submitting || motivo.trim().length < 10}
            style={{ background: "#dc2626", color: "#fff" }}
          >
            {submitting ? "Enviando reporte…" : "Confirmar y enviar reporte"}
          </Button>
        </div>
      </div>
    </dialog>
  );
}