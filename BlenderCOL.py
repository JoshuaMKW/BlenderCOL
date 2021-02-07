from bpy.props import (BoolProperty,
                       FloatProperty,
                       StringProperty,
                       EnumProperty,
                       IntProperty,
                       PointerProperty,
                       )
from bpy_extras.io_utils import ExportHelper
from bpy.app.handlers import persistent
from bpy.types import PropertyGroup, Panel, Scene, Operator
from btypes.big_endian import *
from enum import Enum
import threading
import bmesh
import bpy
import random

bl_info = {
    "name": "Export COL for Super Mario Sunshine",
    "author": "Blank",
    "version": (1, 0, 1),
    "blender": (2, 80, 0),
    "location": "File > Export > Collision (.col)",
    "description": "This script allows you do export col files directly from blender. Based on Blank's obj2col",
    "warning": "Runs update function every 0.2 seconds",
    "category": "Import-Export"
}


def cleanResources():
    bpy.ops.object.mode_set(mode="OBJECT")
    
    for obj in bpy.context.scene.objects:
        obj.select_set(state=obj.type == "MESH")

    bpy.ops.object.delete()

    for block in bpy.data.meshes:
        if block.users == 0:
            bpy.data.meshes.remove(block)

    for block in bpy.data.materials:
        if block.users == 0:
            bpy.data.materials.remove(block)

    for block in bpy.data.textures:
        if block.users == 0:
            bpy.data.textures.remove(block)

    for block in bpy.data.images:
        if block.users == 0:
            bpy.data.images.remove(block)


class Header(Struct):
    vertexCount = uint32
    vertexOffset = uint32
    groupCount = uint32
    groupOffset = uint32


class vertex(Struct):
    x = float32
    y = float32
    z = float32

    def __init__(self, x, y, z):
        self.x = x
        self.y = y
        self.z = z


class Group(Struct):
    collisionType = uint16  # Properties of collision. e.g. is it water? or what?
    triangleCount = uint16

    __padding__ = Padding(1, b"\x00")  # Group flags, set them to 0 here
    # Set 0x0001 to 1 if we have colParameter values so the game doesn"t ignore it
    hasColParameter = bool8
    __padding__ = Padding(2)  # Actual padding
    vertexindexOffset = uint32
    terrainTypeOffset = uint32  # 0-18,20,21,23,24,27-31
    unknownOffset = uint32  # 0-27
    # 0,1,2,3,4,8,255,6000,7500,7800,8000,8400,9000,10000,10300,12000,14000,17000,19000,20000,21000,22000,27500,30300
    colParameterOffset = uint32


class Triangle(object):

    def __init__(self):
        self.vertexIndices = None
        self.colType = 0
        self.terrainType = 0
        self.unknown = 0
        self.colParameter = None

    @property
    def hasColParameter(self):
        return self.colParameter is not None


def pack(stream, vertices, triangles):  # pack triangles into col file
    groups = []

    for triangle in triangles:
        for group in groups:  # for each triangle add to appropriate group
            if triangle.colType != group.collisionType:
                continue  # break out of loop to next cycle
            group.triangles.append(triangle)
            break
        else:  # if no group has been found
            group = Group()  # create a new group
            group.collisionType = triangle.colType
            group.hasColParameter = triangle.hasColParameter
            group.triangles = [triangle]
            groups.append(group)  # add to list of groups

    header = Header()
    header.vertexCount = len(vertices)
    header.vertexOffset = Header.sizeof() + Group.sizeof()*len(groups)
    header.groupCount = len(groups)
    header.groupOffset = Header.sizeof()
    Header.pack(stream, header)

    stream.write(b"\x00"*Group.sizeof()*len(groups))

    for vertex in vertices:
        vertex.pack(stream, vertex)

    for group in groups:
        group.triangleCount = len(group.triangles)
        group.vertexindexOffset = stream.tell()
        for triangle in group.triangles:
            uint16.pack(stream, triangle.vertexIndices[0])
            uint16.pack(stream, triangle.vertexIndices[1])
            uint16.pack(stream, triangle.vertexIndices[2])

    for group in groups:
        group.terrainTypeOffset = stream.tell()
        for triangle in group.triangles:
            uint8.pack(stream, triangle.terrainType)

    for group in groups:
        group.unknownOffset = stream.tell()
        for triangle in group.triangles:
            uint8.pack(stream, triangle.unknown)

    for group in groups:
        if not group.hasColParameter:
            group.colParameterOffset = 0
        else:
            group.colParameterOffset = stream.tell()
            for triangle in group.triangles:
                if triangle.colParameter is not None:
                    uint16.pack(stream, triangle.colParameter)
                else:
                    uint16.pack(stream, 0)

    stream.seek(header.groupOffset)
    for group in groups:
        Group.pack(stream, group)


def unpack(stream):
    header = Header.unpack(stream)

    stream.seek(header.groupOffset)
    groups = [Group.unpack(stream) for _ in range(header.groupCount)]

    stream.seek(header.vertexOffset)
    vertices = [vertex.unpack(stream) for _ in range(header.vertexCount)]

    for group in groups:
        group.triangles = [Triangle() for _ in range(group.triangleCount)]
        for triangle in group.triangles:
            triangle.colType = group.collisionType

    for group in groups:
        stream.seek(group.vertexindexOffset)
        for triangle in group.triangles:
            triangle.vertexIndices = [uint16.unpack(stream) for _ in range(3)]

    for group in groups:
        stream.seek(group.terrainTypeOffset)
        for triangle in group.triangles:
            triangle.terrainType = uint8.unpack(stream)

    for group in groups:
        stream.seek(group.unknownOffset)
        for triangle in group.triangles:
            triangle.unknown = uint8.unpack(stream)

    for group in groups:
        if not group.hasColParameter:
            continue
        stream.seek(group.colParameterOffset)
        for triangle in group.triangles:
            triangle.colParameter = uint16.unpack(stream)

    triangles = sum((group.triangles for group in groups), [])

    return vertices, triangles


# Operator that exports the collision model into .col file
class ImportCOL(Operator, ExportHelper):
    """Import a COL file"""
    bl_idname = "import_mesh.col"
    bl_label = "Import COL"
    filter_glob: StringProperty(
        default="*.col",
        options={"HIDDEN"},
    )  # This property filters what you see in the file browser to just .col files

    check_extension = True
    filename_ext = ".col"  # This is the extension that the model will have

    def execute(self, context):
        #cleanResources()

        collisionvertexList = []  # Store a list of verticies
        triangles = []  # List of triangles, each containing indicies of verticies
        with open(self.filepath, "rb") as colStream:
            collisionvertexList, triangles = unpack(colStream)

        mesh = bpy.data.meshes.new("mesh")  # add a new mesh
        # add a new object using the mesh
        obj = bpy.data.objects.new("Collisionobj", mesh)

        scene = bpy.context.collection
        scene.objects.link(obj)  # put the object into the scene (link)
        context.view_layer.objects.active = obj  # make object active
        obj.select_set(True)  # select object

        mesh = obj.data
        bm = bmesh.new()
        bMeshvertexList = []

        for v in collisionvertexList:
            bMeshvertexList.append(bm.verts.new(
                (v.x, -v.z, v.y)))  # add a new vert

        for f in triangles:
            try:  # Try and catch to avoid exception on duplicate triangles. Dodgy...
                MyFace = bm.faces.new(
                    (bMeshvertexList[f.vertexIndices[0]], bMeshvertexList[f.vertexIndices[1]], bMeshvertexList[f.vertexIndices[2]]))
                for i in range(len(obj.data.materials)):  # Scan materials to find match
                    mat = obj.data.materials[i]
                    if f.colType == mat.colEditor.colType and f.terrainType == mat.colEditor.terrainType and f.unknown == mat.colEditor.UnknownField:  # Equate unknowns
                        colParameterAreEqual = (
                            f.colParameter == mat.colEditor.colParameterField)
                        # If the colParameter doesn"t exist we need to check for that case
                        colParameterDontExist = f.colParameter is None and mat.colEditor.hasColParameterField is False
                        if colParameterAreEqual or colParameterDontExist:
                            MyFace.material_index = i
                            break  # We assigned our material
                else:  # We did not find a material that matched
                    MaterialName = str(f.colType) + "," + str(f.terrainType) + \
                        "," + str(f.unknown) + "," + str(f.colParameter)
                    mat = bpy.data.materials.new(name=MaterialName)

                    random.seed(hash(MaterialName))  # Not actually random
                    Red = random.random()
                    Green = random.random()
                    Blue = random.random()
                    mat.diffuse_color = (Red, Green, Blue, 1.0)

                    mat.colEditor.colType = f.colType  # Set collision values
                    mat.colEditor.terrainType = f.terrainType
                    mat.colEditor.UnknownField = f.unknown

                    if f.colParameter is not None:
                        mat.colEditor.hasColParameterField = True
                        mat.colEditor.colParameterField = f.colParameter
                    else:
                        mat.colEditor.hasColParameterField = False
                        mat.colEditor.colParameterField = 0
                    # add material to our object
                    obj.data.materials.append(mat)
                    # Since material was just added it will be the last index
                    MyFace.material_index = len(obj.data.materials) - 1
            except:
                continue

        bm.to_mesh(mesh)
        mesh.update()
        bm.free()

        for area in context.screen.areas:
            if area.type != "VIEW_3D":
                continue

            area.spaces.active.clip_end = 1000000
            area.spaces.active.clip_start = 100

            for region in area.regions:
                if region.type == "WINDOW":
                    override = {"area": area, "region": region, "edit_object": bpy.context.edit_object}
                    bpy.ops.view3d.view_all(override, center=True)

        return {"FINISHED"}


# Operator that exports the collision model into .col file
class ExportCOL(Operator, ExportHelper):
    """Save a COL file"""
    bl_idname = "export_mesh.col"
    bl_label = "Export COL"
    filter_glob: StringProperty(
        default="*.col",
        options={"HIDDEN"},
    )  # This property filters what you see in the file browser to just .col files

    check_extension = True
    filename_ext = ".col"  # This is the extension that the model will have

    # To do: add material presets

    Scale: FloatProperty(
        name="Scale factor",
        description="Scale the col file by this amount",
        default=1,
    )

    # execute() is called by blender when running the operator.
    def execute(self, context):
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.transform_apply()

        vertexList = []  # Store a list of verticies
        triangles = []  # List of triangles, each containing indicies of verticies
        indexOffset = 0  # Since each object starts their vertex indicies at 0, we need to shift these indicies once we add elements to the vertex list from various objects
        
        for obj in bpy.context.scene.objects:  # for all objects
            # Set mode to be object mode
            if obj.type != "MESH":
                continue
            bm = bmesh.new()  # Define new bmesh
            # make a copy of the object we can modify freely
            depsgraph = context.evaluated_depsgraph_get()
            mesh = obj.evaluated_get(depsgraph).to_mesh()
            bm.from_mesh(mesh)  # Add the above copy into the bmesh
            # triangulate bmesh
            bmesh.ops.triangulate(
                bm, faces=bm.faces)

            for vert in bm.verts:
                # add in verts, make sure y is up
                vertexList.append(
                    vertex(vert.co.x*self.Scale, vert.co.z*self.Scale, -vert.co.y*self.Scale))

            for Face in bm.faces:
                myTriangle = Triangle()
                myTriangle.vertexIndices = [Face.verts[0].index + indexOffset, Face.verts[1].index +
                                             indexOffset, Face.verts[2].index + indexOffset]  # add three vertex indicies

                slot = obj.material_slots[Face.material_index]
                mat = slot.material.colEditor
                if mat is not None:
                    myTriangle.colType = mat.colType
                    myTriangle.terrainType = mat.terrainType
                    myTriangle.unknown = mat.UnknownField
                    if mat.hasColParameterField == True:
                        myTriangle.colParameter = mat.colParameterField
                triangles.append(myTriangle)  # add triangles
            bm.free()
            del bm
            indexOffset = len(vertexList)  # set offset

        with open(self.filepath, "wb") as colStream:
            pack(colStream, vertexList, triangles)
        # this lets blender know the operator finished successfully.
        return {"FINISHED"}


class CollisionProperties(PropertyGroup):  # This defines the UI elements
    # Here we put parameters for the UI elements and point to the Update functions
    colType: IntProperty(name="Collision type", default=0, min=0, max=65535)
    terrainType: IntProperty(name="Sound", default=0, min=0, max=255)
    # I probably should have made these an array
    UnknownField: IntProperty(name="Unknown", default=0, min=0, max=255)
    hasColParameterField: BoolProperty(name="Has Parameter", default=False)
    colParameterField: IntProperty(
        name="Parameter", default=0, min=0, max=65535)


# This panel houses the UI elements defined in the CollisionProperties
class COLLISION_PT_panel(Panel):
    bl_label = "Edit Collision Values"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "material"

    @classmethod
    def poll(cls, context):  # stolen from blender
        return check_material(context.material)

    def draw(self, context):
        mat = context.material.colEditor
        column1 = self.layout.column(align=True)
        column1.prop(mat, "colType")
        column1.prop(mat, "terrainType")
        column1.prop(mat, "UnknownField")

        column1.prop(mat, "hasColParameterField")
        column2 = self.layout.column(align=True)
        column2.prop(mat, "colParameterField")
        # must have "Has colParameter" checked
        column2.enabled = mat.hasColParameterField


def check_material(mat):
    if mat is not None:
        if mat.use_nodes:
            if mat.active_material is not None:
                return True
            return False
        return True
    return False


__classes__ = (ExportCOL,
               ImportCOL,
               COLLISION_PT_panel,
               CollisionProperties)  # list of classes to register/unregister


def register():
    from bpy.utils import register_class, unregister_class
    for cls in __classes__:
        register_class(cls)
    bpy.types.Material.colEditor = PointerProperty(
        type=CollisionProperties)  # store in the scene
    bpy.types.TOPBAR_MT_file_export.append(menu_export)  # Add to export menu
    bpy.types.TOPBAR_MT_file_import.append(menu_import)  # Add to import menu


def unregister():
    from bpy.utils import register_class, unregister_class
    for cls in reversed(__classes__):
        unregister_class(cls)
    bpy.types.TOPBAR_MT_file_export.remove(menu_export)
    bpy.types.TOPBAR_MT_file_import.remove(menu_import)


def menu_export(self, context):
    self.layout.operator(ExportCOL.bl_idname, text="Collision (.col)")


def menu_import(self, context):
    self.layout.operator(ImportCOL.bl_idname, text="Collision (.col)")


# This allows you to run the script directly from blenders text editor
# to test the addon without having to install it.
if __name__ == "__main__":
    register()
