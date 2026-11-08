import { useEffect, useId, useRef, type ReactNode } from 'react'
import { X } from 'lucide-react'

export default function DossierModal({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const titleId = useId()
  useEffect(() => {
    const element = dialog.current
    if (!element) return
    const previous = document.activeElement as HTMLElement | null
    const overflow = document.body.style.overflow
    element.showModal()
    document.body.style.overflow = 'hidden'
    return () => {
      element.close()
      document.body.style.overflow = overflow
      if (previous?.isConnected) previous.focus()
    }
  }, [])
  return <dialog ref={dialog} className="dossier-modal admin-panel" aria-labelledby={titleId} aria-modal="true"
    onCancel={event => { event.preventDefault(); onClose() }}
    onClick={event => {
      if (event.target !== event.currentTarget) return
      const bounds = event.currentTarget.getBoundingClientRect()
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) onClose()
    }}>
    <header className="dossier-modal-heading">
      <h2 id={titleId}>Expediente</h2>
      <button type="button" className="dossier-modal-close" aria-label="Cerrar expediente" onClick={onClose} autoFocus><X size={20} /></button>
    </header>
    <div className="dossier-modal-body">{children}</div>
  </dialog>
}
