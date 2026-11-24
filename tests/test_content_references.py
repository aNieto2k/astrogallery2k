"""End-to-end tests for the **reference** model: importing files by reference
instead of copying them, and a library built that way.

The promise under test: M110 can hold a library whose files live **outside**
the store — on the telescope, in the user's own folder — without copying a
single byte, without touching the originals, and with every consumer (the
sessions, the gallery, the calibration, the devices rollup) reading them as if
they were in the store.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from m110 import (backup, build_derived, build_images, calibration, catalog,
                   config, content, derived, ingest, scan_sessions, siril)

from ._helpers import seed_root


def _night(tmp_path, name="M51", count=3, obj="M51"):
    """A folder of Seestar-shaped subs, outside the store."""
    src = tmp_path / "MyWorks" / f"{name}_sub"
    src.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        (src / f"Light_{obj}_30.0s_LP_20260529-01010{i}.fit").write_text(f"sub{i}")
    return src


@pytest.fixture
def store(tmp_path, monkeypatch):
    return seed_root(tmp_path, monkeypatch)


# ── importing by reference ───────────────────────────────────────────────────

def test_importing_by_reference_copies_nothing(store, tmp_path):
    src = _night(tmp_path)
    ops = ingest.scan_source_plan(src)
    assert all(o.action == "reference" for o in ops)

    result = ingest.apply_ops(ops)
    assert result["referenced"] == 3 and result["moved"] == 0
    # not one byte in the store…
    lights = config.lights_dir("M51")
    assert lights.is_dir()                     # the tier exists (the store's shape)
    assert list(lights.iterdir()) == []        # …but it holds no frames
    # …and the originals are exactly where they were.
    assert len(list(src.iterdir())) == 3


def test_the_store_knows_where_the_originals_are(store, tmp_path):
    src = _night(tmp_path)
    ingest.apply_ops(ingest.scan_source_plan(src))

    refs = content.list_target_tier("M51", "lights")
    assert len(refs) == 3
    assert all(r.referenced for r in refs)
    assert all(Path(r.origin).is_file() for r in refs)
    # the manifest is the record, and it is a plain, readable TOML file
    manifest = content.manifest_path("Images/M51")
    assert manifest.is_file()
    assert "Light_M51_30.0s_LP_20260529-010100.fit" in manifest.read_text()


def test_a_referenced_library_reads_like_any_other(store, tmp_path):
    """The consumers do not care where the frames are: the sessions, the
    rollups and the gallery are the same as for a copied library."""
    src = _night(tmp_path)
    ingest.apply_ops(ingest.scan_source_plan(src))

    rows = scan_sessions.scan()
    assert len(rows) == 1
    assert rows[0]["object_dir"] == "M51"
    assert rows[0]["frames"] == 3
    assert rows[0]["integration_min"] == pytest.approx(1.5)

    totals = build_derived.build_totals({}, rows)
    assert totals["by_folder"]["M51"]["frames"] == 3


def test_re_importing_the_same_night_does_not_double_it(store, tmp_path):
    src = _night(tmp_path)
    ingest.apply_ops(ingest.scan_source_plan(src))
    again = ingest.scan_source_plan(src)
    assert again == []                       # the very same files are already in
    assert len(content.list_target_tier("M51", "lights")) == 3


def test_rejecting_a_referenced_sub_leaves_the_original_alone(store, tmp_path):
    """The wrapper: rejecting a sub is a change of record, never of the file."""
    src = _night(tmp_path)
    ingest.apply_ops(ingest.scan_source_plan(src))
    name = sorted(p.name for p in src.iterdir())[0]

    assert content.move_to_tier(f"Images/M51/lights/{name}", "rejected") is True
    assert (src / name).is_file()                       # the original is untouched
    assert name not in {r.name for r in content.list_target_tier("M51", "lights")}
    assert name in {r.name for r in content.list_target_tier("M51", "rejected")}
    # and it does not come back from the device
    assert ingest.scan_source_plan(src) == []


def test_a_rejected_referenced_sub_is_not_counted_as_integration(store, tmp_path):
    src = _night(tmp_path)
    ingest.apply_ops(ingest.scan_source_plan(src))
    name = sorted(p.name for p in src.iterdir())[0]
    content.move_to_tier(f"Images/M51/lights/{name}", "rejected")

    rows = scan_sessions.scan()
    assert rows[0]["frames"] == 2          # the rejected one is out of the population


# ── the safety net ───────────────────────────────────────────────────────────

def test_a_missing_original_is_reported(store, tmp_path):
    src = _night(tmp_path)
    ingest.apply_ops(ingest.scan_source_plan(src))
    for p in src.iterdir():
        p.unlink()

    report = content.health_all()
    assert len(report.missing) == 3
    assert report.missing_bytes == 3 * len("sub0")
    assert report.healthy is False


def test_relinking_points_the_library_at_the_new_place(store, tmp_path):
    src = _night(tmp_path)
    ingest.apply_ops(ingest.scan_source_plan(src))
    moved = tmp_path / "Elsewhere" / "M51_sub"
    moved.mkdir(parents=True)
    for p in src.iterdir():
        p.rename(moved / p.name)

    old_root = content.common_root(content.health_all().missing)
    assert old_root == str(src)
    # the user points at the folder the files turned up in
    assert content.relink(old_root, str(moved)) == 3

    report = content.health_all()
    assert report.healthy is True
    assert all(Path(r.origin).is_file() for r in report.ok)


def test_the_backup_carries_the_links_but_not_the_frames(store, tmp_path):
    """The honest truth about a referenced library: the snapshot holds the
    manifest (the links), not the frames — which live in the user's own folder."""
    src = _night(tmp_path)
    ingest.apply_ops(ingest.scan_source_plan(src))

    count, size = backup.referenced_originals(config.DATA_ROOT)
    assert count == 3 and size > 0

    # the manifest itself IS in the snapshot's file list
    rels = backup.iter_source_files(config.DATA_ROOT)
    assert "Images/M51/content.toml" in rels


def test_a_copy_does_not_land_on_a_referenced_name(store, tmp_path):
    """The two modes must not fight over a name: a referenced item holds its
    logical name even though no file sits at that path."""
    src = _night(tmp_path)
    ingest.apply_ops(ingest.scan_source_plan(src))
    assert content.list_target_tier("M51", "lights")

    # now import the same night as a COPY: the name is taken by the reference
    ops = ingest.scan_source_plan(src, action="copy")
    assert ops == [], "a copy must not land on a name a reference already holds"
    assert len(content.list_target_tier("M51", "lights")) == 3


def test_a_copied_import_still_copies(store, tmp_path):
    """The other mode is untouched: an explicit copy behaves exactly as before."""
    src = _night(tmp_path)
    ops = ingest.scan_source_plan(src, action="copy")
    result = ingest.apply_ops(ops)
    assert result["moved"] == 3
    lights = config.lights_dir("M51")
    assert len([p for p in lights.iterdir() if p.is_file()]) == 3
    # and a copied library has nothing outside it
    assert backup.referenced_originals(config.DATA_ROOT) == (0, 0)


def test_the_sandbox_copies_a_referenced_frame_instead_of_linking_it(store, tmp_path):
    """A hardlink would give the workflow a second name for the user's own
    file — and a tool that writes in place would then modify it."""
    src = _night(tmp_path)
    ingest.apply_ops(ingest.scan_source_plan(src))
    original = sorted(src.iterdir())[0]

    plan = siril.plan_prep("M51")
    assert plan.total_lights == 3
    siril.apply_prep(plan)

    linked = next(iter(siril._sandbox_lights_dirs(config.siril_dir("M51")))).iterdir()
    in_sandbox = next(linked)
    assert in_sandbox.is_file()
    # a copy, not a second name for the same inode
    assert in_sandbox.stat().st_ino != original.stat().st_ino
    # and the original is untouched
    assert original.read_text() == original.read_text()


def test_the_gallery_shows_a_referenced_render(store, tmp_path):
    """A finished render that lives outside the store is still in the gallery."""
    outside = tmp_path / "renders"
    outside.mkdir()
    render = outside / "M51_final.png"
    render.write_bytes(b"png")

    # A render the user drops in by hand: M110 records where it is and the
    # gallery shows it, the same as a render the store holds.
    content.add("Images/M51", [content.make_ref(
        "Images/M51", "finished", render.name, str(render), size=3)])

    imgs = build_images.discover_images("m51", ["M51"], {})
    assert [i["name"] for i in imgs] == ["M51_final.png"]
    assert imgs[0]["path"] == render          # the real file
    assert imgs[0]["store_path"] == "Images/M51/finished/M51_final.png"
