import { useEffect, useId, useRef } from "react";
import { Clock3 } from "lucide-react";
import { Button } from "./ui";

export default function StartServiceConfirmation({
  onCancel,
  onConfirm,
}: {
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const cancelButton = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const descriptionId = useId();

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    element.showModal();
    cancelButton.current?.focus();
    document.body.style.overflow = "hidden";
    return () => {
      element.close();
      document.body.style.overflow = overflow;
      if (previous?.isConnected) previous.focus();
    };
  }, []);

  return (
    <dialog
      ref={dialog}
      className="dossier-modal client-request-modal start-service-confirmation"
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
      onCancel={(event) => {
        event.preventDefault();
        onCancel();
      }}
    >
      <header className="dossier-modal-heading">
        <h2 id={titleId}><Clock3 size={20} aria-hidden="true" /> Iniciar servicio</h2>
      </header>
      <div className="dossier-modal-body">
        <p id={descriptionId}>¿Ya comenzaste el trabajo? Al confirmar, la solicitud pasará a En proceso.</p>
        <div className="start-service-confirmation-actions">
          <button ref={cancelButton} type="button" className="button button-outline" onClick={onCancel}>Cancelar</button>
          <Button type="button" onClick={onConfirm}>Confirmar inicio</Button>
        </div>
      </div>
    </dialog>
  );
}
