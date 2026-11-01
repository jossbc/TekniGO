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

Los documentos de identidad no se muestran en el perfil público. Con el procesamiento local configurado, el envío inicia una verificación básica; un resultado incierto o un error mantiene la identidad pendiente. El correo confirmado es un requisito adicional para contratar.

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

Para probar estos cambios sin modificar las tablas publicadas, prepara el esquema
de desarrollo con el backend detenido:

```powershell
.\.venv\Scripts\python.exe -m app.setup_development_database
```

El comando configura `teknigo_dev` y activa el procesamiento Python: para probar
identidad prepara también el entorno/modelos de la guía del equipo. Las
migraciones de `public` se coordinan por separado con la publicación.

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

El almacenamiento exige configurar Supabase. Para usar explícitamente el disco durante el desarrollo, establece `PRIVATE_STORAGE_BACKEND=local`; los archivos se guardarán en `backend/.private_storage`. Usa imágenes de prueba y configura una clave aleatoria de al menos 32 bytes en `PRIVATE_STORAGE_SECRET`; conserva esa clave para poder leer los archivos guardados.

Para usar Supabase Storage, el servidor necesita `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` y un contenedor privado llamado `documentos-identidad`. FastAPI recupera y descifra los archivos mediante lectura autenticada; el contenido sigue restringido a administración y los procesos internos de verificación.

No subas credenciales, archivos `.env` ni documentos personales a GitHub. Las credenciales privadas se configuran en el servidor, nunca en las variables `VITE_`.

### Procesamiento local de identidad (opcional)

Para probar las migraciones y el recorrido completo sin cambiar las tablas
publicadas, ejecutar desde `backend/` con el entorno del API:

```powershell
python -m app.setup_development_database
```

Este comando crea el esquema privado `teknigo_dev`, migra únicamente ese esquema
y luego configura `DATABASE_SCHEMA=teknigo_dev` e `IDENTITY_PROCESSOR=python` en
`backend/.env`. No cambia `DATABASE_URL` ni copia cuentas/documentos de `public`.
Reiniciar FastAPI y completar un perfil de desarrollo con el acceso Firebase
existente si la aplicación lo solicita. No registrar otra cuenta Firebase con
el mismo correo. Los usuarios y documentos de desarrollo son independientes.

Las transacciones de desarrollo excluyen `public` del search_path. Alembic lleva
su revisión en `teknigo_dev.alembic_version`; al faltar una tabla de desarrollo
no se consulta su equivalente publicado. En Render conservar el esquema
predeterminado `public`; no copiar DATABASE_SCHEMA=teknigo_dev al servicio publicado.

FastAPI puede ejecutar PaddleOCR y DeepFace en un proceso Python local con tiempo
límite de 180 segundos. Las imágenes se transmiten por memoria, no mediante
archivos temporales sin cifrar. El proceso no recibe credenciales del backend.

Desde `backend/`, usando Python 3.13 instalado en Windows:

```powershell
py -3.13 -m venv .venv-identity
.\.venv-identity\Scripts\python.exe -m pip install -r requirements-identity.txt
.\.venv-identity\Scripts\python.exe -m app.identity_worker --prepare
```

La preparación descarga los modelos la primera vez y requiere conexión. No
envía DNI ni selfies a un proveedor externo. Los modelos se guardan en
`.identity_models/`, excluida de Git. Si la instalación o preparación falla,
resolver primero la compatibilidad de los paquetes antes de habilitar el modo.

Para comprobar OCR y rostros con imágenes originales locales antes de conectar
la base de datos, ejecutar en ese mismo entorno:

```powershell
.\.venv-identity\Scripts\python.exe -m app.identity_worker --check-local
```

El comando solicita las rutas del frente, reverso y selfie, el DNI y el nombre
esperados. Solo imprime controles y versiones de modelos; no sube archivos ni
crea solicitudes. `null` indica control incierto o no implementado. Usa imágenes
propias o autorizadas y comparte únicamente los controles, no datos personales.

Una vez preparada la instalación, agregar en `backend/.env` y reiniciar FastAPI:

```dotenv
IDENTITY_PROCESSOR=python
```

Por defecto se usa el intérprete de `.venv-identity`. Para otro entorno local,
configura `IDENTITY_PYTHON` con su ruta absoluta; no cambia el Python del API.

La evaluación se inicia al enviar documentos; `POST /users/me/identity-verification`
permite reintentar sobre la última solicitud propia, con espera de 60 segundos.
La consulta de documentos muestra el estado y una observación comprensible.
Se registra la versión de librerías/modelos en la evidencia privada del resultado.

OCR compara el número de DNI y el nombre normalizado cuando la lectura tiene
confianza suficiente. DeepFace compara rostros con Facenet512 y exige un único
rostro en la selfie. Si el DNI contiene dos rostros, la heurística provisional
solo selecciona el principal cuando su área es al menos tres veces la otra;
en otro caso conserva la ambigüedad. Compara los recortes seleccionados sin
elegir el rostro que mejor coincida con la selfie. Una lectura incompleta o rostro ambiguo produce un
resultado incierto. Esto no demuestra autenticidad documental ni detección de
vida: ambos controles permanecen sin confirmar. Con el CA-04 actualizado, la
versión 2 del proceso puede aprobar la verificación básica si legibilidad,
datos del DNI y comparación facial son positivos. Un control requerido incierto
o un error mantiene la identidad pendiente; una discrepancia la rechaza.
El correo confirmado sigue siendo un requisito separado para contratar.
Los resultados anteriores se conservan y no se aprueban retroactivamente.
Este modo aún requiere validación real
de precisión; los tests con simulaciones no validan el comportamiento del modelo.

Antes de probar estos endpoints con una base real, coordinar la aplicación de
las migraciones 010 y 011 con la actualización del backend. No se aplican
automáticamente al instalar estas dependencias.

### Habilitación del cliente para contratar (HU-17A ST-06)

`GET /users/me/hiring-eligibility` consulta la cuenta autenticada y devuelve
`puede_contratar`, `identidad_verificada`, `correo_verificado` y `motivos`.
La regla central en `app/permissions.py` exige rol CLIENTE, identidad VERIFICADO
en PostgreSQL y correo actual confirmado en Firebase, coincidente con el perfil.
Los errores del proveedor no habilitan la contratación. Cambiar de correo exige
confirmar la nueva dirección antes de poder contratar.

Los futuros endpoints de creación y confirmación de contrataciones deben usar
`client: User = Depends(get_enabled_client)` importado desde `app.permissions`,
compartir `db: Session = Depends(get_db)` y guardar la operación en esa sesión.
La dependencia vuelve a consultar la identidad con bloqueo de fila y comprueba
el correo en ese momento. Consultar antes el estado o habilitar un botón no
autoriza una escritura posterior. Para llamadas internas, reutilizar
`require_enabled_client` sobre la cuenta obtenida del servidor.

Actualmente no existen endpoints de creación/confirmación de contrataciones en
el repositorio. La conexión de esta dependencia con esos endpoints queda pendiente
de HU-20; la creación de servicios del catálogo del técnico no es una contratación.
Esta regla no requiere nuevas migraciones.

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
