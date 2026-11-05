"""Regenerate the CP-47 schematic fixtures in this folder.

Written with litemapy and raw nbtlib only (never with eve_design's own writer), so reading them tests
eve_design's reader against independently produced files. Expected results live in manifest.json and
were written by hand from the layouts below, not computed by eve_design.

    python tests/fixtures/schematics/make_fixtures.py
"""
import gzip
from pathlib import Path

import nbtlib
import numpy as np
from litemapy import BlockState, Entity, Region, Schematic, TileEntity
from nbtlib import ByteArray, Compound, Int, IntArray, List, LongArray, Short, String

HERE = Path(__file__).parent
DV = 5023  # Minecraft 26.3


def bs(s):
    name, _, props = s.partition("[")
    return BlockState(name, **dict(kv.split("=") for kv in props.rstrip("]").split(",") if kv))


def lite(name, regions, version=7, dv=DV):
    Schematic(name=name, author="fixture", regions=regions, lm_version=version, mc_version=dv).save(str(HERE / name))


def region(x, y, z, w, h, l, blocks):
    r = Region(x, y, z, w, h, l)
    for (bx, by, bz), s in blocks.items():
        r[bx, by, bz] = bs(s)
    return r


def schem(name, body, v3=True):
    f = nbtlib.File({"Schematic": Compound(body)}, gzipped=True) if v3 else \
        nbtlib.File(body, gzipped=True, root_name="Schematic")
    f.save(str(HERE / name))


def raw_lite(name, regions_nbt, version=7, dv=DV):
    root = Compound({"Version": Int(version), "SubVersion": Int(1), "MinecraftDataVersion": Int(dv),
                     "Metadata": Compound({"Name": String(name), "Author": String("fixture")})})
    if regions_nbt is not None:
        root["Regions"] = Compound(regions_nbt)
    nbtlib.File(root, gzipped=True).save(str(HERE / name))


def raw_region(size, palette, longs, pos=(0, 0, 0)):
    return Compound({
        "Position": Compound(dict(zip("xyz", map(Int, pos)))), "Size": Compound(dict(zip("xyz", map(Int, size)))),
        "BlockStatePalette": List[Compound]([Compound({"Name": String(p)}) for p in palette]),
        "BlockStates": LongArray(longs), "TileEntities": List[Compound](), "Entities": List[Compound](),
        "PendingBlockTicks": List[Compound](), "PendingFluidTicks": List[Compound]()})


def v3(w, h, l, palette: dict, data: bytes, bes=(), offset=(0, 0, 0), dv=DV, version=3, **extra):
    return {"Version": Int(version), "DataVersion": Int(dv), "Width": Short(w), "Height": Short(h), "Length": Short(l),
            "Offset": IntArray(offset), **extra,
            "Blocks": Compound({"Palette": Compound({k: Int(v) for k, v in palette.items()}),
                                "Data": ByteArray(np.frombuffer(bytes(data), np.int8)), "BlockEntities": List[Compound](list(bes))})}


def main():
    # ---- normal ----
    lite("n01_single_block.litematic", {"r": region(0, 0, 0, 1, 1, 1, {(0, 0, 0): "minecraft:stone"})})
    mixed = {(0, 0, 0): "minecraft:oak_stairs[facing=east,half=top]", (1, 0, 0): "minecraft:stone_slab[type=top]",
             (2, 0, 0): "minecraft:oak_door[facing=south,half=lower,hinge=right]",
             (2, 1, 0): "minecraft:oak_door[facing=south,half=upper,hinge=right]",
             (3, 0, 1): "minecraft:oak_log[axis=x]", (0, 2, 1): "minecraft:glass",
             (1, 1, 1): "minecraft:spruce_slab[type=double]"}
    lite("n02_mixed_states.litematic", {"r": region(0, 0, 0, 4, 3, 2, mixed)})
    lite("n03_negative_size.litematic", {"r": region(5, 0, 5, -3, 2, -2, {
        (-2, 0, -1): "minecraft:stone", (0, 1, 0): "minecraft:oak_planks"})})
    lite("n04_two_regions.litematic", {
        "a": region(0, 0, 0, 2, 1, 1, {(0, 0, 0): "minecraft:stone", (1, 0, 0): "minecraft:stone"}),
        "b": region(3, 0, 0, 1, 2, 1, {(0, 1, 0): "minecraft:glass"})})
    r = region(0, 0, 0, 2, 1, 1, {(0, 0, 0): "minecraft:chest[facing=west]", (1, 0, 0): "minecraft:oak_sign[rotation=4]"})
    r.tile_entities.append(TileEntity(Compound({"id": String("minecraft:chest"), "Items": List[Compound](),
                                                "x": Int(0), "y": Int(0), "z": Int(0)})))
    r.tile_entities.append(TileEntity(Compound({
        "id": String("minecraft:sign"), "is_waxed": nbtlib.Byte(0), "x": Int(1), "y": Int(0), "z": Int(0),
        "front_text": Compound({"messages": List[String]([String("hi"), String(""), String(""), String("")])})})))
    lite("n05_block_entities.litematic", {"r": r})
    lite("n06_all_air.litematic", {"r": Region(0, 0, 0, 3, 3, 3)})
    lite("n07_tall_column.litematic", {"r": region(0, 0, 0, 1, 384, 1, {(0, y, 0): "minecraft:stone" for y in range(384)})})
    stairs = "minecraft:oak_stairs[facing=west,half=bottom,shape=outer_left,waterlogged=false]"
    schem("n08_v3.schem", v3(3, 2, 2, {"minecraft:stone": 0, "minecraft:air": 1, "minecraft:oak_planks": 2, stairs: 3},
                             bytes([0, 1, 2, 1, 1, 1, 3, 1, 1, 1, 1, 0]), offset=(-2, 1, 3)))
    schem("n09_v2.schem", {
        "Version": Int(2), "DataVersion": Int(DV), "Width": Short(2), "Height": Short(1), "Length": Short(1),
        "Offset": IntArray([0, 0, 0]), "PaletteMax": Int(2),
        "Palette": Compound({"minecraft:barrel[facing=up,open=false]": Int(0), "minecraft:furnace[facing=north,lit=false]": Int(1)}),
        "BlockData": ByteArray([0, 1]),
        "BlockEntities": List[Compound]([
            Compound({"Pos": IntArray([0, 0, 0]), "Id": String("minecraft:barrel"), "Items": List[Compound]()}),
            Compound({"Pos": IntArray([1, 0, 0]), "Id": String("minecraft:furnace"), "lit_time_remaining": Short(0)})])},
        v3=False)
    combos = [f"minecraft:{wood}_stairs[facing={f},half={h},shape={s},waterlogged={w}]"
              for wood in ("oak", "spruce", "birch") for f in ("north", "south", "west", "east") for h in ("top", "bottom")
              for s in ("straight", "inner_left", "inner_right", "outer_left", "outer_right") for w in ("true", "false")][:200]
    data = bytearray()
    for i in range(200):  # palette index == cell index; >=128 needs two varint bytes
        data += bytes([i]) if i < 128 else bytes([(i & 0x7F) | 0x80, i >> 7])
    schem("n10_big_palette.schem", v3(10, 2, 10, {s: i for i, s in enumerate(combos)}, bytes(data)))
    lite("n11_old_dv_v6.litematic", {"r": region(0, 0, 0, 1, 1, 1, {
        (0, 0, 0): "minecraft:oak_stairs[facing=north,half=bottom,shape=straight,waterlogged=true]"})}, version=6, dv=3955)
    lite("n12_v5_offset.litematic", {"r": region(-7, 70, 12, 2, 1, 1, {(1, 0, 0): "minecraft:cobblestone"})}, version=5)

    # ---- malformed / oversized / unsupported ----
    (HERE / "m01_not_gzip.litematic").write_bytes(b"hello, this is not a schematic\n")
    b = (HERE / "n02_mixed_states.litematic").read_bytes()
    (HERE / "m02_truncated.litematic").write_bytes(b[: len(b) // 2])
    (HERE / "m03_gzip_not_nbt.litematic").write_bytes(gzip.compress(b"\x99garbage-not-nbt"))
    (HERE / "m04_bomb.schem").write_bytes(gzip.compress(bytes(80 << 20), 9))
    raw_lite("m06_huge_dims.litematic", {"r": raw_region((10000, 10000, 10000), ["minecraft:air"], [0])})
    schem("m07_volume.schem", v3(300, 300, 300, {"minecraft:air": 0}, b"\x00"))
    raw_lite("m08_short_blockstates.litematic", {"r": raw_region((4, 4, 4), ["minecraft:air", "minecraft:stone"], [0])})
    schem("m09_palette_oob.schem", v3(2, 1, 1, {"minecraft:air": 0, "minecraft:stone": 1}, bytes([0, 5])))
    raw_lite("m10_missing_regions.litematic", None)
    raw_lite("m11_version99.litematic", {"r": raw_region((1, 1, 1), ["minecraft:air"], [0])}, version=99)
    schem("m12_schem_v1.schem", v3(1, 1, 1, {"minecraft:air": 0}, b"\x00", version=1))
    nbtlib.File({"Width": Short(1), "Height": Short(1), "Length": Short(1), "Blocks": ByteArray([1]),
                 "Data": ByteArray([0]), "Materials": String("Alpha")}, gzipped=True,
                root_name="Schematic").save(str(HERE / "m13_mcedit.schematic"))
    schem("m14_newer_dv.schem", v3(1, 1, 1, {"minecraft:stone": 0}, b"\x00", dv=9999))
    schem("m15_unknown_block.schem", v3(1, 1, 1, {"minecraft:unobtainium": 0}, b"\x00"))
    lite("m16_bad_property.litematic", {"r": region(0, 0, 0, 1, 1, 1, {(0, 0, 0): "minecraft:oak_stairs[facing=up]"})})
    r = region(0, 0, 0, 2, 1, 1, {(0, 0, 0): "minecraft:stone"})
    r.entities.append(Entity(Compound({"id": String("minecraft:armor_stand"),
                                       "Pos": List[nbtlib.Double]([nbtlib.Double(1.5), nbtlib.Double(0), nbtlib.Double(0.5)])})))
    r.block_ticks.append(Compound({"Block": String("minecraft:stone"), "Time": Int(1), "Priority": Int(0),
                                   "x": Int(0), "y": Int(0), "z": Int(0)}))
    lite("m17_entities_ticks.litematic", {"r": r})
    schem("m18_contents_spawner.schem", v3(2, 1, 1, {"minecraft:chest[facing=north]": 0, "minecraft:spawner": 1}, bytes([0, 1]), bes=[
        Compound({"Pos": IntArray([0, 0, 0]), "Id": String("minecraft:chest"), "Data": Compound({"Items": List[Compound]([
            Compound({"Slot": nbtlib.Byte(0), "id": String("minecraft:diamond"), "count": Int(1)})])})}),
        Compound({"Pos": IntArray([1, 0, 0]), "Id": String("minecraft:mob_spawner"), "Data": Compound()})]))
    lite("m19_overlap.litematic", {"a": region(0, 0, 0, 2, 1, 1, {(0, 0, 0): "minecraft:stone"}),
                                   "b": region(1, 0, 0, 2, 1, 1, {(1, 0, 0): "minecraft:dirt"})})
    schem("m20_varint_short.schem", v3(3, 1, 1, {"minecraft:air": 0, "minecraft:stone": 1}, bytes([0, 1])))
    depth = 100_000  # list-of-list nesting: a recursion bomb for naive parsers
    payload = b"\x0a\x00\x00" + b"\x09\x00\x01a" + b"\x09\x00\x00\x00\x01" * depth + b"\x00\x00\x00\x00\x00" + b"\x00"
    (HERE / "m21_deep_nesting.litematic").write_bytes(gzip.compress(payload))
    raw_lite("m22_too_many_regions.litematic",
             {f"r{i}": raw_region((1, 1, 1), ["minecraft:air"], [0], pos=(i * 2, 0, 0)) for i in range(17)})
    schem("m23_dup_palette_index.schem", v3(1, 1, 1, {"minecraft:air": 0, "minecraft:stone": 0}, b"\x00"))
    schem("m24_be_outside.schem", v3(1, 1, 1, {"minecraft:chest": 0}, b"\x00", bes=[
        Compound({"Pos": IntArray([5, 0, 0]), "Id": String("minecraft:chest"), "Data": Compound()})]))
    print("fixtures written to", HERE)


if __name__ == "__main__":
    main()
