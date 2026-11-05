# eve_design — Eve's offline design system

Python 3.12. Needs no running Minecraft. Produces design folders (`contracts/design-project.md`) that the mind
approves and the body builds.

## Setup
```
cd parts/design
py -3.12 -m venv .venv
.venv/Scripts/python -m pip install -e .[test]
.venv/Scripts/python tools/extract_game_data.py      # Mojang manifest -> verified 26.3 jars -> data/game/26.3/
.venv/Scripts/python -m pytest
```
`extract_game_data.py` needs Java 25 for the server's own data generator (`--reports`: legal block states,
item stack sizes). It finds Temurin 25 under `C:\Program Files\Eclipse Adoptium` or uses `EVE_JAVA`.

## Use
```
python -m eve_design compile examples/cabin.eve --out designs/cabin/v1 [--brief brief.json] [--storage storage_index.json]
python -m eve_design import  some.litematic      --out designs/imported/v1
# writes design.json, design.litematic, scaffold.json, bom.json, validation.json, previews/ (exit 3 if validation fails)
python -m eve_design generate brief.json --out designs/x/v1 --generator fake|deepseek [--critic none|fake|deepseek]
# + critique.json, candidates.json; deepseek (OpenRouter, S15, same settings as the mind) refuses until
# EVE_DEEPSEEK_ENABLED=1 + OPENROUTER_API_KEY + EVE_DEEPSEEK_MODELS + EVE_DEEPSEEK_PRICES + the mind's DB exist
python -m eve_design paste designs/x/v1 --out pack [--origin 100,70,-40]   # CP-62: data pack for a DISPOSABLE world
python -m eve_design compare designs/x/v1/previews/SE.png --shot shot_se.png --out cmp   # renderer vs screenshot aids
python examples/hut5.py                      # the hand-built 5x5 hut -> examples/out/hut5/
python examples/test3.py                     # the 3x3x3 CP-49 placement fixture -> examples/out/test3/
python tools/evidence.py CP-47               # run a checkpoint's tests and record an evidence attempt
python tools/style_metrics.py cozy_cabin taiga_small_house_1 ...   # reference metrics -> styles/<style>.references.json
```

## Modules
| module | job |
|---|---|
| `gamedata` | pinned 26.3 data: block states + defaults (data-generator `blocks.json`), item stack sizes, recipes, tags |
| `grid` | `Design`: labelled grid `[y,z,x]`, roles, rooms, `design.json`, canonical hash, rotation |
| `schem_io` | `.litematic` (read v5–7, write v7) and Sponge `.schem` (read v2/v3, write v3/v2), limits, unsupported-feature list |
| `bom` | exact final items, scaffold separate, recipe expansion with real yields/leftovers, deficit vs `storage.index`, labelled estimates |
| `lang` | the building language parser (hand-written; no eval/exec/imports) |
| `compiler` | the fixed command table → labelled grid, with time/write/size budgets, the role-priority collision rule and detailing rules |
| `validate` | grid-derived checks: site fit, walkable, enclosed, lit, functional, stable, fire-safe, buildable, affordable, limits |
| `scaffold` | greedy temporary pillars until every block is within reach |
| `preview` | isometric, top and room plan-cut renders from the game's own block models and textures |
| `style` | vanilla structure loader and exact reference metrics for style cards |
| `models` | generator/critic interfaces: deterministic fakes + DeepSeek adapters (disabled until key/prices; spend shared with the mind) |
| `generate` | brief → concepts → program → validated design with bounded repairs (CP-61) and the ≤3-round critique loop (CP-64) |
| `buildorder` | placement order (layers, roles, attachments, scaffold) → `build_order.json` |
| `progress` | reference arithmetic for the mind's construction-progress record (remaining items per placed cells) |
| `world` | CP-62 prep: strict-setblock data pack + camera teleports for a disposable world; renderer-vs-screenshot aids |

### Screenshots for CP-62 (foundation, disposable world only)
Copy the pack into `<world>/datapacks/`, `/reload`, then `/function eve:clear_<id>` and `/function eve:paste_<id>`.
For each view run `/function eve:cam_<id>_se` (…`_ne`, `_nw`, `_sw`, `_top`) as the screenshot player, with F1 (HUD
hidden), FOV 30, noon, clear weather, render distance ≥ 8; save as `shot_<view>.png` and run `compare` per view.
`camera.json` lists the exact positions/angles (true isometric: pitch 35.26°, yaws 135/45/−45/−135).

Building a design in the world: see `BUILD_SCHEMATIC.md` (for body's `build_schematic`).

## design.json encoding (for readers in other parts)
- `blocks`: base64 of little-endian uint16 palette indices; `roles`: base64 of uint8; both x-fastest, then z, then y.
- `palette[0]` is `minecraft:air`; other entries are **full** canonical states (every property, sorted), e.g.
  `minecraft:oak_stairs[facing=north,half=bottom,shape=straight,waterlogged=false]`.
- roles: 0 none 1 foundation 2 wall 3 window 4 door 5 roof 6 floor 7 stair 8 furniture 9 light 10 ground 11 support 12 trim.
- `hash` = sha256 of `json.dumps({"blocks","origin":[x,y,z],"palette","roles","size":[x,y,z]}, sort_keys=True,
  separators=(",",":"))` where blocks/roles are the base64 strings above and the palette is canonical (air first,
  rest sorted, unused dropped). `Design.from_json` re-checks it.

## Language (vocabulary)
`Build(style, seed)`, `foundation(w,d,h,block)`, `walls(h,frame,fill,frame_every,storeys,setback_every,setback,part)`,
`floor(block,part)`, `door(side,at,block,part)`, `windows(side,count,w,h,sill,block,storey,part)`,
`room(type,furnish,storey,part)`, `stairs(storey,side,at,block,part)`, `roof(kind,ridge,overhang,block,part)` (`gable`,
`flat`), `chimney(side,block,part)`, `porch(side,depth,block,part)`,
`wing(name,side,length,depth,at,h,storeys,frame,fill,frame_every)`, `detail(rule,on,rows)`. Omitted blocks come from the
style card in `styles/`. Grammar and limits: `src/eve_design/lang.py`; command table and collision rule:
`src/eve_design/compiler.py`. Examples: `examples/*.eve`.
