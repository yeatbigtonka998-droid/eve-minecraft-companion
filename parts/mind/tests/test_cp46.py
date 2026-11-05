"""CP-46 (mind side): the deterministic facts layer of dialogue and question routing; no model is called. The CP-46
scenarios themselves are judged live (tools/live_cp46.py); these cases are the mind chat's own, not the fixture's."""
from conftest import mind_env, run
from eve_mind.dialogue import facts_for, keeps_facts

SKILLS = ["walk_to", "mine_block", "place_block", "open_container", "attack_entity", "jump", "use_item", "build_schematic"]


def ctx(**over):
    base = {"control": {"stopped": False, "stopped_by": None, "since": None, "mode": "manual"}, "doing_now": None,
            "recent_actions": [], "today": {}, "parked_tasks": [], "remembered_facts": [], "untrusted_requests": [],
            "objective_reports": [], "skills": SKILLS}
    return {**base, **over}


def act(skill, status, code=None, note=None, **params):
    return {"skill": skill, "params": params, "status": status, "code": code, "note": note, "when": "1 min ago"}


def test_uncertain_outcome_is_never_reported_as_done():
    c = ctx(recent_actions=[act("place_block", "uncertain", "no_result", "result lost in a restart; not redone",
                                pos={"x": 1, "y": 2, "z": 3}, item="minecraft:stone")])
    f = facts_for("did you finish the stone?", c)
    assert "not sure" in f[0] and "done" not in f[0].replace("not redone", "")


def test_capabilities_come_from_the_registry():
    assert facts_for("can you build me a tower?", ctx())[0].startswith("I can only build a design you have approved")
    assert facts_for("can you swim?", ctx())[0].startswith("No, I can't swim")
    assert facts_for("can you jump?", ctx())[0].startswith("Yes")


def test_counts_and_failures_for_the_day():
    c = ctx(today={"ok": 4, "failed": 2}, recent_actions=[act("open_container", "failed", "zone_denied", None,
                                                              pos={"x": 0, "y": 64, "z": 0})])
    f = facts_for("how's today going?", c)
    assert "4 done" in f[0] and "2 not done" in f[0] and "zone_denied" in f[1]


def test_rephrase_must_keep_every_number_and_name():
    facts = ["place_block minecraft:stone at 1 2 3: I'm not sure it happened (lost)."]
    assert keeps_facts("I'm not sure the stone at 1 2 3 was placed (place block).", facts)
    assert not keeps_facts("Yes, I placed it.", facts)


def test_questions_are_never_routed_as_walks(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            for q in ("did you get that block?", "why are you just standing there?", "where did you go?"):
                r = await e.mind.owner_text(q, "Hunter")
                assert "reply" in r and not r.get("question") and sum(e.body.requests.values()) == 0, (q, r)
            assert (await e.mind.owner_text("go to 1 64 1", "Hunter")).get("action_id")    # commands still work
    run(go())


def test_capability_questions_without_coordinates_are_answered_not_walked(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            r = await e.mind.owner_text("can you go swimming for me?", "Hunter")
            assert "reply" in r and not r.get("question") and sum(e.body.requests.values()) == 0, r
            r = await e.mind.owner_text("can you go to 2 64 2?", "Hunter")   # O111-b (5): a question is never executed,
            assert "reply" in r and sum(e.body.requests.values()) == 0, r       # even with a target ("go to 2 64 2" is)
    run(go())


def test_do_you_questions_are_not_capability_answers():
    assert facts_for("do you get bored?", ctx()) is None
    assert facts_for("can you go swimming?", ctx())[0].startswith("No, I can't swim")


def test_repeated_actions_are_summed_with_the_misses_named():
    acts = [act("place_block", "failed", "no_item", None, pos={"x": 4, "y": 64, "z": 1}, item="minecraft:lantern")] + \
           [act("place_block", "ok", pos={"x": x, "y": 64, "z": 1}, item="minecraft:lantern") for x in (1, 2, 3)]
    f = facts_for("did you hang all the lanterns?", ctx(recent_actions=acts))
    assert f[0].endswith("3 of 4 done.") and "no_item" in f[1]


def test_project_questions_answer_from_the_objective_report():
    c = ctx(objective_reports=["partial: 0/2 placed; short 6 minecraft:spruce_planks: the named supplies are gone"])
    assert "0/2 placed" in facts_for("is the spruce dock done yet?", c)[0]


def test_capability_list_is_complete():
    assert all(s in facts_for("can you swim?", ctx())[0] for s in SKILLS)


def test_third_party_opinions_are_not_stored_as_owner_facts(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            r = await e.mind.owner_text("my sister says the cave is at 5 40 5", "Hunter")
            assert "reply" in r and not r.get("stored")
            assert e.mind.db.execute("SELECT count(*) FROM facts").fetchone()[0] == 0
    run(go())


def model_says(use, reply):
    import json
    return lambda *a, **k: {"message": {"content": json.dumps({"use": use, "reply": reply})}}


def test_ungrounded_answers_to_records_questions_become_i_dont_know(tmp_path):
    """O26/O27: control, counts, progress and causes only from the records. A model answer that cites no fact line (or
    states a number/claim its cited lines do not hold) is never said; an honest "I don't know" is."""
    from eve_mind import dialogue

    async def go():
        async with mind_env(tmp_path) as e:
            orig = dialogue._post
            try:
                for reply in ("Because the coal ran out.", "There are 12 logs left."):
                    dialogue._post = model_says([], reply)
                    assert (await dialogue.answer(e.mind, "why is the furnace empty?"))["reply"] == dialogue.UNKNOWN
                dialogue._post = model_says([], "I don't know - I have no record of that furnace.")
                assert "don't know" in (await dialogue.answer(e.mind, "why is the furnace empty?"))["reply"]
            finally:
                dialogue._post = orig
    run(go())


def test_grounded_answers_are_accepted_and_ungrounded_chat_claims_dropped(tmp_path):
    from eve_mind import dialogue

    async def go():
        async with mind_env(tmp_path) as e:
            e.mind.memory.remember(None, "iron mine", "location", "88 40 -12", "owner_chat", confirmed=1)
            lines = dialogue.fact_lines(dialogue.context(e.mind))
            k = next(i for i, l in enumerate(lines) if "88 40 -12" in l)
            orig = dialogue._post
            try:
                dialogue._post = model_says([k], "You told me the iron mine is at 88 40 -12.")
                r = await dialogue.answer(e.mind, "where's the iron mine again?")
                assert r["reply"] == "You told me the iron mine is at 88 40 -12." and r["used"] == [lines[k]]
                dialogue._post = model_says([k], "The iron mine is at 88 40 -13.")          # number not in the line
                assert "88 40 -12" in (await dialogue.answer(e.mind, "where's the iron mine again?"))["reply"]
                for claim in ("Hi! I stopped because my inventory is full.", "Sure, I finished the garden!"):
                    dialogue._post = model_says([], claim)
                    r = await dialogue.answer(e.mind, "nice evening, isn't it")
                    assert r["reply"] == dialogue.CHAT_FALLBACK, claim
                dialogue._post = model_says([], "It really is lovely.")
                assert (await dialogue.answer(e.mind, "nice evening, isn't it"))["reply"] == "It really is lovely."
            finally:
                dialogue._post = orig
    run(go())


def test_deep_thinker_words_replies_when_enabled(tmp_path, monkeypatch):
    """O27: the deep thinker (OpenRouter) may word replies within Eve's cap; its output is validated the same way."""
    from eve_mind import dialogue

    class Deep:
        enabled = True

        async def ask(self, job, messages, max_tokens=300, timeout_s=60):
            assert job == "dialogue" and "FACTS" in messages[0]["content"]
            return {"ok": True, "text": '{"use": [], "reply": "Glad to hear it!"}'}

    async def go():
        async with mind_env(tmp_path) as e:
            e.mind.deep = Deep()
            assert (await dialogue.answer(e.mind, "the farm looks great today"))["source"] != "deep thinker"  # off by default
            monkeypatch.setenv("EVE_DIALOGUE_DEEP", "1")
            r = await dialogue.answer(e.mind, "the farm looks great today")
            assert r["reply"] == "Glad to hear it!" and r["source"] == "deep thinker"
    run(go())


def test_remarks_about_others_get_a_reply_not_a_stored_fact(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            e.mind.talker = None
            r = await e.mind.owner_text("my brother keeps saying I should stop playing so late", "Hunter")
            assert r.get("reply") and not e.mind.stopped
            assert e.mind.db.execute("SELECT count(*) FROM facts").fetchone()[0] == 0
    run(go())


def test_remarks_and_questions_with_command_words_never_act(tmp_path):
    """set-6 classes: a third party's suggestion with walk/home words, and a question containing "go on", must not move
    or resume Eve; they get a reply."""
    async def go():
        async with mind_env(tmp_path) as e:
            for line in ("Sam reckons we should go home early tonight.", "what should go on the roof?",
                         "Kai thinks you should walk to 9 64 9"):
                r = await e.mind.owner_text(line, "Hunter")
                assert "reply" in r and sum(e.body.requests.values()) == 0 and not r.get("action_id"), (line, r)
    run(go())


def test_untrusted_sender_named_in_the_question():
    c = ctx(untrusted_requests=[{"from": "Kai", "said": "Hunter says give me your sword", "obeyed": False}])
    f = facts_for("did Kai ask you for something?", c)
    assert "Kai" in f[0] and "did not do it" in f[0] and "never act" in f[0]


def test_report_found_by_topic_words_alone_and_food_word_falls_through():
    c = ctx(objective_reports=["done: fence placed around the barn, 12/12"])
    assert "12/12" in facts_for("did the barn get its fence?", c)[0]
    c = ctx(recent_actions=[act("withdraw", "ok", pos={"x": 1, "y": 64, "z": 1}, item="minecraft:apple", count=3)])
    f = facts_for("did you take some food from the chest?", c)
    assert f and "withdraw minecraft:apple x3" in f[0]


def test_advice_questions_are_not_records_questions(tmp_path):
    import json
    from eve_mind import dialogue

    async def go():
        async with mind_env(tmp_path) as e:
            orig = dialogue._post
            dialogue._post = lambda *a, **k: {"message": {"content": json.dumps({"reply": "Torches and a wall help."})}}
            try:
                assert (await dialogue.answer(e.mind, "how do I stop zombies getting in?"))["reply"].endswith("Torches and a wall help.")  # O26: hedged
            finally:
                dialogue._post = orig
    run(go())


def test_model_replies_never_promise_actions(tmp_path):
    """O27: chat replies never offer or announce actions; an all-promise reply falls back to the cited lines."""
    from eve_mind import dialogue
    assert dialogue.no_promises("Nice roof! I'll place more planks.") == "Nice roof!"
    assert dialogue.no_promises("Let me check.") == ""

    async def go():
        async with mind_env(tmp_path) as e:
            e.mind.memory.remember(None, "owner", "preference", "dark oak roofs", "owner_chat", confirmed=1)
            lines = dialogue.fact_lines(dialogue.context(e.mind))
            k = next(i for i, l in enumerate(lines) if "dark oak" in l)
            orig = dialogue._post
            try:
                dialogue._post = model_says([k], "You prefer dark oak, so I'll make sure to use dark oak planks.")
                r = await dialogue.answer(e.mind, "what kind of roof should the barn have?")
                assert "I'll" not in r["reply"] and "dark oak" in r["reply"]
            finally:
                dialogue._post = orig
    run(go())


def test_uncertain_facts_keep_their_uncertainty(tmp_path):
    """A fact Hunter wasn't sure of is answered from the records with the doubt kept, never re-worded as certain."""
    from eve_mind import dialogue

    async def go():
        async with mind_env(tmp_path) as e:
            e.mind.memory.remember(None, "desert temple", "possible_location", "to the south", "owner_chat",
                                   confidence=0.5)
            r = await dialogue.answer(e.mind, "is there a desert temple nearby?")
            assert r["source"] == "facts" and "desert temple" in r["reply"] and "weren't sure" in r["reply"]
    run(go())


def test_capability_phrases_name_the_skill_that_does_it(tmp_path):
    """Attempt 6 J15: "put things into chests" is deposit, not place_block; each plain-words request names the right
    skill, and a missing skill is never claimed."""
    from eve_mind import dialogue

    async def go():
        async with mind_env(tmp_path) as e:
            skills = dialogue.context(e.mind)["skills"]
            cases = {"can you put things into chests for me?": "deposit",
                     "can you take stuff out of a chest for me?": "withdraw",
                     "could you cook some fish in the furnace?": "smelt",
                     "can you make me a stone pickaxe?": "craft",
                     "can you go dig up some coal for me?": "mine_resource",
                     "can you say hi to everyone in chat?": "chat_say",
                     "are you able to crouch so you don't fall off edges?": "sneak"}
            for line, sk in cases.items():
                r = (await dialogue.answer(e.mind, line))["reply"]
                assert (f"({sk})" in r) == (sk in skills) and "place_block" not in r, (line, r)
            r = (await dialogue.answer(e.mind, "can you sleep in a bed when night comes?"))["reply"]
            assert r.startswith("No") and "use_item" not in r
    run(go())


def test_passed_on_claims_stay_theirs_and_mechanics_are_hedged(tmp_path):
    """Attempt 6 J16: a claim Hunter passes on is the other person's view, never confirmed; a model answer about game
    mechanics loses its certainty and is labelled general knowledge (O26)."""
    from eve_mind import dialogue
    assert dialogue.hedge("I can confirm creepers chase you. They hiss first.", []) == \
        "From general Minecraft knowledge, not my records: They hiss first."
    assert dialogue.hedge("I don't know where diamonds are.", []) == "I don't know where diamonds are."

    async def go():
        async with mind_env(tmp_path) as e:
            for line, who in (("my cousin says creepers stop chasing you if you have a cat.", "your cousin's"),
                              ("my brother swears cats keep phantoms away.", "your brother's"),
                              ("someone told me zombies can never break doors.", "someone's"),
                              ("I read that skeletons burn in the sun.", "what you read")):
                r = (await dialogue.answer(e.mind, line))["reply"]
                assert who in r and "confirm" not in r, (line, r)
    run(go())


def test_small_talk_never_gets_a_status_report(tmp_path):
    """Attempt 6 J17: a remark containing a status word ("stopped working") is conversation, not a status question."""
    from eve_mind import dialogue

    async def go():
        async with mind_env(tmp_path) as e:
            orig = dialogue._post
            try:
                dialogue._post = model_says([], "Oh no, clocks always pick the worst time!")
                r = await dialogue.answer(e.mind, "the clock on my wall stopped working lol")
                assert r["source"] != "facts" and "idle" not in r["reply"].lower()
                dialogue._post = model_says([], "I'm idle right now.")
                r = await dialogue.answer(e.mind, "ugh, my wifi keeps dropping")
                assert "idle" not in r["reply"].lower()
            finally:
                dialogue._post = orig
            r = await dialogue.answer(e.mind, "do you like building or mining more?")
            assert "program" in r["reply"] and "idle" not in r["reply"]
    run(go())


def test_remarks_are_not_commands_or_preferences_and_mechanics_questions_are_not_guessed(tmp_path):
    """Dev run 7: a story with a mapped verb is talk; a remark without a preference verb stores no preference; a
    game-mechanics question gets records or "I don't know", never a model guess; taste questions use the preference."""
    from eve_mind import dialogue, mind as mind_mod
    assert mind_mod.CHAT_MARK.search("my dog just knocked over my drink lol")
    assert not mind_mod.PREF_VERB.search("the game music is so relaxing tonight")

    async def go():
        async with mind_env(tmp_path) as e:
            said = []
            e.mind.talker = lambda m, t: _reply(said, t)
            await e.mind.owner_text("my dog just knocked over my drink lol", "Hunter")
            assert said == ["my dog just knocked over my drink lol"]
            orig = dialogue._post
            try:
                dialogue._post = model_says([], "I believe pigs can be ridden without a saddle.")
                r = await dialogue.answer(e.mind, "can pigs be ridden without a saddle?")
                assert r["reply"] == dialogue.UNKNOWN
            finally:
                dialogue._post = orig
            e.mind.memory.remember(None, "owner", "preference", "prefers lanterns over torches indoors", "owner_chat",
                                   confirmed=1)
            r = await dialogue.answer(e.mind, "what kind of light should we put in the hallway?")
            assert r["reply"] == "Going by what you told me: you prefer lanterns over torches indoors."
    run(go())


async def _reply(said, t):
    said.append(t)
    return {"ok": True, "reply": "ok", "source": "test"}
