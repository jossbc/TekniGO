import type { User } from "firebase/auth";
import { Link, Navigate } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import Header from "../components/Header";
import { AccountStatus } from "../components/AccountStatus";
import OpenRequestForm from "../components/OpenRequestForm";
import { accountPath } from "../lib/accountPath";
import { useAccount } from "../lib/useAccount";

export default function PublishOpenRequestPage({ user }: { user: User }) {
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
      <main className="account open-request-page">
        <Link className="public-profile-back" to="/mis-solicitudes">
          <ArrowLeft size={16} />
          Volver a mis solicitudes
        </Link>

        <div className="catalog-heading">
          <span className="catalog-eyebrow">LICITACIÓN ABIERTA</span>
          <h1>Publicar solicitud abierta</h1>
          <p>
            Publica una oferta de trabajo para recibir propuestas de técnicos verificados en tu zona. Tu
            dirección exacta y tu teléfono permanecen protegidos hasta que aceptes a un técnico.
          </p>
        </div>

        {state.status !== "ready" ? (
          <AccountStatus state={state} retry={retry} />
        ) : (
          <OpenRequestForm user={user} />
        )}
      </main>
    </div>
  );
}
