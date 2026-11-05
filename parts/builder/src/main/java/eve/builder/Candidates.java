package eve.builder;

import com.google.gson.JsonArray;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.security.MessageDigest;
import java.time.OffsetDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.UUID;

/**
 * O94f candidate-option logging (eve-learning's parts/learning/CONTRACTS.md, eve-candidates/1): one row per stand choice with
 * every stand considered, its cost or why it was rejected, the pick and a hash of the state the rule read, so a learned model
 * can later be compared with the deterministic pick. Never written by a safety path (pauses, leaving the site, stops).
 * Logging must never break a build: any failure is swallowed.
 */
final class Candidates {
	static final DateTimeFormatter TS = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss.SSSXXX");
	static final int TOP = 16;

	/** A stand considered: feasible with its cost (lower is better), or rejected with the reason. */
	record Opt(int x, int y, int z, Double cost, String rejectedBy) {
		String id() {
			return x + "," + y + "," + z;
		}
	}

	/** The row: feasible stands cheapest first (top 16), then every rejected one by id and reason; pick null when nothing was
	 *  feasible (the op gives up or tries another rule). stepKey = the target cell as build evidence names cells
	 *  ({x, y, z}, as in mismatches / manual), so each stand choice joins its own outcome on (action_id, step_key). */
	static JsonObject row(String objectiveId, String rule, String phase, long tick, Map<String, Object> state, List<Opt> opts, String pick, String actionId, String ts, JsonObject stepKey) {
		JsonObject r = new JsonObject();
		r.addProperty("schema", "eve-candidates/1");
		r.addProperty("ts", ts);
		r.addProperty("tick", tick);
		r.addProperty("objective_id", objectiveId);
		r.addProperty("runner", "build_schematic");
		r.addProperty("rule", rule);
		r.addProperty("phase", phase);
		r.addProperty("decision_type", "stand_choice");
		r.addProperty("decision_id", UUID.randomUUID().toString());
		r.add("step_key", stepKey);
		JsonObject st = canonical(state);
		r.addProperty("state_hash", stateHash(st));
		r.add("state", st);
		List<Opt> ok = new ArrayList<>(), no = new ArrayList<>();
		for (Opt o : opts) (o.rejectedBy() == null ? ok : no).add(o);
		ok.sort(Comparator.comparingDouble(Opt::cost));
		JsonArray options = new JsonArray();
		for (Opt o : ok.subList(0, Math.min(TOP, ok.size()))) options.add(option(o));
		for (Opt o : no) options.add(option(o));
		r.add("options", options);
		if (pick == null) r.add("pick", JsonNull.INSTANCE); // nothing feasible: no walk, so no action either
		else r.addProperty("pick", pick);
		if (pick == null || actionId == null) r.add("action_id", JsonNull.INSTANCE);
		else r.addProperty("action_id", actionId);
		r.addProperty("safety", false);
		return r;
	}

	static JsonObject option(Opt o) {
		JsonObject j = new JsonObject(), a = new JsonObject(), p = new JsonObject();
		j.addProperty("id", o.id());
		p.addProperty("x", o.x() + 0.5);
		p.addProperty("y", o.y());
		p.addProperty("z", o.z() + 0.5);
		a.addProperty("skill", "walk_to");
		a.add("params", p);
		j.add("action", a);
		if (o.cost() == null) j.add("score", JsonNull.INSTANCE);
		else j.addProperty("score", Math.round(o.cost() * 1000) / 1000.0);
		j.addProperty("score_kind", "cost");
		j.addProperty("feasible", o.rejectedBy() == null);
		if (o.rejectedBy() != null) j.addProperty("rejected_by", o.rejectedBy());
		return j;
	}

	/** Sorted keys at every level, so the same state always serialises (and hashes) the same. */
	static JsonObject canonical(Map<String, Object> state) {
		JsonObject o = new JsonObject();
		for (var e : new TreeMap<>(state).entrySet()) {
			Object v = e.getValue();
			if (v instanceof int[] a) {
				JsonArray arr = new JsonArray();
				for (int i : a) arr.add(i);
				o.add(e.getKey(), arr);
			} else if (v instanceof Number n) o.addProperty(e.getKey(), n);
			else if (v instanceof Boolean b) o.addProperty(e.getKey(), b);
			else o.addProperty(e.getKey(), v == null ? null : v.toString());
		}
		return o;
	}

	static String stateHash(JsonObject canonical) {
		try {
			return "sha256:" + HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(canonical.toString().getBytes(StandardCharsets.UTF_8)));
		} catch (Exception e) {
			throw new IllegalStateException(e);
		}
	}

	static String now() {
		return OffsetDateTime.now().format(TS);
	}

	/** Append one row; never throws (logging must not break a build). */
	static void append(Path file, JsonObject row) {
		try {
			Files.createDirectories(file.getParent());
			Files.writeString(file, row.toString() + "\n", StandardCharsets.UTF_8, StandardOpenOption.CREATE, StandardOpenOption.APPEND);
		} catch (Exception e) {
			EveBuilder.LOG.warn("eve-builder: candidates row not written: {}", e.toString());
		}
	}
}
