# Base visual de TekniGO

Colores confirmados por el equipo: azul #2563EB y naranja #EA580C. Logo definitivo pendiente de la diseñadora. La marca actual es un texto e ícono provisional reemplazable en un solo componente `Brand`.

| Token | Valor | Uso |
| --- | --- | --- |
| --blue | #2563EB | Acción principal, enlaces, selección |
| --blue-hover | #1D4ED8 | Interacción principal |
| --orange | #EA580C | Acentos sobre fondo claro |
| --ink | #18263B | Texto principal |
| --muted | #6E7888 | Texto secundario |
| --surface | #F6F8FC | Fondo secundario |
| --line | #E5E9F0 | Bordes |

Manrope para títulos y DM Sans para contenido son una propuesta de implementación, no una tipografía recibida en el material de referencia. Si Google Fonts no está disponible, se utiliza sans-serif del dispositivo.

Usar componentes `Brand`, `Button`, `Field` y `PasswordField` de `frontend/src/components/ui.tsx`. Etiquetas visibles, foco de teclado y errores anunciados con role=alert. Los campos de teléfono activan teclado telefónico y los de correo el teclado adecuado.

Diseño fluido sin anchura mínima global. Dos columnas en escritorio y una en móviles. Ajustes a 1000, 760 y 380 px; los formularios reducen columnas antes de causar desbordamiento. Comprobar también tamaños intermedios, zoom y orientación horizontal. Los estilos atienden `prefers-reduced-motion`.

Las pantallas usan lenguaje dirigido al usuario y explican lo pendiente. No mostrar reseñas, técnicos o validaciones ficticias como datos reales. No agregar controles sin comportamiento ni enlaces a políticas inexistentes.
