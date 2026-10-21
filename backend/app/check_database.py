"""Read-only connectivity check: python -m app.check_database."""
import subprocess
import sys

CHECK_TIMEOUT_SECONDS = 25


def main():
    print("Comprobando Supabase (limite total: 25 segundos)...", flush=True)
    try:
        result = subprocess.run(
            [sys.executable, "-u", "-m", "app.check_database", "--worker"],
            timeout=CHECK_TIMEOUT_SECONDS,
            check=False,
        )
        return result.returncode
    except subprocess.TimeoutExpired:
        print("Tiempo agotado. Se detuvo la comprobacion sin modificar la base.", flush=True)
        print("La ultima etapa mostrada indica donde quedo esperando.", flush=True)
        print("Si fue DNS o conexion, revisa red/VPN/firewall y que el proyecto este activo.", flush=True)
        return 1
    except KeyboardInterrupt:
        print("\nComprobacion cancelada.", flush=True)
        return 130


def check_connection():
    print("1/4: cargando controlador y configuracion...", flush=True)
    import socket
    from sqlalchemy import inspect, text
    from sqlalchemy.exc import OperationalError, SQLAlchemyError
    from app.database import get_engine

    try:
        engine = get_engine()
        print("2/4: resolviendo el servidor (DNS)...", flush=True)
        socket.getaddrinfo(engine.url.host, engine.url.port or 5432, type=socket.SOCK_STREAM)
        print("3/4: conectando a PostgreSQL con TLS...", flush=True)
        with engine.connect() as connection:
            # Applies only inside this read-only check transaction.
            connection.execute(text("SET LOCAL statement_timeout = '5000'"))
            connection.execute(text("SELECT 1"))
            print("4/4: consultando tablas y revision...", flush=True)
            tables = set(inspect(connection).get_table_names(schema="public"))
            expected = {"usuarios", "usuarios_roles", "perfiles_tecnicos"}
            missing = sorted(expected - tables)
            print("OK: conexión PostgreSQL comprobada con SELECT 1.")
            if "alembic_version" in tables:
                version = connection.execute(text("SELECT version_num FROM public.alembic_version")).scalars().all()
                print("Revisión Alembic:", ", ".join(version) or "sin revisión")
            if missing:
                print("Tablas iniciales pendientes:", ", ".join(missing))
                print("La conexión funciona. Falta aplicar la migración: python -m alembic upgrade head")
            else:
                print("OK: las tres tablas iniciales existen.")
        return 0
    except socket.gaierror:
        print("No se pudo resolver el servidor. Revisa host y DNS de tu red.", flush=True)
    except RuntimeError as error:
        print(f"Configuración pendiente: {error}")
    except OperationalError as error:
        # Do not print exception text: driver errors can include connection details.
        code = getattr(error.orig, "sqlstate", None)
        if code == "28P01":
            print("No se pudo autenticar: revisa usuario y contraseña de la base de datos.")
        elif code == "3D000":
            print("La base indicada no existe. Revisa el nombre en DATABASE_URL.")
        elif "tenant or user not found" in str(error.orig).lower():
            print("El pooler no reconoce el usuario/proyecto. Copia nuevamente su URI desde Connect.")
        else:
            print("No se pudo conectar: revisa red/DNS, proyecto activo, host, puerto y credenciales.")
            print("Si tu red usa IPv4, usa la cadena Session pooler de Supabase (puerto 5432).")
    except SQLAlchemyError:
        print("La consulta no pudo completarse. Revisa permisos y configuración PostgreSQL.")
    return 1


if __name__ == "__main__":
    sys.exit(check_connection() if sys.argv[1:] == ["--worker"] else main())
