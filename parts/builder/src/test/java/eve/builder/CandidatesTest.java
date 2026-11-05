package eve.builder;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.*;

/** O94f: the stand-choice row follows eve-learning's eve-candidates/1 contract (parts/learning/CONTRACTS.md). */
class CandidatesTest {
	static Map<String, Object> state() {
		Map<String, Object> st = new LinkedHashMap<>();
		st.put("target", new int[] {27, 155, -43});
		st.put("op", "place");
		st.put("her", new int[] {29, 152, -42});
		st.put("support", null);
		st.put("steady", true);
		return st;
	}

	@Test
	void theRowFollowsTheContract() {
		List<Candidates.Opt> opts = new ArrayList<>();
		for (int i = 0; i < 20; i++) opts.add(new Candidates.Opt(i, 152, -40, 20.0 - i, null)); // 20 feasible, cheapest last
		opts.add(new Candidates.Opt(30, 152, -40, null, "no_steady_click"));
		opts.add(new Candidates.Opt(31, 152, -40, null, "bad_spot"));
		JsonObject r = Candidates.row("hut5tall-0001", "stand@jar:4f3ba1a7", "build/phase2", 9123, state(), opts, "19,152,-40", "a71b7f43", "2026-10-21T21:30:00.000-05:00", key(27, 155, -43));
		for (String k : new String[] {"schema", "ts", "tick", "objective_id", "runner", "rule", "phase", "decision_type", "decision_id", "state_hash", "options", "pick", "action_id", "safety", "step_key"})
			assertTrue(r.has(k), "required field " + k);
		assertEquals("eve-candidates/1", r.get("schema").getAsString());
		assertEquals(key(27, 155, -43), r.getAsJsonObject("step_key"), "the target cell, as evidence names cells (mismatches / manual pos)");
		assertTrue(r.get("rule").getAsString().matches("[a-z_]+@jar:[0-9a-f]{8}"), "ASCII @");
		assertEquals("stand_choice", r.get("decision_type").getAsString());
		assertFalse(r.get("safety").getAsBoolean());
		for (String k : new String[] {"status", "code", "result", "success"}) assertFalse(r.has(k), "no outcome field " + k);
		var options = r.getAsJsonArray("options");
		assertEquals(16 + 2, options.size(), "the 16 cheapest feasible plus every rejected one");
		assertEquals("19,152,-40", options.get(0).getAsJsonObject().get("id").getAsString(), "cheapest first");
		double last = -1;
		Set<String> ids = new HashSet<>();
		for (JsonElement e : options) {
			JsonObject o = e.getAsJsonObject();
			ids.add(o.get("id").getAsString());
			assertEquals("cost", o.get("score_kind").getAsString());
			if (o.get("feasible").getAsBoolean()) {
				assertTrue(o.get("score").getAsDouble() >= last);
				last = o.get("score").getAsDouble();
				assertFalse(o.has("rejected_by"));
			} else {
				assertTrue(o.get("score").isJsonNull());
				assertTrue(o.has("rejected_by"));
			}
			assertEquals("walk_to", o.getAsJsonObject("action").get("skill").getAsString());
		}
		assertTrue(ids.contains(r.get("pick").getAsString()), "the pick is one of the options");
		assertEquals(18, ids.size(), "option ids are unique");
	}

	@Test
	void nothingFeasibleIsPickNull() {
		List<Candidates.Opt> opts = List.of(new Candidates.Opt(1, 2, 3, null, "no_sight"));
		JsonObject r = Candidates.row("hut5_tall@v1", "stand@dev", "remove", 1, state(), opts, null, "a71b7f43", Candidates.now(), key(1, 2, 4));
		assertTrue(r.get("pick").isJsonNull());
		assertTrue(r.get("action_id").isJsonNull(), "no walk: no action");
		assertEquals(1, r.getAsJsonArray("options").size(), "no made-up option");
	}

	static JsonObject key(int x, int y, int z) {
		JsonObject o = new JsonObject();
		o.addProperty("x", x);
		o.addProperty("y", y);
		o.addProperty("z", z);
		return o;
	}

	@Test
	void theStateHashIsStableAndOrderFree() {
		Map<String, Object> a = state(), b = new LinkedHashMap<>();
		List<String> keys = new ArrayList<>(a.keySet());
		java.util.Collections.reverse(keys);
		for (String k : keys) b.put(k, a.get(k));
		String ha = Candidates.stateHash(Candidates.canonical(a)), hb = Candidates.stateHash(Candidates.canonical(b));
		assertEquals(ha, hb, "same state, other key order: same hash");
		assertTrue(ha.startsWith("sha256:") && ha.length() == 7 + 64);
		b.put("her", new int[] {29, 152, -41});
		assertNotEquals(ha, Candidates.stateHash(Candidates.canonical(b)));
	}
}
