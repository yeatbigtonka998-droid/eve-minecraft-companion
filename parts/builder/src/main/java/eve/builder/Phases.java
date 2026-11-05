package eve.builder;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.function.Function;

/**
 * CP-75 phased builds, the world-free half. A phase is a contiguous slice of the build order (never reordered: design's stands
 * assume its order), with its own item budget, cut only where the state after it is safe (every placed door keeps a level way
 * through on both sides). A run's progress lives in a JSON file (its phases, the phase it is in, the cells it placed and the
 * items it spent) so a later dispatch with the same run_id continues it exactly: re-places what went missing, reports what
 * someone else changed, and goes on with the phase it was in (cells already right are skipped, so nothing is placed twice).
 */
final class Phases {
	/** Steps [from, to) of the build order; budget = the items those steps place. */
	record Phase(int from, int to, Map<String, Integer> budget) {}

	/** The build steps (final and scaffold placements, design's order); removals and anything else are the last stage. */
	static List<Design.Step> buildSteps(List<Design.Step> order) {
		List<Design.Step> out = new ArrayList<>();
		for (Design.Step s : order)
			if (s.layer().equals("final") || s.layer().equals("scaffold")) out.add(s);
		return out;
	}

	/** Cut the build steps into phases of about maxSteps: each ends at the last safe cut within maxSteps, else at the first
	 *  safe one after (a phase may run long rather than stop where a door is shut); the last phase ends with the steps. */
	static List<Phase> plan(List<Design.Step> steps, int maxSteps) {
		maxSteps = Math.max(1, maxSteps);
		boolean[] safe = safeCuts(steps);
		List<Phase> out = new ArrayList<>();
		int from = 0, n = steps.size();
		while (from < n) {
			int to = -1;
			for (int c = Math.min(from + maxSteps, n); c > from; c--)
				if (c == n || safe[c]) {
					to = c;
					break;
				}
			if (to < 0) for (int c = from + maxSteps + 1; c <= n; c++)
				if (c == n || safe[c]) {
					to = c;
					break;
				}
			out.add(new Phase(from, to, budget(steps, from, to)));
			from = to;
		}
		return out;
	}

	static Map<String, Integer> budget(List<Design.Step> steps, int from, int to) {
		Map<String, Integer> b = new TreeMap<>();
		for (int i = from; i < to; i++) steps.get(i).items().forEach((k, v) -> b.merge(k, v, Integer::sum));
		return b;
	}

	/** safe[c]: after the first c build steps every placed door has a level way through (design-local, flat plot). */
	static boolean[] safeCuts(List<Design.Step> steps) {
		boolean[] safe = new boolean[steps.size() + 1];
		Map<Walk.P, String> cells = new HashMap<>();
		List<Walk.P> doors = new ArrayList<>();
		int[] box = box(steps);
		Walk w = walk(cells, box); // reads the live map
		safe[0] = true;
		for (int i = 0; i < steps.size(); i++) {
			Design.Step s = steps.get(i);
			Walk.P p = new Walk.P(s.x(), s.y(), s.z());
			cells.put(p, s.state());
			if (isDoor(s.state())) doors.add(p);
			safe[i + 1] = blockedDoors(doors, cells, w).isEmpty();
		}
		return safe;
	}

	/** Design-local box around the steps, 5 out (as Build's walk box) and 3 above. */
	static int[] box(List<Design.Step> steps) {
		int[] b = {0, 0, 0, 0, 0, 0};
		for (Design.Step s : steps) {
			b[0] = Math.min(b[0], s.x());
			b[1] = Math.min(b[1], s.y());
			b[2] = Math.min(b[2], s.z());
			b[3] = Math.max(b[3], s.x());
			b[4] = Math.max(b[4], s.y());
			b[5] = Math.max(b[5], s.z());
		}
		return new int[] {b[0] - 5, b[1] - 2, b[2] - 5, b[3] + 5, b[4] + 3, b[5] + 5};
	}

	static Walk walk(Map<Walk.P, String> cells, int[] box) {
		return new Walk(world(cells), box[0], box[1], box[2], box[3], box[4], box[5]);
	}

	/** Design-local cells on a flat plot whose top is y = -1 (the site as the harness and design lay it). */
	static Walk.World world(Map<Walk.P, String> cells) {
		return new Walk.World() {
			public boolean free(int x, int y, int z) {
				String s = cells.get(new Walk.P(x, y, z));
				return y >= 0 && (s == null || Plan.isAir(s) || isDoor(s) || s.contains("torch"));
			}

			public boolean floor(int x, int y, int z) {
				String s = cells.get(new Walk.P(x, y, z));
				if (s == null || Plan.isAir(s)) return y == -1;
				return !isDoor(s) && !s.contains("torch") && !s.contains("chest") && !s.contains("pane") && !s.contains("fence")
						&& !s.endsWith("_wall") && !s.contains("_wall[");
			}

			public boolean door(int x, int y, int z) {
				String s = cells.get(new Walk.P(x, y, z));
				return s != null && isDoor(s);
			}
		};
	}

	static boolean isDoor(String state) {
		String id = Plan.State.parse(state).id();
		return id.endsWith("_door");
	}

	static final Map<String, int[]> FACING = Map.of("north", new int[] {0, -1}, "south", new int[] {0, 1}, "west", new int[] {-1, 0}, "east", new int[] {1, 0});

	/** Placed doors (their lower half) whose level cell on either side is not standable: the way through is shut. */
	static List<Walk.P> blockedDoors(Iterable<Walk.P> candidates, Map<Walk.P, String> cells, Walk walk) {
		List<Walk.P> out = new ArrayList<>();
		for (Walk.P p : candidates) {
			String st = cells.get(p);
			if (st == null || !isDoor(st)) continue;
			Plan.State s = Plan.State.parse(st);
			int[] f = FACING.get(s.get("facing"));
			if (!"lower".equals(s.get("half")) || f == null) continue;
			if (!walk.standable(new Walk.P(p.x() + f[0], p.y(), p.z() + f[1])) || !walk.standable(new Walk.P(p.x() - f[0], p.y(), p.z() - f[1])))
				out.add(p);
		}
		return out;
	}

	/** What a resume finds among the steps a run had done: cells gone (re-place them first) and cells someone changed (left
	 *  alone: an owner's edit is kept, CP-76; reported as drift). classify(step) = match | missing | changed. */
	record Recon(List<Integer> redo, List<Integer> drift) {}

	/** The mind's build_cells for steps [from, to) (CP-75, agreed with mind): [x, y, z, placed_ts_ms_or_0, verified_0_1,
	 *  observed_or_null, scaffold_0_1] per step, world coordinates; observed is null when the cell is right (the mind takes
	 *  state_expected from the design), placed_ts 0 when this run did not place it. observed(step) = the cell's state now. */
	static JsonArray cellRows(List<Design.Step> steps, int from, int to, int ox, int oy, int oz, Function<Design.Step, String> observed,
			Function<Design.Step, Boolean> right, Map<String, Long> placedAt) {
		JsonArray rows = new JsonArray();
		for (int i = from; i < to; i++) {
			Design.Step s = steps.get(i);
			int x = ox + s.x(), y = oy + s.y(), z = oz + s.z();
			boolean ok = right.apply(s);
			JsonArray r = new JsonArray();
			r.add(x);
			r.add(y);
			r.add(z);
			r.add(placedAt.getOrDefault(Run.key(x, y, z), 0L));
			r.add(ok ? 1 : 0);
			if (ok) r.add(com.google.gson.JsonNull.INSTANCE);
			else r.add(observed.apply(s));
			r.add(s.layer().equals("scaffold") ? 1 : 0);
			rows.add(r);
		}
		return rows;
	}

	static Recon reconcile(List<Design.Step> steps, int done, Function<Design.Step, String> classify) {
		List<Integer> redo = new ArrayList<>(), drift = new ArrayList<>();
		for (int i = 0; i < done; i++)
			switch (classify.apply(steps.get(i))) {
				case "missing" -> redo.add(i);
				case "changed" -> drift.add(i);
				default -> {}
			}
		return new Recon(redo, drift);
	}

	/** The steps to do now, in order: those of earlier phases that went missing (re-placed first), then the current phase's
	 *  steps not yet right. classify(step) = match | missing | changed (in a done phase, changed = someone else's block: left
	 *  alone; in the phase in progress it may be her own half-done cell, so it is queued and the place op judges it). */
	static List<Integer> todo(List<Design.Step> steps, List<Phase> phases, int phase, Function<Design.Step, String> classify) {
		List<Integer> out = new ArrayList<>(reconcile(steps, phases.get(phase).from(), classify).redo());
		for (int i = phases.get(phase).from(); i < phases.get(phase).to(); i++) // the phase in progress: every step not yet right
			if (!classify.apply(steps.get(i)).equals("match")) out.add(i);
		return out;
	}

	/** pause.day_time: due from the target until half a day later (day time 0..23999; 0 is 06:00, so dusk 12000 is due until
	 *  dawn). A dispatch made just after the target (after gathering, say) still pauses tonight, and one made in the morning
	 *  builds until the target. */
	static boolean pauseDue(long nowDayTime, int target) {
		return Math.floorMod(nowDayTime - target, 24000L) < 12000;
	}

	/** A run's state (one JSON file per run_id, written whole through a temp file and a rename). */
	static final class Run {
		String runId, projectId, designHash, status = "running", pauseReason;
		int version, phase; // phase: the one in progress or next (its steps start at phases.get(phase).from())
		int[] origin;
		List<int[]> phases = new ArrayList<>(); // [from, to]
		final Map<String, String> placed = new LinkedHashMap<>(); // "x,y,z" (world) -> state this run placed there
		final Map<String, Long> placedAt = new TreeMap<>(); // "x,y,z" -> when (ms), for the mind's build_cells.placed_ts
		final Map<String, Integer> spent = new TreeMap<>();
		final Map<String, Integer> phaseMatched = new TreeMap<>(); // phase index -> final cells verified right at its end
		long updatedMs;

		static String key(int x, int y, int z) {
			return x + "," + y + "," + z;
		}

		JsonObject toJson() {
			JsonObject o = new JsonObject();
			o.addProperty("run_id", runId);
			o.addProperty("project_id", projectId);
			o.addProperty("version", version);
			o.addProperty("design_hash", designHash);
			JsonArray org = new JsonArray();
			for (int v : origin) org.add(v);
			o.add("origin", org);
			JsonArray ph = new JsonArray();
			for (int[] p : phases) {
				JsonArray a = new JsonArray();
				a.add(p[0]);
				a.add(p[1]);
				ph.add(a);
			}
			o.add("phases", ph);
			o.addProperty("phase", phase);
			o.addProperty("status", status);
			if (pauseReason != null) o.addProperty("pause_reason", pauseReason);
			JsonObject pl = new JsonObject();
			placed.forEach(pl::addProperty);
			o.add("placed", pl);
			JsonObject pa = new JsonObject();
			placedAt.forEach(pa::addProperty);
			o.add("placed_at", pa);
			JsonObject sp = new JsonObject();
			spent.forEach(sp::addProperty);
			o.add("spent", sp);
			JsonObject pm = new JsonObject();
			phaseMatched.forEach(pm::addProperty);
			o.add("phase_matched", pm);
			o.addProperty("updated_ms", updatedMs);
			return o;
		}

		static Run fromJson(JsonObject o) {
			Run r = new Run();
			r.runId = o.get("run_id").getAsString();
			r.projectId = o.get("project_id").getAsString();
			r.version = o.get("version").getAsInt();
			r.designHash = o.get("design_hash").getAsString();
			JsonArray org = o.getAsJsonArray("origin");
			r.origin = new int[] {org.get(0).getAsInt(), org.get(1).getAsInt(), org.get(2).getAsInt()};
			for (var e : o.getAsJsonArray("phases")) r.phases.add(new int[] {e.getAsJsonArray().get(0).getAsInt(), e.getAsJsonArray().get(1).getAsInt()});
			r.phase = o.get("phase").getAsInt();
			r.status = o.get("status").getAsString();
			r.pauseReason = o.has("pause_reason") ? o.get("pause_reason").getAsString() : null;
			o.getAsJsonObject("placed").entrySet().forEach(e -> r.placed.put(e.getKey(), e.getValue().getAsString()));
			if (o.has("placed_at")) o.getAsJsonObject("placed_at").entrySet().forEach(e -> r.placedAt.put(e.getKey(), e.getValue().getAsLong()));
			o.getAsJsonObject("spent").entrySet().forEach(e -> r.spent.put(e.getKey(), e.getValue().getAsInt()));
			if (o.has("phase_matched")) o.getAsJsonObject("phase_matched").entrySet().forEach(e -> r.phaseMatched.put(e.getKey(), e.getValue().getAsInt()));
			r.updatedMs = o.get("updated_ms").getAsLong();
			return r;
		}

		static Path file(Path dir, String runId) {
			return dir.resolve(runId + ".json");
		}

		void save(Path dir) throws IOException {
			Files.createDirectories(dir);
			Path f = file(dir, runId), tmp = dir.resolve(runId + ".json.tmp");
			Files.writeString(tmp, toJson().toString(), StandardCharsets.UTF_8);
			Files.move(tmp, f, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
		}

		/** The run's saved state, or null when there is none. */
		static Run load(Path dir, String runId) throws IOException {
			Path f = file(dir, runId);
			return Files.isRegularFile(f) ? fromJson(JsonParser.parseString(Files.readString(f, StandardCharsets.UTF_8)).getAsJsonObject()) : null;
		}
	}
}
