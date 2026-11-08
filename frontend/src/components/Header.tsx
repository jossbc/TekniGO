import { Link } from "react-router-dom";
import { ArrowUpRight, CircleHelp } from "lucide-react";
import { Brand } from "./ui";
import AccountMenu from "./AccountMenu";

type Mode = "login" | "register";

export default function Header({ mode, authenticated, name, email, showBrand = true, accountHref = "/perfil", accountLabel = "Mi perfil" }: { mode?: Mode; authenticated: boolean; name?: string; email?: string | null; showBrand?: boolean; accountHref?: string; accountLabel?: string }) {
  return (
    <header className={`header${showBrand ? "" : " header--access"}${authenticated ? " header--authenticated" : ""}`}>
      {showBrand && <Brand />}
      <nav aria-label="Navegación principal">
        {!authenticated && (
          <a className="nav-help" href="#como-funciona">
            <CircleHelp size={17} /> ¿Cómo funciona?
          </a>
        )}
        {mode && (
          <Link className="nav-action" to={mode === "login" ? "/registro" : "/ingresar"}>
            {mode === "login" ? "Crear una cuenta" : "Iniciar sesión"}
            <ArrowUpRight size={17} />
          </Link>
        )}
        {authenticated && <AccountMenu name={name} email={email} accountHref={accountHref} accountLabel={accountLabel} />}

      </nav>
    </header>
  );
}

