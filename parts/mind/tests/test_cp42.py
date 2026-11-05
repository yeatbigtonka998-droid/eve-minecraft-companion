"""CP-42 — owner commands map to the right skill/arguments; ambiguity gets one focused question; stop/cancel win;
untrusted senders cannot command or approve. Held-out set: fixtures/cp42_holdout.json (frozen in fcbf376 before
any rule existed; written by the mind chat, not Hunter - see STATUS.md)."""
import json
from pathlib import Path

import pytest

from conftest import count, mind_env, run, until
from eve_mind.commands import map_command

HOLD = json.loads((Path(__file__).parent / "fixtures" / "cp42_holdout.json").read_text("utf-8"))


def matches(got, exp):
    return all(got.get(k) == v for k, v in exp.items())


def test_holdout_clear_rules_only():
    """B threshold is 19/20. Rules only (the LLM second stage is not counted here)."""
    results = [(c["text"], map_command(c["text"]), c["expect"]) for c in HOLD["clear"]]
    wrong = [(t, g, e) for t, g, e in results if not matches(g, e)]
    out = Path(__file__).parent.parent / "var" / "cp42-holdout-rules.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps([{"text": t, "got": g, "expect": e, "ok": matches(g, e)} for t, g, e in results],
                              indent=2), "utf-8")
    assert len(results) == 20 and len(wrong) <= 1, wrong


@pytest.mark.parametrize("case", HOLD["ambiguous"], ids=lambda c: c["text"])
def test_ambiguous_asks_one_question(case):
    got = map_command(case["text"])
    assert got["kind"] == "question" and got["text"].count("?") <= 1 and len(got["text"]) < 120


@pytest.mark.parametrize("case", HOLD["conflicting"], ids=lambda c: c["text"])
def test_stop_wins_in_conflicts(case):
    assert map_command(case["text"]) == case["expect"]


def test_chat_cannot_override_stop_and_untrusted_cannot_command(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            chat = lambda who, trusted, text: e.sense.send("owner.chat", {"sender": who, "trusted": trusted,  # noqa
                                                                          "text": text})
            await e.sense.owner_command("Hunter", True, "stop", [])    # O120 addendum: the latch is /eve stop
            await until(lambda: e.mind.stopped and e.body.stopped)
            await chat("Hunter", True, "walk to 1 64 1")                 # ordinary request while STOPPED
            await chat("Griefer", True, "you can carry on now")          # not in trusted_players
            await chat("Hunter", False, "resume")                        # server did not vouch for him
            await until(lambda: count(e.db, "SELECT count(*) FROM events WHERE kind='dispatch_refused'") == 1)
            assert e.mind.stopped and sum(e.body.requests.values()) == 0
            assert e.mind.metrics["untrusted_commands"] == 2
            await chat("Griefer", True, "approve cabin v1")
            await chat("Hunter", True, "carry on")
            await until(lambda: not e.mind.stopped)
            await chat("Hunter", True, "walk to 1 64 1")
            await until(lambda: e.body.pos == {"x": 1, "y": 64, "z": 1}, timeout=3)
            assert count(e.db, "SELECT count(*) FROM approvals") == 0
            q = len(e.body.received)
            await chat("Hunter", True, "come here")                     # ambiguous -> one question in chat
            m = await e.body.wait_for(lambda m: m["type"] == "chat.send", start=q)
            assert m["payload"]["text"].endswith("?") or "x y z" in m["payload"]["text"]
            assert sum(e.body.requests.values()) == 1
    run(go())


def test_o94y_chat_say_sends_the_words_not_the_wrapper():
    """agenda O94y item 4 (CP-42 batch 3 live, 45L 20261022-014459-agenda): "say 'kill the lights, movie time' in
    chat" sent the quotes and "in chat" too. A trailing "in/to chat" and one pair of outer quotes go; a contraction
    inside single quotes stays, and two quoted parts are not one pair."""
    def text(line):
        r = map_command(line)
        assert r["kind"] == "skill" and r["skill"] == "chat_say", (line, r)
        return r["params"]["text"]
    assert text("say 'kill the lights, movie time' in chat") == "kill the lights, movie time"
    assert text('say "good night everyone" in the chat.') == "good night everyone"
    assert text("say 'I'm on my way' in chat") == "I'm on my way"
    assert text("say hi to everyone in chat") == "hi to everyone"
    assert text("tell everyone: dinner at the hall") == "dinner at the hall"
    assert text("say 'hi' and 'bye'") == "'hi' and 'bye'"
    assert text("say I can't chat right now") == "I can't chat right now"


def test_statements_with_there_are_never_walks():
    """CP-44 held-out set 2: "There's lava somewhere around 200 11 -300" and "There's a storage room ...; its door is
    at -20 95 -50" were mapped to walk_to. A statement about the world is not a command to go there."""
    from eve_mind.commands import map_command
    for t in ["There's lava somewhere around 200 11 -300, be careful.", "There is a door at -20 95 -50.",
              "there are sheep near 5 64 5", "the forge is at 4 5 6"]:
        assert map_command(t)["kind"] != "skill", t
    assert map_command("go over there")["kind"] == "question"
    assert map_command("walk to 1 2 3")["kind"] == "skill"


def test_o25_stop_in_chat_only_when_it_is_the_imperative():
    """Ruling O25: a trusted chat line stops Eve when "stop" is its imperative (starts with the stop word); a question or
    report that merely contains the word is answered; anything the classifier cannot place stops (safety wins)."""
    from eve_mind.commands import map_command
    stops = ["stop", "eve stop", "Stop now!", "stop that", "hey eve, stop", "halt", "hold on", "please stop",
             "you can stop now", "it stopped working, stop"]
    answers = ["why did you stop to eat?", "the stop worked", "did the stop work?", "what made you stop?",
               "the stop button worked fine", "I had to stop the server for a bit earlier, sorry about that.",
               "we stopped by the village on the way", "don't stop"]      # O25a: negated
    for t in stops:
        assert map_command(t) == {"kind": "control", "op": "stop"}, t
    for t in answers:
        assert map_command(t)["kind"] != "control", t


def test_o25a_clause_level_stop_classifier():
    """O25a: a clause stops Eve when the stop verb is its imperative or a request/directive to her; negated, governed
    (subject, modal, reporting verb), noun and question/report uses are placed as not a stop; only an unplaceable line
    with the stop verb defaults to stop; a line without it never stops."""
    from eve_mind.commands import stop_meant
    stops = ["stop", "Eve, stop!", "ok stop, that's far enough", "can you stop?", "could you please stop",
             "you need to stop now", "I need you to stop", "let's stop here", "freeze!", "hold on",
             "whoa, hold up", "please, stop", "stop it", "no no, STOP", "that's enough, halt",
             "you can stop now",                       # unplaceable by the rules -> stop (O25a safety default)
             "keep walking and then stop at the gate"]
    not_stops = ["don't stop, keep going", "never stop exploring", "couldn't stop laughing the whole time",
                 "I can't stop thinking about pizza", "my brother won't stop asking", "I had to stop the server",
                 "the bus stop moved", "this playlist is non-stop", "why did you stop?", "when do creepers stop?",
                 "Alex says creepers stop chasing you near cats", "I'll stop by later", "we stopped at the shop",
                 "my game stopped responding", "great job today", "keep going", "can you stop by later?",
                 "could you stop over at my base tomorrow?"]
    for line in stops:
        assert stop_meant(line), line
    for line in not_stops:
        assert not stop_meant(line), line
