# Publicar ChaoChaito Quiniela en chaochaito.com/quiniela

Piezas: **GitHub** (código) → **Vercel** (ejecuta la app y la base de datos) → **Cloudflare** (dominio).

## 1. GitHub
1. Crea un repositorio **privado** llamado `chaochaito` en tu cuenta personal (no en una organización),
   **vacío**: sin README, sin .gitignore y sin licencia.
2. Sube el código (lo hace Claude por ti, o con GitHub Desktop: *Add Existing Repository* → esta carpeta).

## 2. Vercel
1. vercel.com → **Add New… → Project** → importa el repositorio `chaochaito`.
2. El framework lo detecta solo (Flask / Python). No cambies nada más y pulsa **Deploy**.
   El primer despliegue funcionará, pero todavía sin base de datos ni PIN.
3. **Base de datos**: en el proyecto → pestaña **Storage** → **Create Database** → **Postgres (Neon)**,
   plan gratis, región Europa (Frankfurt). Conéctala al proyecto: Vercel añade solo la variable `DATABASE_URL`.
4. **Variables**: *Settings → Environment Variables* (marca Production y Preview):

   | Nombre | Valor |
   |---|---|
   | `QUINIELA_BASE_PATH` | `/quiniela` |
   | `QUINIELA_ADMIN_PIN` | el PIN que quieras para la zona de administrador |
   | `QUINIELA_SECRET_KEY` | una cadena larga aleatoria (opcional: si falta, se deriva del PIN) |

5. *Deployments* → en el último, **⋯ → Redeploy** para que coja las variables.
6. Prueba la dirección que te da Vercel: `https://<tu-proyecto>.vercel.app/quiniela/`.
   Pulsa el candado 🔒, mete el PIN y abre `…/quiniela/api/diagnostico` para ver si Loterías responde
   y si la base de datos es `postgres`.

## 3. Cloudflare (dominio)
1. En Vercel: *Settings → Domains* → añade `chaochaito.com` (y `www.chaochaito.com` si quieres).
   Vercel te muestra los registros DNS que necesita (normalmente un **A** para `chaochaito.com` y un
   **CNAME** para `www`).
2. En Cloudflare: *chaochaito.com → DNS → Records* → crea esos registros con los valores exactos que te dio
   Vercel y con el **proxy desactivado (nube gris, "DNS only")**.
3. Espera unos minutos: Vercel pone el certificado HTTPS solo. Ya está en `https://chaochaito.com/quiniela`
   (`chaochaito.com` a secas redirige a `/quiniela`).

## Cada semana
- Si Loterías bloquea la lectura desde Vercel: entra como administrador → **Introducir a mano** → pega los
  datos del sorteo (los botones de fechas abren la página de Loterías en tu navegador).
- Confirma las 4 apuestas desde la zona de administrador.

## Actualizar la web
Cualquier cambio que se suba a GitHub se publica solo en Vercel en un par de minutos.
