# Contexto del Proyecto (PROJECT_CONTEXT.md)
> Archivo mantenido automáticamente por el Agente de Desarrollo. Refleja la radiografía actual del repositorio.

## 1. Resumen y Stack

- **Propósito:** **M110** — app de escritorio multiplataforma para gestionar una colección de astrofotografía de cielo profundo con telescopio inteligente (Seestar / DwarfLab). Catálogo, ingesta, seguimiento de capturas, pre-procesado Siril/AstroWizard, planificación de sesión, backup, publicación y asistente MCP. Norte: *"Lightroom for smart telescopes"*.
- **Stack Principal:** Python ≥3.11 · **PySide6** (UI) sobre un **motor headless sin Qt** (importable en proceso, sin servidor API) · `astropy` (headers FITS) · `numpy` · `pillow` (thumbnails/hero) · `tifffile` (TIF float de Siril) · SQLite solo para el cache de hashes de backup.
- **Gestor de Paquetes:** `pip` + `setuptools` (`pyproject.toml`). Extras opcionales: `online` (astroquery), `s3` (boto3+keyring), `publish` (jinja2+markdown), `assistant` (mcp>=2,<3), `build` (pyinstaller), `dev`.
- **Licencia:** Apache-2.0. **Paquete:** `m110` (entry points: `m110` = GUI, `m110-mcp` = servidor MCP stdio, `m110-stack` = apilado headless).
- **No hay linter/type-checker configurado.** La verificación = `pytest -q` + revisión de los docs canónicos.

## 2. Comandos Oficiales

- **Instalar dependencias:** `pip install -e ".[dev]"`
- **Linter:** N/A (sin ruff/flake8/black/mypy en el repo — la disciplina la fija la revisión manual + `pytest`)
- **Typecheck:** N/A (sin mypy/pyright configurado; el tipado es *disciplina*, no tooling)
- **Tests Unitarios:** `pytest -q` (~2200 tests, todos con fixtures; offscreen Qt vía `tests/conftest.py`)
  - `pytest -q tests/test_ingest.py` (fichero concreto) · `pytest -q -k "nombre"`
- **Smoke sin pantalla:** `QT_QPA_PLATFORM=offscreen python -m m110.ui.main`
- **Generar Migración:** N/A (las migraciones son pasos idempotentes a mano en `m110/migrate.py` + bump de `STORE_VERSION` + `DATA_MODEL.md`)
- **Build / Compilación:** `python tools/release.py` (corta release) · builds PyInstaller en `packaging/{macos,linux,windows}` (specs **comunes** en `packaging/common/`)
- **Despliegue (Cloudflare u otro):** N/A — app de escritorio. Publicación de la web estática: `m110/publish/ghpages.py` (git) o `publish/site.py` (carpeta local); docs versionadas en `site/docs/<tag>/` vía `python tools/build_docs.py`.
- **Otros scripts útiles:** `tools/smoke_mcp.py` (guarda el *bound* del SDK MCP), `tools/make_test_corpus.py` (corpus de test manual), `tools/gen_{caldwell,catalogs,geonames}.py`, `tools/drill_backup_s3.py`.

### Reglas de worktree / venv (trampa conocida)

El venv es **compartido** y el install editable apunta al checkout principal. Desde un worktree:
- `python -m m110.ui.main` y `pytest -q` → ✅ ejecutan el código del worktree.
- El console script `m110` y `python tools/<script>.py` → ❌ ejecutan el código de `main` (su `sys.path[0]` es `.venv/bin`). Usar `PYTHONPATH=$PWD`.
- **Nunca** `pip install -e .` dentro de un worktree (reapunta el venv compartido).

## 3. Mapa de Arquitectura y Convenciones Locales

- **Patrón estructural:** **Clean Architecture pragmática en dos capas** — `m110/ui/` (PySide6, cliente delgado) importa **en proceso** el motor `m110/` (sin Qt, testeable y reutilizable: CLI, servidor MCP, futuros clientes). Dentro del motor: dominio/aplicación en los módulos de primer nivel, infraestructura en `m110/backup/`, `m110/publish/`, `m110/assistant/`.
- **Ubicación de Logger central:** `m110/logsetup.py` — log rotatorio en `~/.m110/logs/m110.log`. **Prohibido `print()`/`console.log` en código de producción.**
- **Ubicación de Clase Base de Errores:** no hay una clase base única; el patrón del repo son **excepciones de dominio con nombre semántico** definidas junto a su módulo (p. ej. `OnlineLookupError` en `catalog.py`, `PublishDepsMissing`/`BackupDepsMissing`/`SkymapDepsMissing` en sus módulos). Un error semántico nuevo → clase propia, nunca `raise Exception("texto")`.
- **Ubicación de Tests:** carpeta raíz `tests/` (no co-localizados), un fichero por módulo/subsistema: `tests/test_<módulo>.py`. Fixtures compartidos en `tests/_helpers.py`; aislamiento global en `tests/conftest.py` (autouse session-scoped que redirige `M110_DATA_ROOT`, `config.*`, `SETTINGS_FILE`, `APP_CONFIG_DIR` a un dir temporal). Goldens de render en `tests/goldens/` (refrescar con `M110_UPDATE_GOLDENS=1`).
- **Source of truth vs derivado:** los ficheros legibles (TOML/JSONL/MD) son la verdad; `derived/` + `renders/` son **regenerables** (un Refresh los reconstruye). Nada valioso existe solo en la capa derivada.

### 3.1 El árbol de contenido y sus ejes

```
<data_root>/                        (M110_DATA_ROOT → pref. → ~/Documents/M110)
  Objects/<catalog id>/journal.md   eje catálogo-objeto (un stub por cada objeto)
  Images/<target>/                  eje capture-target (a qué apuntó el telescopio)
    lights/ rejected/ stacks/ device-stacks/ finished/ previews/ working_files/
    darks/ flats/ biases/            overrides por target de Calibration/<device>/
    siril/ astrowizard/             sandboxes por workflow
  Media/<Category>_photo|_video/    media lunar/planetario/escénica
  Calibration/<device>/{darks,flats,biases}/   librería master por dispositivo
  Inbox/                            staging + holding area
  Plans/                            field guides guardados
  .m110_internal_data/              library.toml goals.toml pins.toml devices.toml
                                    integration_goals.toml sessions.jsonl profiles/
                                    derived/ renders/ assistant/ .store_version (=6)
```

- **Resolución del data root** (`m110/config.py`): env `M110_DATA_ROOT` → preferencia (`~/.m110/settings.json`) → `~/Documents/M110`. `ensure_data_root()` migra (`migrate.migrate_store()`, idempotente, versionado, nunca destructivo) y hace seed. Cambiar la raíz = reiniciar.
- **Rutas por target:** **siempre** vía helpers de `config.py` (`target_dir`, `lights_dir`, `rejected_dir`, `stacks_dir`, `device_stacks_dir`, `finished_dir`, `previews_dir`, `working_files_dir`, `darks_dir`, `flats_dir`, `biases_dir`, `calibration_dir`, `siril_dir`, `siril_job_dir`, `astrowizard_dir`). *Varios walks hardcodean el string del tier* (`scan_sessions.py:465`, `build_derived.py:442/682/696/699`, `roundtrip.py:403`, `processing.py:143/209`, `catalog.py:926`, `stacking.py:1755`) — al tocar el árbol, revisarlos todos.

### 3.2 Puntos de acoplamiento críticos (mapa de "no tocar sin mirar")

| Acoplamiento | Dónde | Por qué importa |
|---|---|---|
| **Escritura en el árbol de contenido** | `ingest.apply_ops` (`ingest.py:1736`) — único escritor en `Images/` | Toda escritura es **preview-then-confirm**: `scan_*_plan()` (solo lectura) → `apply_ops()` (escritor único) tras confirmación del diálogo. Guard de contención: el dest debe resolver dentro de `DATA_ROOT`. |
| **Copiar = bytes, no metadatos** | `shutil.copyfile` (no `copy2`) + `.part` → `os.replace` | Copiar flags/xattrs desde un Seestar SMB lanza `EPERM` en macOS. `copy2` solo se usa en `assistant/client_config.py`. |
| **Hardlinks** | `roundtrip.link_or_copy` (`roundtrip.py:75`) — sandboxes Siril/AstroWizard + `backup/mirrored.py` | El ingest **nunca** hace hardlink. Los hardlinks de sandbox están excluidos de backup vía `config.SANDBOX_LINKED_INPUTS`. |
| **Caminos persistidos** | root-relativos: `derived/images.json` (`src`/`full`), `.m110_internal_data/renders/hero/<slug>.src`, `derived/devices.json` (`rel`/`preview`/`lights_rel`), `siril/.m110_calibration.json` (`library`); absolutos: manifests de backup (`source_root`), `backup/hashcache.py` (clave = path absoluto), `~/.m110/settings.json` (`data_root`) | Un cambio de raíz/ruta rompe estos → usar `config.upgrade_legacy_rel()` en los 3 sitios de lectura (`derived.py:100`, `build_images.py:209/226`). |
| **Backup vs. store** | `backup/scope.iter_source_files` camina `DATA_ROOT` | Lo que no está **en** el store no se respalda. Referenciar contenido externo obliga a tocar este módulo. |
| **Headers ≠ nombres de fichero** | `scan_sessions`, `calibration`, `capture_profiles` | Los hechos de captura salen de la cabecera FITS; el nombre solo es *fast path* (Seestar). `config.is_fits_file` (`.fit`/`.fits`, nunca `._*` AppleDouble) es la única autoridad. |
| **Jerarquía de sandboxes** | `config.SANDBOX_LINKED_INPUTS` / `SANDBOX_DIRNAMES` / `SANDBOX_SCRATCH` | Deben declararse en los **tres** sitios que caminan: `roundtrip.ROOT_SKIP_DIRS`, `ingest._SKIP_DIRS`, `backup.scope.is_excluded`. Un sandbox sin declarar falla en 3 sitios, de 3 formas distintas. |
| **Qt desde workers** | `m110/ui/widgets.py` (`drain_worker`, `ProgressDialog`, `defer`) | Solo señales cruzadas. Nunca tocar widgets desde un hilo. Nunca `QIcon(path)` para iconos de tamaño fijo. |

## 4. Módulos y Dominios Activos

### 4.1 Motor (`m110/`, Qt-free)

| Módulo | Responsabilidad |
|---|---|
| `config.py` | Resolución del data root, bootstrap/seed, **helpers de ruta** (la autoridad), settings, detección de montaje Seestar, `is_fits_file`/`is_hidden_name`/`is_light_frame`, registro de sandboxes |
| `migrate.py` | Migración idempotente in-place del store (`STORE_VERSION = 6`); `_rename_device_stacks`, `_reshape_v0_to_v2` |
| `ingest.py` | **Escaneo read-only del plan** (`scan_directory_plan`, layouts Seestar/Dwarf/N.I.N.A./raw/store/calibration) + `apply_ops` (único escritor), holding area en `Inbox/`, canonicalización de target (`canonical_target`), alias table |
| `scan_sessions.py` | `Images/<t>/lights/` → `sessions.jsonl` (rollup por noche/dispositivo/filtro) |
| `build_derived.py` | `derived/{totals,priorities,summary,processing,goals,devices}.json` |
| `build_images.py` | Thumbnails + hero + `derived/images.json`; cache por hash de contenido; identidad del hero en sidecar `.src` |
| `calibration.py` | Matching de masters **por cabecera** (nunca por nombre); `raw_sets`; rollup `derived/devices.json` |
| `capture_profiles.py` | Qué software grabó cada frame → `device_key`, `darks_applied` (por frame), `filter_name`, `object_name` |
| `devices.py` | Identidad del telescopio desde `TELESCOP`; registro `devices.toml` |
| `roundtrip.py` | Mitad tool-neutral del sandbox: clasificar, `scan_finished`/`apply_import`/`archive_run`, `prune_archives`, guardas de concurrencia (`require_idle`, `sandbox_busy`) |
| `siril.py` | Round-trip Siril: `plan_prep`/`apply_prep` (hardlink de `lights/` + calibración por filtro y por job dir), `autoprep`, manifest de calibración, `prune_rejected` |
| `astrowizard.py` | Round-trip AstroWizard: `is_master`/`is_autosave`/`is_handoff` (por sidecar, no por nombre), `prepare_lights` |
| `stacking.py` / `stackhammer.py` | Apilado headless (`m110-stack`): plan read-only vs. ejecución; calibración por subset; motor Stackhammer sobre su binario `--json` |
| `processing.py` | Registro de workflows, `run_autoprep`, `prepare_missing`, `reconcile_rejected` |
| `launch.py` | Lanzador de apps externas (Siril, AstroWizard, StackingWizard) con `_child_env()` saneado |
| `catalog.py` | `library.toml` (colección capturada) vs. catálogos seed; add/remove/enrich; Simbad (opcional) |
| `objects.py` · `derived.py` · `goals.py` · `pins.py` · `integration_goals.py` | Journal y frontmatter · lectura de `derived/` · goals/listas por target · pins/overrides · metas de integración por (objeto, modelo) |
| `refresh.py` | Orquestador: scan → derive → render (`run_refresh`, `rederive`) |
| `planning.py` · `fieldguide.py` · `planning_config.py` · `horizon.py` · `glow.py` · `prioritize.py` | Crepúsculo, observabilidad, plan de noche, campo imprimible, perfiles de sitio, máscara de horizonte, light dome, ranker determinista |
| `media.py` · `webexport.py` · `hints.py` | Media no-catalogada · export PNG/JPEG para compartir · vocabulario de nombres `finished` |
| `backup/` | Motor de snapshot: `destination` (carpeta o `s3://`), `backends/` (local/memory/S3), formatos `mirrored` (hardlink incremental) y `pooled` (content-addressed), `scope` (denylist + tier `essentials`), `slots`, `retention`, `probe`, `recovery` |
| `publish/` | Publicador: `site` (Jinja2 + sweep de stale), `ghpages` (git), `images` (niveles de galería), `options` |
| `assistant/` | Asistente MCP: `registry` (19 tools), `serialize`, `proposals`, `outbox` (**único escritor, solo-crea**), `apply` (lado app), `mcp_server` (SDK v2, stdout→stderr), `skills`, `vision` |
| `updates.py` · `running_app.py` · `logsetup.py` | Check de releases (GitHub) · registro de la GUI activa · logger rotatorio |
| `seed/` | `objects.toml` (referencia), `catalogs/*.toml`, subconjunto GeoNames — generados por `tools/gen_*.py` |

### 4.2 UI (`m110/ui/`, PySide6)

| Módulo | Responsabilidad |
|---|---|
| `main.py` | Shell: sidebar agrupado → 8 lugares (`sidebar.PLACES`: Overview · Deep Sky · Media · Journal · Import · Processing · Planning · Devices), barra de título, menús, `RefreshWorker` |
| `sidebar.py` · `toolbar.py` · `undo.py` · `macos_chrome.py` | Lista lateral · barra de cabecera · pila de Undo · chrome AppKit |
| `widgets.py` | Tablas/thumbnails compartidos, `drain_worker`, `ProgressDialog`, `modal_loop_active`/`defer`, `connect_context_menu` |
| `image_grid.py` · `detail.py` | Grid de tiles justificado · `DetailPane` (inspector por objeto: hero, facts, gallery, notas, metas) |
| `pages/` | `catalog.py` (Deep Sky), `overview.py` + `catalog_board.py`, `processing.py`, `sessions.py`, `journal.py`, `media.py`, `planning.py`, `devices.py`, `import_page.py` |
| Diálogos | `ingest_dialog.py`, `import_dialog.py`, `holding_inspect_dialog.py`, `preset_conflict_dialog.py`, `handoff_dialog.py`, `export_dialog.py`, `add_object_dialog.py`, `image_viewer.py`, `media_detail.py`, `media_cleanup_dialog.py`, `site_profile_editor.py`, `field_guide_dialog.py`, `rematch_dialog.py` |
| `preferences.py` + `backup_pane.py` + `publish_dialog.py` + `restore_dialog.py` + `mcp_details_dialog.py` | Ventana de Settings (pestañas, apply instantáneo) |
| `theme/` | `tokens` (light/dark/night), `contrast`, `qss`, `manager` (sigue el SO), `night` (imágenes en rojo), `fonts`, `icons` (Lucide), `brand` — **colores/espacios siempre desde `tokens`, nunca hex hardcodeado** |

### 4.3 Tests, CI y empaquetado

- `tests/` — 135 ficheros `test_*.py` + `_helpers.py` + `conftest.py` + `goldens/` + `data/`. Naming: `tests/test_<módulo>.py`.
- CI (`.github/workflows/ci.yml`): Python **3.11 y 3.14**, `pip install -e ".[dev]"`, `pytest -q`, y un smoke real del servidor MCP (`tools/smoke_mcp.py`) que **falla ruidosamente** si el SDK `mcp` rompe su API. Instala `uranometria` desde git (no está en PyPI).
- `packaging/` — specs PyInstaller por SO en `packaging/{macos,linux,windows}`, hooks comunes en `packaging/common/`. Regla: **todo lo que lee dist-info/ficheros de datos en import** (metadatos de astropy, parsetabs PLY, modelos de botocore, JSON de uranometria) debe recogerse en los specs y **validarse contra un build real**.

### 4.4 Documentos canónicos (leer antes de tocar un subsistema)

| Fichero | Contenido |
|---|---|
| `CLAUDE.md` | Mapa de módulos + convenciones + **gotchas** (se carga en cada turno). Empezar por aquí. |
| `DATA_MODEL.md` | Modelo de datos: principios e invariantes, jerarquía de entidades, layout del store, tiers, backup, versionado, flujo de datos, seams futuros |
| `ROADMAP.md` | Qué falta y en qué orden. `DONE.md` = por qué/cómo se entregó cada subsistema (grep por módulo) |
| `BUGS.md` | Issues abiertos. `TESTING.md` = runbook de test manual. `UI_ROADMAP.md` = UX pendiente |
| `CHANGELOG.md` | `[Unreleased]` → release. `tools/release.py` mueve `[Unreleased]` y **falla si está vacío** |
| `docs/` | Guía de usuario (`docs/ingest.md`, `library.md`, `processing.md`, `backup.md`, `planning.md`, `publishing.md`, `devices.md`, `assistant.md`) |

### 4.5 Gotchas de alto impacto (resumen; el detalle está en `CLAUDE.md`/DONE.md)

1. **Nunca escribir en el árbol de contenido sin confirmación explícita** — todo es `scan_*_plan()` → `apply_ops()`.
2. **`.fit` y `.fits`**: usar siempre `config.is_fits_file`, nunca un check de sufijo pelado (rechaza los `._` AppleDouble, #161).
3. **Los sub del *app* de Seestar ya están restados de dark en el dispositivo** — la bandera `darks_applied` es **por frame**, no por modelo.
4. **Los facts de captura vienen de la cabecera**, nunca del nombre de fichero.
5. **Caminar saltando `lights/`/sandboxes se poda en el directorio** (`roundtrip._walk_files`), nunca `rglob` + filtro.
6. **Nunca confiar en mtime** para procedencia/frescura (el mtime de un ingest es el de la copia) → cabeceras (`DATE-OBS`, `DATE`).
7. **Opciones de Siril con quotes de token completo**: `"-dark=<path>"`; `-dark="<path>"` deja las comillas en el nombre y falla.
8. **La calibración de Siril va en cada job dir** (`siril/<FILTER>/darks/`), no solo en la raíz del sandbox.
9. **El motor es Qt-free** (nada de `PySide6` en `m110/*.py`).
10. **Los tests nunca tocan datos reales** — `conftest.py` sella `M110_DATA_ROOT` + `config.*` + `SETTINGS_FILE` + `APP_CONFIG_DIR` por sesión.
11. **El harness de test manual viaja con el cambio** (`tools/make_test_corpus.py` + `create_test_harness.sh` en el mismo commit).
12. **Cierre de cada cambio**: `pytest -q` verde + docs actualizados (`ROADMAP`/`BUGS`/`DATA_MODEL`/`TESTING`/`CHANGELOG [Unreleased]`/`DONE`) en el **mismo commit**.

### 4.6 Flujo de entrega (GitLab/GitHub)

Rama por unidad de trabajo (`feature/<short>` sobre `main`; los cambios solo-docs pueden ir directos a `main`) → `pytest -q` → actualizar docs → commit Conventional Commits → push → MR a `main` (`glab mr create --target-branch main --fill --yes`; `glab ci status` para el pipeline). Despliegue en Cloudflare: solo si lo pide el usuario (`npx wrangler deploy`) — no aplica a esta app de escritorio.
