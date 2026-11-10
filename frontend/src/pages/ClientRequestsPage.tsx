import type { User } from "firebase/auth";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import Header from "../components/Header";
import { AccountStatus } from "../components/AccountStatus";
import ClientRequestsManager from "../components/ClientRequestsManager";
import { accountPath } from "../lib/accountPath";
import { useAccount } from "../lib/useAccount";

export default function ClientRequestsPage({ user }: { user: User }) {
  const { id } = useParams<{ id?: string }>();
  const navigate = useNavigate();
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
            <h1>Seguimiento de mis solicitudes</h1>
            <p>
              Consulta tus solicitudes directas y abiertas, filtra por estado y revisa la propuesta pactada y
              la línea de tiempo de cada trabajo.
            </p>
          </div>
          <Link className="button" style={{ width: "auto" }} to="/solicitudes/nueva">
            Publicar solicitud abierta
          </Link>
        </div>

        {state.status !== "ready" ? (
          <AccountStatus state={state} retry={retry} />
        ) : (
          <ClientRequestsManager
            user={user}
            initialRequestId={id || ""}
            onSelectRequestId={(nextId) => {
              if (nextId) {
                navigate(`/mis-solicitudes/${encodeURIComponent(nextId)}`, { replace: true });
              } else if (id) {
                navigate("/mis-solicitudes", { replace: true });
              }
            }}
          />
        )}
      </main>
    </div>
  );
}
