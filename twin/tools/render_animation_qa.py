"""Render evenly spaced action frames for an anatomy and intent review."""

import argparse
import os
import sys

import bpy
from mathutils import Vector


def args_after_separator():
    return sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []


def world_bounds(objects):
    points = []
    for obj in objects:
        if obj.type != "MESH":
            continue
        points.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
    low = Vector((min(point.x for point in points), min(point.y for point in points), min(point.z for point in points)))
    high = Vector((max(point.x for point in points), max(point.y for point in points), max(point.z for point in points)))
    return low, high


def point_camera(camera, target):
    camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--display-model", help="Optional same-rig mesh for animation-only FBX clips")
    parser.add_argument("--action", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--frames", required=True, help="comma-separated source frames")
    args = parser.parse_args(args_after_separator())

    bpy.ops.wm.read_factory_settings(use_empty=True)
    input_path = os.path.abspath(args.input)
    if input_path.lower().endswith(".fbx"):
        bpy.ops.import_scene.fbx(filepath=input_path)
    else:
        bpy.ops.import_scene.gltf(filepath=input_path)
    armature = next(obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE")
    action = bpy.data.actions.get(args.action)
    if action is None:
        raise RuntimeError("Action not found: %s" % args.action)
    if args.display_model:
        source_armature = armature
        bpy.ops.import_scene.fbx(filepath=os.path.abspath(args.display_model))
        display_armature = [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE" and obj != source_armature][0]
        for obj in list(bpy.context.scene.objects):
            if obj.type == "MESH" and obj.parent == display_armature:
                obj.parent = source_armature
                for modifier in obj.modifiers:
                    if modifier.type == "ARMATURE":
                        modifier.object = source_armature
        bpy.data.objects.remove(display_armature, do_unlink=True)
        armature = source_armature
    armature.animation_data_create()
    armature.animation_data.action = action

    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH" and obj.parent == armature]
    low, high = world_bounds(meshes)
    center = (low + high) * 0.5
    height = max(high.z - low.z, 0.1)

    bpy.ops.object.camera_add(location=(center.x + height * 2.1, center.y - height * 4.0, low.z + height * 1.15))
    camera = bpy.context.object
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = height * 1.45
    point_camera(camera, Vector((center.x, center.y, low.z + height * 0.72)))
    bpy.context.scene.camera = camera

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "MATERIAL"
    scene.display.shading.show_shadows = True
    scene.display.shading.show_cavity = True
    scene.display.shading.cavity_type = "WORLD"
    scene.display.shading.curvature_ridge_factor = 1.4
    scene.world = bpy.data.worlds.new("QA_World")
    scene.world.color = (0.12, 0.14, 0.17)
    scene.render.resolution_x = 560
    scene.render.resolution_y = 560
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"

    os.makedirs(args.output_dir, exist_ok=True)
    for frame in [int(value) for value in args.frames.split(",")]:
        scene.frame_set(frame)
        scene.render.filepath = os.path.join(args.output_dir, "%s_%03d.png" % (args.action, frame))
        bpy.ops.render.render(write_still=True)
        print("QA_FRAME|%s|%s" % (args.action, frame))


if __name__ == "__main__":
    main()
