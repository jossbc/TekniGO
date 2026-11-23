import { useState, type ChangeEvent } from "react";
import type { User } from "firebase/auth";
import { FileText, Lock, Paperclip, Trash2 } from "lucide-react";
import { errorMessage } from "../lib/api";
import { uploadCancellationEvidenceApi, type UploadedEvidence } from "../lib/cancellations";

const ALLOWED = ["image/jpeg", "image/png", "image/webp", "application/pdf"];
const MAX_BYTES = 5 * 1024 * 1024;
const MAX_FILES = 10;

export default function EvidenceUploader({ user, solicitudId, value, onChange, disabled }: {
  user: User; solicitudId: string; value: UploadedEvidence[];
  onChange: (next: UploadedEvidence[]) => void; disabled?: boolean;
}) {
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");

  async function handleFiles(e: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    setError("");
    if (value.length + files.length > MAX_FILES) {
      setError(`Máximo ${MAX_FILES} archivos de evidencia.`);
      return;
    }
    setUploading(true);
    const uploaded: UploadedEvidence[] = [];
    try {
      for (const file of files) {
        if (!ALLOWED.includes(file.type)) throw new Error(`«${file.name}» no es JPG, PNG, WEBP o PDF.`);
        if (file.size > MAX_BYTES) throw new Error(`«${file.name}» supera los 5 MB.`);
        uploaded.push(await uploadCancellationEvidenceApi(user, solicitudId, file));
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      if (uploaded.length) onChange([...value, ...uploaded]);
      setUploading(false);
    }
  }

  return (
    <div style={{ border: "1px dashed #cbd5e1", borderRadius: 8, padding: 14, background: "#fbfcfd" }}>
      <strong style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13 }}>
        <Paperclip size={16} /> Evidencia (opcional): JPG, PNG, WEBP o PDF, hasta 5 MB
      </strong>
      <p style={{ margin: "6px 0 10px", fontSize: 12, color: "#64748b" }}>
        <Lock size={12} style={{ verticalAlign: "middle", marginRight: 4 }} />
        Se almacena cifrada. Solo tú y la administración pueden verla; la otra parte no.
      </p>
      <label style={{ cursor: disabled || uploading ? "not-allowed" : "pointer" }}>
        <input type="file" multiple accept="image/jpeg,image/png,image/webp,application/pdf"
          onChange={handleFiles} disabled={disabled || uploading} style={{ display: "none" }} />
        <span className="button button-outline" style={{ fontSize: 13, padding: "6px 12px" }}>
          {uploading ? "Subiendo…" : "Seleccionar archivos"}
        </span>
      </label>
      {error && <p className="alert" role="alert" style={{ marginTop: 8 }}>{error}</p>}
      {value.map((item, index) => (
        <div key={item.referencia} style={{ display: "flex", justifyContent: "space-between", alignItems: "center",
          background: "#f1f5f9", padding: "6px 10px", borderRadius: 6, fontSize: 13, marginTop: 6 }}>
          <span style={{ display: "flex", alignItems: "center", gap: 6 }}><FileText size={14} /> {item.nombre}</span>
          <button type="button" aria-label={`Quitar ${item.nombre}`} disabled={disabled}
            onClick={() => onChange(value.filter((_, i) => i !== index))}
            style={{ background: "none", border: "none", cursor: "pointer", color: "#dc2626" }}>
            <Trash2 size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}