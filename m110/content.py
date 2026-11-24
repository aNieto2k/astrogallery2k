"""Content references — the store's index of the files that live **outside** it.

Until now M110's store was self-contained: importing a night of subs **copied**
every frame into `Images/<target>/lights/`, so a library cost twice the disk
(telescope + store) and every backup carried a second full copy of the frames.
This module is the seam that removes the copy.

The model is Adobe Lightroom's, applied to a capture library:

* **The original never moves and is never modified.** It stays wherever the
  user's telescope (or folder) put it.
* **M110 keeps a "wrapper"** — a small, human-readable record — describing what
  the content *is* (which target, which tier, which name, where it came from)
  instead of moving the bytes around to express it. Rejecting a sub, renaming
  it canonically, recording where it came from: all of it is a field in the
  wrapper, never a filesystem operation on the user's file.

**The wrapper is a manifest**: one `content.toml` per *owner* — a capture
target (`Images/<target>/`), a device's calibration library
(`Calibration/<device>/`) or a media category (`Media/<category>/`). It lists
the content whose bytes are **not** in the store, pointing at the real path.

    schema = 1

    [[items]]
    path = "lights/Light_M81_001.fit"
    origin = "/Volumes/SEESTAR/MyWorks/M81/Light_M81_001.fit"
    volume = "/Volumes/SEESTAR"
    rel = "MyWorks/M81/Light_M81_001.fit"
    size = 48234496
    mtime_ns = 1764000000000000000
    imported_at = "2026-11-24T09:12:03+00:00"
    source = "seestar"

Two sources of truth, one per kind of content — never both:

* **managed** — the bytes are in the store. The file on disk *is* the record;
  the manifest says nothing about it. This is every store that existed before
  this module, and it keeps working untouched.
* **referenced** — the bytes are outside the store. The manifest entry *is* the
  record, and it is the only link to the file.

`list_tier()` returns both, as one uniform `ContentRef`, so a consumer never
has to care which it got: `ref.origin` is always the path to open.

**The manifest is not derived data.** It is the only pointer to files M110 does
not own, so it lives in the *visible* content axis (next to the content it
describes), never under `.m110_internal_data/` — that directory is documented
as disposable, and losing it would silently orphan every referenced frame.

**What this costs, stated plainly:** a referenced store is no longer
self-contained. If the original is deleted or its volume is unmounted, the
library can no longer open it — `health()` reports that, and `relink()` /
`materialize()` repair it. The backup engine copies the manifests (the links)
but **not** the referenced bytes: backing those up is the user's own job, and
the backup UI says so out loud.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from . import config

# The manifest format version. Bump only for a breaking change to the file
# layout; readers accept anything they understand and ignore the rest.
SCHEMA = 1

# The manifest's file name, inside an owner directory. Not a leading-dot name:
# the store's own convention is that only `.m110_internal_data/` and
# `.store_version` are dot-files M110 creates (DATA_MODEL.md, principle 6).
MANIFEST_NAME = "content.toml"

# The tiers a capture target can hold (config.CONTENT_TIERS) and a device's
# calibration library can hold (config.CALIBRATION_TIERS). The sandbox
# directories (`siril/`, `astrowizard/`) are deliberately absent — a sandbox is
# a workflow's working area, not library content.

# A manifest item's own field names, as they appear in the TOML.
_FIELDS = ("path", "origin", "volume", "rel", "size", "mtime_ns", "sha256",
           "imported_at", "source")


@dataclass(frozen=True)
class ContentRef:
    """One piece of library content, and where its bytes actually are.

    `store_path` is the **logical** path M110 shows the user and records in
    its metadata — store-relative, POSIX-style, e.g.
    ``Images/M81/lights/Light_M81_001.fit``. For a managed item that path also
    exists on disk; for a referenced item it does not, and `origin` is the
    real one.
    """

    store_path: str
    origin: str
    referenced: bool
    size: int = 0
    mtime_ns: int = 0
    sha256: str | None = None
    imported_at: str = ""
    source: str = ""
    volume: str = ""
    rel: str = ""

    @property
    def name(self) -> str:
        """The file name (the last segment of the logical path)."""
        return self.store_path.rsplit("/", 1)[-1]

    @property
    def tier(self) -> str:
        """The tier this item lives in (``lights``, ``stacks``, …); ``""`` for
        media, which has no tier."""
        return tier_of(self.store_path)

    @property
    def owner(self) -> str:
        """The owner directory of the logical path (``Images/M81``)."""
        return owner_of(self.store_path)

    @property
    def exists(self) -> bool:
        """True if the bytes are reachable right now."""
        return Path(self.origin).exists()


@dataclass
class Health:
    """Whether a referenced item's original is still reachable."""

    ok: list[ContentRef] = field(default_factory=list)
    missing: list[ContentRef] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return not self.missing

    @property
    def missing_bytes(self) -> int:
        return sum(r.size for r in self.missing)

    @property
    def total_bytes(self) -> int:
        return sum(r.size for r in self.ok) + self.missing_bytes


# ── layout rules ─────────────────────────────────────────────────────────────
# Where an owner ends and a tier begins, and vice versa. One place, so a new
# axis is a one-line change rather than a scavenger hunt.

def owner_of(store_path: str) -> str:
    """The owner directory of a store-relative logical path.

    ``Images/M81/lights/x.fit`` → ``Images/M81``; ``Calibration/S50/darks/y.fit``
    → ``Calibration/S50``; ``Media/Lunar_photo/z.mp4`` → ``Media/Lunar_photo``.
    A path that *is* an owner directory (``Images/M81``) is its own owner.

    The two axes with tiers (``Images``, ``Calibration``) put the owner two
    levels down — the tier sits between the owner and the file. Media has no
    tier: the category directory *is* the owner.
    """
    parts = store_path.split("/")
    if parts[0] in ("Images", "Calibration") and len(parts) >= 2:
        return "/".join(parts[:2])
    if parts[0] == "Media":
        if len(parts) >= 3:
            return "/".join(parts[:2])
        return store_path
    return "/".join(parts[:-1])


def tier_of(store_path: str) -> str:
    """The tier of a store-relative logical path (``""`` for media)."""
    parts = store_path.split("/")
    if len(parts) >= 3 and parts[0] in ("Images", "Calibration") \
            and parts[2] in _tiers_for(parts[0]):
        return parts[2]
    return ""


def _tiers_for(axis: str) -> tuple[str, ...]:
    return config.CONTENT_TIERS if axis == "Images" else config.CALIBRATION_TIERS


def store_path_of(owner: str, item_path: str) -> str:
    """The store-relative logical path of an item inside an owner."""
    return f"{owner}/{item_path}" if item_path else owner


def owner_dir(owner: str) -> Path:
    """The absolute directory of an owner (``Images/M81`` → ``<root>/Images/M81``).

    Resolved through the **axis** directories (`config.IMAGES_DIR`,
    `config.CALIBRATION_DIR`, `config.MEDIA_DIR`) rather than `DATA_ROOT` — the
    axes are the authority for where content lives, and they are what a caller
    (or a test) may have pointed somewhere else.
    """
    axis, _, rest = owner.partition("/")
    base = _AXES.get(axis)
    if base is not None and rest:
        return base() / rest
    return config.DATA_ROOT / owner


def store_path_to_path(store_path: str) -> Path:
    """A store-relative logical path → the path it occupies inside the store."""
    axis, _, rest = store_path.partition("/")
    base = _AXES.get(axis)
    if base is not None and rest:
        return base() / rest
    return config.DATA_ROOT / store_path


def logical_path(path) -> str | None:
    """The store-relative logical path of a file inside the store, or None when
    it is not in the store at all (a referenced original, out on the device)."""
    p = Path(path)
    for axis, base in (("Images", config.IMAGES_DIR),
                        ("Calibration", config.CALIBRATION_DIR),
                        ("Media", config.MEDIA_DIR)):
        try:
            rel = p.relative_to(base)
        except ValueError:
            continue
        return f"{axis}/{rel.as_posix()}"
    return None


# The three visible content axes, resolved lazily so a test (or a caller) that
# re-points them is honoured.
_AXES = {
    "Images": lambda: config.IMAGES_DIR,
    "Calibration": lambda: config.CALIBRATION_DIR,
    "Media": lambda: config.MEDIA_DIR,
}


def manifest_path(owner: str) -> Path:
    """The manifest file of an owner."""
    return owner_dir(owner) / MANIFEST_NAME


def tier_dir(owner: str, tier: str) -> Path:
    """The physical directory a tier occupies inside an owner (``""`` = the owner
    itself, which is how media is laid out)."""
    return owner_dir(owner) / tier if tier else owner_dir(owner)


def is_manifest_name(name: str) -> bool:
    """True for the manifest itself and for the transient ``.part`` file a
    write leaves behind — neither is library content."""
    return name == MANIFEST_NAME or name.endswith(".part")


# ── the manifest ─────────────────────────────────────────────────────────────

def load(owner: str) -> list[ContentRef]:
    """The referenced items of an owner, read from its manifest.

    Tolerant by design: a missing or malformed manifest yields no items (the
    store then simply holds nothing referenced), never an exception. A broken
    manifest must not take the library down — it costs the user the links,
    not the app.
    """
    path = manifest_path(owner)
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError, ValueError):
        return []
    items = data.get("items")
    if not isinstance(items, list):
        return []
    out: list[ContentRef] = []
    for entry in items:
        ref = _ref_from_entry(owner, entry)
        if ref is not None:
            out.append(ref)
    return out


def _ref_from_entry(owner: str, entry) -> ContentRef | None:
    """One manifest entry → a `ContentRef`, or None if it is unusable."""
    if not isinstance(entry, dict):
        return None
    path = entry.get("path")
    origin = entry.get("origin")
    if not isinstance(path, str) or not path.strip():
        return None
    if not isinstance(origin, str) or not origin.strip():
        return None
    return ContentRef(
        store_path=store_path_of(owner, path.strip()),
        origin=origin,
        referenced=True,
        size=_as_int(entry.get("size")),
        mtime_ns=_as_int(entry.get("mtime_ns")),
        sha256=_as_str(entry.get("sha256")),
        imported_at=_as_str(entry.get("imported_at")),
        source=_as_str(entry.get("source")),
        volume=_as_str(entry.get("volume")),
        rel=_as_str(entry.get("rel")),
    )


def _as_int(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _as_str(value) -> str:
    return value if isinstance(value, str) else ""


def save(owner: str, refs: list[ContentRef]) -> None:
    """Rewrite an owner's manifest. The single writer of the file.

    Atomic (``.part`` → ``os.replace``), so a crash mid-write cannot leave a
    half-written manifest that would orphan every frame it names.
    """
    path = manifest_path(owner)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# M110 content manifest — the files that live OUTSIDE this store.",
        "#",
        "# Each entry points at a file M110 did not copy: the bytes stay where",
        "# they are, and this file is the only link to them. Rejecting a sub,",
        "# renaming it canonically or recording its origin is a field here —",
        "# never a change to the original.",
        "#",
        "# This file is not derived: delete it and M110 forgets the content.",
        f"schema = {SCHEMA}",
        "",
    ]
    for ref in sorted(refs, key=lambda r: r.store_path):
        lines.append("[[items]]")
        lines.append(f"path = {_q(_item_path(owner, ref.store_path))}")
        lines.append(f"origin = {_q(ref.origin)}")
        for name in ("volume", "rel", "sha256", "imported_at", "source"):
            value = getattr(ref, name)
            if value:
                lines.append(f"{name} = {_q(value)}")
        for name in ("size", "mtime_ns"):
            value = getattr(ref, name)
            if value:
                lines.append(f"{name} = {int(value)}")
        lines.append("")
    tmp = path.parent / (path.name + ".part")
    tmp.write_text("\n".join(lines), encoding="utf-8")
    os.replace(tmp, path)


def _item_path(owner: str, store_path: str) -> str:
    """The manifest-relative path of an item (the store path minus the owner)."""
    prefix = owner.rstrip("/") + "/"
    return store_path[len(prefix):] if store_path.startswith(prefix) else store_path


def item_path(owner: str, store_path: str) -> str:
    """The path of an item relative to its owner (``lights/x.fit``)."""
    return _item_path(owner, store_path)


def add(owner: str, refs: list[ContentRef]) -> list[ContentRef]:
    """Add (or replace) items in an owner's manifest. Returns the new item list.

    An item whose logical path is already taken is replaced — the manifest is
    keyed by the logical path, so re-importing the same file updates its
    record instead of duplicating it.
    """
    merged = {r.store_path: r for r in load(owner)}
    for ref in refs:
        merged[ref.store_path] = ref
    out = sorted(merged.values(), key=lambda r: r.store_path)
    save(owner, out)
    return out


def remove(owner: str, store_path: str) -> bool:
    """Drop one item from an owner's manifest. True if it was there."""
    items = load(owner)
    kept = [r for r in items if r.store_path != store_path]
    if len(kept) == len(items):
        return False
    save(owner, kept)
    return True


def update(owner: str, store_path: str, **fields) -> bool:
    """Change fields of one manifest item. True if the item was there.

    This is the "wrapper" operation the whole model exists for: moving a frame
    from ``lights/`` to ``rejected/`` is a change of the item's logical path,
    and nothing on disk moves.
    """
    items = load(owner)
    found = False
    out: list[ContentRef] = []
    for ref in items:
        if ref.store_path != store_path:
            out.append(ref)
            continue
        found = True
        out.append(replace(ref, **{k: v for k, v in fields.items()
                                   if k in _FIELDS or k == "path"}))
    if found:
        save(owner, out)
    return found


def move(owner: str, store_path: str, new_tier: str) -> bool:
    """Move a referenced item to another tier of the same owner.

    The bytes do not move — only the record does. This is what "reject this
    sub" becomes under the reference model, and it is why rejecting a frame
    can no longer touch the user's original.
    """
    for ref in load(owner):
        if ref.store_path != store_path:
            continue
        item = _item_path(owner, store_path)
        head, _, tail = item.partition("/")
        new_item = f"{new_tier}/{tail}" if tail else new_tier
        save(owner, [replace(r, store_path=store_path_of(owner, new_item))
                     if r.store_path == store_path else r
                     for r in load(owner)])
        return True
    return False


def move_to_tier(store_path: str, new_tier: str) -> bool:
    """Move a piece of content to another tier of its owner.

    The operation behind "reject this sub", and it works the same for both
    kinds of content:

    * **managed** — the file moves inside the store, exactly as it always did.
    * **referenced** — only the *record* moves. The file stays where it is and
      is not touched, renamed or deleted: the tier is a fact about the entry,
      not a place the bytes live.

    True when something moved.
    """
    owner = owner_of(store_path)
    if move(owner, store_path, new_tier):
        return True
    src = store_path_to_path(store_path)
    if not src.is_file():
        return False
    dest_dir = owner_dir(owner) / new_tier
    dest_dir.mkdir(parents=True, exist_ok=True)
    os.replace(src, dest_dir / src.name)
    return True


# ── enumeration ──────────────────────────────────────────────────────────────

def list_owner(owner: str) -> list[ContentRef]:
    """Every piece of content an owner holds — managed and referenced."""
    items = load(owner)
    out: list[ContentRef] = []
    seen: set[str] = set()
    for tier in _owner_tiers(owner):
        for ref in _tier_items(items, owner, tier):
            if ref.store_path in seen:
                continue
            seen.add(ref.store_path)
            out.append(ref)
    return out


def list_tier(owner: str, tier: str) -> list[ContentRef]:
    """The content of one tier of an owner — managed files and referenced items.

    Managed content is whatever is on disk in the tier directory; referenced
    content is what the manifest says. A logical path claimed by both resolves
    to the referenced item (the manifest is the more specific record) and the
    physical file is reported by `orphans()`.
    """
    return _tier_items(load(owner), owner, tier)


def _tier_items(items: list[ContentRef], owner: str, tier: str) -> list[ContentRef]:
    """One tier's content, from an already-loaded manifest (so a caller that
    walks every tier reads the manifest once, not once per tier)."""
    out: list[ContentRef] = []
    seen: set[str] = set()
    for ref in items:
        if ref.tier != tier:
            continue
        seen.add(ref.store_path)
        out.append(ref)
    for ref in _managed_in(tier_dir(owner, tier), owner, tier):
        if ref.store_path not in seen:
            out.append(ref)
    return out


def _owner_tiers(owner: str) -> tuple[str, ...]:
    """The tiers an owner can hold."""
    if owner.startswith("Images/"):
        return config.CONTENT_TIERS
    if owner.startswith("Calibration/"):
        return config.CALIBRATION_TIERS
    return ("",)          # media: the category directory itself


def _managed_in(directory: Path, owner: str, tier: str) -> list[ContentRef]:
    """The files physically present in a tier directory, as managed refs.

    Media (a tier of "") is walked recursively: a category can hold nested
    folders, and the tier directories are flat.
    """
    out: list[ContentRef] = []
    for path in _managed_paths(directory):
        name = path.name
        try:
            st = path.stat()
        except OSError:
            continue
        rel = _rel_to(directory, path)
        if tier:
            rel = f"{tier}/{rel}"
        out.append(ContentRef(
            store_path=store_path_of(owner, rel),
            origin=str(path),
            referenced=False,
            size=st.st_size,
            mtime_ns=st.st_mtime_ns,
        ))
    return out


def _managed_paths(directory: Path) -> list[Path]:
    """The content files of a tier directory (no hidden names, no manifest, no
    leftover ``.part`` from an interrupted write)."""
    if not directory.is_dir():
        return []
    out: list[Path] = []
    try:
        entries = sorted(directory.rglob("*"))
    except OSError:
        return out
    for path in entries:
        if not path.is_file():
            continue
        if config.is_hidden_name(path.name) or is_manifest_name(path.name):
            continue
        out.append(path)
    return out


def _rel_to(base: Path, path: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.name


def list_target(target: str) -> list[ContentRef]:
    """Every piece of content of a capture target."""
    return list_owner(f"Images/{target}")


def list_target_tier(target: str, tier: str) -> list[ContentRef]:
    """One tier of a capture target (``lights``, ``rejected``, ``stacks``, …)."""
    return list_tier(f"Images/{target}", tier)


def list_device_tier(device: str, tier: str) -> list[ContentRef]:
    """One tier of a device's calibration library."""
    return list_tier(f"Calibration/{device}", tier)


def list_media_category(category: str) -> list[ContentRef]:
    """The media of one category (``Media/Lunar_photo``)."""
    return list_tier(f"Media/{category}", "")


def orphans(owner: str) -> list[str]:
    """Logical paths claimed by the manifest whose managed file also exists —
    the manifest wins, so the file on disk is a leftover."""
    claimed = {r.store_path for r in load(owner)}
    out: list[str] = []
    for tier in _owner_tiers(owner):
        for ref in _managed_in(tier_dir(owner, tier), owner, tier):
            if ref.store_path in claimed:
                out.append(ref.store_path)
    return out


# ── resolution ───────────────────────────────────────────────────────────────

def resolve_path(store_path: str) -> Path:
    """The real path of a store-relative logical path.

    Referenced content resolves to its origin; managed content resolves to the
    file in the store. This is the one function that replaces
    ``config.DATA_ROOT / rel`` everywhere a path is opened.
    """
    owner = owner_of(store_path)
    for ref in load(owner):
        if ref.store_path == store_path:
            return Path(ref.origin)
    return store_path_to_path(store_path)


def resolve(ref: ContentRef) -> Path:
    """The real path of a `ContentRef`."""
    return Path(ref.origin)


def mtime(ref: ContentRef) -> float:
    """When the file was written (seconds since the epoch).

    For a referenced item the origin's own mtime — the truth about the user's
    file — and the value recorded at import time when the origin is gone.
    """
    try:
        return Path(ref.origin).stat().st_mtime
    except OSError:
        return ref.mtime_ns / 1e9


def size(ref: ContentRef) -> int:
    """The file's size in bytes (the recorded one when the origin is gone)."""
    try:
        return Path(ref.origin).stat().st_size
    except OSError:
        return ref.size


def is_referenced(store_path: str) -> bool:
    """True if a logical path points outside the store."""
    return any(r.store_path == store_path for r in load(owner_of(store_path)))


def real_paths(refs: list[ContentRef]) -> list[Path]:
    """The real paths of a list of refs (the common `for ref in refs: open(...)`
    shape)."""
    return [Path(r.origin) for r in refs]


# ── health ───────────────────────────────────────────────────────────────────

def health(owner: str) -> Health:
    """Which of an owner's referenced items still have a reachable original."""
    report = Health()
    for ref in load(owner):
        (report.ok if Path(ref.origin).exists() else report.missing).append(ref)
    return report


def health_all() -> Health:
    """The health of every owner that has a manifest."""
    report = Health()
    for owner in manifest_owners():
        part = health(owner)
        report.ok.extend(part.ok)
        report.missing.extend(part.missing)
    return report


def manifest_owners() -> list[str]:
    """Every owner directory that has a manifest."""
    out: list[str] = []
    for base in (config.IMAGES_DIR, config.CALIBRATION_DIR, config.MEDIA_DIR):
        try:
            children = sorted(base.iterdir())
        except OSError:
            continue
        for child in children:
            if not child.is_dir():
                continue
            if (child / MANIFEST_NAME).is_file():
                out.append(_rel(child))
    return out


def _rel(path: Path) -> str:
    try:
        return path.relative_to(config.DATA_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


# ── naming ───────────────────────────────────────────────────────────────────

def free_name(owner: str, tier: str, name: str) -> str:
    """A logical name that collides with nothing.

    Considers both the files physically in the tier and the items the manifest
    already claims, so a referenced item and a managed file can never end up
    with the same logical path. Mirrors `ingest._free_dest`: the original name
    is preserved and only disambiguated (``x.fit`` → ``x_1.fit``).
    """
    return free_name_from({r.name for r in list_tier(owner, tier)}, name)


def free_name_from(taken: set[str], name: str) -> str:
    """A logical name that is not in `taken` — the same rule as `free_name`,
    against a set the caller already holds (so a whole import assigns its names
    in one pass instead of re-reading the manifest per file)."""
    if name not in taken:
        return name
    stem, ext = os.path.splitext(name)
    i = 1
    while f"{stem}_{i}{ext}" in taken:
        i += 1
    return f"{stem}_{i}{ext}"


# ── building refs ────────────────────────────────────────────────────────────

def make_ref(owner: str, tier: str, name: str, origin: str, *,
             size: int = 0, mtime_ns: int = 0, source: str = "",
             imported_at: str = "") -> ContentRef:
    """Build a referenced item for a file outside the store."""
    volume, rel = volume_and_rel(origin)
    return ContentRef(
        store_path=store_path_of(owner, f"{tier}/{name}" if tier else name),
        origin=str(origin),
        referenced=True,
        size=size,
        mtime_ns=mtime_ns,
        imported_at=imported_at or now_iso(),
        source=source,
        volume=volume,
        rel=rel,
    )


def volume_and_rel(path: str) -> tuple[str, str]:
    """Split a path into a *volume* and a path relative to it.

    The pair is a relocation hint: if the user's Seestar comes back at a
    different mount point, `rel` still says where inside the volume the file
    lives, so the app can offer to re-link it instead of declaring it lost.
    """
    p = Path(path)
    drive, tail = os.path.splitdrive(str(p))
    if drive:
        return drive.rstrip("\\/") or drive, tail.replace("\\", "/").lstrip("/")
    parts = str(p).split("/")
    if len(parts) >= 3 and parts[1] == "Volumes":
        volume = "/" + "/".join(parts[1:3])
        rel = "/".join(parts[3:])
        return volume, rel
    return "/", str(p).lstrip("/")


def now_iso() -> str:
    """The current UTC time, ISO-8601, seconds resolution."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── TOML helpers ─────────────────────────────────────────────────────────────

def _q(s: str) -> str:
    """Quote a TOML basic string."""
    out = str(s).replace("\\", "\\\\").replace('"', '\\"')
    out = out.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return '"' + out + '"'
