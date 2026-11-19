# Guía Paso a Paso para Publicación en GitHub Pages — Práctica 4.1

Esta aplicación utiliza rutas relativas (`./assets/...` y `./js/...`), por lo que funciona tanto en un dominio raíz como en una subruta de proyecto (`https://<usuario>.github.io/<repositorio>/`) sin errores `404` ni problemas de Mixed Content.

---

## 1. Inicializar el Repositorio Local y Subir a GitHub

Abre una terminal PowerShell dentro de la carpeta `practica_4_1_control_iot`:

```powershell
cd C:\Users\l2120\OneDrive\Documentos\practica_4_1_control_iot

git init
git branch -M main
git add .
git commit -m "feat(control): implementar aplicacion web de control IoT CAT-320 para Practica 4.1"
```

Crea un nuevo repositorio público en tu cuenta de GitHub (por ejemplo `practica-4-1-control-iot`) y enlázalo:

```powershell
git remote add origin https://github.com/<TU_USUARIO_GITHUB>/practica-4-1-control-iot.git
git push -u origin main
```

---

## 2. Activar GitHub Pages

1. Entra a tu repositorio en GitHub (`https://github.com/<TU_USUARIO_GITHUB>/practica-4-1-control-iot`).
2. Haz clic en la pestaña **Settings** (Configuración).
3. En el menú lateral izquierdo, selecciona **Pages**.
4. En la sección **Build and deployment**:
   - En **Source**, selecciona **Deploy from a branch**.
   - En **Branch**, selecciona `main` y la carpeta `/ (root)`.
   - Haz clic en **Save**.
5. Espera entre 1 y 2 minutos y recarga la página de **Settings → Pages**. Aparecerá la URL pública HTTPS:
   `https://<TU_USUARIO_GITHUB>.github.io/practica-4-1-control-iot/`

---

## 3. Verificación Post-Despliegue en GitHub Pages

1. **HTTPS y Favicon:** Abre la URL de GitHub Pages y confirma que el candado HTTPS está activo y que el icono de la Excavadora CAT-320 (`./assets/favicon.svg`) se muestra en la pestaña del navegador.
2. **Créditos al pie de la página:** Verifica que en la parte inferior se visualiza `Desarrollado por Claudia America Hernandez Jimenez · 2026` sin superponerse con ningún botón en móvil ni escritorio.
3. **CORS con FastAPI AWS:** Si al abrir desde el dominio `.github.io` observas un bloqueo CORS en la consola del navegador (`F12`), asegúrate de que el origen `https://<TU_USUARIO_GITHUB>.github.io` esté incluido en la lista `allow_origins` de la configuración CORS del servidor FastAPI en EC2.
4. **Ausencia de secretos:** Ningún archivo del repositorio contiene claves privadas, contraseñas de RDS, credenciales AWS ni cabeceras `X-API-Key`.

---

**Desarrollado por Claudia America Hernandez Jimenez · 2026**  
Práctica 4.1 — Aplicación de control para IoT.
