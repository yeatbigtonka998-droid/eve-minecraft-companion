r"""Asking a real player (Hunter) to do one manual step in a 45L harness: shared by cp13/cp17/cp18_45l.py (copy it next to
them in C:\eve\ops). Before prompting it checks every target block exists; it marks each target with a glowing outline
(a block_display entity, so no block changes and no diff ever sees it) and a floating label, puffs end_rod particles
there every few seconds, offers a click-to-fill /tp to a spot 3 blocks away facing the first target, and repeats the
one-line instruction every minute. The step stays open until done() sees the change in the world (default 30 min).
"<name> done" in chat is acknowledged and re-checked; if the world does not show the change yet, the player is told
what is still missing. All markers are removed when the step ends.
"""
import json
import re
import time

TAG = "cp_hunter_marker"


def ask(rcon, root, name, text, targets, done, missing=None, minutes=30, stand=None):
    """targets: [((x, y, z), "BREAK THIS" | "PLACE ON THIS" | ..., expected_block_or_None)]; done() -> bool;
    missing() -> str (what is not done yet, for the chat reply); stand: where the /tp puts the player (feet), default
    3 blocks south of the first target at its height. Returns done()."""
    R = lambda c: rcon.command(root, c)  # noqa: E731
    for pos, label, block in targets:
        if block and not R(f"execute if block {pos[0]} {pos[1]} {pos[2]} {block}").startswith("Test passed"):
            R(f"tellraw {name} " + json.dumps({"text": f"[test] setup problem: no {block} at {pos[0]} {pos[1]} {pos[2]}; tell the operator", "color": "red"}))
            print(f"ask: target {pos} is not {block}; step not asked", flush=True)
            return False
    first = targets[0][0]
    stand = stand or (first[0] + 0.5, first[1], first[2] + 3.5)              # 3 blocks south, facing it
    tp = f"/tp @s {stand[0]} {stand[1]} {stand[2]} facing {first[0] + 0.5} {first[1] + 0.5} {first[2] + 0.5}"
    for pos, label, _ in targets:
        x, y, z = pos
        R(f"summon minecraft:block_display {x - 0.01} {y - 0.01} {z - 0.01} {{Tags:[\"{TAG}\"],Glowing:1b,glow_color_override:16766720,"
          f"block_state:{{Name:\"minecraft:glass\"}},transformation:{{left_rotation:[0f,0f,0f,1f],right_rotation:[0f,0f,0f,1f],"
          f"translation:[0f,0f,0f],scale:[1.02f,1.02f,1.02f]}}}}")
        R(f"summon minecraft:text_display {x + 0.5} {y + 1.6} {z + 0.5} {{Tags:[\"{TAG}\"],billboard:\"center\","
          f"text:{json.dumps({'text': label, 'color': 'gold', 'bold': True})}}}")
    msg = [{"text": text + " ", "color": "yellow"},
           {"text": "[click: teleport there]", "color": "aqua", "underlined": True,
            "click_event": {"action": "suggest_command", "command": tp}},
           {"text": "  Say \"done\" in chat when finished.", "color": "gray"}]
    log = root / "logs" / "latest.log"
    offset = log.stat().st_size
    end, last_msg, last_fx = time.time() + 60 * minutes, 0, 0
    ok = False
    try:
        while time.time() < end:
            if done():
                ok = True
                break
            now = time.time()
            if now - last_fx > 3:
                for pos, _, _ in targets:
                    R(f"particle minecraft:end_rod {pos[0] + 0.5} {pos[1] + 0.5} {pos[2] + 0.5} 0.4 0.4 0.4 0.02 25 force {name}")
                last_fx = now
            if now - last_msg > 60:
                R(f"tellraw {name} " + json.dumps(msg))
                last_msg = now
            with log.open("rb") as f:
                f.seek(offset)
                new = f.read().decode("utf-8", "replace")
            offset += len(new.encode("utf-8"))
            if re.search(rf"<{re.escape(name)}> done\b", new, re.I):
                if done():
                    ok = True
                    break
                R(f"tellraw {name} " + json.dumps({"text": "[test] Thanks! I don't see it yet: " + (missing() if missing else text), "color": "gold"}))
            time.sleep(2)
        R(f"tellraw {name} " + json.dumps({"text": "[test] Got it, thank you!" if ok else "[test] Time is up for this step, thanks anyway.",
                                           "color": "green" if ok else "gray"}))
        return ok
    finally:
        R(f"kill @e[tag={TAG}]")
