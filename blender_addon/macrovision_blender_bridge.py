"""Blender add-on bridge for the MacroVision Blender MCP specialist.

Install this file through Edit > Preferences > Add-ons > Install from Disk,
enable it, then use the 3D View sidebar's "Agent" tab to start the bridge.
"""
from __future__ import annotations

bl_info = {
    "name": "MacroVision Blender Bridge",
    "author": "MacroVision",
    "version": (0, 1, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Agent",
    "description": "Explicit scene, rigging, and animation commands over localhost",
    "category": "Interface",
}

import json
import queue
import socket
import threading
import traceback
from pathlib import Path

import bpy
from mathutils import Quaternion, Vector

_requests = queue.Queue()
_server = None
_thread = None


def _object(name, expected=None):
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ValueError(f"Object {name!r} does not exist.")
    if expected and obj.type != expected:
        raise ValueError(f"Object {name!r} is {obj.type}, expected {expected}.")
    return obj


def _select_only(*objects):
    bpy.ops.object.mode_set(mode="OBJECT") if bpy.context.object and bpy.context.object.mode != "OBJECT" else None
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[-1]


def _dispatch(command, p):
    if command == "status":
        return {
            "version": ".".join(map(str, bpy.app.version)),
            "file": bpy.data.filepath or None,
            "scene": bpy.context.scene.name,
        }
    if command == "scene_summary":
        return {
            "frame_range": [bpy.context.scene.frame_start, bpy.context.scene.frame_end],
            "objects": [
                {
                    "name": o.name, "type": o.type,
                    "location": list(o.location), "rotation_mode": o.rotation_mode,
                    "parent": o.parent.name if o.parent else None,
                    "bones": [b.name for b in o.data.bones] if o.type == "ARMATURE" else None,
                    "action": o.animation_data.action.name
                    if o.animation_data and o.animation_data.action else None,
                }
                for o in bpy.context.scene.objects
            ],
        }
    if command == "inspect_mesh":
        obj = _object(p["object_name"], "MESH")
        corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
        mins = [min(c[i] for c in corners) for i in range(3)]
        maxs = [max(c[i] for c in corners) for i in range(3)]
        return {
            "name": obj.name,
            "vertices": len(obj.data.vertices),
            "edges": len(obj.data.edges),
            "polygons": len(obj.data.polygons),
            "dimensions": list(obj.dimensions),
            "world_bounds": {"min": mins, "max": maxs},
            "materials": [slot.material.name if slot.material else None for slot in obj.material_slots],
            "modifiers": [{"name": m.name, "type": m.type} for m in obj.modifiers],
            "vertex_groups": [{"name": g.name, "index": g.index} for g in obj.vertex_groups],
            "armatures": [m.object.name for m in obj.modifiers if m.type == "ARMATURE" and m.object],
            "parent": obj.parent.name if obj.parent else None,
        }
    if command == "render_mesh_views":
        objects = [_object(name, "MESH") for name in p["object_names"]]
        directory = Path(p["directory"]).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        corners = [obj.matrix_world @ Vector(corner) for obj in objects for corner in obj.bound_box]
        mins = Vector([min(c[i] for c in corners) for i in range(3)])
        maxs = Vector([max(c[i] for c in corners) for i in range(3)])
        center = (mins + maxs) / 2
        radius = max((maxs - mins).length / 2, 0.5)
        distance = radius * 3.2
        scene = bpy.context.scene
        old_camera, old_path = scene.camera, scene.render.filepath
        old_x, old_y, old_pct = scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage
        camera_data = bpy.data.cameras.new("AgentInspectionCamera")
        camera = bpy.data.objects.new("AgentInspectionCamera", camera_data)
        scene.collection.objects.link(camera)
        camera_data.lens = 55
        light_data = bpy.data.lights.new("AgentInspectionLight", "AREA")
        light_data.energy, light_data.shape, light_data.size = 1200, "DISK", radius * 2
        light = bpy.data.objects.new("AgentInspectionLight", light_data)
        scene.collection.objects.link(light)
        light.location = center + Vector((distance, -distance, distance))
        light.rotation_euler = (center - light.location).to_track_quat("-Z", "Y").to_euler()
        scene.camera = camera
        scene.render.resolution_x = scene.render.resolution_y = int(p["resolution"])
        scene.render.resolution_percentage = 100
        views = {
            "front": Vector((0, -distance, 0)),
            "side": Vector((distance, 0, 0)),
            "perspective": Vector((distance * 0.75, -distance, distance * 0.65)),
        }
        paths = []
        try:
            for name, offset in views.items():
                camera.location = center + offset
                camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
                path = directory / f"{name}.png"
                scene.render.filepath = str(path)
                scene.render.image_settings.file_format = "PNG"
                bpy.ops.render.render(write_still=True)
                paths.append(str(path))
        finally:
            scene.camera, scene.render.filepath = old_camera, old_path
            scene.render.resolution_x, scene.render.resolution_y = old_x, old_y
            scene.render.resolution_percentage = old_pct
            bpy.data.objects.remove(camera, do_unlink=True)
            bpy.data.cameras.remove(camera_data)
            bpy.data.objects.remove(light, do_unlink=True)
            bpy.data.lights.remove(light_data)
        return {"paths": paths, "center": list(center), "radius": radius}
    if command == "import_asset":
        path = Path(p["path"]).resolve()
        before = set(bpy.data.objects)
        ext = path.suffix.lower()
        if ext == ".blend":
            with bpy.data.libraries.load(str(path), link=False) as (source, target):
                target.objects = source.objects
            for obj in target.objects:
                if obj:
                    bpy.context.collection.objects.link(obj)
        elif ext == ".fbx":
            bpy.ops.wm.fbx_import(filepath=str(path))
        elif ext == ".obj":
            bpy.ops.wm.obj_import(filepath=str(path))
        elif ext in {".glb", ".gltf"}:
            bpy.ops.import_scene.gltf(filepath=str(path))
        else:
            raise ValueError(f"Unsupported asset extension: {ext}")
        return {"imported": [o.name for o in set(bpy.data.objects) - before]}
    if command == "create_armature":
        armature = bpy.data.armatures.new(p["name"])
        obj = bpy.data.objects.new(p["name"], armature)
        bpy.context.collection.objects.link(obj)
        obj.location = Vector(p["location"])
        return {"armature": obj.name}
    if command == "add_bone":
        obj = _object(p["armature"], "ARMATURE")
        _select_only(obj)
        bpy.ops.object.mode_set(mode="EDIT")
        existing = obj.data.edit_bones.get(p["name"])
        bone = existing or obj.data.edit_bones.new(p["name"])
        bone.head, bone.tail = Vector(p["head"]), Vector(p["tail"])
        if p.get("parent"):
            parent = obj.data.edit_bones.get(p["parent"])
            if not parent:
                raise ValueError(f"Parent bone {p['parent']!r} does not exist.")
            bone.parent, bone.use_connect = parent, bool(p.get("connected"))
        bpy.ops.object.mode_set(mode="OBJECT")
        return {"bone": bone.name, "armature": obj.name}
    if command == "parent_mesh":
        mesh, armature = _object(p["mesh"], "MESH"), _object(p["armature"], "ARMATURE")
        _select_only(mesh, armature)
        if p.get("automatic_weights", True):
            bpy.ops.object.parent_set(type="ARMATURE_AUTO")
        else:
            mesh.parent = armature
            modifier = mesh.modifiers.new("Armature", "ARMATURE")
            modifier.object = armature
        return {"mesh": mesh.name, "armature": armature.name}
    if command == "create_action":
        obj = _object(p["armature"], "ARMATURE")
        obj.animation_data_create()
        action = bpy.data.actions.new(p["name"])
        obj.animation_data.action = action
        bpy.context.scene.frame_start, bpy.context.scene.frame_end = p["frame_start"], p["frame_end"]
        return {"action": action.name, "armature": obj.name}
    if command == "keyframe_bone":
        obj = _object(p["armature"], "ARMATURE")
        bone = obj.pose.bones.get(p["bone"])
        if not bone:
            raise ValueError(f"Pose bone {p['bone']!r} does not exist.")
        frame = int(p["frame"])
        if p.get("location") is not None:
            bone.location = Vector(p["location"])
            bone.keyframe_insert("location", frame=frame)
        if p.get("rotation_quaternion") is not None:
            bone.rotation_mode = "QUATERNION"
            bone.rotation_quaternion = Quaternion(p["rotation_quaternion"])
            bone.keyframe_insert("rotation_quaternion", frame=frame)
        if p.get("scale") is not None:
            bone.scale = Vector(p["scale"])
            bone.keyframe_insert("scale", frame=frame)
        return {"bone": bone.name, "frame": frame}
    if command == "set_interpolation":
        obj = _object(p["armature"], "ARMATURE")
        action = obj.animation_data.action if obj.animation_data else None
        if not action:
            raise ValueError("Armature has no active action.")
        count = 0
        for curve in action.fcurves:
            for point in curve.keyframe_points:
                point.interpolation = p["interpolation"]
                count += 1
        return {"action": action.name, "keyframes_updated": count}
    if command == "render_preview":
        path = str(Path(p["filepath"]).resolve())
        bpy.context.scene.render.filepath = path
        bpy.context.scene.render.image_settings.file_format = "PNG"
        bpy.ops.render.render(write_still=True)
        return {"path": path}
    if command == "save_project":
        path = str(Path(p["path"]).resolve())
        bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
        return {"path": path}
    raise ValueError(f"Unknown command: {command}")


def _pump():
    try:
        while True:
            item = _requests.get_nowait()
            try:
                item["result"] = {"ok": True, "result": _dispatch(item["command"], item["params"])}
            except Exception as exc:
                item["result"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                traceback.print_exc()
            finally:
                item["done"].set()
    except queue.Empty:
        pass
    return 0.05


def _serve(sock):
    while _server is sock:
        try:
            conn, _ = sock.accept()
            with conn, conn.makefile("rwb") as stream:
                request = json.loads(stream.readline())
                item = {**request, "done": threading.Event(), "result": None}
                _requests.put(item)
                item["done"].wait(125)
                response = item["result"] or {"ok": False, "error": "Blender command timed out."}
                stream.write((json.dumps(response) + "\n").encode())
                stream.flush()
        except OSError:
            break


class AGENT_OT_start_bridge(bpy.types.Operator):
    bl_idname = "agent.start_bridge"
    bl_label = "Start bridge"

    def execute(self, context):
        global _server, _thread
        if _server:
            return {"FINISHED"}
        _server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        _server.bind(("127.0.0.1", 9877))
        _server.listen(4)
        _thread = threading.Thread(target=_serve, args=(_server,), daemon=True)
        _thread.start()
        bpy.app.timers.register(_pump, persistent=True)
        return {"FINISHED"}


class AGENT_OT_stop_bridge(bpy.types.Operator):
    bl_idname = "agent.stop_bridge"
    bl_label = "Stop bridge"

    def execute(self, context):
        global _server
        server, _server = _server, None
        if server:
            server.close()
        return {"FINISHED"}


class AGENT_PT_bridge(bpy.types.Panel):
    bl_label = "Blender Agent"
    bl_idname = "AGENT_PT_bridge"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Agent"

    def draw(self, context):
        self.layout.label(text="Running on 127.0.0.1:9877" if _server else "Stopped")
        self.layout.operator("agent.stop_bridge" if _server else "agent.start_bridge")


_CLASSES = (AGENT_OT_start_bridge, AGENT_OT_stop_bridge, AGENT_PT_bridge)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    global _server
    if _server:
        _server.close()
        _server = None
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
