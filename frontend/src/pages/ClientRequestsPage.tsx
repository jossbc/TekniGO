import type { User } from "firebase/auth";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import Header from "../components/Header";
import { AccountStatus } from "../components/AccountStatus";
import ClientRequestsManager from "../components/ClientRequestsManager";
import { accountPath } from "../lib/accountPath";
import { useAccount } from "../lib/useAccount";

export default function ClientRequestsPage({ user, jobsOnly = false }: { user: User; jobsOnly?: boolean }) {
  const { id } = useParams<{ id?: string }>();
  const navigate = useNavigate();
  const listPath = jobsOnly ? "/mis-trabajos" : "/mis-solicitudes";
  const { state, retry } = useAccount(user);
  const profile = state.status === "ready" ? state.profile : null;

  if (profile?.roles.includes("ADMIN")) return <Navigate to="/admin" replace />;
  if (profile && !profile.roles.includes("CLIENTE")) {
    return <Navigate to={accountPath(profile.roles)} replace />;
  }

  return (
    <div className="site">
      <Header
        authenticated={true}
        name={profile?.nombre_completo}
        email={user.email}
        accountHref="/cliente"
      />
      <main className="account client-requests-page">
        <Link className="public-profile-back" to="/cliente">
          <ArrowLeft size={16} />
          Volver a mi espacio de cliente
        </Link>

        <div className="catalog-heading client-requests-page-heading">
          <div>
            <span className="catalog-eyebrow">MIS CONTRATACIONES</span>
            <h1>{jobsOnly ? "Mis trabajos" : "Seguimiento de mis solicitudes"}</h1>
            <p>
              {jobsOnly
                ? "Consulta los trabajos aceptados, en proceso y completados."
                : "Consulta las solicitudes pendientes, publicadas, con ofertas, canceladas o rechazadas."}
            </p>
          </div>
          <Link className="button button-outline" style={{ width: "auto" }} to={jobsOnly ? "/mis-solicitudes" : "/mis-trabajos"}>
            {jobsOnly ? "Ver mis solicitudes" : "Ver mis trabajos"}
          </Link>
          {!jobsOnly && (
            <Link className="button" style={{ width: "auto" }} to="/solicitudes/nueva">
              Publicar solicitud abierta
            </Link>
          )}
        </div>

        {state.status !== "ready" ? (
          <AccountStatus state={state} retry={retry} />
        ) : (
          <ClientRequestsManager
            key={listPath}
            user={user}
            jobsOnly={jobsOnly}
            initialRequestId={id || ""}
            onSelectRequestId={(nextId) => {
              if (nextId) {
                navigate(`${listPath}/${encodeURIComponent(nextId)}`, { replace: true });
              } else if (id) {
                navigate(listPath, { replace: true });
              }
            }}
          />
        )}
      </main>
    </div>
  );
}
