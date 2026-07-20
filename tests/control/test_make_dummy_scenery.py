"""Tests for simpit_control.scripts.make_dummy_scenery."""
import json
import shutil
import struct

import pytest

from simpit_control.scripts import make_dummy_scenery as mds
from simpit_control.scripts import set_scenery_profile as ssp

# ── Fake source scenery ──────────────────────────────────────────────────
DSF_BYTES = b"XPLNDSF-fake-mesh-bytes" * 100
TER_TEXT = "A\n800\nTERRAIN\nBASE_TEX_NOWRAP ../textures/24528_13872_BI16.dds\n"
PNG_BYTES = b"\x89PNG-fake-mask"


def make_source_tile(root, name, dds_names, with_junk=True):
    """Build one fake zOrtho4XP folder with the real on-disk layout."""
    tile = root / name
    nav = tile / "Earth nav data" / "+40-080"
    nav.mkdir(parents=True)
    (nav / "+42-073.dsf").write_bytes(DSF_BYTES)
    (nav / "+42-073.dsf.bak").write_bytes(b"stale")
    terrain = tile / "terrain"
    terrain.mkdir()
    for i, dds in enumerate(dds_names):
        (terrain / f"terrain_{i}.ter").write_text(
            TER_TEXT.replace("24528_13872_BI16.dds", dds), encoding="utf-8")
    textures = tile / "textures"
    textures.mkdir()
    for dds in dds_names:
        (textures / dds).write_bytes(b"\x00" * 4096)  # stand-in "big" atlas
    (textures / "24528_13872_BI16_sea_mask.png").write_bytes(PNG_BYTES)
    if with_junk:
        # Ortho4XP build intermediates X-Plane never reads.
        for ext in (".alt", ".mesh", ".node", ".poly", ".apt", ".cfg"):
            (tile / f"{name}{ext}").write_bytes(b"junk" * 1000)
    return tile


@pytest.fixture
def scenery(tmp_path):
    src = tmp_path / "Custom Scenery"
    src.mkdir()
    make_source_tile(src, "zOrtho4XP_Z18_+42-073",
                     ["24528_13872_BI16.dds", "98304_43056_Arc18.dds"])
    make_source_tile(src, "zOrtho4XP_Z16_+42-073", ["6132_3468_BI16.dds"])
    (src / "Global Airports").mkdir()  # non-ortho pack: must be ignored
    dest = tmp_path / "Custom Scenery DUMMY"
    return src, dest


@pytest.fixture(autouse=True)
def _env(scenery, monkeypatch):
    monkeypatch.setenv("CUSTOM_SCENERY_FOLDER", str(scenery[0]))
    monkeypatch.delenv("DUMMY_SCENERY_FOLDER", raising=False)


# ── Dummy DDS format ─────────────────────────────────────────────────────
class TestDummyDds:
    def test_header_fields(self):
        data = mds.make_dummy_dds()
        assert data[:4] == b"DDS "
        (size, flags, height, width, linear, _depth,
         mips) = struct.unpack_from("<7I", data, 4)
        assert size == 124
        assert height == width == 16
        assert mips == 5
        assert linear == 128            # 4x4 DXT1 blocks * 8 bytes
        assert flags & 0x20000 and flags & 0x80000
        pf_size, pf_flags = struct.unpack_from("<2I", data, 76)
        assert pf_size == 32 and pf_flags == 0x4
        assert data[84:88] == b"DXT1"
        caps = struct.unpack_from("<I", data, 108)[0]
        assert caps & 0x1000 and caps & 0x400000

    def test_full_mip_chain_length(self):
        # 16/8/4/2/1 px mips: 16+4+1+1+1 blocks of 8 bytes + 128 header.
        assert len(mds.make_dummy_dds()) == 128 + 23 * 8

    def test_uniform_color_blocks(self):
        data = mds.make_dummy_dds(color=(255, 0, 0))
        c0, c1 = struct.unpack_from("<HH", data, 128)
        assert c0 == c1 == 0xF800       # pure red in RGB565
        assert data[132:136] == b"\x00" * 4

    def test_color_parse(self):
        assert mds.parse_color("6e6a52") == (0x6E, 0x6A, 0x52)
        with pytest.raises(SystemExit):
            mds.parse_color("not-hex")


# ── Building ─────────────────────────────────────────────────────────────
class TestBuild:
    def test_mirrors_structure_with_dummy_textures(self, scenery):
        src, dest = scenery
        assert mds.main([str(dest)]) == 0
        tile = dest / "zOrtho4XP_Z18_+42-073"
        dsf = tile / "Earth nav data" / "+40-080" / "+42-073.dsf"
        assert dsf.read_bytes() == DSF_BYTES
        assert not dsf.with_suffix(".dsf.bak").exists()
        ters = sorted((tile / "terrain").glob("*.ter"))
        assert len(ters) == 2
        src_ter = src / "zOrtho4XP_Z18_+42-073" / "terrain" / ters[0].name
        assert ters[0].read_bytes() == src_ter.read_bytes()
        for dds in ("24528_13872_BI16.dds", "98304_43056_Arc18.dds"):
            out = tile / "textures" / dds
            assert out.read_bytes() == mds.make_dummy_dds()
        mask = tile / "textures" / "24528_13872_BI16_sea_mask.png"
        assert mask.read_bytes() == PNG_BYTES

    def test_skips_build_intermediates_and_other_packs(self, scenery):
        src, dest = scenery
        mds.main([str(dest)])
        tile = dest / "zOrtho4XP_Z18_+42-073"
        left = {p.name for p in tile.iterdir()}
        assert left == {"Earth nav data", "terrain", "textures",
                        mds.MARKER_NAME}
        assert not (dest / "Global Airports").exists()

    def test_source_never_modified(self, scenery):
        src, dest = scenery
        before = sorted(p.relative_to(src) for p in src.rglob("*"))
        sizes = {p: (src / p).stat().st_size
                 for p in before if (src / p).is_file()}
        mds.main([str(dest)])
        after = sorted(p.relative_to(src) for p in src.rglob("*"))
        assert after == before
        assert all((src / p).stat().st_size == n for p, n in sizes.items())

    def test_marker_written(self, scenery):
        src, dest = scenery
        mds.main([str(dest)])
        marker = json.loads(
            (dest / "zOrtho4XP_Z18_+42-073" / mds.MARKER_NAME).read_text())
        assert marker["schema"] == mds.MARKER_SCHEMA
        assert marker["counts"] == {"dsf": 1, "ter": 2, "dds": 2, "extra": 1}

    def test_dry_run_writes_nothing(self, scenery):
        src, dest = scenery
        mds.main([str(dest), "--dry-run"])
        assert not dest.exists()

    def test_only_filter(self, scenery):
        src, dest = scenery
        mds.main([str(dest), "--only", "zOrtho4XP_Z16_*"])
        assert (dest / "zOrtho4XP_Z16_+42-073").is_dir()
        assert not (dest / "zOrtho4XP_Z18_+42-073").exists()


# ── Incremental behavior ─────────────────────────────────────────────────
class TestIncremental:
    def test_rerun_skips_marked_folders(self, scenery, capsys):
        src, dest = scenery
        mds.main([str(dest)])
        capsys.readouterr()
        mds.main([str(dest)])
        out = capsys.readouterr().out
        assert "to build: 0" in out

    def test_new_source_folder_is_picked_up(self, scenery, capsys):
        src, dest = scenery
        mds.main([str(dest)])
        make_source_tile(src, "zOrtho4XP_Z18_+34-119",
                         ["103824_44416_Arc18.dds"])
        capsys.readouterr()
        mds.main([str(dest)])
        out = capsys.readouterr().out
        assert "to build: 1" in out
        assert (dest / "zOrtho4XP_Z18_+34-119" / "textures"
                / "103824_44416_Arc18.dds").exists()

    def test_missing_marker_forces_rebuild(self, scenery):
        src, dest = scenery
        mds.main([str(dest)])
        tile = dest / "zOrtho4XP_Z18_+42-073"
        (tile / mds.MARKER_NAME).unlink()
        (tile / "textures" / "24528_13872_BI16.dds").unlink()
        mds.main([str(dest)])
        assert (tile / "textures" / "24528_13872_BI16.dds").exists()
        assert (tile / mds.MARKER_NAME).exists()

    def test_verify_rebuilds_on_source_change(self, scenery, capsys):
        src, dest = scenery
        mds.main([str(dest)])
        extra = (src / "zOrtho4XP_Z16_+42-073" / "textures"
                 / "6132_3470_BI16.dds")
        extra.write_bytes(b"\x00" * 4096)
        capsys.readouterr()
        mds.main([str(dest)])          # without --verify: trusted, skipped
        assert "to build: 0" in capsys.readouterr().out
        mds.main([str(dest), "--verify"])
        assert "to build: 1" in capsys.readouterr().out
        assert (dest / "zOrtho4XP_Z16_+42-073" / "textures"
                / "6132_3470_BI16.dds").read_bytes() == mds.make_dummy_dds()

    def test_prune_removes_only_marked_orphans(self, scenery, tmp_path):
        src, dest = scenery
        mds.main([str(dest)])
        shutil.rmtree(src / "zOrtho4XP_Z16_+42-073")
        foreign = dest / "zOrtho4XP_Z18_+99-999"
        foreign.mkdir()                # no marker: not ours, must survive
        mds.main([str(dest), "--prune"])
        assert not (dest / "zOrtho4XP_Z16_+42-073").exists()
        assert (dest / "zOrtho4XP_Z18_+42-073").is_dir()
        assert foreign.is_dir()


# ── Safety ───────────────────────────────────────────────────────────────
class TestSafety:
    def test_refuses_dest_equal_to_source(self, scenery):
        src, dest = scenery
        with pytest.raises(SystemExit):
            mds.main([str(src)])

    def test_refuses_dest_inside_source(self, scenery):
        src, dest = scenery
        with pytest.raises(SystemExit):
            mds.main([str(src / "DUMMY")])

    def test_refuses_source_inside_dest(self, scenery, tmp_path):
        with pytest.raises(SystemExit):
            mds.main([str(tmp_path)])

    def test_no_dest_fails(self, scenery, monkeypatch):
        with pytest.raises(SystemExit):
            mds.main([])


# ── set_scenery_profile compatibility ────────────────────────────────────
class TestSceneryProfileCompat:
    def test_dummy_folders_match_profile_scanner(self, scenery):
        """The ini generator must see dummy folders exactly like real ones."""
        src, dest = scenery
        mds.main([str(dest)])
        tiles = ssp.scan_tiles(dest)
        assert tiles == {(42, -73): {"Z16", "Z18"}}

    def test_folder_name_patterns_agree(self):
        for name in ("zOrtho4XP_Z18_+42-073", "zOrtho4XP_Z16_+34-119",
                     "zOrtho4XP_Z18_-04+142"):
            assert bool(mds.FOLDER_RE.match(name))
            assert bool(ssp.FOLDER_RE.match(name))
        for name in ("zOrtho4XP_Z18_+42-073 copy", "yOrtho_Z18_+42-073"):
            assert not mds.FOLDER_RE.match(name)
            assert not ssp.FOLDER_RE.match(name)
