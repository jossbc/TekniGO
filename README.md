# TekniGO

Monorepositorio: React + TypeScript + Vite en `frontend/` y FastAPI + SQLAlchemy en `backend/`. Frontend preparado para Vercel; API para Render; PostgreSQL en Supabase; autenticación en Firebase.

## Estado actual

Implementación inicial de HU-01 a HU-04: registro de cliente/técnico, acceso y cierre de sesión. Una cuenta puede tener ambos roles, sin duplicar identidad. La usuaria confirmó la conexión a Supabase y la migración 001_identity. El despliegue público y la validación completa en navegador siguen pendientes.

La verificación de identidad sigue pendiente tras registrarse. Catálogo, edición de perfil, documentos y contratación corresponden a las siguientes historias. El espacio de cuenta lo indica sin simular esas funciones.

## Backend

Desde `backend/`, PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# Completar DATABASE_URL con la cadena PostgreSQL de Supabase.
# Usar el dialecto postgresql+psycopg y TLS (sslmode=require).
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Guardar la cuenta de servicio Firebase en `backend/secrets/firebase-admin.json` o indicar su ruta mediante `GOOGLE_APPLICATION_CREDENTIALS`. No subir credenciales a Git. La conexión de migraciones debe tener permisos para crear tablas. La migración inicial aplica RLS y revoca acceso de los roles públicos de Supabase: los endpoints de negocio son la única entrada. La conexión de ejecución del backend debe tener los privilegios necesarios; no habilitar políticas públicas para resolver un error de permisos.

La migración está dirigida a Supabase: supone que existen los roles `anon` y `authenticated`. Para PostgreSQL independiente, adaptar esas concesiones a sus roles.

Swagger: http://localhost:8000/docs. El frontend obtiene el JWT de Firebase y lo envía como `Authorization: Bearer TOKEN`.

### Comprobar Supabase antes de continuar

En el panel de tu proyecto, abrir **Connect → Session pooler → URI** (puerto 5432). Copiar la cadena a `backend/.env` como `DATABASE_URL="postgresql://..."`, reemplazar el marcador de contraseña por la contraseña de la base (no la contraseña de la cuenta de Supabase) y codificar caracteres especiales de la contraseña en formato URL. No usar una API key como contraseña de PostgreSQL. El controlador acepta `postgresql://` o `postgresql+psycopg://` y exige TLS. La conexión session pooler permite redes IPv4.

Desde `backend/`, ejecutar:

```powershell
.\.venv\Scripts\python.exe -m app.check_database
# Solo después de comprobar conexión y confirmar que es la base de desarrollo:
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m app.check_database
```

La comprobación es de solo lectura, informa si existen las tablas y no muestra la contraseña ni la cadena de conexión. Cargar variables usa la ruta explícita a backend/.env, independientemente del directorio desde el que se importe la aplicación. La presencia de tablas no reemplaza la comprobación posterior de permisos, RLS y reglas del negocio.

| Endpoint | Contrato |
| --- | --- |
| GET /health | Estado del proceso; no comprueba disponibilidad de Firebase o BD |
| POST /users/register | JWT y cuerpo con nombre_completo, dni, telefono, rol CLIENTE o TECNICO |
| GET /users/me | Perfil propio y lista de roles; 404 si falta completar alta local |
| POST /auth/logout | Revoca todas las sesiones Firebase de la cuenta; 204 |

Validación inicial: DNI hondureño de 13 dígitos; celular hondureño de 8 dígitos que inicia en 9, 8 o 3. Acepta espacios, guiones y prefijo +504 opcional; se guarda como +504 seguido de 8 dígitos. La misma regla se aplica en el formulario y en FastAPI antes de persistir. La posesión del teléfono NO se da por verificada. El registro toma correo y uid exclusivamente del token validado. Duplicados normalizados producen 409; campos inválidos, 422; token inválido, 401. Un reintento idéntico de un rol existente devuelve el mismo perfil. Agregar el otro rol exige los mismos datos de identidad, sin modificarlos.

La cuenta Firebase puede existir aunque falle el alta local: no se elimina automáticamente. Iniciar sesión y completar registro permite reintentar sin crear otra cuenta. No mostrar éxito del registro local hasta que PostgreSQL confirme la operación.

## Frontend

Desde `frontend/`:

```powershell
npm install
Copy-Item .env.example .env
# Completar las cuatro variables del proyecto web de Firebase y VITE_API_URL.
npm run dev
```

Habilitar Email/Password en Firebase Authentication y autorizar los dominios de desarrollo y despliegue. El proyecto Firebase web debe ser el mismo que utiliza el backend. Las claves de configuración web de Firebase no sustituyen las credenciales privadas del servidor; nunca copiar la cuenta de servicio a variables VITE_. La política de contraseña real la aplica Firebase (la interfaz valida un mínimo inicial de 6 caracteres).

Rutas: `/ingresar`, `/registro`, `/mi-cuenta`, `/cliente`, `/tecnico`. Si Firebase web no está configurado, se puede revisar la interfaz, pero el formulario muestra el bloqueo de configuración al enviarse. No existe autenticación simulada.

## Verificación

```powershell
# Desde backend:
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m alembic upgrade head --sql
# Desde frontend:
npm run build
```

Las pruebas unitarias invocan los servicios de registro y perfil con SQLite en memoria y Firebase sustituido por dobles. No demuestran transporte HTTP, conectividad a Supabase, semántica RLS, concurrencia PostgreSQL ni revocación real. Antes de declarar las HU terminadas: ejecutar migración en BD de prueba, probar dos cuentas reales, duplicados simultáneos, registro de ambos roles y cierre con JWT anterior revocado.

El frontend tiene package-lock.json y la compilación fue comprobada. Usar npm ci para instalaciones reproducibles. Las pruebas de concurrencia usan SQLite local; falta comprobar concurrencia PostgreSQL y revocación real en el recorrido público.

## Guía de colaboración

- Una rama por tarea, Pull Request revisado y migraciones incluidas para cambios de esquema. No ejecutar `create_all` en producción ni crear tablas manualmente por separado.
- Reutilizar `frontend/src/components/ui.tsx`, los colores de `styles.css` y las funciones de `lib/api.ts`. Añadir páginas nuevas sin copiar la autenticación.
- Backend compartido: `get_current_user` verifica identidad; cada módulo debe validar además roles, estado y propiedad del recurso. Cambiar la vista de rol no otorga permisos nuevos.
- Roles: CLIENTE y TECNICO son autoservicio; ADMIN no se acepta en registros públicos. Ambas identidades profesionales/personales comparten el mismo usuario.
- El cierre actual desconecta TODOS los dispositivos mediante revocación Firebase. Cerrar solo un dispositivo exige un diseño adicional de sesiones y no está implementado.
- No subir `.env`, `node_modules`, entornos virtuales ni archivos de cuenta de servicio.

## Despliegue

Guía paso a paso y colaboración: [docs/despliegue.md](docs/despliegue.md). Configuración de Render: `render.yaml` en la raíz.

Vercel: raíz `frontend`, compilación `npm run build`, salida `dist`, variables VITE_ definidas antes de compilar. `vercel.json` resuelve rutas de la SPA.

Render: raíz `backend`, instalar `requirements.txt`, iniciar `uvicorn app.main:app --host 0.0.0.0 --port $PORT`; configurar DATABASE_URL, GOOGLE_APPLICATION_CREDENTIALS (archivo secreto) y CORS_ORIGINS con el origen HTTPS exacto de Vercel. Ejecutar migraciones como paso controlado de despliegue, no en cada petición ni al importar el módulo.

## Diseño

Azul `#2563EB`, naranja `#EA580C`, fondos claros, texto oscuro. Tipografías propuestas Manrope y DM Sans, con respaldo sans-serif. La marca tipográfica e ícono de herramienta son provisionales, no el logo definitivo de la diseñadora. Ver `docs/linea-grafica.md`.
