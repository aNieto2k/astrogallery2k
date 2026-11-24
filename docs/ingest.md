# Ingest & the library layout

← [Back to the guide](README.md)

## Bringing captures in

Open the **Import** page. Pick a source — a mounted Seestar, a DwarfLab Dwarf 3, an
SD card, or any folder of FITS — and M110 scans it (without changing anything) and
shows a **grouped preview**: one row per object, with the kind of file (light subs,
device stack, finished render, media), the file count, size, and where each group
will land. Device layouts are recognized automatically — a Seestar export, a Dwarf 3
`DWARF_RAW_*` / `STARTRAILS_*` session, an M110 store, or a loose pile of FITS.

- Check or uncheck groups; retarget a group to a different object if the name or
  pointing looks off.
- Nothing is written until you click **Import**. From a telescope, M110
  **references** your files (see below); from a staging folder it can move.
- The **Store** column says what happens to each group: **reference** (the
  files stay where they are and M110 keeps a link — nothing is copied) or
  **copy** (they are copied into your library). Change it per row; the default
  is in **Settings → Import → Keep imported files**.
- Files M110 can't confidently classify go to a **holding area** (the `Inbox/`
  folder) where you assign them by hand — it never silently drops or misfiles a
  frame. The holding panel **suggests** an object and kind from each file's header
  (double-click a row to inspect the header + a thumbnail), and you can **select many
  rows and assign them to one object/kind at once**.

M110 reads the FITS headers to identify each frame (object, date, exposure, filter,
pointing), so it works across devices regardless of their filename conventions.

### Referencing instead of copying

By default M110 does **not** copy your captures into the library. It keeps a
**link** to the file where it already is — on the telescope, on the card, in
your own folder — and records what that file *is* in a small `content.toml`
beside the object it belongs to.

What that buys you:

- **Half the disk.** A night of subs is copied zero times, and a backup of
  your library no longer carries a second copy of every frame.
- **Your originals are yours.** M110 opens them read-only and never renames,
  moves or rewrites them. Rejecting a sub from processing is a change of
  record, not a move of the file.
- **The library stays light** and the heavy stuff stays where your backup
  already reaches it.

What it costs, so there are no surprises:

- **The library is no longer self-contained.** If you delete the original or
  unplug the volume it lives on, M110 cannot open it. The Import page says
  how many are missing, and **Reconnect…** re-points the links when the
  folder turns up somewhere else.
- **Backups carry the links, not the frames.** The Backup pane says how many
  files (and how much) that is. Backing up the originals is your job — the
  folder they sit in is the thing to copy.

Files you already imported keep living in the library exactly as they are;
this only changes what a *new* import does.

### The Seestar's dark library

Recent Seestar firmware keeps its master darks in a `darklibrary/` folder beside
`MyWorks/`. Importing from the Seestar brings those in too, filed under
`Calibration/<telescope>/darks/` (the telescope is named from the subs on the same
device, e.g. `S50_15e7e390`). Reconnecting copies only darks you don't have yet.

M110 **never applies** these darks to the Seestar app's subs. The app already
subtracts a dark from every sub before saving it, so subtracting one again would do
it twice. (Frames from the same Seestar driven by N.I.N.A. are not dark-subtracted;
see below.) They're kept as a
record of what the telescope did:

- The **Dark** column in an object's Sessions table (and in the full session log)
  shows the dark the telescope most likely used and how far the sensor drifted from
  it during the capture — "24.9 °C · Δ10.5 ⚠". A ⚠ means more than 3 °C: hot pixels
  in the drifted subs are over- or under-subtracted. `m110-stack --max-dark-gap 3`
  leaves those subs out of a stack.
- The **[Devices](devices.md)** page (last on the rail) shows each telescope's dark
  library, the captures each dark served, and its sensor health (dark level and
  hot-pixel count against temperature). Darks are named by when the telescope made
  them and at what temperature ("Oct 15 21:52 · 13.9 °C") in both tables; select a
  capture to highlight its darks, and right-click a dark to reveal its file.

Which dark a capture got is *inferred* — the Seestar doesn't record it — from
when each dark was made (its filename time) and the sensor temperature. A link says
**confirmed** when the dark was made for that capture minutes before it started at
the same temperature, and **inferred** otherwise.

### N.I.N.A. and PINS

Frames captured with **N.I.N.A.** or **PINS** (N.I.N.A. on a Raspberry Pi), for
example a Seestar driven over Alpaca, import from any folder layout: N.I.N.A.'s file
pattern is your choice, so M110 reads the headers rather than the folder names. The
preview labels these rows **N.I.N.A.**

- **Mosaic panels** named by N.I.N.A.'s framing assistant ("M 45 Panel 1",
  "M 45 Panel 2" and so on, in any language N.I.N.A. uses) import together as one
  **M45_mosaic** dataset.
- **Darks, flats and biases** shot without a target go to your telescope's library,
  `Calibration/<telescope>/`. A Seestar keeps one name whether you captured with its
  app or with N.I.N.A.: M110 reads its serial from the camera name.
- **Darks are applied.** Unlike the Seestar app, N.I.N.A. saves frames without
  subtracting a dark. M110 groups N.I.N.A.'s single dark frames by exposure, gain,
  night and temperature. When you stack, `m110-stack` builds a master from the
  matching group first, and Siril prep links the group for the Naztronomy script.
  An object can hold both N.I.N.A. and Seestar-app captures (N.I.N.A.'s "IR" filter
  on a Seestar is the app's "IRCUT"). Siril prep gives each kind its own job folder.
  For example, `siril/IRCUT/` holds the app subs with no darks, and
  `siril/IRCUT_30s/` holds the N.I.N.A. subs with theirs, so the Naztronomy script
  calibrates each correctly. Stack each folder and combine the stacks in Siril, or
  point `m110-stack` at either folder: it stacks both together in one pass, each with
  its own calibration.
- A target named for a field, such as "NGC 2024 IC 434", gets a dropdown in the
  preview offering the catalog objects in its name. If you accept **Remember
  alias?**, M110 remembers that target name, not the folder.

## Where things land — the library layout

Your data folder is organized around **two views of the same collection**:

```
<data folder>/            (default ~/Documents/M110)
  Objects/<catalog id>/       one folder per catalog object (M101, C20, …)
    journal.md                  your notes for that object
  Images/<target>/            one folder per capture target
    content.toml                 the links to your originals (when referenced)
    lights/                     raw light subs — the immutable originals
    stacks/                     Siril stacks you've imported back
    device-stacks/              the telescope's own in-app stacks
    finished/                   your finished, hand-processed renders
    working_files/              processing by-products (starless, crop, stretch…)
    previews/                   per-sub JPG previews (only if you opt in)
    siril/                      the processing sandbox (see Processing)
    (darks/ flats/ biases/)     calibration frames, if you have them
  Calibration/<telescope>/     master darks/flats/biases that belong to a telescope
  Media/<category>_photo|_video/   lunar / planetary / scenery / startrails
  Plans/                      saved session field guides (see Session planning)
  Inbox/                      holding area for files awaiting manual assignment
  .m110_internal_data/        hidden app state — catalog, sessions, thumbnails
```

**Why two axes?** `Objects/` is the *catalog* view (one entry per sky object).
`Images/` is the *capture* view (one folder per thing you pointed at). They're kept
separate because a single capture can cover several catalog objects — e.g. one
`M81 M82` session feeds both M81 and M82 — so objects and capture targets are a
many-to-many relationship, not a 1:1 mapping.

**The tiers inside `Images/<target>/` matter.** M110 keeps raw subs, stacks, and
finished renders in separate folders on purpose:

- `lights/` holds **only** raw light subs. M110 treats these as immutable — it reads
  them but never writes into this folder. This is what everything else is derived
  from, so keep it clean. (With *reference* mode the folder can be empty: the
  subs are still listed by M110, they just live in your own folder.)
- `stacks/` and `device-stacks/` hold integrated stacks (yours from Siril, and the
  telescope's own in-app stacks — Seestar, Dwarf or Draco — respectively).
  (Older versions called it `seestar-stacks/`; M110 renames it for you.)
- `finished/` holds your deliverables — the images you've hand-processed and are
  proud of.

You can browse all of this in your file manager, but the easiest way to see it is the
**Library** page and each object's **detail pane**, which groups the gallery into
"Finished" and "Working files."

Next: **[The library & object metadata →](library.md)**
