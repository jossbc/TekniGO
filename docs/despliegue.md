# Despliegue inicial y trabajo en equipo

Este es un entorno de desarrollo compartido. No habilita contrataciones ni completa la verificación de teléfono pendiente de consultar con el docente.

## 1. Publicar el código en GitHub

Repositorio: https://github.com/jossbc/TekniGO

Desde la raíz, revisar `git status` y subir los archivos del proyecto mediante commit y push. No incluir `.env`, `.venv`, `node_modules` ni `backend/secrets/`. Los ejemplos `.env.example` sí se comparten. El archivo Firebase Admin debe permanecer privado.

## 2. Backend en Render

En https://dashboard.render.com seleccionar New > Web Service, conectar GitHub y elegir TekniGO. Configurar:

| Campo | Valor |
| --- | --- |
| Nombre | teknigo-api-dev (o un nombre disponible) |
| Runtime | Python |
| Root Directory | backend |
| Build Command | pip install -r requirements.txt |
| Start Command | uvicorn app.main:app --host 0.0.0.0 --port $PORT |
| Instance | Free |
| Health Check Path | /health |

Variables de entorno del servicio, sin las comillas externas del archivo `.env`:

| Variable | Valor |
| --- | --- |
| PYTHON_VERSION | 3.14.0 |
| DATABASE_URL | Copiar el valor de backend/.env: Session pooler de Supabase con TLS |
| GOOGLE_APPLICATION_CREDENTIALS | /etc/secrets/firebase-admin.json |
| CORS_ORIGINS | http://localhost:5173,http://127.0.0.1:5173 (añadir después el origen HTTPS de Vercel) |

En Environment > Secret Files crear `firebase-admin.json` y pegar el contenido del archivo privado local `backend/secrets/firebase-admin.json`. Render lo monta en `/etc/secrets/firebase-admin.json`. Hacerlo antes del despliegue correcto: Firebase Admin se inicializa al arrancar.

Alternativa: importar el `render.yaml` raíz como Blueprint. También requiere agregar el archivo secreto desde el panel; el YAML no contiene las credenciales.

La migración `001_identity` ya fue confirmada por la usuaria en la base actual de Supabase. No se crean tablas al arrancar. Para nuevas migraciones, un responsable ejecuta `python -m alembic upgrade head` con la conexión del entorno objetivo antes de desplegar código que dependa de ellas.

Al finalizar, guardar la URL real del servicio. Abrir `/health` y `/docs`. El primero comprueba el proceso, no la conexión a Supabase. Free suspende el servicio tras inactividad: la primera solicitud puede tardar.

## 3. Frontend en Vercel

En https://vercel.com/new importar el mismo repositorio:

| Campo | Valor |
| --- | --- |
| Framework | Vite |
| Root Directory | frontend |
| Install Command | npm ci |
| Build Command | npm run build |
| Output Directory | dist |

Copiar las cuatro variables `VITE_FIREBASE_*` desde frontend/.env a las variables del proyecto. Configurar `VITE_API_URL` con la URL HTTPS real de Render, sin barra final ni `/docs`. No colocar DATABASE_URL ni la cuenta Firebase Admin en Vercel o en variables VITE_.

Publicar y guardar el dominio estable asignado, por ejemplo `https://NOMBRE-REAL.vercel.app`; no usar literalmente este ejemplo.

## 4. Conectar los dominios

En Render, actualizar CORS_ORIGINS a una lista separada por comas con el origen real de Vercel y ambos orígenes locales. Sin rutas ni barra final. Guardar y redesplegar.

En Firebase Console > Authentication > Settings > Authorized domains añadir solo el hostname de Vercel, sin https:// ni rutas. Mantener los dominios locales necesarios para desarrollo.

Las variables VITE_ se incorporan al compilar: si cambia la URL de API o Firebase, redesplegar el frontend. Las URLs de previews de Vercel necesitan autorización explícita si se usarán con esta API; no habilitar todos los dominios indiscriminadamente.

## 5. Verificación pública

- Abrir `/registro` directamente y recargar: debe cargar la SPA.
- Registrar una cuenta cliente y otra técnica con datos de prueba distintos; comprobar el perfil pendiente y la confirmación.
- Probar correo/contraseña incorrectos, DNI y teléfono duplicados (con otro formato).
- Confirmar `/cliente`, `/tecnico` y ambos roles.
- Cerrar sesión, volver atrás y abrir la ruta protegida directamente. Probar también que el JWT anterior reciba 401 de la API.
- Revisar en celular y escritorio. Registrar resultados; el despliegue no sustituye estas pruebas.

## 6. Incorporación de compañeros

Dar acceso al repositorio como colaboradores desde GitHub. Cada persona clona el proyecto y trabaja en una rama por HU; propone un Pull Request a la rama de despliegue. Los cambios integrados en esa rama se publican mediante las integraciones de GitHub de Render y Vercel.

Para desarrollar solo frontend, copiar frontend/.env.example a frontend/.env, completar Firebase web y usar la URL pública de API. CORS debe conservar localhost y 127.0.0.1. Así no necesitan la contraseña PostgreSQL ni Firebase Admin. Todas las escrituras se harán en la base compartida de desarrollo: usar datos de prueba identificables.

Quien trabaje backend configura su entorno local siguiendo README.md y recibe credenciales de desarrollo por un canal privado. Para cambios de esquema crea una migración Alembic y coordina su aplicación con un responsable; no cambia tablas manualmente en Supabase.

Reutilizar api(), get_current_user y los componentes compartidos. La función require_verified_technician es una regla de identidad preparada para los futuros endpoints de contratación, no una autorización completa: aún requiere teléfono verificado y las demás condiciones de negocio.
