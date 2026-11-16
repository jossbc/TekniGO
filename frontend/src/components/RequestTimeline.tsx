import { CheckCircle2 } from "lucide-react";
import { formatRequestStateLabel, type ClientRequestTimelineItem } from "../lib/clientRequests";

const dateTime = new Intl.DateTimeFormat("es-HN", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "America/Tegucigalpa",
});

const roleLabels: Record<string, string> = {
  CLIENTE: "Cliente",
  TECNICO: "Técnico",
  SISTEMA: "Sistema",
};

export default function RequestTimeline({ items }: { items: ClientRequestTimelineItem[] }) {
  return (
    <section className="client-request-box" aria-label="Línea de tiempo de cambios">
      <h3>Línea de tiempo de cambios</h3>
      <ol className="request-timeline">
        {items.map((event, index) => (
          <li key={`${event.estado}-${index}`} className="request-timeline-item">
            <span className="request-timeline-dot" aria-hidden="true"><CheckCircle2 size={16} /></span>
            <div className="request-timeline-content">
              <div className="request-timeline-header">
                <strong>{formatRequestStateLabel(event.estado)}</strong>
                <span>{event.fecha ? dateTime.format(new Date(event.fecha)) : "Fecha sin registrar"}</span>
              </div>
              <p>{event.descripcion} · {roleLabels[event.responsable ?? ""] ?? "Responsable sin registrar"}</p>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
