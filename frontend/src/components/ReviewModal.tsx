import { useState, type FormEvent } from "react";
import type { User } from "firebase/auth";
import { Star, X, CheckCircle2, AlertCircle } from "lucide-react";
import { Button } from "./ui";
import { errorMessage } from "../lib/api";
import {
  submitServiceReview,
  TECHNICIAN_HIGHLIGHT_CHIPS,
  CLIENT_HIGHLIGHT_CHIPS,
  type SubmitReviewResponse,
} from "../lib/reviews";

interface ReviewModalProps {
  user: User;
  tipo_solicitud: "DIRECTA" | "ABIERTA";
  solicitud_id: string;
  evaluadoNombre: string;
  rolEvaluador: "CLIENTE" | "TECNICO"; // CLIENTE califica a técnico; TECNICO califica a cliente
  onClose: () => void;
  onSuccess: (result: SubmitReviewResponse) => void;
}

export function resolveReviewPublicationBanner(submittedResponse: SubmitReviewResponse): {
  isPublished: boolean;
  displayMessage: string;
  badgeText: string;
} {
  const isPublished =
    submittedResponse.estado_publicacion === "PUBLICADA" ||
    Boolean(submittedResponse.publicada ?? submittedResponse.publicada_inmediata);
  const isTimeout48h =
    isPublished &&
    (submittedResponse.motivo_publicacion === "EXPIRACION_SILENCIOSA_48H" ||
      (submittedResponse.mensaje.includes("48 horas") &&
        !submittedResponse.mensaje.includes("evaluación mutua")));
  const displayMessage =
    isTimeout48h && submittedResponse.mensaje.includes("evaluación mutua")
      ? "Calificación publicada inmediatamente al haberse cumplido el plazo de 48 horas."
      : submittedResponse.mensaje;
  const badgeText = isPublished
    ? isTimeout48h
      ? "Calificación publicada al cumplirse el plazo de 48 horas"
      : "Evaluación publicada inmediatamente"
    : "Evaluación guardada · Pendiente de publicación";
  return { isPublished, displayMessage, badgeText };
}

export default function ReviewModal({
  user,
  tipo_solicitud,
  solicitud_id,
  evaluadoNombre,
  rolEvaluador,
  onClose,
  onSuccess,
}: ReviewModalProps) {
  const [rating, setRating] = useState<number>(5);
  const [hoverRating, setHoverRating] = useState<number | null>(null);
  const [selectedChips, setSelectedChips] = useState<string[]>([]);
  const [comentario, setComentario] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [submittedResponse, setSubmittedResponse] = useState<SubmitReviewResponse | null>(null);

  const chips = rolEvaluador === "CLIENTE" ? TECHNICIAN_HIGHLIGHT_CHIPS : CLIENT_HIGHLIGHT_CHIPS;
  const isTargetTech = rolEvaluador === "CLIENTE";

  const toggleChip = (chip: string) => {
    setSelectedChips((prev) =>
      prev.includes(chip) ? prev.filter((c) => c !== chip) : [...prev, chip],
    );
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!comentario.trim()) {
      setError("Por favor escribe un comentario sobre tu experiencia.");
      return;
    }
    if (rating < 1 || rating > 5) {
      setError("Por favor selecciona una calificación de 1 a 5 estrellas.");
      return;
    }

    setBusy(true);
    setError("");

    try {
      const res = await submitServiceReview(user, {
        tipo_solicitud,
        solicitud_id,
        puntuacion: rating,
        comentario: comentario.trim(),
        aspectos_destacados: selectedChips,
      });
      const published = Boolean(
        res.publicada ?? res.publicada_inmediata ?? res.estado_publicacion === "PUBLICADA",
      );
      const normalizedRes: SubmitReviewResponse = {
        ...res,
        publicada: published,
      };
      setSubmittedResponse(normalizedRes);
      onSuccess(normalizedRes);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="modal-backdrop"
      style={{
        position: "fixed",
        inset: 0,
        backgroundColor: "rgba(15, 23, 42, 0.65)",
        backdropFilter: "blur(4px)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 9999,
        padding: "1rem",
      }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="review-modal-title"
    >
      <div
        className="account-card"
        style={{
          width: "100%",
          maxWidth: "520px",
          background: "#fff",
          borderRadius: "1.25rem",
          boxShadow: "0 25px 50px -12px rgba(0, 0, 0, 0.25)",
          padding: "2rem",
          position: "relative",
          animation: "fadeIn 0.2s ease-out",
        }}
      >
        <button
          type="button"
          onClick={onClose}
          aria-label="Cerrar ventana"
          style={{
            position: "absolute",
            top: "1.25rem",
            right: "1.25rem",
            background: "#f3f4f6",
            border: "none",
            borderRadius: "50%",
            width: "36px",
            height: "36px",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            cursor: "pointer",
            color: "#6b7280",
          }}
        >
          <X size={18} />
        </button>

        {submittedResponse ? (
          (() => {
            const { isPublished, displayMessage, badgeText } = resolveReviewPublicationBanner(submittedResponse);
            return (
              <div style={{ textAlign: "center", padding: "1rem 0" }}>
                <div
                  style={{
                    width: "60px",
                    height: "60px",
                    borderRadius: "50%",
                    background: "#ecfdf5",
                    color: "#10b981",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    margin: "0 auto 1.25rem",
                  }}
                >
                  <CheckCircle2 size={36} />
                </div>
                <h2 id="review-modal-title" style={{ fontSize: "1.5rem", marginBottom: "0.5rem" }}>
                  ¡Gracias por tu evaluación!
                </h2>
                <p style={{ color: "var(--muted)", lineHeight: 1.6, marginBottom: "1.5rem" }}>
                  {displayMessage}
                </p>
                <div
                  style={{
                    display: "inline-block",
                    padding: "0.5rem 1rem",
                    borderRadius: "999px",
                    fontWeight: 600,
                    fontSize: "0.9rem",
                    background: isPublished ? "#ecfdf5" : "#eff6ff",
                    color: isPublished ? "#065f46" : "#1e40af",
                    marginBottom: "1.5rem",
                  }}
                >
                  {badgeText}
                </div>
                <div>
                  <Button type="button" onClick={onClose} style={{ width: "auto", minWidth: "140px" }}>
                    Aceptar
                  </Button>
                </div>
              </div>
            );
          })()
        ) : (
          <form onSubmit={handleSubmit}>
            <div style={{ textAlign: "center", marginBottom: "1.5rem" }}>
              <span
                style={{
                  display: "inline-block",
                  fontSize: "0.8rem",
                  fontWeight: 700,
                  textTransform: "uppercase",
                  letterSpacing: "0.05em",
                  color: "var(--blue)",
                  background: "#eff6ff",
                  padding: "0.25rem 0.75rem",
                  borderRadius: "999px",
                  marginBottom: "0.5rem",
                }}
              >
                {isTargetTech ? "Califica al profesional" : "Califica al cliente"}
              </span>
              <h2
                id="review-modal-title"
                style={{ fontSize: "1.6rem", margin: "0.25rem 0 0.5rem", color: "var(--ink)" }}
              >
                ¿Cómo estuvo el servicio con {evaluadoNombre}?
              </h2>
              <p style={{ color: "#6b7280", fontSize: "0.95rem", margin: 0 }}>
                Tu opinión nos ayuda a mantener una comunidad segura y de alta calidad.
              </p>
            </div>

            {error && (
              <div
                className="alert"
                role="alert"
                style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: "1.25rem" }}
              >
                <AlertCircle size={18} />
                <span>{error}</span>
              </div>
            )}

            {/* Selector de estrellas interactivo */}
            <div
              style={{
                display: "flex",
                justifyContent: "center",
                alignItems: "center",
                gap: "0.5rem",
                marginBottom: "1.5rem",
              }}
              onMouseLeave={() => setHoverRating(null)}
            >
              {[1, 2, 3, 4, 5].map((star) => {
                const isFilled = (hoverRating !== null ? hoverRating : rating) >= star;
                return (
                  <button
                    key={star}
                    type="button"
                    onClick={() => setRating(star)}
                    onMouseEnter={() => setHoverRating(star)}
                    style={{
                      background: "transparent",
                      border: "none",
                      cursor: "pointer",
                      padding: "4px",
                      color: isFilled ? "#f59e0b" : "#d1d5db",
                      transition: "transform 0.15s ease, color 0.15s ease",
                      transform: (hoverRating ?? rating) === star ? "scale(1.15)" : "scale(1)",
                    }}
                    aria-label={`${star} ${star === 1 ? "estrella" : "estrellas"}`}
                  >
                    <Star size={34} fill={isFilled ? "currentColor" : "none"} strokeWidth={1.75} />
                  </button>
                );
              })}
            </div>

            {/* Chips de aspectos destacados (tipo PedidosYa / Uber) */}
            <div style={{ marginBottom: "1.5rem" }}>
              <label
                style={{
                  display: "block",
                  fontSize: "0.9rem",
                  fontWeight: 650,
                  color: "var(--ink)",
                  marginBottom: "0.6rem",
                }}
              >
                ¿Qué aspectos destacarías? <span style={{ color: "#6b7280", fontWeight: 400 }}>(opcional)</span>
              </label>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
                {chips.map((chip) => {
                  const active = selectedChips.includes(chip);
                  return (
                    <button
                      key={chip}
                      type="button"
                      onClick={() => toggleChip(chip)}
                      style={{
                        padding: "0.4rem 0.85rem",
                        borderRadius: "999px",
                        fontSize: "0.88rem",
                        fontWeight: active ? 650 : 500,
                        border: active ? "1.5px solid var(--blue)" : "1.5px solid #e5e7eb",
                        background: active ? "#eff6ff" : "#f9fafb",
                        color: active ? "var(--blue)" : "#374151",
                        cursor: "pointer",
                        transition: "all 0.15s ease",
                      }}
                    >
                      {active ? `✓ ${chip}` : `+ ${chip}`}
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Cuadro de texto para comentario obligatorio */}
            <div style={{ marginBottom: "1.5rem" }}>
              <label
                htmlFor="review-comment"
                style={{
                  display: "block",
                  fontSize: "0.9rem",
                  fontWeight: 650,
                  color: "var(--ink)",
                  marginBottom: "0.5rem",
                }}
              >
                Cuéntanos tu experiencia <span style={{ color: "#dc2626" }}>*</span>
              </label>
              <textarea
                id="review-comment"
                rows={4}
                required
                value={comentario}
                onChange={(e) => setComentario(e.target.value)}
                placeholder={
                  isTargetTech
                    ? "Describe la puntualidad, calidad del trabajo y trato del técnico…"
                    : "Describe la comunicación, claridad de los requerimientos y trato del cliente…"
                }
                style={{
                  width: "100%",
                  padding: "0.75rem 1rem",
                  borderRadius: "0.75rem",
                  border: "1px solid #d1d5db",
                  font: "inherit",
                  fontSize: "0.95rem",
                  resize: "vertical",
                  outline: "none",
                }}
              />
            </div>

            <p style={{ fontSize: "0.82rem", color: "#6b7280", margin: "-0.5rem 0 1.25rem", lineHeight: 1.4 }}>
              <strong>Publicación simultánea:</strong> Tu calificación se mantendrá oculta hasta que la otra parte también te califique o transcurran 48 horas desde la finalización del servicio.
            </p>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "0.75rem" }}>
              <Button
                type="button"
                className="button-outline"
                onClick={onClose}
                disabled={busy}
                style={{ width: "auto" }}
              >
                Cancelar
              </Button>
              <Button type="submit" disabled={busy} style={{ width: "auto" }}>
                {busy ? "Enviando…" : "Enviar calificación"}
              </Button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

export { ReviewModal };
