# Microsoft Rocketbox NPC source

Fix 73 replaces every active simulation citizen with a curated avatar from the
[Microsoft Rocketbox Avatar Library](https://github.com/microsoft/Microsoft-Rocketbox),
commit `0943055db6ec570bcef9f2c8b41c9e5467c808f9` (retrieved 2026-09-11).

Rocketbox is released under the MIT License; the repository's license text is
included as `LICENSE-MIT.md`. Source model and animation filenames are retained.

## Included production assets

- 20 adult civilian/profession avatars: resident, business/public-service,
  delivery, medical, construction, gardener, and chef wardrobe families.
- 19 male and 19 female motion-capture sources covering neutral locomotion,
  idle recovery, looking, phone use, documents, crouched inspection, work at
  mid/table height, presenting, listening, speaking, door work, trolley work,
  waving, clapping, and moderation.
- All 167 source material textures are included—including uniforms, helmets,
  tools, glasses, stethoscopes, opacity, normal, and specular layers—and are
  resampled to 1024 px maximum for predictable real-time memory use across the
  32-person city population.

## Runtime safety policy

The model and motion files share the authored `Bip01` production skeleton. The
runtime library copies only target-valid bone tracks, removes locomotion root
motion because the sidewalk planner owns position, removes all scale tracks,
and removes non-pelvis translation tracks. Motion-capture rotations and pelvis
weight shift remain intact. This prevents a clip from stretching limbs, moving
an avatar off its reserved station, or applying the FBX bind-pose orientation a
second time.

`scripts/rocketbox_animation_library.gd` assigns every job and interaction
phase to an explicit semantic motion. `tests/test_rocketbox_npc_quality.gd`
audits model replacement, role/wardrobe mapping, bone hierarchy, animation
targets, translation/scale safety, finite joints, pose envelopes, and segment
length invariance at three samples in every clip for both male and female rigs.
