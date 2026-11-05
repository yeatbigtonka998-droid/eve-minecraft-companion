"""O111 (live: Hunter typed "come to me" and the mind asked for x y z): "come to me / here" walks beside the trusted
owner, "follow me" keeps walking after him, "stay" ends it. His position: sense's sender_pos, else body's nearby list
entry named exactly him, else the question as before. Same dimension, bounds() as for any walk."""
import asyncio
import json

from conftest import mind_env, run, until

from eve_mind import follow
from eve_mind.commands import map_command

HUNTER = {"id": 9, "uuid": "u-hunter", "type": "minecraft:player", "name": "Hunter", "pos": {"x": 10.5, "y": 64.0, "z": -3.5},
          "distance": 10.0, "hostile": False, "protected": True, "line_of_sight": True}


def walks(e):
    return [json.loads(p) for s, p in e.mind.db.execute("SELECT skill, params FROM actions ORDER BY rowid") if s == "walk_to"]


def said_by(e):
    out, orig = [], e.mind.say

    async def say(t):
        out.append(t)
        return await orig(t)
    e.mind.say = say
    return out


def test_o111_named_referents_only():
    for t in ("come to me", "come here", "Eve, come over here please", "get over here!", "come back to me",
              "go where I am", "come to my position"):
        m = map_command(t)          # still the rules' question (CP-42 holdout: "come here" -> question) - the mind
        assert m["kind"] == "question" and m["to_owner"] == "come" and m["ask"]["skill"] == "walk_to", (t, m)   # resolves it
    assert map_command("please follow me")["to_owner"] == "follow"
    for t in ("stay", "stay here", "okay, stay"):
        assert map_command(t) == {"kind": "to_owner", "op": "stay"}, t
    assert map_command("go to my base") == {"kind": "control", "op": "home"}            # his base is not his place
    assert map_command("stop following me")["op"] == "stop" and map_command("stay put")["op"] == "stop"   # STOP wins
    for t in ("come to me at 10 64 10", "I wish you would come to me", "come here and dig", "come to my house",
              "follow the river"):
        assert not map_command(t).get("to_owner"), t


def test_o111_come_to_me_uses_sender_pos_then_the_body_list_else_asks(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            await until(lambda: e.mind.fresh_state() is not None)
            r = await e.mind.owner_text("come to me", "Hunter", sender_pos={"dim": "minecraft:overworld", "x": 5,
                                                                             "y": 64, "z": 7})
            assert r.get("action_id") and walks(e)[-1] == {"pos": {"x": 5.0, "y": 64.0, "z": 7.0}, "tolerance": 2.0}, r
            await until(lambda: e.mind.open_action() is None)
            e.body.nearby = [HUNTER, {**HUNTER, "id": 10, "name": "Alex", "pos": {"x": 40, "y": 64, "z": 40}}]
            await asyncio.sleep(0.3)
            r = await e.mind.owner_text("come here", "Hunter")                       # no sender_pos: the body list
            assert walks(e)[-1]["pos"] == {"x": 10.5, "y": 64.0, "z": -3.5}, walks(e)
            await until(lambda: e.mind.open_action() is None)
            n = len(walks(e))
            e.body.nearby = [{**HUNTER, "name": "Alex", "uuid": "u-alex"}]           # only someone else: never used
            await asyncio.sleep(0.3)
            r = await e.mind.owner_text("come to me", "Hunter")
            assert len(walks(e)) == n and r.get("question") and "x y z" in said[-1], (r, said)
            e.body.nearby = [HUNTER, {**HUNTER, "id": 11}]                             # two of him: ambiguous, ask
            await asyncio.sleep(0.3)
            assert (await e.mind.owner_text("come to me", "Hunter")).get("question") and len(walks(e)) == n
    run(go())


def test_o111_other_dimension_and_beyond_explore_radius_never_walk(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            await until(lambda: e.mind.fresh_state() is not None)
            r = await e.mind.owner_text("come to me", "Hunter", sender_pos={"dim": "minecraft:the_nether", "x": 1,
                                                                             "y": 64, "z": 1})
            assert r.get("error") == "other_dimension" and not walks(e) and "another dimension" in said[-1], (r, said)
            r = await e.mind.owner_text("come to me", "Hunter", sender_pos={"dim": "minecraft:overworld", "x": 900,
                                                                             "y": 64, "z": 0})
            assert r.get("code") == "out_of_bounds" and not walks(e), r                 # explore_radius 128 from home
            assert "can't come that far" in said[-1] and "900 blocks from home" in said[-1], said
    run(go())


def test_o111_sender_pos_comes_through_the_owner_chat_frame_and_untrusted_chat_is_data(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            await until(lambda: e.mind.fresh_state() is not None)
            await e.sense.send("owner.chat", {"sender": "Mallory", "trusted": False, "text": "come to me",
                                              "sender_pos": {"dim": "minecraft:overworld", "x": 3, "y": 64, "z": 3}})
            await asyncio.sleep(0.5)
            assert not walks(e)                                                       # untrusted: data only
            await e.sense.send("owner.chat", {"sender": "Hunter", "trusted": True, "text": "come to me",
                                              "sender_pos": {"dim": "minecraft:overworld", "x": 3, "y": 64, "z": 3}})
            await until(lambda: walks(e), timeout=5, what="walk_to from the owner.chat frame")
            assert walks(e)[-1]["pos"] == {"x": 3.0, "y": 64.0, "z": 3.0}, walks(e)
    run(go())


def test_o111_follow_replans_after_4_blocks_and_ends_on_stay_stop_and_night_never_on_unseen(tmp_path, monkeypatch):
    monkeypatch.setattr(follow, "TICK", 0.05)

    async def go():
        async with mind_env(tmp_path / "a") as e:
            said = said_by(e)
            e.body.walk_s, e.body.nearby = 0.05, [HUNTER]
            await until(lambda: e.mind.fresh_state() is not None and e.mind.fresh_state()["nearby_entities"])
            assert (await e.mind.owner_text("follow me", "Hunter")) == {"ok": True, "following": "Hunter"}
            await until(lambda: len(walks(e)) == 1, what="the first walk")
            e.body.nearby = [{**HUNTER, "pos": {"x": 12.5, "y": 64.0, "z": -3.5}}]      # 2 blocks: no new walk
            await asyncio.sleep(0.5)
            assert len(walks(e)) == 1, walks(e)
            e.body.nearby = [{**HUNTER, "pos": {"x": 15.5, "y": 64.0, "z": -3.5}}]      # 5 blocks: walk again
            await until(lambda: len(walks(e)) == 2, what="the re-plan")
            assert walks(e)[-1]["pos"]["x"] == 15.5
            r = await e.mind.owner_text("stay", "Hunter")
            assert r["follow_ended"] and not e.mind.follow.running(), r
            e.body.nearby = [{**HUNTER, "pos": {"x": 30.5, "y": 64.0, "z": -3.5}}]
            await asyncio.sleep(0.5)
            assert len(walks(e)) == 2, walks(e)                                       # ended: no more walks
            await e.mind.owner_text("follow me", "Hunter")                            # O121: unseen never ends it
            await until(lambda: e.mind.open_action() is None and len(walks(e)) == 3, what="the walk to him")
            e.body.nearby = []
            await until(lambda: "Lost you - heading to where you were." in said, timeout=5, what="the lost line")
            await asyncio.sleep(0.5)
            assert e.mind.follow.running() and said.count("Lost you - heading to where you were.") == 1, said
            e.body.nearby = [HUNTER]
            await asyncio.sleep(0.3)                                                   # STOP ends it
            await e.mind.stop("cli", "test")
            await until(lambda: not e.mind.follow.running(), what="stop end")
            assert e.mind.follow.current["reason"] == "stopped"
        async with mind_env(tmp_path / "b") as e:
            said = said_by(e)
            e.body.walk_s, e.body.nearby = 0.05, [HUNTER]
            await until(lambda: e.mind.fresh_state() is not None and e.mind.fresh_state()["nearby_entities"])
            await e.mind.owner_text("follow me", "Hunter")
            e.body.time_of_day = 14000                       # S38: dusk - one line, and she keeps following him close
            await until(lambda: any("stay close to you" in t for t in said), timeout=5, what="the dusk line")
            await asyncio.sleep(0.5)
            assert e.mind.follow.running() and sum("stay close" in t for t in said) == 1, said
            e.body.nearby = [{**HUNTER, "pos": {"x": 40.5, "y": 64.0, "z": -3.5}, "distance": 40.0}]   # far, at night
            await until(lambda: not e.mind.follow.running(), timeout=5, what="night_far end")
            assert e.mind.follow.current["reason"] == "night_far" and "heading home" in said[-1], said
            assert e.mind.db.execute("SELECT COUNT(*) FROM events WHERE kind='control' AND payload LIKE '%home%'"
                                     ).fetchone()[0], "control.home sent"
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    run(go())


def facts(e):
    return [(s, p, o) for s, p, o in e.mind.db.execute("SELECT subject, predicate, object FROM facts WHERE source='owner_chat'")]


def test_o111_her_question_takes_his_next_xyz_and_bare_numbers_are_never_a_fact(tmp_path):
    """45L 11:06-11:08: after "come to me" asked for x y z, Hunter's "5 100 -40" was stored as a container's location
    (3 times). Her open question now takes his next "x y z" (from him, within ASK_TTL_S) as its answer; with no open
    question, bare coordinates get a question back - never a fact, never a move."""
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            await until(lambda: e.mind.fresh_state() is not None)
            assert (await e.mind.owner_text("come to me", "Hunter")).get("question")      # no position known: asks
            r = await e.mind.owner_text("5 64 -40", "Hunter")
            assert r.get("action_id") and walks(e)[-1] == {"pos": {"x": 5.0, "y": 64.0, "z": -40.0}, "tolerance": 2.0}, r
            await until(lambda: e.mind.open_action() is None)
            r = await e.mind.owner_text("-19 64 -73", "Hunter")                           # nothing asked now
            assert r.get("question") and "go to -19 64 -73" in said[-1] and len(walks(e)) == 1, (r, said)
            assert facts(e) == [], facts(e)
            await e.mind.owner_text("mine", "Hunter")                                     # "Which block? ... x y z"
            r = await e.mind.owner_text("at 1 64 1", "Hunter")
            assert r.get("action_id") and e.mind.action(r["action_id"])["skill"] == "mine_block", r
            await until(lambda: e.mind.open_action() is None)
            await e.mind.owner_text("come to me", "Hunter")                               # asked by Hunter ...
            await e.mind.owner_text("2 64 2", "Alex")                                      # ... answered by Alex: no
            assert len(walks(e)) == 1 and facts(e) == [], walks(e)
    run(go())


def test_o111_go_to_xyz_is_a_rule_and_a_new_walk_replaces_a_running_one(tmp_path, monkeypatch):
    """45L 11:06:18: "come to 5 100 -40" waited 20 s for the O30 model before walking, and the same line again was
    refused busy in silence. A walk order with coordinates is a rule (no model call) and replaces her running walk;
    anything else running is said, not silently refused."""
    from eve_mind import mind as mind_mod

    async def no_model(*a, **k):
        raise AssertionError("no model call for a walk order with coordinates")
    monkeypatch.setattr(mind_mod, "classify", no_model)

    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.walk_s = 3.0
            await until(lambda: e.mind.fresh_state() is not None)
            r1 = await e.mind.owner_text("come to 5 64 -40", "Hunter")
            assert r1.get("action_id"), r1
            await until(lambda: e.mind.status_of(r1["action_id"]) in ("accepted", "running"))
            r2 = await e.mind.owner_text("go to 6 64 -41", "Hunter")
            assert r2.get("action_id") and e.mind.status_of(r1["action_id"]) == "cancelled", (r2, e.mind.action(r1["action_id"]))
            assert walks(e)[-1]["pos"] == {"x": 6, "y": 64, "z": -41}, walks(e)
            await e.mind.cancel_active("test", source="cli")
            await until(lambda: e.mind.open_action() is None, timeout=8)
            r3 = await e.mind.do("look_at", {"pos": {"x": 1, "y": 64, "z": 1}}, source="cli")
            assert r3.get("action_id"), r3
            r4 = await e.mind.owner_text("go to 7 64 7", "Hunter")                     # O120: any task is replaced
            assert r4.get("action_id") and e.mind.status_of(r3["action_id"]) == "cancelled", (r4, said)
            assert "Stopping look at, doing walking to 7 64 7." in said, said
    run(go())


def test_o111_what_she_carries_comes_from_state_and_is_never_a_command(tmp_path):
    """45L 11:09: "what do you have on you?" -> "not in my records"; "you don't have bread on you?" -> "Yes, I can do
    that (use_item)". Both are questions about her inventory, answered from the state frame; nothing is dispatched."""
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.inventory = [{"slot": 0, "item": "minecraft:bread", "count": 6},
                                {"slot": 1, "item": "minecraft:stone_pickaxe", "count": 1}]
            await until(lambda: (e.mind.fresh_state() or {}).get("inventory"))
            assert (await e.mind.owner_text("what do you have on you?", "Hunter"))["reply"] == "I have 6 bread, 1 stone pickaxe."
            assert (await e.mind.owner_text("you don't have bread on you?", "Hunter"))["reply"] == "Yes - I have 6 bread."
            assert (await e.mind.owner_text("do you have a pickaxe?", "Hunter"))["reply"] == "Yes - I have 1 pickaxe."
            assert (await e.mind.owner_text("do you have any torches?", "Hunter"))["reply"] == "No, I have no torches."
            assert not e.mind.db.execute("SELECT COUNT(*) FROM actions").fetchone()[0] and said, said
    run(go())


def test_o111_yes_or_no_answers_her_confirm_question(tmp_path):
    """Her O42 "Just to be sure: X is now Y?" is answered by his next yes / no - yes stores it as his, no keeps the old."""
    import time

    async def go():
        async with mind_env(tmp_path) as e:
            await until(lambda: e.mind.fresh_state() is not None and e.mind.world_id)
            doubt = {"subject": "mill", "predicate": "location", "object": "5 64 5", "world_specific": True}
            e.mind.asked = {"kind": "confirm", "sender": "Hunter", "at": time.monotonic(), "fact": doubt}
            assert (await e.mind.owner_text("no", "Hunter"))["confirmed"] is False and facts(e) == []
            e.mind.asked = {"kind": "confirm", "sender": "Hunter", "at": time.monotonic(), "fact": doubt}
            r = await e.mind.owner_text("yes", "Hunter")
            assert r["confirmed"] and facts(e) == [("mill", "location", "5 64 5")], (r, facts(e))
    run(go())


def test_o111_a_failed_plan_is_said_and_logged_with_its_reasons(tmp_path, caplog):
    """45L 11:10:56: the planner's "chop 8 logs" plan failed validation and nothing was said, logged or stored. Now the
    reason is said, logged at INFO and kept as a plan.invalid event; every owner line is logged with its outcome."""
    import logging
    caplog.set_level(logging.INFO, logger="eve.mind")

    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            await until(lambda: e.mind.fresh_state() is not None)

            async def plan(text, registry, caps, st, env):
                return {"ok": True, "model": "stub", "prompt_version": "stub", "latency_ms": 1, "raw": "{}",
                        "plan": {"steps": [{"skill": "teleport", "params": {}, "depends_on": []}], "question": ""}}
            e.mind.planner.plan = plan
            r = await e.mind.plan_and_run("chop 8 logs", "owner")
            assert r["error"] == "invalid_plan" and said[-1] == "I don't know how to do that yet.", (r, said)
            ev = e.mind.db.execute("SELECT payload FROM events WHERE kind='plan.invalid'").fetchone()
            assert ev and "teleport" in ev[0], ev
            await e.mind.owner_text("what do you have on you?", "Hunter")
        assert any("plan for 'chop 8 logs' rejected" in m for m in caplog.messages), caplog.messages
        assert any("owner Hunter: 'what do you have on you?'" in m for m in caplog.messages), caplog.messages
    run(go())


def test_o111_owner_is_matched_by_uuid_after_first_sight_and_follow_bounds_are_from_him(tmp_path):
    """Body (live jar 596380e7): match the owner by uuid once seen by name - the network id is not stable, a name can
    be another player's. S38: while following, explore_radius is measured from him; "come to me" stays bounded from
    home."""
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.walk_s, e.body.nearby = 0.05, [HUNTER]
            await until(lambda: e.mind.fresh_state() is not None and e.mind.fresh_state()["nearby_entities"])
            assert e.mind.owner_pos("Hunter")["x"] == 10.5 and e.mind.owner_uuid == {"Hunter": "u-hunter"}
            e.body.nearby = [{**HUNTER, "uuid": "u-other"}]                       # his name, someone else's uuid
            await asyncio.sleep(0.3)
            assert e.mind.owner_pos("Hunter") is None
            e.body.nearby = [{**HUNTER, "name": "Hunter_2", "id": 77}]               # renamed, same uuid: still him
            await asyncio.sleep(0.3)
            assert e.mind.owner_pos("Hunter")["x"] == 10.5
            far = {**HUNTER, "pos": {"x": 300.5, "y": 64.0, "z": -3.5}}             # 300 blocks from home (radius 128)
            e.body.nearby = [far]
            await asyncio.sleep(0.3)
            r = await e.mind.owner_text("come to me", "Hunter")
            assert r.get("code") == "out_of_bounds" and "can't come that far" in said[-1], (r, said)
            await e.mind.owner_text("follow me", "Hunter")
            await until(lambda: walks(e) and walks(e)[-1]["pos"]["x"] == 300.5, what="a follow walk past the radius")
            await e.mind.owner_text("stay", "Hunter")
    run(go())


def test_o114_chat_lines_are_spaced_marked_as_replies_and_a_rate_limited_one_is_resent_once(tmp_path):
    """O114: the live body drops a chat line within 2 s of the last (11:07:12). Lines go out chat_gap_s apart, carry
    reply: true (today's body ignores it), and a rate_limited refusal is re-sent once after 2 s - never twice."""
    async def go():
        async with mind_env(tmp_path, chat_gap_s=0.4) as e:
            await until(lambda: e.mind.fresh_state() is not None)

            def chats():
                return [m for m in e.body.received if m["type"] == "chat.send"]
            await asyncio.gather(e.mind.say("one"), e.mind.say("two"))
            await until(lambda: len(chats()) == 2)
            a, b = chats()
            assert b["ts"] - a["ts"] >= 390 and a["payload"] == {"text": "one", "reply": True}, (a, b)
            await e.body.send("error", {"code": "rate_limited", "message": "chat.send refused: rate_limited",
                                        "about_id": b["id"]})
            await until(lambda: len(chats()) == 3, timeout=5, what="the resend")
            assert chats()[2]["payload"]["text"] == "two"
            await e.body.send("error", {"code": "rate_limited", "message": "again", "about_id": chats()[2]["id"]})
            await asyncio.sleep(2.5)
            assert len(chats()) == 3                                                   # once only
    run(go())


def test_o111_every_owner_line_gets_a_reply_and_a_slow_one_a_holding_line(tmp_path, monkeypatch, caplog):
    """Bar (O112 / CP-85): every owner line gets a chat reply - plan / ask / refuse / answer / busy - and silence is a
    FAIL; a line still working after INTERIM_S gets "give me a moment"; the INFO line carries the elapsed seconds."""
    import logging

    from eve_mind import mind as mind_mod
    caplog.set_level(logging.INFO, logger="eve.mind")
    monkeypatch.setattr(mind_mod, "INTERIM_S", 0.2)

    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            await until(lambda: e.mind.fresh_state() is not None)
            await e.mind.owner_text("walk to 5 64 5", "Hunter")
            assert said[-1] == "On my way.", said
            await until(lambda: e.mind.open_action() is None)
            await e.mind.owner_text("stay", "Hunter")
            assert said[-1] == "OK, I'll stay here.", said

            async def slow_plan(text, registry, caps, st, env):
                await asyncio.sleep(0.6)
                return {"ok": False, "error": "planner_timeout", "model": "stub", "prompt_version": "stub", "latency_ms": 600}
            e.mind.planner.plan = slow_plan
            n = len(said)
            await e.mind.owner_text("build a small wall", "Hunter")            # a planner line (chop N logs, houses: rules)
            assert said[n:] == ["On it - give me a moment.", "I couldn't work out a plan just now (planner_timeout)."], said
        assert any(m.startswith("owner Hunter: 'walk to 5 64 5' -> action ") and m.endswith(" s)") for m in caplog.messages)
    run(go())


def test_o111b_chop_n_logs_is_gather_wood_and_planner_log_names_become_the_logs_tag(tmp_path):
    """45L 11:10 and 11:13: "chop 8 logs" -> the planner wrote mine_resource {block: "minecraft:log"} (no such id),
    invalid twice, in silence. Under 0.15.0 wood is gather {product: wood} (mine_resource over #minecraft:logs): the
    rule maps it; a planner plan naming logs as a block is normalised to the tag before the validator."""
    assert map_command("chop 8 logs") == {"kind": "skill", "skill": "gather", "params": {"product": "wood", "count": 8}}
    assert map_command("get me 12 oak logs")["params"] == {"product": "wood", "count": 12}
    assert map_command("chop down the tree")["kind"] != "skill" and map_command("gather 300 logs")["kind"] != "skill"

    async def go():
        async with mind_env(tmp_path) as e:
            await until(lambda: e.mind.fresh_state() is not None)
            seen = {}

            async def plan(text, registry, caps, st, env):
                return {"ok": True, "model": "stub", "prompt_version": "stub", "latency_ms": 1, "raw": "{}",
                        "plan": {"steps": [{"skill": "mine_resource", "params": {"count": 8, "block": "minecraft:log",
                                                                                 "radius": 16}, "depends_on": []}],
                                 "question": ""}}
            e.mind.planner.plan = plan
            r = await e.mind.plan_and_run("chop some wood", "owner", step_wait=0.1)
            seen = r["plan"]["steps"][0]["params"]
            assert seen == {"count": 8, "tag": "minecraft:logs", "radius": 16}, seen
            assert not any("minecraft:log" in str(x) and "logs" not in str(x) for x in r.get("detail") or []), r
    run(go())


def test_o111b_questions_are_never_executed_and_state_questions_come_from_state(tmp_path):
    """O111-b (5): a line that ends with "?" or starts with do/does/are/is/can/what/where/which/how is answered, never
    executed; where she is, her health and where home is come from the state frame and the envelope (45L 11:18:
    "whats in your inventory?" -> "not in my records")."""
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.inventory = [{"slot": 0, "item": "minecraft:bread", "count": 3}]
            await until(lambda: (e.mind.fresh_state() or {}).get("inventory"))
            for q in ("can you go to 2 64 2?", "is the door open", "where did you go?"):
                r = await e.mind.owner_text(q, "Hunter")
                assert "reply" in r and sum(e.body.requests.values()) == 0, (q, r)
            assert (await e.mind.owner_text("whats in your inventory?", "Hunter"))["reply"] == "I have 3 bread."
            assert (await e.mind.owner_text("where are you?", "Hunter"))["reply"] == "I'm at 0 64 0."
            assert (await e.mind.owner_text("how is your health?", "Hunter"))["reply"].startswith("I have 20/20 health")
            assert (await e.mind.owner_text("where is home?", "Hunter"))["reply"] == "Home is at 0 64 0."
            assert (await e.mind.owner_text("go to 2 64 2", "Hunter")).get("action_id")       # an order still runs
    run(go())


def test_o120_every_new_order_replaces_her_task_with_one_line_and_says_why_it_failed(tmp_path, monkeypatch):
    """O120 (45L 11:51:45: "chop 8 logs" during a follow left the follow running; the gather was refused break no_zone
    in silence; "stop following" was a hard STOP, so his next order was refused STOPPED). A new order ends her task -
    follow included - with "Stopping <old>, doing <new>."; a question leaves it running; his order's failure is said;
    "stop following" ends only the follow."""
    monkeypatch.setattr(follow, "TICK", 0.05)

    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.walk_s, e.body.nearby = 0.3, [HUNTER]
            await until(lambda: e.mind.fresh_state() is not None and e.mind.fresh_state()["nearby_entities"])
            await e.mind.owner_text("follow me", "Hunter")
            await until(lambda: len(walks(e)) == 1, what="the first walk")
            assert (await e.mind.owner_text("where are you?", "Hunter")).get("reply")   # a question: still following
            assert e.mind.follow.running()
            e.body.result_override = {"gather": ("failed", "zone_denied")}
            e.body.result_note = "the server refused breaking -122, 96, 17; server denied: break no_zone"
            r = await e.mind.owner_text("chop 8 logs", "Hunter")
            assert r.get("action_id") and not e.mind.follow.running(), r
            assert "Stopping following you, doing gather wood." in said, said
            await until(lambda: any("outside every zone" in t for t in said), timeout=5, what="the failure line")
            assert said[-1] == "I couldn't gather there - that spot is outside every zone I'm allowed to change.", said
            await until(lambda: e.mind.open_action() is None)
            e.body.result_note = "the server refused breaking 1, 64, 1; server denied: break placed_block"
            r = await e.mind.owner_text("chop 8 logs", "Hunter")                   # nothing running: no stop line
            await until(lambda: "part of a build" in said[-1], timeout=5, what="the build-logs line")
            assert said[-1] == "Those logs are part of a build - I only chop natural trees." and \
                not any(t.startswith("Stopping") for t in said[said.index("Stopping following you, doing gather wood.") + 1:]), said
            await until(lambda: e.mind.open_action() is None)
            e.body.result_override = e.body.result_note = None
            await e.mind.owner_text("follow me", "Hunter")
            assert e.mind.follow.running()
            r = await e.mind.owner_text("stop following me", "Hunter")
            assert r["latched"] is False and not e.mind.follow.running() and not e.mind.stopped, r
            assert said[-1] == "Stopped following you.", said
            await until(lambda: e.mind.open_action() is None)
            assert (await e.mind.owner_text("chop 8 logs", "Hunter")).get("action_id")   # not refused STOPPED
            for t in ("stop", "stop that", "hold on"):     # O120 addendum: cancel + stand still, never the latch
                await until(lambda: e.mind.open_action() is None)
                e.body.walk_s = 3.0
                r0 = await e.mind.owner_text("go to 9 64 9", "Hunter")
                await until(lambda: e.mind.status_of(r0["action_id"]) in ("accepted", "running"))
                r = await e.mind.owner_text(t, "Hunter")
                assert not e.mind.stopped and not e.body.stopped and e.mind.status_of(r0["action_id"]) == "cancelled", (t, r)
                assert said[-1] == "Stopped walk to.", (t, said)
            await until(lambda: e.mind.open_action() is None)
            assert (await e.mind.owner_text("stop", "Hunter"))["latched"] is False and said[-1] == "Stopped.", said
            await e.sense.owner_command("Hunter", True, "stop", [])          # the latch: /eve stop (and the CLI)
            await until(lambda: e.mind.stopped and e.body.stopped)
            assert (await e.mind.owner_text("go to 9 64 9", "Hunter")).get("code") == "stopped"
    run(go())


def test_o121_follow_uses_sense_owner_pos_out_of_view_and_ends_only_on_leave_or_dimension(tmp_path, monkeypatch):
    """O121 (Hunter: "she lost sight of me and stopped following; even out of view she should still follow"): sense's
    owner.pos (<= OWNER_POS_S old) comes before body's nearby list; with neither she keeps walking to where he was and
    says so once; the follow ends when he leaves (online:false) or changes dimension (she asks), never on unseen."""
    from eve_mind import mind as mind_mod
    monkeypatch.setattr(follow, "TICK", 0.05)
    monkeypatch.setattr(mind_mod, "OWNER_POS_S", 0.6)

    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.walk_s, e.body.nearby = 0.05, []                       # out of her view the whole time
            pos = lambda x, **k: e.sense.send("owner.pos", {"sender": "Hunter", "uuid": "u-hunter",  # noqa: E731
                                                            "dim": "minecraft:overworld", "x": x, "y": 64.0, "z": 0.5,
                                                            "ts": 1, **k})
            await until(lambda: e.mind.fresh_state() is not None)
            await e.sense.send("owner.pos", {"sender": "Mallory", "dim": "minecraft:overworld", "x": 1, "y": 64, "z": 1})
            await pos(20.5)
            await until(lambda: "Hunter" in e.mind.owner_live)
            assert "Mallory" not in e.mind.owner_live and e.mind.db.execute(
                "SELECT COUNT(*) FROM events WHERE kind='owner.pos'").fetchone()[0] == 0     # never logged
            assert (await e.mind.owner_text("follow me", "Hunter")) == {"ok": True, "following": "Hunter"}
            await until(lambda: walks(e) and walks(e)[-1]["pos"]["x"] == 20.5, what="the walk to his owner.pos")
            await pos(30.5)
            await until(lambda: walks(e)[-1]["pos"]["x"] == 30.5, what="the re-plan from owner.pos")
            await until(lambda: "Lost you - heading to where you were." in said, timeout=5, what="stale -> lost")
            await asyncio.sleep(0.4)
            assert e.mind.follow.running() and said.count("Lost you - heading to where you were.") == 1, said
            await pos(40.5)                                                  # seen again: on he goes
            await until(lambda: walks(e)[-1]["pos"]["x"] == 40.5, what="following again")
            await pos(40.5, dim="minecraft:the_nether")
            await until(lambda: not e.mind.follow.running(), what="the dimension end")
            assert e.mind.follow.current["reason"] == "dimension" and "another dimension" in said[-1], said
            await pos(41.5)
            await until(lambda: e.mind.owner_live["Hunter"]["dim"] == "minecraft:overworld", what="back in his world")
            await e.mind.owner_text("follow me", "Hunter")
            assert e.mind.follow.running(), said
            await pos(41.5, online=False)
            await until(lambda: not e.mind.follow.running(), what="the offline end")
            assert e.mind.follow.current["reason"] == "offline" and said[-1] == "You left, so I've stopped following.", said
    run(go())


def test_o122_mine_n_resource_is_mine_resource_and_then_runs_orders_in_turn(tmp_path):
    """O122 (45L session 3: "mine up some logs and then craft a crafting table" and "mine 7 logs" got "Which block?
    Give me its x y z."): a resource order is mine_resource with the resource normalised (logs -> #minecraft:logs,
    "some" -> 8); "a and then b" / "a, then b" runs b when a ends ok, and on a failure says what was left undone."""
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.walk_s = 0.1
            await until(lambda: e.mind.fresh_state() is not None)

            def rows():
                return [(s, json.loads(p)) for s, p in e.mind.db.execute("SELECT skill, params FROM actions ORDER BY rowid")]
            r = await e.mind.owner_text("mine 7 logs", "Hunter")
            assert rows()[-1] == ("mine_resource", {"tag": "minecraft:logs", "count": 7, "max_blocks": 64, "radius": 32}), (r, rows(), said)
            await until(lambda: e.mind.open_action() is None)
            await e.mind.owner_text("mine up some logs and then craft a crafting table", "Hunter")
            assert rows()[-1] == ("mine_resource", {"tag": "minecraft:logs", "count": 8, "max_blocks": 64, "radius": 32}), rows()
            assert not any("Which block" in t for t in said), said
            await until(lambda: e.mind.open_action() is None)
            await asyncio.sleep(0.5)                       # the craft step (whatever it does) settles first
            await until(lambda: e.mind.open_action() is None)
            n = len(walks(e))
            await e.mind.owner_text("go to 5 64 5 and then go to 6 64 6, then go to 7 64 7", "Hunter")
            await until(lambda: len(walks(e)) == n + 3 and e.mind.open_action() is None, timeout=5, what="three walks")
            assert [w["pos"]["x"] for w in walks(e)[n:]] == [5, 6, 7], walks(e)[n:]
            e.body.result_override = {"walk_to": ("failed", "stuck")}
            await e.mind.owner_text("go to 8 64 8 and then go to 9 64 9", "Hunter")
            await until(lambda: any(t.startswith("So I didn't go to 9 64 9") for t in said), timeout=5, what="the stop line")
            assert len(walks(e)) == n + 5, walks(e)[n:]   # + O142: the failed walk is retried once, digging
    run(go())
