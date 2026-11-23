import { useEffect, useId, useRef, type ReactNode } from "react";
import { X } from "lucide-react";

export default function ModalShell({ isOpen, onClose, title, children }: {
  isOpen: boolean; onClose: () => void; title: string; children: ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    if (!isOpen) return;
    const el = dialog.current;
    if (el && typeof el.showModal === "function" && !el.open) el.showModal();
    document.body.style.overflow = "hidden";
    return () => {
      if (el?.open) el.close();
      document.body.style.overflow = "";
    };
  }, [isOpen]);

  if (!isOpen) return null;
  return (
    <dialog
      ref={dialog}
      className="dossier-modal admin-panel"
      aria-labelledby={titleId}
      aria-modal="true"
      onCancel={(e) => { e.preventDefault(); onClose(); }}
    >
      <header className="dossier-modal-heading">
        <h2 id={titleId}>{title}</h2>
        <button type="button" className="dossier-modal-close" aria-label="Cerrar ventana" onClick={onClose} autoFocus>
          <X size={20} />
        </button>
      </header>
      <div className="dossier-modal-body" style={{ padding: "20px" }}>{children}</div>
    </dialog>
  );
}