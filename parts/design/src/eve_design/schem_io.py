"""Read/write .litematic (Litematica v5-7 read, v7 write) and Sponge .schem (v2/v3 read, v3 or v2 write).

Trust boundary: schematic files are untrusted. Malformed, oversized or unreadable files raise SchematicError.
Readable files with features we cannot build are loaded WITHOUT substitution and every such feature is
listed in the returned issues (also kept in design.meta['import_issues']) so it is visible before approval.
"""
from __future__ import annotations

import io
import math
import os
import zlib
from dataclasses import dataclass
from pathlib import Path

import nbtlib
import numpy as np
from nbtlib import ByteArray, Compound, Int, IntArray, List, Short, String

from .gamedata import GameData, StateError, format_state, parse_state
from .grid import AIR, MAX_DIM, Design

# Block entities the builder can recreate in survival (data preserved verbatim either way).
SUPPORTED_BLOCK_ENTITIES = {f"minecraft:{n}" for n in (
    "chest trapped_chest barrel furnace smoker blast_furnace bed sign hanging_sign banner lectern bell "
    "campfire brewing_stand decorated_pot chiseled_bookshelf shelf crafter hopper dispenser dropper "
    "ender_chest enchanting_table").split()}


@dataclass(frozen=True)
class Limits:
    file_bytes: int = 8 << 20          # compressed file on disk
    nbt_bytes: int = 64 << 20          # decompressed NBT
    volume: int = 2_097_152            # expanded cells (matches grid.MAX_VOLUME)
    dim: int = MAX_DIM
    regions: int = 16
    palette: int = 4096
    block_entities: int = 4096
    entities: int = 4096


LIMITS = Limits()


class SchematicError(ValueError):
    """Malformed, oversized or unsupported-at-file-level schematic. Nothing is loaded."""


class UnsupportedFeatures(ValueError):
    def __init__(self, issues: list[dict]):
        super().__init__("; ".join(f"{i['feature']}: {i['detail']}" for i in issues))
        self.issues = issues


def require_supported(issues: list[dict]) -> None:
    if issues:
        raise UnsupportedFeatures(issues)


def _issue(feature: str, detail: str, pos=None) -> dict:
    return {"feature": feature, "detail": detail, **({"pos": pos} if pos is not None else {})}


# --- NBT with bounds ------------------------------------------------------------------------------
def read_nbt(path: Path, limits: Limits = LIMITS) -> nbtlib.File:
    size = os.path.getsize(path)
    if size > limits.file_bytes:
        raise SchematicError(f"file too large: {size} bytes > {limits.file_bytes}")
    data = Path(path).read_bytes()
    if data[:2] != b"\x1f\x8b":
        raise SchematicError("not gzip-compressed NBT")
    d = zlib.decompressobj(31)
    try:
        raw = d.decompress(data, limits.nbt_bytes + 1)
    except zlib.error as e:
        raise SchematicError(f"corrupt gzip: {e}") from None
    if len(raw) > limits.nbt_bytes or d.unconsumed_tail:
        raise SchematicError(f"decompressed NBT larger than {limits.nbt_bytes} bytes")
    if not d.eof:
        raise SchematicError("truncated gzip stream")
    try:
        return nbtlib.File.parse(io.BytesIO(raw))
    except Exception as e:  # nbtlib raises assorted errors (incl. RecursionError) on hostile input
        raise SchematicError(f"malformed NBT: {type(e).__name__}") from None


def _get(c, key: str, typ, where: str):
    v = c.get(key) if isinstance(c, dict) else None
    if not isinstance(v, typ):
        raise SchematicError(f"{where}: missing or invalid {key!r}")
    return v


def _xyz(c, where: str) -> tuple[int, int, int]:
    return tuple(int(_get(c, k, (Int, nbtlib.Short, nbtlib.Byte, nbtlib.Long), where)) for k in "xyz")


def _check_size(size, limits: Limits, where: str) -> int:
    if any(s == 0 or abs(s) > limits.dim for s in size):
        raise SchematicError(f"{where}: size {size} outside 1..{limits.dim} per axis")
    vol = abs(size[0] * size[1] * size[2])
    if vol > limits.volume:
        raise SchematicError(f"{where}: volume {vol} > limit {limits.volume}")
    return vol


def _state_from_nbt(entry, where: str) -> str:
    name = str(_get(entry, "Name", String, where))
    props = entry.get("Properties", Compound())
    if not isinstance(props, Compound) or not all(isinstance(v, String) for v in props.values()):
        raise SchematicError(f"{where}: invalid Properties")
    return format_state(name, {str(k): str(v) for k, v in props.items()})


def _check_states(states: list[str], gd: GameData) -> tuple[list[str], list[dict]]:
    out, issues = [], []
    for s in states:
        try:
            out.append(gd.canonical(s))
        except StateError as e:
            issues.append(_issue("invalid_block_state", str(e)))
            out.append(s)  # kept verbatim, never substituted
    return out, issues


def _check_block_entity(be: dict, g: Design) -> list[dict]:
    p = be["pos"]
    if not g.in_bounds(p["x"], p["y"], p["z"]):
        raise SchematicError(f"block entity outside the volume at {p}")
    issues = []
    if g.get(p["x"], p["y"], p["z"]) == AIR:
        issues.append(_issue("orphan_block_entity", f"{be['id']} on air", p))
    if be["id"] not in SUPPORTED_BLOCK_ENTITIES:
        issues.append(_issue("unsupported_block_entity", str(be["id"]), p))
    if "Items:[{" in be["nbt"].replace(" ", "") or "Book:{" in be["nbt"].replace(" ", ""):
        issues.append(_issue("container_contents", f"{be['id']} has contents; the builder places the block only", p))
    return issues


# --- Litematica -----------------------------------------------------------------------------------
def _unpack_bits(longs, n: int, nbits: int) -> np.ndarray:
    """Litematica's LitematicaBitArray: entries packed LSB-first and may span two longs."""
    a = np.asarray(longs, dtype=np.int64).view(np.uint64)
    bit = np.arange(n, dtype=np.uint64) * np.uint64(nbits)
    s = (bit >> np.uint64(6)).astype(np.int64)
    o = bit & np.uint64(63)
    v = a[s] >> o
    span = (o + np.uint64(nbits)) > np.uint64(64)
    if span.any():
        v[span] |= a[s[span] + 1] << (np.uint64(64) - o[span])
    return v & np.uint64((1 << nbits) - 1)


def load_litematic(path: Path, gd: GameData, limits: Limits = LIMITS) -> tuple[Design, list[dict]]:
    root = read_nbt(path, limits)
    ver = int(_get(root, "Version", Int, "root"))
    if ver not in (5, 6, 7):
        raise SchematicError(f"unsupported Litematica schematic version {ver} (supported 5-7)")
    dv = int(_get(root, "MinecraftDataVersion", Int, "root"))
    if dv > gd.data_version:
        raise SchematicError(f"data version {dv} is newer than the pinned game ({gd.data_version})")
    regions = _get(root, "Regions", Compound, "root")
    if not 1 <= len(regions) <= limits.regions:
        raise SchematicError(f"{len(regions)} regions (allowed 1..{limits.regions})")
    meta = root.get("Metadata", Compound())

    parsed, issues = [], []
    for name, reg in regions.items():
        where = f"region {name!r}"
        if not isinstance(reg, Compound):
            raise SchematicError(f"{where}: not a compound")
        pos = _xyz(_get(reg, "Position", Compound, where), where)
        size = _xyz(_get(reg, "Size", Compound, where), where)
        vol = _check_size(size, limits, where)
        pal = _get(reg, "BlockStatePalette", List, where)
        if not 1 <= len(pal) <= limits.palette:
            raise SchematicError(f"{where}: palette length {len(pal)}")
        states = [_state_from_nbt(e, f"{where} palette") for e in pal]
        nbits = max(2, math.ceil(math.log2(len(states))))
        longs = _get(reg, "BlockStates", nbtlib.LongArray, where)
        if len(longs) != math.ceil(vol * nbits / 64):
            raise SchematicError(f"{where}: BlockStates has {len(longs)} longs, expected {math.ceil(vol * nbits / 64)}")
        idx = _unpack_bits(longs, vol, nbits)
        if idx.size and int(idx.max()) >= len(states):
            raise SchematicError(f"{where}: palette index out of range")
        lo = tuple(min(p, p + s + 1) if s < 0 else p for p, s in zip(pos, size))
        dims = tuple(abs(s) for s in size)
        tes = _get(reg, "TileEntities", List, where) if "TileEntities" in reg else []
        ents = _get(reg, "Entities", List, where) if "Entities" in reg else []
        if len(tes) > limits.block_entities or len(ents) > limits.entities:
            raise SchematicError(f"{where}: too many (block) entities")
        for e in ents:
            eid = str(e.get("id", "?")) if isinstance(e, Compound) else "?"
            issues.append(_issue("entity", f"{eid} in {where} (entities are not built)"))
        for key in ("PendingBlockTicks", "PendingFluidTicks"):
            if len(reg.get(key, [])):
                issues.append(_issue("pending_ticks", f"{len(reg[key])} {key} in {where} dropped"))
        parsed.append((name, lo, dims, states, idx.reshape(dims[1], dims[2], dims[0]), tes))

    mins = [min(p[1][i] for p in parsed) for i in range(3)]
    maxs = [max(p[1][i] + p[2][i] for p in parsed) for i in range(3)]
    total = (maxs[0] - mins[0], maxs[1] - mins[1], maxs[2] - mins[2])
    _check_size(total, limits, "enclosing box")
    g = Design(total)
    for i, a in enumerate(parsed):
        for b in parsed[i + 1:]:
            if all(a[1][k] < b[1][k] + b[2][k] and b[1][k] < a[1][k] + a[2][k] for k in range(3)):
                issues.append(_issue("region_overlap", f"regions {a[0]!r} and {b[0]!r} overlap"))
    for name, lo, dims, states, idx, tes in parsed:
        canon, bad = _check_states(states, gd)
        issues += bad
        lut = np.array([g.idx(s) for s in canon], np.uint16)
        ox, oy, oz = (lo[k] - mins[k] for k in range(3))
        view = g.blocks[oy:oy + dims[1], oz:oz + dims[2], ox:ox + dims[0]]
        mapped = lut[idx]
        np.copyto(view, mapped, where=mapped != 0)  # g.palette[0] is air: regions merge non-air over air
        for te in tes:
            if not isinstance(te, Compound):
                raise SchematicError(f"region {name!r}: bad tile entity")
            x, y, z = _xyz(te, f"region {name!r} tile entity")
            data = Compound({k: v for k, v in te.items() if k not in ("x", "y", "z", "id")})
            be_id = str(te["id"]) if "id" in te else parse_state(g.get(x + ox, y + oy, z + oz))[0]
            g.block_entities.append({"pos": {"x": x + ox, "y": y + oy, "z": z + oz}, "id": be_id, "nbt": data.snbt()})
    if dv < gd.data_version and g.block_entities:
        issues.append(_issue("old_block_entity_data", f"block entity NBT from data version {dv} is not upgraded to {gd.data_version}"))
    for be in g.block_entities:
        issues += _check_block_entity(be, g)
    g.meta["source"] = {"format": "litematic", "version": ver, "data_version": dv, "offset": mins,
                        "name": str(meta.get("Name", "")), "author": str(meta.get("Author", "")),
                        "regions": [p[0] for p in parsed]}
    g.meta["import_issues"] = issues
    return g, issues


def save_litematic(g: Design, path: Path, gd: GameData, name: str = "eve", author: str = "Eve",
                   description: str = "") -> None:
    """Single region at the stored source offset (0,0,0 for compiled designs). Final blocks only."""
    try:                                  # optional GPL-3.0 extra: the MIT package does not depend on it
        from litemapy import BlockState, Region, Schematic, TileEntity
    except ImportError as e:
        raise ImportError("writing .litematic needs the optional GPL-3.0 extra: pip install 'eve-design[litematic]' "
                          "(litemapy)") from e

    off = g.meta.get("source", {}).get("offset", [0, 0, 0])
    reg = Region(*off, *g.size)
    cache: dict[str, BlockState] = {}
    for x, y, z, s, _ in g.cells():
        if s not in cache:
            bid, props = parse_state(s)
            cache[s] = BlockState(bid, **props)
        reg[x, y, z] = cache[s]
    for be in g.block_entities:
        c = nbtlib.parse_nbt(be["nbt"])
        c["id"] = String(be["id"])
        c.update({k: Int(be["pos"][k]) for k in "xyz"})
        reg.tile_entities.append(TileEntity(c))
    Schematic(name=name, author=author, description=description, regions={name: reg},
              lm_version=7, lm_subversion=1, mc_version=gd.data_version).save(str(path))


# --- Sponge .schem --------------------------------------------------------------------------------
def _varints(data: bytes, n: int) -> np.ndarray:
    b = np.frombuffer(bytes(data), np.uint8)
    if len(b) == n and (b < 128).all():
        return b.astype(np.int64)
    out, val, shift = [], 0, 0
    for byte in b.tolist():
        val |= (byte & 0x7F) << shift
        if byte & 0x80:
            shift += 7
            if shift > 28:
                raise SchematicError("varint too long")
        else:
            out.append(val)
            val, shift = 0, 0
            if len(out) > n:
                break
    if len(out) != n or shift:
        raise SchematicError(f"block data holds {len(out)} values, expected {n}")
    return np.array(out, np.int64)


def _to_varints(idx: np.ndarray) -> bytes:
    if idx.max(initial=0) < 128:
        return idx.astype(np.uint8).tobytes()
    out = bytearray()
    for v in idx.tolist():
        while v >= 0x80:
            out.append((v & 0x7F) | 0x80)
            v >>= 7
        out.append(v)
    return bytes(out)


def load_schem(path: Path, gd: GameData, limits: Limits = LIMITS) -> tuple[Design, list[dict]]:
    root = read_nbt(path, limits)
    s = root["Schematic"] if isinstance(root.get("Schematic"), Compound) else root
    ver = int(_get(s, "Version", Int, "schematic"))
    if ver not in (2, 3):
        raise SchematicError(f"unsupported Sponge schematic version {ver} (supported 2, 3)")
    dv = int(_get(s, "DataVersion", Int, "schematic"))
    if dv > gd.data_version:
        raise SchematicError(f"data version {dv} is newer than the pinned game ({gd.data_version})")
    size = tuple(int(_get(s, k, Short, "schematic")) & 0xFFFF for k in ("Width", "Height", "Length"))
    vol = _check_size(size, limits, "schematic")
    if ver == 3:
        blocks = _get(s, "Blocks", Compound, "schematic")
        pal, data = _get(blocks, "Palette", Compound, "Blocks"), _get(blocks, "Data", ByteArray, "Blocks")
        bes = blocks.get("BlockEntities", List())
    else:
        pal, data = _get(s, "Palette", Compound, "schematic"), _get(s, "BlockData", ByteArray, "schematic")
        bes = s.get("BlockEntities", List())
    if not 1 <= len(pal) <= limits.palette:
        raise SchematicError(f"palette length {len(pal)}")
    by_index: dict[int, str] = {}
    for k, v in pal.items():
        if not isinstance(v, Int) or not 0 <= int(v) < len(pal) or int(v) in by_index:
            raise SchematicError(f"bad palette index for {k!r}")
        try:
            parse_state(str(k))
        except StateError as e:
            raise SchematicError(str(e)) from None
        by_index[int(v)] = str(k)
    states = [by_index[i] for i in range(len(pal))]
    idx = _varints(data, vol)
    if int(idx.max(initial=0)) >= len(states):
        raise SchematicError("block data index outside palette")
    canon, issues = _check_states(states, gd)
    g = Design(size)
    lut = np.array([g.idx(c) for c in canon], np.uint16)
    g.blocks = lut[idx].reshape(size[1], size[2], size[0])
    if not isinstance(bes, List) or len(bes) > limits.block_entities:
        raise SchematicError("bad or too many block entities")
    for be in bes:
        p = _get(be, "Pos", IntArray, "block entity")
        if len(p) != 3:
            raise SchematicError("block entity Pos must have 3 ints")
        be_id = str(_get(be, "Id", String, "block entity"))
        data_c = be.get("Data", Compound()) if ver == 3 else Compound({k: v for k, v in be.items() if k not in ("Pos", "Id")})
        g.block_entities.append({"pos": dict(zip("xyz", map(int, p))), "id": be_id, "nbt": data_c.snbt()})
    if len(s.get("Entities", [])) > limits.entities:
        raise SchematicError("too many entities")
    for e in s.get("Entities", []):
        issues.append(_issue("entity", f"{e.get('Id', '?')} (entities are not built)"))
    if "Biomes" in s or "BiomeData" in s:
        issues.append(_issue("biomes", "biome data ignored (designs do not change biomes)"))
    if dv < gd.data_version and g.block_entities:
        issues.append(_issue("old_block_entity_data", f"block entity NBT from data version {dv} is not upgraded to {gd.data_version}"))
    for be in g.block_entities:
        issues += _check_block_entity(be, g)
    off = s.get("Offset", IntArray([0, 0, 0]))
    g.meta["source"] = {"format": "schem", "version": ver, "data_version": dv, "offset": [int(v) for v in off]}
    g.meta["import_issues"] = issues
    return g, issues


def save_schem(g: Design, path: Path, gd: GameData, version: int = 3) -> None:
    pal = Compound({s: Int(i) for i, s in enumerate(g.palette)})
    data = ByteArray(np.frombuffer(_to_varints(g.blocks.ravel()), np.int8))
    bes = List[Compound]([
        Compound({"Pos": IntArray([be["pos"][k] for k in "xyz"]), "Id": String(be["id"]),
                  **({"Data": nbtlib.parse_nbt(be["nbt"])} if version == 3 else dict(nbtlib.parse_nbt(be["nbt"])))})
        for be in g.block_entities])
    off = IntArray(g.meta.get("source", {}).get("offset", [0, 0, 0]))
    common = {"Version": Int(version), "DataVersion": Int(gd.data_version),
              "Width": Short(g.size[0]), "Height": Short(g.size[1]), "Length": Short(g.size[2]), "Offset": off}
    if version == 3:
        body = Compound({**common, "Blocks": Compound({"Palette": pal, "Data": data, "BlockEntities": bes})})
        f = nbtlib.File({"Schematic": body}, gzipped=True)
    else:
        f = nbtlib.File({**common, "PaletteMax": Int(len(g.palette)), "Palette": pal, "BlockData": data,
                         "BlockEntities": bes}, gzipped=True, root_name="Schematic")
    f.save(str(path))


# --- dispatch ------------------------------------------------------------------------------------
def load(path: Path, gd: GameData, limits: Limits = LIMITS) -> tuple[Design, list[dict]]:
    ext = Path(path).suffix.lower()
    if ext == ".litematic":
        return load_litematic(path, gd, limits)
    if ext == ".schem":
        return load_schem(path, gd, limits)
    raise SchematicError(f"unsupported schematic format {ext!r} (supported: .litematic, .schem)")
