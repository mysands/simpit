# Dummy scenery for the master

The X-Plane master (flight model / cockpit) loads the same ortho packs
as the visual slaves but must not stream hundreds of GB of atlases
through the NAS. `simpit_control/scripts/make_dummy_scenery.py` mirrors
every `zOrtho4XP_Z*` folder into a parallel tree — real DSFs and `.ter`
files, `textures/*.dds` replaced by ~300-byte uniform-color DXT1
stand-ins (X-Plane accepts any resolution), water masks copied, Ortho4XP
build intermediates skipped. The full 866×Z18 + 376×Z16 set shrinks
from hundreds of GB to roughly 23 GB, dominated by the copied DSFs.

```bat
set CUSTOM_SCENERY_FOLDER=\\YourNAS\XPlane12\Custom Scenery
python simpit_control\scripts\make_dummy_scenery.py "\\YourNAS\XPlane12\Custom Scenery DUMMY"
```

Builds are incremental: each finished folder gets a `.simpit_dummy.json`
marker and later runs only build tiles that are new in the source, so
re-run the script after adding ortho for a new region. `--verify`
recounts sources against the markers, `--prune` drops dummy folders
whose source tile is gone, `--only GLOB` restricts the run, and
`--dry-run` previews. Point the master's Custom Scenery (junction or
`CUSTOM_SCENERY_FOLDER`) at the dummy root; folder names are identical,
so `set_scenery_profile.py` manages the master's `scenery_packs.ini`
unchanged.
