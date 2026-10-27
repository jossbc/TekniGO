# TekniGO

TekniGO es un proyecto en desarrollo para conectar a personas que necesitan un servicio con técnicos que pueden realizarlo, pensado para Honduras.

Una misma cuenta puede usarse como **cliente y técnico**, sin volver a registrarse.

## Qué se puede hacer actualmente

- Crear una cuenta, iniciar sesión y recuperar la contraseña por correo.
- Entrar al espacio de cliente o técnico. Si la cuenta tiene ambos roles, elegir cómo continuar.
- Agregar el otro rol desde la cuenta sin volver a ingresar los datos personales.
- Consultar el perfil y editar el correo y el teléfono. El nombre y el DNI no se pueden editar desde el formulario.
- Elegir varias especialidades y proponer una nueva con la opción **Otro**.
- Como técnico, crear, editar, activar y desactivar servicios relacionados con las especialidades guardadas.
- Cambiar la disponibilidad del técnico. Estar disponible no reemplaza la verificación de identidad.
- Enviar imágenes del DNI por ambos lados y una selfie para solicitar la verificación de identidad. Se aceptan JPG, PNG y WEBP de hasta 5 MB por archivo.

Los documentos de identidad no se muestran en el perfil público. Enviarlos deja la solicitud en revisión; no significa que la identidad ya esté aprobada.

La interfaz incluye ajustes para celular, tableta y computadora. Todavía está pendiente comprobar visualmente todas las pantallas en distintos dispositivos.

## Cómo está organizado

| Carpeta | Contenido |
| --- | --- |
| `frontend/` | Pantallas, formularios y diseño. Usa React y TypeScript. |
| `backend/` | Cuentas, roles, servicios y validaciones. Usa Python y FastAPI. |
| `docs/` | Referencias de diseño del proyecto. |

Firebase gestiona el acceso de los usuarios. Supabase guarda los datos de la aplicación. La publicación está preparada para usar Vercel y Render.

## Ejecutar el proyecto en tu equipo

Necesitas **Git, Python y Node.js con npm**, además de la configuración de Firebase y Supabase del equipo.

Los comandos siguientes son para PowerShell. Comienza en la carpeta principal de TekniGO y mantén abiertas dos terminales: una para el servidor y otra para las pantallas.

### 1. Preparar el servidor

Solo la primera vez:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Crea `backend/.env` y agrega la conexión de la base de datos de desarrollo en `DATABASE_URL`. Pide al equipo el valor correcto; no uses una clave de la API como contraseña de la base.

Guarda la credencial privada de Firebase en `backend/secrets/firebase-admin.json`. Si está en otra ubicación, indícala mediante `GOOGLE_APPLICATION_CREDENTIALS`.

Desde `backend/`, comprueba la conexión:

```powershell
.\.venv\Scripts\python.exe -m app.check_database
```

Si estás preparando una base nueva o hay cambios pendientes en su estructura, confirma con el equipo que estás usando la base correcta y ejecuta:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

Para iniciar el servidor, desde `backend/`:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

La documentación de las funciones del servidor estará en [localhost:8000/docs](http://localhost:8000/docs).

### 2. Preparar las pantallas

En otra terminal, desde la carpeta principal:

```powershell
cd frontend
npm ci
```

Crea `frontend/.env` con estas variables y los valores del proyecto del equipo:

```dotenv
VITE_API_URL=http://localhost:8000
VITE_FIREBASE_API_KEY=valor_del_proyecto
VITE_FIREBASE_AUTH_DOMAIN=valor_del_proyecto
VITE_FIREBASE_PROJECT_ID=valor_del_proyecto
VITE_FIREBASE_APP_ID=valor_del_proyecto
```

El servidor y las pantallas deben usar el mismo proyecto de Firebase. El acceso por correo y contraseña debe estar habilitado en Firebase.

Para abrir la aplicación:

```powershell
npm run dev
```

Abre la dirección que indique la terminal, normalmente [localhost:5173](http://localhost:5173). Reinicia este comando si cambias las variables de configuración.

### Documentos de identidad durante el desarrollo

Sin configuración de almacenamiento de Supabase, los archivos se guardan en `backend/.private_storage`. Usa imágenes de prueba y configura una clave privada propia en `PRIVATE_STORAGE_SECRET` antes de probar las cargas; conserva esa clave para poder leer los archivos guardados.

Para usar Supabase Storage, el servidor necesita `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` y un contenedor privado llamado `documentos-identidad`. La lectura administrativa desde Supabase sigue pendiente, como se indica arriba.

No subas credenciales, archivos `.env` ni documentos personales a GitHub. Las credenciales privadas se configuran en el servidor, nunca en las variables `VITE_`.

## Comprobar los cambios

Desde `backend/`, ejecutar las pruebas:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Desde `frontend/`:

```powershell
npm test
npm run build
```

Estas comprobaciones ayudan a detectar errores, pero también hay que probar los recorridos completos en el navegador: iniciar sesión, editar el perfil, agregar el otro rol y usar las funciones del técnico. Las pruebas automáticas no confirman por sí solas que Firebase, Supabase y la web publicada estén conectados correctamente.

## Si algo no funciona

- **No inicia sesión:** comprueba la configuración de Firebase y que ambos servidores estén encendidos.
- **Pide completar el registro:** puede existir el acceso en Firebase, pero faltar el registro de la cuenta en TekniGO. La aplicación permite completarlo sin crear otra cuenta de acceso.
- **No conecta con TekniGO:** revisa que `VITE_API_URL` apunte al servidor que estás usando.
- **Aparece un error de sesión justo al entrar:** comprueba que la fecha y hora del equipo estén sincronizadas. El servidor admite una diferencia pequeña de reloj de hasta 5 segundos.
- **No conecta con Supabase:** ejecuta `app.check_database` y revisa la conexión con el equipo.

El botón **Cerrar sesión** cierra las sesiones de esa cuenta en todos los dispositivos.

## Trabajo en equipo y publicación

Trabajamos con una rama por tarea y revisión antes de integrar los cambios. Reutiliza los componentes y estilos existentes para mantener el mismo diseño. Si cambias la estructura de la base, incluye su actualización en `backend/migrations/`.

Para publicar las pantallas en Vercel, usa `frontend` como carpeta del proyecto, `npm run build` como comando de construcción y `dist` como carpeta de salida.

El servidor tiene una configuración inicial para Render en [render.yaml](render.yaml). Antes de publicar, configura Firebase, la conexión a Supabase y `CORS_ORIGINS` con la dirección de la web autorizada. Aplica también las actualizaciones pendientes de la base de datos y revisa el almacenamiento privado de documentos.

La referencia de colores y tipografías está en [la guía de diseño](docs/linea-grafica.md).
