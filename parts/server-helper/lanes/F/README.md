# Lane F (45L dev-account lane): the sense side

Sense side, 2026-10-22, for foundation's build of lane F (O104; plan `parts/watch/45L-LANE-F-PLAN.md` at ac22fa32). **This touches nothing live:**
- lane F is its own world (`dev-45l-f`) and its own tree (`C:\eve-lanes\F`);
- its ports are its own, on 127.0.0.1;
- nothing here goes under `C:\eve`.

## 1. Helper jar: sense 0.1.21 (recommended), or live's 0.1.20

**Recommended: 0.1.21.** Commit cbe72625, jar sha256 `fb7f3f8190806ecab6e29980527c126bb4a787045e6cfb55a9f6952e139128b8` (reproduced from a fresh clone). It is built from F's src as the plan says, and F's src must carry cbe72625's `parts/server-helper/src`.
- **Same behaviour without v9.** 0.1.21 is 0.1.20 plus CP-39. Without `animals` or `farmland` in the envelope, every rule behaves exactly as 0.1.20: the I28a guard, the v8 doorway key, rails, grants and rescue. Without those keys an animal action is decided "no policy", i.e. allowed as before.
- **It matches the other lanes.** The Shark lanes already build 0.1.21 at their next refresh from main.
- **It saves a jar swap for CP-39.** Combat's CP-39 is among F's first series (plan §4), and it needs 0.1.21 (with v9 only in its window, `LANE-CP39-SETUP.md`). So this also makes F the first lane proof of 0.1.21's load, guard and binding.

**Alternative:** live's 0.1.20, `bdb7e64a…` (bcc00d6), if the orchestrator wants exact parity with the live 45L. Then CP-39 on F needs a jar change first.

**Pin field** for `C:\eve-lanes\pins\laneF.json`:

```json
"sense": "fb7f3f8190806ecab6e29980527c126bb4a787045e6cfb55a9f6952e139128b8", "sense_src": "cbe72625"
```

The check-pins form is `eve-sense=fb7f3f81`. setup's `eve-sense jar … sha256` line must match. If it doesn't, F's src has other sense code: stop.

## 2. Files in this folder

| File | Goes to | What |
|---|---|---|
| `envelope.json` | `C:\eve-lanes\F\server-dev\config\envelope.json`, F's mind `config\envelope.json`, F's dev client `config\envelope.json` (**three identical copies**) | Lane F envelope, described below. eve_account `EveDevF` (foundation's offline dev name); `trusted_players` empty (see 2). |
| `eve-sense.json` | `C:\eve-lanes\F\server-dev\config\eve-sense.json` | `{"rcon_write_lock": "C:\\eve-lanes\\F\\LANE-IN-USE.txt"}`: the I28a guard on F's own lock (plan §1). |

**Lane F envelope:**
- It is live's v8 (`parts/foundation/ops/envelope-v8-doorway.json`, 9afa289e) made into a lane copy by eve-watch's own `envelope_laneb.py` with `EVE_LANE=E`. That gives lane D/E's shape:
  - zones home (task_only), mine (free), reflex_arena (free), build_plot (free) and combat_arena (free), at the same coordinates as lanes C/D/E;
  - the skills-pad and build-plot container tags (14);
  - limits 1000/h break and place (O45 lanes).
- `world_id` = `dev-45l-f:a1a5b81d7d18`: level-name `dev-45l-f` plus the shared lane seed's hash. This assumes F's `level-seed=20261019` like every lane (lane D: `dev-shark-d:a1a5b81d7d18`). With another seed, sense refuses the world with `world_id … does not match` (fail closed), so the envelope's world_id has to be redone.
- home center (-16,99,-48) and doorway (-16,99..100,-45) are as live: the shelter that F's lane-local park-eve builds.
- **trusted_players = `[]` (no in-game owner), while F runs offline-mode.** This is sense's decision of 2026-10-22, on foundation's report that F's runClient client can't sign in a real account. Offline names can be spoofed by any local process on the 45L (the evejobs job user included), on F's loopback server. An empty list leaves nothing to spoof. See §5 for how owner work is done instead.
- **No `animals` or `farmland`:** F starts as a plain lane. v9 goes on F only for a CP-39 window, by the lane note.
- **No overlap with anything live:** F is a separate world on a separate server, so coordinates can't collide with the live 45L world.

## 3. Before the first boot (foundation; the dev account is Hunter's)

1. **Eve's account is set:** `EveDevF` (foundation, 2026-10-22: F's offline runClient name). This file can be copied byte for byte into all three places.
   - If the account ever changes (e.g. a real account under online-mode), set it in all three copies with:

     ```powershell
     py -3.12 -c "import json,sys; p=sys.argv[1]; e=json.load(open(p,encoding='utf-8')); e['owner']['eve_account']=sys.argv[2]; open(p,'w',encoding='utf-8',newline='\n').write(json.dumps(e,indent=2,ensure_ascii=False)+'\n')" <copy> <AccountName>
     ```

   - The account must never be a trusted player. The `SET_ME` placeholder is refused by sense and by foundation's validator, so a copy left unset can't come up.
2. **Validate** each copy with `py -3.12 <F src>\parts\foundation\ops\validate_envelope.py <copy>`: VALID. All three must be byte-identical (same sha256), because sense and F's mind bind on the same hash.
3. **F's server.properties, the sense-relevant lines (plan §1):**
   - `server-ip=127.0.0.1`, `server-port=25571`
   - `enable-rcon=true`, `rcon.port=25581`, `rcon.password=<lane-local, generated>`
   - `level-name=dev-45l-f`, `level-seed=20261019` (see world_id above)
   - `online-mode`: F runs **false** (foundation: runClient like lanes B–E, offline name EveDevF; a real-account client would be a 45L software change, the orchestrator's call).
     - sense logs its online-mode warning, which is expected.
     - That is why `trusted_players` is empty (§2).
     - If F ever runs online with a real account, a trusted owner can be added back.
4. **F's server task MUST pass `-Deve.link.url=ws://127.0.0.1:8962/sense` to java** (HARD; the same pattern as setup-laneb's lane launcher).
   - sense's hub URL is this JVM property, and its **default is `ws://127.0.0.1:8951/sense`, live's mind hub on this same host** (`Link.java` line 62).
   - Without the property, F's sense would dial live's mind. Live's hub should refuse F's token, but F would still have sent to live, which the plan forbids.
   - Check it before the first boot, in the task's action or launcher: the java command line contains exactly `-Deve.link.url=ws://127.0.0.1:8962/sense`. The §7 key-hygiene selftest can assert this too.
   - The link token sense sends is F's lane-local one, from `server-dev\config\eve-link-token.txt` per foundation's lane wiring; never live's.

## 4. First boot: what sense must log (`C:\eve-lanes\F\server-dev\logs\latest.log`)

- `eve-sense 0.1.21` (or 0.1.20 if that's chosen)
- `envelope loaded: .\config\envelope.json version=8 sha256=<canonical>`
- `RCON write guard: on, world-changing RCON commands need C:\eve-lanes\F\LANE-IN-USE.txt`
- `world_id dev-45l-f:a1a5b81d7d18 matches the envelope`
- `link: hello sent to ws://127.0.0.1:8962/sense` … then `link: hello.ok`. **If the line says `8951`, stop F's server at once:** the `-Deve.link.url` property is missing (3.4) and F is talking to live's hub.

**I28a check**, with F's lock free, using F's own rcon.py on 25581 only:
- a `say` is refused with the I28a text, and one `rcon.refused` audit line appears;
- with F's lane-lock taken, the same `say` passes.

## 5. safe_tp and grants defaults for the dev account

- **safe_tp:** eve-watch's lane copy in `F\ops` refuses any teleport whose destination isn't in an envelope zone, or lacks a solid floor and air at feet and head. F's park spot, the shelter at (-15.5, 99, -47.5), is inside `home`. Every harness teleport of the dev account goes through it (O39), on F's RCON 25581 only.
- **Grants:** sense's rails default to the envelope.
  - `home` is task_only: a break or place there needs the mind's exact-cell task.grant or a region grant.
  - mine, reflex_arena, build_plot and combat_arena are free.
  - Outside every zone: refused.
  - The 1000/h break and place caps are lane-sized (live keeps 200/h).
  - Protected entities (players, villagers, golems, pets, named) and hazards (fire, lava, tnt off) are as on every lane.
  - The fall rescue (O20) is never denied.
  - No grant is standing: grants come only from F's mind, per action, and expire.
- **Owner: none in game while F is offline** (`trusted_players` empty). `/eve` is refused to everyone on F; it only ever accepts a trusted player, never the console or RCON. The dev account (Eve) can't command herself either way.
  - Owner work on F goes through **F's mind CLI** (F's `eve.ps1`: stop, resume, mode) and **F's own RCON 25581**, whose password the evejobs user can't read. Foundation's watchdog stops F's processes directly.
  - Nothing in F's first series (skills a22, builder hut5_sim, combat CP-39) needs an in-game owner. Harnesses that script an owner by name (sense's cp52/cp17 `evetest as <owner>`) are not for F while it is offline.

## 6. Rollback

Lane F is disposable: stop F's tasks, then remove `server-dev\config\eve-sense.json` (guard off) or the whole `C:\eve-lanes\F`. Nothing of sense's touches live.
