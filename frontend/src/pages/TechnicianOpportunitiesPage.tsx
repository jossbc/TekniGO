import type { User } from "firebase/auth";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import Header from "../components/Header";
import { AccountStatus } from "../components/AccountStatus";
import TechnicianOpportunitiesManager from "../components/TechnicianOpportunitiesManager";
import { accountPath } from "../lib/accountPath";
import { useAccount } from "../lib/useAccount";

export default function TechnicianOpportunitiesPage({ user }: { user: User }) {
  const { id } = useParams<{ id?: string }>();
  const navigate = useNavigate();
  const { state, retry } = useAccount(user);
  const profile = state.status === "ready" ? state.profile : null;

  if (profile?.roles.includes("ADMIN")) return <Navigate to="/admin" replace />;
  if (profile && !profile.roles.includes("TECNICO")) {
    return <Navigate to={accountPath(profile.roles)} replace />;
  }

  return (
    <div className="site">
      <Header
        authenticated={true}
        name={profile?.nombre_completo}
        email={user.email}
        accountHref="/tecnico"
      />
      <main className="account client-requests-page">
        <Link className="public-profile-back" to="/tecnico">
          <ArrowLeft size={16} />
          Volver a mi espacio de técnico
        </Link>

        <div className="catalog-heading client-requests-page-heading">
          <div>
            <span className="catalog-eyebrow">LICITACIONES Y OPORTUNIDADES</span>
            <h1>Ofertas disponibles en tu zona</h1>
            <p>
              Consulta solicitudes abiertas compatibles con tus especialidades y zonas de cobertura,
              revisa el presupuesto ofrecido y descarta las que no sean de tu interés.
            </p>
          </div>
        </div>

        {state.status !== "ready" ? (
          <AccountStatus state={state} retry={retry} />
        ) : (
          <TechnicianOpportunitiesManager
            user={user}
            initialRequestId={id || ""}
            onSelectRequestId={(nextId) => {
              if (nextId) {
                navigate(`/tecnico/oportunidades/${encodeURIComponent(nextId)}`, { replace: true });
              } else if (id) {
                navigate("/tecnico/oportunidades", { replace: true });
              }
            }}
          />
        )}
      </main>
    </div>
  );
}
