"""Tests for the content-reference layer — the store's index of the files that
live outside it (`m110/content.py`).

The model under test: content is either **managed** (the bytes are in the store
and the file on disk is the record) or **referenced** (the bytes are outside
and a manifest entry is the only link). Both must be enumerable through one
API, and a referenced item must resolve to the real file.
"""
from __future__ import annotations

import tomllib

import pytest

from m110 import config, content

from ._helpers import seed_root


@pytest.fixture
def store(tmp_path, monkeypatch):
    return seed_root(tmp_path, monkeypatch)


def _outside(tmp_path, name="Light_M81_001.fit", data=b"x" * 32):
    """A file that lives OUTSIDE the store — the user's original."""
    src = tmp_path / "elsewhere" / name
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(data)
    return src


# ── layout rules ─────────────────────────────────────────────────────────────

def test_owner_and_tier_of_a_target_path():
    assert content.owner_of("Images/M81/lights/x.fit") == "Images/M81"
    assert content.tier_of("Images/M81/lights/x.fit") == "lights"
    assert content.tier_of("Images/M81/siril/process/x.fit") == ""


def test_owner_and_tier_of_a_calibration_path():
    assert content.owner_of("Calibration/S50/darks/y.fit") == "Calibration/S50"
    assert content.tier_of("Calibration/S50/darks/y.fit") == "darks"


def test_owner_and_tier_of_a_media_path():
    assert content.owner_of("Media/Lunar_photo/z.mp4") == "Media/Lunar_photo"
    assert content.tier_of("Media/Lunar_photo/z.mp4") == ""


def test_store_path_of_builds_the_logical_path():
    assert content.store_path_of("Images/M81", "lights/x.fit") == "Images/M81/lights/x.fit"
    assert content.store_path_of("Media/Lunar_photo", "z.mp4") == "Media/Lunar_photo/z.mp4"


# ── the manifest ─────────────────────────────────────────────────────────────

def test_manifest_round_trip(store, tmp_path):
    src = _outside(tmp_path)
    ref = content.make_ref("Images/M81", "lights", src.name, str(src),
                           size=32, source="seestar")
    content.add("Images/M81", [ref])

    loaded = content.load("Images/M81")
    assert len(loaded) == 1
    assert loaded[0].store_path == "Images/M81/lights/Light_M81_001.fit"
    assert loaded[0].origin == str(src)
    assert loaded[0].size == 32
    assert loaded[0].source == "seestar"
    assert loaded[0].referenced is True


def test_manifest_lives_next_to_the_content_it_describes(store):
    content.add("Images/M81", [])
    assert content.manifest_path("Images/M81") == config.DATA_ROOT / "Images/M81/content.toml"


def test_manifest_is_valid_toml_and_readable_by_tomllib(store, tmp_path):
    """The manifest is a documented, hand-editable file — it must parse with a
    plain TOML reader, not just with our own."""
    src = _outside(tmp_path)
    content.add("Images/M81", [content.make_ref("Images/M81", "lights", src.name, str(src))])
    with open(content.manifest_path("Images/M81"), "rb") as f:
        data = tomllib.load(f)
    assert data["schema"] == content.SCHEMA
    assert data["items"][0]["origin"] == str(src)


def test_missing_manifest_is_not_an_error(store):
    assert content.load("Images/Nothing") == []


def test_malformed_manifest_does_not_take_the_library_down(store):
    """A broken manifest costs the user the links, not the app."""
    path = content.manifest_path("Images/M81")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("this is not = = toml", encoding="utf-8")
    assert content.load("Images/M81") == []


def test_write_leaves_no_part_file(store, tmp_path):
    src = _outside(tmp_path)
    content.add("Images/M81", [content.make_ref("Images/M81", "lights", src.name, str(src))])
    leftovers = [p.name for p in content.manifest_path("Images/M81").parent.iterdir()
                 if p.name.endswith(".part")]
    assert leftovers == []


def test_add_replaces_an_item_with_the_same_logical_path(store, tmp_path):
    src = _outside(tmp_path)
    one = content.make_ref("Images/M81", "lights", src.name, str(src), size=1)
    two = content.make_ref("Images/M81", "lights", src.name, str(src), size=2)
    content.add("Images/M81", [one])
    content.add("Images/M81", [two])
    loaded = content.load("Images/M81")
    assert len(loaded) == 1
    assert loaded[0].size == 2


def test_remove_drops_one_item(store, tmp_path):
    src = _outside(tmp_path)
    ref = content.make_ref("Images/M81", "lights", src.name, str(src))
    content.add("Images/M81", [ref])
    assert content.remove("Images/M81", ref.store_path) is True
    assert content.remove("Images/M81", ref.store_path) is False
    assert content.load("Images/M81") == []


# ── the wrapper: state changes without touching the original ────────────────

def test_move_changes_the_tier_without_touching_the_file(store, tmp_path):
    """Rejecting a sub is a change of record, not a move of bytes — the user's
    original is not renamed, moved or deleted."""
    src = _outside(tmp_path)
    ref = content.make_ref("Images/M81", "lights", src.name, str(src))
    content.add("Images/M81", [ref])

    assert content.move("Images/M81", ref.store_path, "rejected") is True

    assert content.load("Images/M81")[0].store_path == "Images/M81/rejected/Light_M81_001.fit"
    assert src.exists(), "the original must still be exactly where it was"
    assert content.list_target_tier("M81", "rejected")[0].origin == str(src)
    assert content.list_target_tier("M81", "lights") == []


def test_update_changes_a_field(store, tmp_path):
    src = _outside(tmp_path)
    ref = content.make_ref("Images/M81", "lights", src.name, str(src))
    content.add("Images/M81", [ref])
    assert content.update("Images/M81", ref.store_path, source="dwarf") is True
    assert content.load("Images/M81")[0].source == "dwarf"


# ── enumeration ──────────────────────────────────────────────────────────────

def test_list_tier_returns_managed_files(store):
    lights = config.lights_dir("M81")
    lights.mkdir(parents=True)
    (lights / "Light_1.fit").write_text("x")
    refs = content.list_target_tier("M81", "lights")
    assert len(refs) == 1
    assert refs[0].referenced is False
    assert refs[0].origin == str(lights / "Light_1.fit")


def test_list_tier_returns_managed_and_referenced_together(store, tmp_path):
    lights = config.lights_dir("M81")
    lights.mkdir(parents=True)
    (lights / "Light_1.fit").write_text("x")
    src = _outside(tmp_path)
    content.add("Images/M81", [content.make_ref("Images/M81", "lights", src.name, str(src))])

    refs = content.list_target_tier("M81", "lights")
    assert len(refs) == 2
    assert {r.referenced for r in refs} == {True, False}


def test_the_manifest_is_not_itself_content(store):
    """`content.toml` lives in the target directory but is not library content."""
    config.lights_dir("M81").mkdir(parents=True)
    content.add("Images/M81", [])
    refs = content.list_target("M81")
    assert all(r.name != content.MANIFEST_NAME for r in refs)


def test_a_leftover_part_file_is_not_content(store):
    lights = config.lights_dir("M81")
    lights.mkdir(parents=True)
    (lights / "Light_1.fit.part").write_text("x")
    assert content.list_target_tier("M81", "lights") == []


def test_apple_double_sidecars_are_not_content(store):
    """A store on a share without xattr support grows `._<name>` beside every
    file (#161) — those are never content."""
    lights = config.lights_dir("M81")
    lights.mkdir(parents=True)
    (lights / "._Light_1.fit").write_text("x")
    assert content.list_target_tier("M81", "lights") == []


def test_list_owner_covers_every_tier(store, tmp_path):
    config.lights_dir("M81").mkdir(parents=True)
    (config.lights_dir("M81") / "Light_1.fit").write_text("x")
    src = _outside(tmp_path)
    content.add("Images/M81", [content.make_ref("Images/M81", "stacks", src.name, str(src))])
    names = {r.store_path for r in content.list_target("M81")}
    assert names == {"Images/M81/lights/Light_1.fit",
                     "Images/M81/stacks/Light_M81_001.fit"}


def test_media_category_is_walked_recursively(store, tmp_path):
    cat = config.media_dir("Lunar_photo")
    (cat / "2026").mkdir(parents=True)
    (cat / "2026" / "clip.mp4").write_text("x")
    refs = content.list_media_category("Lunar_photo")
    assert [r.store_path for r in refs] == ["Media/Lunar_photo/2026/clip.mp4"]


# ── resolution ───────────────────────────────────────────────────────────────

def test_resolve_path_returns_the_origin_of_a_referenced_item(store, tmp_path):
    src = _outside(tmp_path)
    content.add("Images/M81", [content.make_ref("Images/M81", "lights", src.name, str(src))])
    assert content.resolve_path("Images/M81/lights/Light_M81_001.fit") == src


def test_resolve_path_returns_the_store_path_of_managed_content(store):
    lights = config.lights_dir("M81")
    lights.mkdir(parents=True)
    (lights / "Light_1.fit").write_text("x")
    assert content.resolve_path("Images/M81/lights/Light_1.fit") == lights / "Light_1.fit"


def test_resolve_path_of_an_unknown_path_is_the_store_path(store):
    assert content.resolve_path("Images/M81/lights/nope.fit") == \
        config.DATA_ROOT / "Images/M81/lights/nope.fit"


def test_is_referenced(store, tmp_path):
    src = _outside(tmp_path)
    content.add("Images/M81", [content.make_ref("Images/M81", "lights", src.name, str(src))])
    assert content.is_referenced("Images/M81/lights/Light_M81_001.fit") is True
    assert content.is_referenced("Images/M81/lights/other.fit") is False


# ── naming ───────────────────────────────────────────────────────────────────

def test_free_name_avoids_managed_files(store):
    lights = config.lights_dir("M81")
    lights.mkdir(parents=True)
    (lights / "Light_1.fit").write_text("x")
    assert content.free_name("Images/M81", "lights", "Light_1.fit") == "Light_1_1.fit"


def test_free_name_avoids_referenced_items(store, tmp_path):
    src = _outside(tmp_path)
    content.add("Images/M81", [content.make_ref("Images/M81", "lights", src.name, str(src))])
    assert content.free_name("Images/M81", "lights", src.name) == "Light_M81_001_1.fit"


def test_free_name_keeps_a_free_name(store):
    assert content.free_name("Images/M81", "lights", "Light_1.fit") == "Light_1.fit"


# ── health ───────────────────────────────────────────────────────────────────

def test_health_reports_a_missing_origin(store, tmp_path):
    src = _outside(tmp_path)
    content.add("Images/M81", [content.make_ref("Images/M81", "lights", src.name, str(src))])
    src.unlink()
    report = content.health("Images/M81")
    assert report.healthy is False
    assert len(report.missing) == 1
    assert report.missing[0].name == "Light_M81_001.fit"


def test_health_is_ok_when_the_origin_is_there(store, tmp_path):
    src = _outside(tmp_path)
    content.add("Images/M81", [content.make_ref("Images/M81", "lights", src.name, str(src))])
    assert content.health("Images/M81").healthy is True


def test_orphans_report_a_file_the_manifest_also_claims(store, tmp_path):
    lights = config.lights_dir("M81")
    lights.mkdir(parents=True)
    (lights / "Light_1.fit").write_text("x")
    src = _outside(tmp_path, name="Light_1.fit")
    content.add("Images/M81", [content.make_ref("Images/M81", "lights", src.name, str(src))])
    assert content.orphans("Images/M81") == ["Images/M81/lights/Light_1.fit"]


# ── relocation hints ─────────────────────────────────────────────────────────

def test_volume_and_rel_of_a_posix_path(tmp_path):
    volume, rel = content.volume_and_rel("/Volumes/SEESTAR/MyWorks/M81/x.fit")
    assert volume == "/Volumes/SEESTAR"
    assert rel == "MyWorks/M81/x.fit"


def test_volume_and_rel_of_a_plain_posix_path():
    volume, rel = content.volume_and_rel("/home/user/x.fit")
    assert volume == "/"
    assert rel == "home/user/x.fit"


def test_make_ref_records_the_relocation_hint(store, tmp_path):
    src = _outside(tmp_path)
    ref = content.make_ref("Images/M81", "lights", src.name, str(src))
    assert ref.volume and ref.rel
