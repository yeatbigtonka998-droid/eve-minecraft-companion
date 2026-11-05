package eve.builder;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import eve.body.api.BodyServices;
import eve.body.api.Skill;
import eve.body.api.Step;
import eve.body.api.SubAction;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.inventory.ContainerInput;
import net.minecraft.world.entity.player.Input;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.level.block.state.properties.Property;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;

/**
 * build_schematic v1: design folder -> placed blocks, through the body's own primitives only (walk_to, place_block, mine_block,
 * use_item as sub-actions; hotbar swaps by clickSlot). The world is the progress record: every op first looks at its cell and
 * skips it when it is already right, so a resumed or re-dispatched build never places twice. Phases: build (build_order.json:
 * scaffold + final, in order) -> fix rounds (compare every final cell, break/re-place mismatches) -> scaffold removal -> verify.
 * The client's view of the world is what the skill acts on; the qualifying comparison is the server's (harness / query.blocks).
 */
final class Build implements Skill {
	enum Phase { BUILD, FIX, REMOVE, TIDY, VERIFY }

	/** kind: place | break; expect = the block a break may remove (null = whatever is there). */
	record Op(String kind, int x, int y, int z, String target, Map<String, Integer> items, String expect) {
		BlockPos pos() {
			return new BlockPos(x, y, z);
		}
	}

	final JsonObject params;
	Design d;
	int ox, oy, oz; // world position of design cell (0,0,0)
	Phase phase = Phase.BUILD;
	final ArrayDeque<Op> queue = new ArrayDeque<>();
	final List<Op> deferred = new ArrayList<>();
	final Set<BlockPos> deferredOnce = new HashSet<>();
	final Map<BlockPos, String> manual = new LinkedHashMap<>();
	final Map<String, Integer> shortItems = new TreeMap<>();
	final Set<BlockPos> touched = new HashSet<>();
	final Map<String, BlockPos> planned = new java.util.HashMap<>(); // kind+target -> design's planned standing spot (world)
	final Set<BlockPos> badSpots = new HashSet<>(); // standing spots Baritone could not reach
	final Set<BlockPos> opBad = new HashSet<>(); // spots from which v2 could not produce this op's state
	boolean moveOn;
	boolean plannedTried; // this op already walked to (or skipped) its planned spot // v2 said state_unreachable from here: the next try must come from another spot
	boolean v2 = true; // place_block v2 (registry 0.6.0+); a v1 body rejects `state` and the build goes on with v1
	BlockPos walkGoal;
	int fixRounds, maxFixRounds, already, placed, fixed, removed, total, opsDone, opsTotal;

	// the op in progress
	Op cur;
	int stage, attempts, waitTicks;
	Plan.Placement pl;
	Plan.Option opt;
	Plan.Dir face;
	int walks, slabWalks, afterSwap = 1;
	String lastChild; // the last sub-action's failure, for the manual note
	SubAction child;
	String childKind;

	final boolean repair; // repair_zone (params.steps) rather than build_schematic

	// CP-75 phased builds (params.run): the run's saved state, its build steps and phases, and when to pause
	Phases.Run run;
	List<Design.Step> bsteps;
	List<Phases.Phase> phases;
	Set<BlockPos> scope; // cells the fix rounds may touch: the phases done so far (null: all cells, the last phase or one-shot)
	int phasesHere; // phases finished in this dispatch
	int opsSinceSave;
	long lastSaveMs;
	String pauseCode = "paused", pauseNote; // a phase short of items ends the same safe way, with code no_item
	boolean dirty; // the run changed since it was last saved
	int[] wbox; // design-local walk box (walkBox), once d is known
	int entries; // enterSite walks this dispatch
	String pauseWhy;
	Step ending; // how next() ends the build early (a pause, a phase short of items)
	final JsonArray cellRows = new JsonArray(); // CP-75: build_cells rows of the phases finished in this dispatch (mind)
	static final int MAX_ROWS = 4000; // ~30 bytes a row: well under the link's 262 KB frame

	Build(JsonObject params) {
		this.params = params;
		repair = params.has("steps");
		maxFixRounds = params.has("max_fix_rounds") ? params.get("max_fix_rounds").getAsInt() : 2;
	}

	@Override
	public boolean resumable() {
		return true; // every op re-reads its cell first, so continuing after a reflex is safe
	}

	@Override
	public void resume(BodyServices b) {
		if (run != null && "stopped".equals(run.status)) { // the arbiter cancel()s on every reflex: the run goes on
			run.status = "running";
			dirty = true;
		}
		child = null;
		if (cur != null) queue.addFirst(cur);
		cur = null;
	}

	@Override
	public Step tick(BodyServices b) {
		if (b.ticks() == 0) {
			Step s = start(b);
			if (s != null) return s;
		}
		for (int guard = 0; guard < 64; guard++) { // finished/skipped ops cost no tick; a running sub-action returns its step
			if (child != null) {
				Step st = child.tick();
				if (!st.done()) return st;
				SubAction c = child;
				child = null;
				Step end = afterChild(b, c.result());
				if (end != null) return end;
				continue;
			}
			if (cur == null) {
				cur = next(b);
				if (cur == null) return ending != null ? ending : finish(b);
				Step stranded = strandGuard(b, "op_start"); // O94ah review #2: no walk starts from a stranded spot, so check here too
				if (stranded != null) return stranded;
				stage = attempts = waitTicks = walks = 0;
				lastChild = null;
				plannedTried = false;
				b.progress(opsTotal == 0 ? null : Math.min(99, 100 * opsDone / opsTotal), phase.name().toLowerCase() + " " + cur.kind + " " + cur.pos().toShortString());
				if (enterSite(b)) continue; // off the walk box: onto the site's ground first, then the op is judged on the model
			}
			Step s = switch (cur.kind) {
				case "place" -> place(b);
				case "use" -> useOp(b);
				case "till" -> tillOp(b);
				case "leave" -> leaveOp(b);
				default -> breakOp(b);
			};
			if (s != null) return s;
		}
		return Step.running();
	}

	// ---- start: folder, hash, gate, deficit ----

	Step start(BodyServices b) {
		JsonObject ev = b.evidence();
		if (repair) { // repair_zone: sense's steps as a sparse design, at their own world positions
			ev.addProperty("zone_id", params.get("zone_id").getAsString());
			ev.addProperty("snapshot_id", params.get("snapshot_id").getAsString());
			if (params.getAsJsonArray("steps").isEmpty() && (!params.has("replant") || params.getAsJsonArray("replant").isEmpty()))
				return report(b, 0, new JsonArray(), null, null); // nothing to restore
			d = Design.fromSteps(params.getAsJsonArray("steps"), params.has("replant") ? params.getAsJsonArray("replant") : new JsonArray());
			ox = d.grid.ox();
			oy = d.grid.oy();
			oz = d.grid.oz();
			return plan(b, ev);
		}
		String project = params.get("project_id").getAsString(), want = params.get("design_hash").getAsString();
		int version = params.get("version").getAsInt();
		ev.addProperty("design_hash", want);
		try {
			Path dir = Design.find(roots(), project, version);
			if (dir == null) return Step.failed("no_design", project + " v" + version + " not under " + roots() + " (config/eve-builder.json design_roots)");
			d = Design.load(dir);
		} catch (Exception e) {
			return Step.failed("no_design", "unreadable design folder: " + e);
		}
		if (!d.grid.hash().equals(want)) return Step.failed("design_hash_mismatch", "folder hash " + d.grid.hash() + " != approved " + want);
		if (d.gate != null) {
			boolean gateOn = approvalGate();
			ev.addProperty("approval_gate", gateOn);
			String refused = gateRefusal(d.gate, want, gateOn);
			if (refused != null) return Step.failed("gate_refused", refused);
			if (gateOn && "hold".equals(str(d.gate, "decision")) && !isOwner(d.gate)) { // O94q: a hold builds only on the owner's approval.json for this design
				List<String> why = Approval.problems(d.dir, want, String.valueOf(str(d.json, "project_id")),
						d.json.has("version") ? d.json.get("version").getAsInt() : -1);
				if (!why.isEmpty()) return Step.failed("gate_refused", "gate.json: hold; approval.json: " + String.join("; ", why));
				ev.addProperty("approval_id", str(Approval.read(d.dir.resolve("approval.json")), "approval_id"));
			}
		}
		JsonObject o = params.has("origin") ? params.getAsJsonObject("origin") : null;
		ox = o != null ? o.get("x").getAsInt() : d.grid.ox();
		oy = o != null ? o.get("y").getAsInt() : d.grid.oy();
		oz = o != null ? o.get("z").getAsInt() : d.grid.oz();
		JsonObject org = new JsonObject();
		org.addProperty("x", ox);
		org.addProperty("y", oy);
		org.addProperty("z", oz);
		ev.add("origin", org);
		if (params.has("run")) { // CP-75 (registry 0.16.0 delta: run, pause_at, phases)
			Step s = openRun(b, ev, project, version, want);
			if (s != null) return s;
		}
		return plan(b, ev);
	}

	static Path runsDir() {
		return FabricLoader.getInstance().getConfigDir().resolve("eve-builder").resolve("runs");
	}

	/** CP-75: the run's saved state (a new one on its first dispatch); a run_id saved for another design or site is refused. */
	Step openRun(BodyServices b, JsonObject ev, String project, int version, String hash) {
		String id = params.get("run").getAsString();
		ev.addProperty("run_id", id);
		if (!id.matches("[a-z0-9_-]{8,64}")) return Step.failed("bad_run", "run " + id + " is not [a-z0-9_-]{8,64} (it names a file)");
		try {
			run = Phases.Run.load(runsDir(), id);
		} catch (Exception e) {
			return Step.failed("bad_run", "run " + id + ": file unreadable: " + e);
		}
		if (run != null && (!run.designHash.equals(hash) || run.origin[0] != ox || run.origin[1] != oy || run.origin[2] != oz))
			return Step.failed("bad_run", "run " + id + " is for " + run.designHash.substring(0, 12) + " at " + java.util.Arrays.toString(run.origin) + ": start a new run");
		if (run == null) {
			run = new Phases.Run();
			run.runId = id;
			run.projectId = project;
			run.version = version;
			run.designHash = hash;
			run.origin = new int[] {ox, oy, oz};
		}
		run.status = "running";
		run.pauseReason = null;
		dirty = true;
		return null;
	}

	/** Deficit first (CP-51), then the queue: build_order (or sense's repair steps) in order. */
	Step plan(BodyServices b, JsonObject ev) {
		ClientLevel lv = b.minecraft().level;
		for (Design.Step s : d.steps)
			if (s.layer().equals("final")) total++;
		if (run != null) { // CP-75: one phase at a time, in design's order
			for (Design.Step s : d.steps)
				if (s.stand() != null)
					planned.put((s.layer().equals("scaffold_removal") ? "break " : "place ") + new BlockPos(ox + s.x(), oy + s.y(), oz + s.z()),
							new BlockPos(ox + s.stand()[0], oy + s.stand()[1], oz + s.stand()[2]));
			bsteps = Phases.buildSteps(planned.isEmpty() ? Plan.lookFirst(d.steps) : d.steps);
			int max = 256; // ponytail: one phase size for every build; a param when a build needs another (planner budget)
			if (run.phases.isEmpty()) // fixed on the first dispatch: a resume keeps the same cuts
				for (Phases.Phase p : Phases.plan(bsteps, max)) run.phases.add(new int[] {p.from(), p.to()});
			phases = new ArrayList<>();
			for (int[] p : run.phases) phases.add(new Phases.Phase(p[0], p[1], Phases.budget(bsteps, p[0], p[1])));
			opsTotal = bsteps.size() + (int) d.steps.stream().filter(s -> s.layer().equals("scaffold_removal")).count();
			if (run.phase >= phases.size()) { // build steps all done (removal begun or finished, or none): fix rounds, removal,
				scope = null;                 // verify; never the scaffold again
				return null;
			}
			return planPhase(b, ev);
		}
		Map<String, Integer> need = new TreeMap<>();
		boolean hoe = false;
		for (Design.Step s : d.steps) {
			if (s.layer().equals("scaffold_removal") || s.layer().equals("remove")) continue;
			if (Plan.isFarmland(s.state())) hoe = true;
			BlockPos p = new BlockPos(ox + s.x(), oy + s.y(), oz + s.z());
			Plan.stillNeeded(s.state(), canonical(lv.getBlockState(p)), s.items()).forEach((k, v) -> need.merge(k, v, Integer::sum));
		}
		Map<String, Integer> inv = b.inventoryCounts();
		if (hoe && hoeSlot(b.player()) < 0 && inv.keySet().stream().noneMatch(k -> k.endsWith("_hoe")))
			need.put("minecraft:wooden_hoe", 1); // any hoe tills; the mind may supply any
		JsonObject deficit = deficit(need, inv);
		ev.add("deficit", deficit);
		if (!deficit.getAsJsonObject("missing").isEmpty())
			return Step.failed("no_item", "supplies short for the cells not yet right: " + deficit.getAsJsonObject("missing"));
		for (Design.Step s : d.steps)
			if (s.stand() != null)
				planned.put((s.layer().equals("scaffold_removal") ? "break " : "place ") + new BlockPos(ox + s.x(), oy + s.y(), oz + s.z()),
						new BlockPos(ox + s.stand()[0], oy + s.stand()[1], oz + s.stand()[2]));
		for (Design.Step s : planned.isEmpty() && !repair ? Plan.lookFirst(d.steps) : d.steps) { // planned/sense's order is kept
			BlockPos w = new BlockPos(ox + s.x(), oy + s.y(), oz + s.z());
			if (s.layer().equals("remove")) {
				// trampled farmland comes back as dirt -> farmland: keep the dirt and till it instead of breaking it
				if (Plan.tillable(Plan.State.parse(s.state()).id()) && Plan.isFarmland(d.grid.state(s.x(), s.y(), s.z()))) continue;
				queue.add(new Op("break", w.getX(), w.getY(), w.getZ(), Design.AIR, Map.of(), Plan.State.parse(s.state()).id()));
			} else if (!s.layer().equals("scaffold_removal"))
				queue.add(new Op("place", w.getX(), w.getY(), w.getZ(), s.state(), s.items(), null));
		}
		opsTotal = queue.size() + (int) d.steps.stream().filter(s -> s.layer().equals("scaffold_removal")).count();
		return null;
	}

	/** CP-75: a step's cell now: match (right, or only derived properties differ), missing (air), changed (anything else). */
	String classifyStep(ClientLevel lv, Design.Step s) {
		String c = Plan.classify(s.state(), canonical(lv.getBlockState(new BlockPos(ox + s.x(), oy + s.y(), oz + s.z()))));
		return ok(c) ? "match" : c.equals("missing") ? "missing" : "changed";
	}

	/** CP-75: queue the current phase (cells of done phases that went missing first), after checking its items. */
	Step planPhase(BodyServices b, JsonObject ev) {
		ClientLevel lv = b.minecraft().level;
		Phases.Phase ph = phases.get(run.phase);
		if (phasesHere == 0) { // a resume: what changed since the run last stood here
			JsonArray drift = new JsonArray();
			for (int i : Phases.reconcile(bsteps, ph.from(), s -> classifyStep(lv, s)).drift()) {
				Design.Step s = bsteps.get(i);
				BlockPos p = new BlockPos(ox + s.x(), oy + s.y(), oz + s.z());
				if (drift.size() < 50) drift.add(pos(p));
				// not what this run left there: someone else's block, kept (never broken by the fix rounds); her own wrong
				// block (the same state she placed) is fixed as usual
				if (!canonical(lv.getBlockState(p)).equals(run.placed.get(Phases.Run.key(p.getX(), p.getY(), p.getZ()))))
					manual.put(p, "changed since this run placed it: kept (an edit by someone else, CP-76)");
			}
			ev.add("drift", drift);
		}
		List<Integer> todo = Phases.todo(bsteps, phases, run.phase, s -> classifyStep(lv, s));
		Map<String, Integer> need = new TreeMap<>();
		boolean hoe = false;
		for (int i : todo) {
			Design.Step s = bsteps.get(i);
			if (Plan.isFarmland(s.state())) hoe = true;
			Plan.stillNeeded(s.state(), canonical(lv.getBlockState(new BlockPos(ox + s.x(), oy + s.y(), oz + s.z()))), s.items())
					.forEach((k, v) -> need.merge(k, v, Integer::sum));
		}
		Map<String, Integer> inv = b.inventoryCounts();
		if (hoe && hoeSlot(b.player()) < 0 && inv.keySet().stream().noneMatch(k -> k.endsWith("_hoe"))) need.put("minecraft:wooden_hoe", 1);
		JsonObject deficit = deficit(need, inv);
		ev.add("deficit", deficit);
		if (!deficit.getAsJsonObject("missing").isEmpty()) {
			pauseWhy = "no_item";
			pauseCode = "no_item";
			pauseNote = "phase " + (run.phase + 1) + " of " + phases.size() + " short: " + deficit.getAsJsonObject("missing");
			BlockPos off = phasesHere > 0 ? offSite(b) : null; // she built this dispatch: off the site first, as for a pause
			if (off != null) {
				queue.add(new Op("leave", off.getX(), off.getY(), off.getZ(), null, Map.of(), null));
				return null;
			}
			return pause(b);
		}
		for (int i : todo) {
			Design.Step s = bsteps.get(i);
			queue.add(new Op("place", ox + s.x(), oy + s.y(), oz + s.z(), s.state(), s.items(), null));
		}
		if (run.phase == phases.size() - 1) scope = null; // the last phase: the whole design is judged, as a one-shot build
		else {
			scope = new HashSet<>();
			for (int i = 0; i < ph.to(); i++) scope.add(new BlockPos(ox + bsteps.get(i).x(), oy + bsteps.get(i).y(), oz + bsteps.get(i).z()));
		}
		return null;
	}

	/** CP-75: a phase is built and its fix rounds are done. Record it, then pause here if asked and safe, else the next phase. */
	Step phaseDone(BodyServices b) {
		ClientLevel lv = b.minecraft().level;
		Phases.Phase ph = phases.get(run.phase);
		int matched = 0;
		for (int i = ph.from(); i < ph.to(); i++)
			if (bsteps.get(i).layer().equals("final") && classifyStep(lv, bsteps.get(i)).equals("match")) matched++;
		run.phaseMatched.put(String.valueOf(run.phase), matched);
		addRows(Phases.cellRows(bsteps, ph.from(), ph.to(), ox, oy, oz, s -> canonical(lv.getBlockState(new BlockPos(ox + s.x(), oy + s.y(), oz + s.z()))),
				s -> classifyStep(lv, s).equals("match"), run.placedAt));
		run.phase++;
		phasesHere++;
		dirty = true;
		saveRun();
		String why = pauseDue(b);
		if (why != null) {
			JsonObject safe = safety(b);
			if (safe.get("doors_clear").getAsBoolean() && safe.get("site_exit").getAsBoolean()) {
				pauseWhy = why;
				BlockPos off = offSite(b);
				if (off == null) return pause(b);
				queue.add(new Op("leave", off.getX(), off.getY(), off.getZ(), null, Map.of(), null)); // walk off the site, then pause
				return null;
			}
			EveBuilder.LOG.info("eve-builder: pause ({}) not safe after phase {}: {}; building on to the next boundary", why, run.phase, safe);
		}
		return planPhase(b, b.evidence());
	}

	/** phases: this dispatch has done that many; pause_at: the world's day time is in its window (Phases.pauseDue). */
	String pauseDue(BodyServices b) {
		if (params.has("phases") && phasesHere >= params.get("phases").getAsInt()) return "phases";
		if (params.has("pause_at") && Phases.pauseDue(Math.floorMod(b.minecraft().level.getOverworldClockTime(), 24000L), params.get("pause_at").getAsInt()))
			return "day_time";
		return null;
	}

	boolean outside(Walk.P q) {
		Design.Grid g = d.grid;
		return q.x() < ox || q.x() >= ox + g.sx() || q.z() < oz || q.z() >= oz + g.sz();
	}

	/** CP-75 boundary checks on the live world: every placed door of the design keeps a level way through on both sides; from
	 *  where she stands she can walk down to the ground and off the footprint. */
	JsonObject safety(BodyServices b) {
		ClientLevel lv = b.minecraft().level;
		Walk w = walk(lv);
		Map<Walk.P, String> doors = new HashMap<>();
		Design.Grid g = d.grid;
		for (int y = 0; y < g.sy(); y++)
			for (int z = 0; z < g.sz(); z++)
				for (int x = 0; x < g.sx(); x++)
					if (Phases.isDoor(g.state(x, y, z)) && lv.getBlockState(new BlockPos(ox + x, oy + y, oz + z)).getBlock() instanceof net.minecraft.world.level.block.DoorBlock)
						doors.put(new Walk.P(ox + x, oy + y, oz + z), g.state(x, y, z));
		List<Walk.P> shut = Phases.blockedDoors(doors.keySet(), doors, w);
		Walk.P here = walkSpot(w, b.player());
		JsonObject o = new JsonObject();
		o.addProperty("doors_clear", shut.isEmpty());
		o.addProperty("route_down", here != null && w.reaches(here, this::ground));
		o.addProperty("site_exit", here != null && w.reaches(here, q -> ground(q) && outside(q)));
		JsonArray sh = new JsonArray();
		for (Walk.P p : shut) sh.add(pos(new BlockPos(p.x(), p.y(), p.z())));
		o.add("doors_shut", sh);
		Walk.P off = here == null ? null : ground(here) && outside(here) ? here : w.find(here, q -> ground(q) && outside(q));
		o.add("exit_to", off == null ? com.google.gson.JsonNull.INSTANCE : pos(new BlockPos(off.x(), off.y(), off.z())));
		return o;
	}

	void addRows(JsonArray rows) {
		for (var r : rows)
			if (cellRows.size() < MAX_ROWS) cellRows.add(r);
	}

	/** The nearest ground cell off the footprint she can walk to, or null when she already stands on one. */
	BlockPos offSite(BodyServices b) {
		Walk w = walk(b.minecraft().level);
		Walk.P here = walkSpot(w, b.player());
		if (here == null || ground(here) && outside(here)) return null;
		Walk.P q = w.find(here, c -> ground(c) && outside(c));
		return q == null ? null : new BlockPos(q.x(), q.y(), q.z());
	}

	Step leaveOp(BodyServices b) {
		if (stage == 0) {
			stage = 1;
			return walk(b, cur.pos());
		}
		return pause(b); // wherever the walk ended: the evidence says what holds there
	}

	/** CP-75: end at a phase boundary, resumable with the same run_id: failed{paused} with the run's state. */
	Step pause(BodyServices b) {
		ClientLevel lv = b.minecraft().level;
		JsonObject ev = b.evidence();
		ev.addProperty("pause_reason", pauseWhy);
		ev.add("safety", safety(b));
		Map<String, Integer> rest = new TreeMap<>(), next = new TreeMap<>();
		Phases.Phase ph = phases.get(run.phase);
		for (int i = ph.from(); i < bsteps.size(); i++) {
			Design.Step s = bsteps.get(i);
			Map<String, Integer> n = Plan.stillNeeded(s.state(), canonical(lv.getBlockState(new BlockPos(ox + s.x(), oy + s.y(), oz + s.z()))), s.items());
			n.forEach((k, v) -> rest.merge(k, v, Integer::sum));
			if (i < ph.to()) n.forEach((k, v) -> next.merge(k, v, Integer::sum));
		}
		JsonObject r = new JsonObject();
		rest.forEach(r::addProperty);
		ev.add("remaining", r);
		ev.add("next_phase_deficit", deficit(next, b.inventoryCounts()).getAsJsonObject("missing"));
		run.status = "paused";
		run.pauseReason = pauseWhy;
		dirty = true;
		saveRun();
		return report(b, null, null, pauseCode, pauseNote != null ? pauseNote : "paused (" + pauseWhy + ") before phase " + (run.phase + 1) + " of " + phases.size());
	}

	void runEvidence(BodyServices b) {
		JsonObject ev = b.evidence();
		ev.addProperty("run_id", run.runId);
		ev.addProperty("phase", run.phase);
		ev.addProperty("phases_total", phases == null ? 0 : phases.size());
		if (phases != null && run.phase < phases.size()) ev.addProperty("next_step", phases.get(run.phase).from());
	}

	/** The run's file, when it changed (each placement and removal, the phase, the end); a failed write is logged, not fatal:
	 *  the world still holds every placed block and a resume skips them. */
	void saveRun() {
		saveRun(true);
	}

	/** force false (after an op): at most every 32 ops or 5 s, so a big run's file is not rewritten on every placement; a
	 *  client crash loses at most that much of the ledger (accounting only: the world keeps the blocks). */
	void saveRun(boolean force) {
		if (run == null || !dirty) return;
		long now = System.currentTimeMillis();
		if (!force && opsSinceSave < 32 && now - lastSaveMs < 5000) return;
		run.updatedMs = now;
		try {
			run.save(runsDir());
			dirty = false;
			opsSinceSave = 0;
			lastSaveMs = now;
		} catch (Exception e) {
			EveBuilder.LOG.warn("eve-builder: run {} not saved: {}", run.runId, e.toString());
		}
	}

	void ledger(BlockPos p, String state, Map<String, Integer> items, boolean gone) {
		if (run == null) return;
		String k = Phases.Run.key(p.getX(), p.getY(), p.getZ());
		if (gone) {
			run.placedAt.remove(k);
			String was = run.placed.remove(k);
			if (was != null) for (String i : itemsFor(was).keySet()) run.spent.merge(i, -1, Integer::sum); // picked up again
		} else {
			run.placed.put(k, state);
			run.placedAt.putIfAbsent(k, System.currentTimeMillis()); // the first placement (a double slab's second click keeps it)
			items.forEach((i, n) -> run.spent.merge(i, n, Integer::sum));
		}
		dirty = true;
	}

	@Override
	public void cancel(BodyServices b) {
		if (run != null && "running".equals(run.status)) {
			run.status = "stopped"; // a hard stop or cancel: a re-dispatch with the run_id reconciles and goes on
			dirty = true;
			saveRun();
		}
	}

	/**
	 * CP-51: {want, inventory, missing}. want = items for every build step (final + scaffold) whose cell is not yet right (a
	 * wrong-state block is reused, a single slab needs one more: Plan.stillNeeded); inventory = what Eve holds of those
	 * items; missing = want - inventory. The mind nets `missing` against its live storage index and the recipe solver
	 * before it plans gathering with eve-skills (design's note: the BOM's own deficit is only a design-time estimate).
	 */
	static JsonObject deficit(Map<String, Integer> want, Map<String, Integer> inv) {
		JsonObject w = new JsonObject(), have = new JsonObject(), miss = new JsonObject(), out = new JsonObject();
		want.forEach((k, v) -> {
			int h = inv.getOrDefault(k, 0);
			w.addProperty(k, v);
			have.addProperty(k, Math.min(h, v));
			if (h < v) miss.addProperty(k, v - h);
		});
		out.add("want", w);
		out.add("inventory", have);
		out.add("missing", miss);
		return out;
	}

	/**
	 * O94q-flag: config/eve-builder.json "approval_gate", default false. On: a gate hold builds only on the owner's §8
	 * approval.json for this design (O94q). Off: a hold is refused, the pre-O94q rule the live jar has, because the mind's
	 * approval.json is not in §8 form until its §8 writer lands after the 0.16.0 install; the install turns it on then.
	 */
	static boolean approvalGate() {
		try {
			Path cfg = FabricLoader.getInstance().getConfigDir().resolve("eve-builder.json");
			if (!Files.isRegularFile(cfg)) return false;
			JsonObject c = Design.read(cfg);
			return c.has("approval_gate") && c.get("approval_gate").getAsBoolean();
		} catch (Exception e) {
			EveBuilder.LOG.warn("eve-builder: config/eve-builder.json approval_gate unreadable, off: {}", e.toString());
			return false;
		}
	}

	/** Owner-provided schematics build as given (S16; O61: CP-50's hut is category "schematic", like pillar_tall). */
	static boolean isOwner(JsonObject gate) {
		return java.util.Set.of("owner_supplied", "schematic").contains(String.valueOf(str(gate, "category")));
	}

	/** gate.json's verdict on this dispatch before any approval is read, or null when it may go on: the hash must match;
	 *  owner schematics and "build" pass; a "hold" passes only with approval_gate on (then approval.json decides). */
	static String gateRefusal(JsonObject gate, String want, boolean approvalGate) {
		String dec = str(gate, "decision");
		if (want.equals(str(gate, "design_hash")) && (isOwner(gate) || "build".equals(dec) || (approvalGate && "hold".equals(dec)))) return null;
		return "gate.json: decision " + dec + ", hash " + str(gate, "design_hash")
				+ ("hold".equals(dec) && !approvalGate ? " (approval_gate off: a hold is refused, as before O94q)" : "");
	}

	static List<Path> roots() {
		List<Path> out = new ArrayList<>();
		try {
			Path cfg = FabricLoader.getInstance().getConfigDir().resolve("eve-builder.json");
			if (Files.isRegularFile(cfg))
				for (var e : Design.read(cfg).getAsJsonArray("design_roots")) out.add(Path.of(e.getAsString()));
		} catch (Exception e) {
			EveBuilder.LOG.warn("eve-builder: config/eve-builder.json unreadable: {}", e.toString());
		}
		return out;
	}

	static String str(JsonObject o, String k) {
		return o.has(k) && !o.get(k).isJsonNull() ? o.get(k).getAsString() : null;
	}

	// ---- phases ----

	Op next(BodyServices b) {
		while (true) {
			if (!queue.isEmpty()) return queue.poll();
			if (!deferred.isEmpty()) { // supports that came later in the order; one more try each
				queue.addAll(deferred);
				deferred.clear();
				continue;
			}
			switch (phase) {
				case BUILD, FIX -> {
					if (fixRounds < maxFixRounds && planFixes(b) > 0) {
						phase = Phase.FIX;
						fixRounds++;
						continue;
					}
					if (run != null && run.phase < phases.size() - 1) { // CP-75: a phase is done: the next one, or a pause
						ending = phaseDone(b);
						if (ending != null) return null;
						phase = Phase.BUILD;
						fixRounds = 0;
						continue;
					}
					phase = Phase.REMOVE;
					if (run != null) {
						run.phase = phases.size();
						dirty = true;
						saveRun();
					}
					for (Design.Step s : d.steps)
						if (s.layer().equals("scaffold_removal"))
							queue.add(new Op("break", ox + s.x(), oy + s.y(), oz + s.z(), Design.AIR, Map.of(), d.scaffoldAt(s.x(), s.y(), s.z()).split("\\[")[0]));
				}
				case REMOVE -> { // Baritone opens doors it walks through: put every door/trapdoor back to the design's `open`
					phase = Phase.TIDY;
					ClientLevel lv = b.minecraft().level;
					Design.Grid g = d.grid;
					for (int y = 0; y < g.sy(); y++)
						for (int z = 0; z < g.sz(); z++)
							for (int x = 0; x < g.sx(); x++) {
								BlockPos p = new BlockPos(ox + x, oy + y, oz + z);
								String want = g.state(x, y, z);
								if (!want.equals(Design.ANY) && !secondHalf(want) && Plan.onlyOpenDiffers(want, canonical(lv.getBlockState(p))))
									queue.add(new Op("use", p.getX(), p.getY(), p.getZ(), want, Map.of(), null));
							}
					opsTotal += queue.size();
				}
				case TIDY -> phase = Phase.VERIFY;
				case VERIFY -> {
					return null;
				}
			}
		}
	}

	/** Design-local cells a scaffold_removal row breaks. FIX leaves them to REMOVE (design: house7's doorstep (3,1,7) is broken
	 *  at its own row from the ground; FIX took it from under her last stand (3,2,7), a 1-block drop). */
	static java.util.Set<Walk.P> removalCells(List<Design.Step> steps) {
		java.util.Set<Walk.P> out = new java.util.HashSet<>();
		for (Design.Step s : steps) if (s.layer().equals("scaffold_removal")) out.add(new Walk.P(s.x(), s.y(), s.z()));
		return out;
	}

	/** Queues break/place ops for every final cell that is wrong (manual cells and derived-only differences excluded). */
	int planFixes(BodyServices b) {
		ClientLevel lv = b.minecraft().level;
		int n = 0;
		Design.Grid g = d.grid;
		java.util.Set<Walk.P> removal = removalCells(d.steps);
		for (int y = 0; y < g.sy(); y++)
			for (int z = 0; z < g.sz(); z++)
				for (int x = 0; x < g.sx(); x++) {
					BlockPos p = new BlockPos(ox + x, oy + y, oz + z);
					String want = g.state(x, y, z);
					if (want.equals(Design.ANY) || manual.containsKey(p) || secondHalf(want)) continue;
					if (Plan.isAir(want) && removal.contains(new Walk.P(x, y, z))) continue; // its scaffold_removal row breaks it, from its stand
					// CP-75, a phase that is not the last: only cells of the phases done so far that the design fills (a cell the
					// design leaves air may hold scaffold a later phase stands on: anything extra waits for the last phase)
					if (scope != null && (Plan.isAir(want) || !scope.contains(p))) continue;
					String c = Plan.classify(want, canonical(lv.getBlockState(p)));
					Map<String, Integer> items = itemsFor(want);
					switch (c) {
						case "missing" -> queue.add(new Op("place", p.getX(), p.getY(), p.getZ(), want, items, null));
						case "wrong_block", "wrong_state" -> {
							if (Plan.onlyOpenDiffers(want, canonical(lv.getBlockState(p)))) {
								queue.add(new Op("use", p.getX(), p.getY(), p.getZ(), want, items, null));
								break;
							}
							Plan.State w_ = Plan.State.parse(want), h_ = Plan.State.parse(canonical(lv.getBlockState(p)));
							if (v2 && "double".equals(w_.get("type")) && h_.id().equals(w_.id())) { // single slab: second click, no break
								queue.add(new Op("place", p.getX(), p.getY(), p.getZ(), want, items, null));
								break;
							}
							if (Plan.isFarmland(want) && Plan.tillable(Plan.State.parse(canonical(lv.getBlockState(p))).id())) {
								queue.add(new Op("till", p.getX(), p.getY(), p.getZ(), want, Map.of(), null)); // dried back to dirt: re-till, no break
								break;
							}
							queue.add(new Op("break", p.getX(), p.getY(), p.getZ(), Design.AIR, Map.of(), null));
							queue.add(new Op("place", p.getX(), p.getY(), p.getZ(), want, items, null));
						}
						case "extra" -> queue.add(new Op("break", p.getX(), p.getY(), p.getZ(), Design.AIR, Map.of(), null));
						default -> {
							continue;
						}
					}
					n++;
				}
		opsTotal += queue.size();
		return n;
	}

	Map<String, Integer> itemsFor(String state) {
		for (Design.Step s : d.steps) if (s.state().equals(state) && !s.items().isEmpty()) return s.items();
		return Map.of(Plan.State.parse(state).id(), 1);
	}

	static boolean secondHalf(String state) {
		Plan.State s = Plan.State.parse(state);
		return "upper".equals(s.get("half")) && s.get("hinge") != null || "head".equals(s.get("part"));
	}

	static boolean ok(String cls) {
		return cls.equals("match") || cls.equals("derived_mismatch");
	}

	// ---- place op ----

	Step place(BodyServices b) {
		LocalPlayer p = b.player();
		ClientLevel lv = b.minecraft().level;
		BlockPos pos = cur.pos();
		switch (stage) {
			case 0 -> {
				String now = canonical(lv.getBlockState(pos));
				if (ok(Plan.classify(cur.target, now))) {
					if (!touched.contains(pos) && phase == Phase.BUILD) already++;
					return endOp();
				}
				if (Plan.isFarmland(cur.target) && Plan.tillable(Plan.State.parse(now).id())) { // dirt already there: just till it
					queue.addFirst(new Op("till", pos.getX(), pos.getY(), pos.getZ(), cur.target, Map.of(), null));
					return endOp();
				}
				pl = Plan.of(cur.target, cur.items, v2);
				Plan.State want = Plan.State.parse(cur.target), have = Plan.State.parse(now);
				boolean secondSlab = v2 && "double".equals(want.get("type")) && have.id().equals(want.id()) && !"double".equals(have.get("type"));
				if (secondSlab) { // a deferred second slab (retried once a neighbour gives a click into the upper half)
					afterSwap = 5;
					stage = 5;
					if (b.hotbarSlot(pl.item()) < 0) {
						int inv = invSlot(p, pl.item());
						if (inv < 0) return endOp();
						b.clickSlot(inv, 8, ContainerInput.SWAP);
						waitTicks = 0;
						stage = 10;
					}
					return null;
				}
				afterSwap = 1;
				if (!lv.getBlockState(pos).canBeReplaced()) return endOp(); // a wrong block: the fix rounds break and re-place it
				opt = null;
				for (Plan.Option o : pl.options()) if (support(lv, pos, o.face()) != null) { opt = o; break; }
				if (opt == null) {
					if (deferredOnce.add(pos)) deferred.add(cur);
					else manual.put(pos, "no support block to click for " + cur.target);
					return endOp();
				}
				if (b.inventoryCounts().getOrDefault(pl.item(), 0) <= 0) {
					shortItems.merge(pl.item(), 1, Integer::sum);
					manual.put(pos, "no " + pl.item() + " in the inventory");
					return endOp();
				}
				stage = 1;
				if (b.hotbarSlot(pl.item()) < 0) {
					int inv = invSlot(p, pl.item());
					if (inv < 0) return endOp();
					b.clickSlot(inv, 8, ContainerInput.SWAP); // main inventory slot (menu index = inventory index 9..35) <-> hotbar 9
					waitTicks = 0;
					stage = 10;
				}
				return null;
			}
			case 10 -> { // wait for the swap's slot sync
				if (b.hotbarSlot(pl.item()) >= 0) stage = afterSwap;
				else if (++waitTicks > 20) {
					manual.put(pos, "could not move " + pl.item() + " to the hotbar");
					return endOp();
				}
				return Step.running();
			}
			case 1 -> { // pick option, face and standing spot: the eye must see the clicked face (else place_block ends obstructed)
				if (goPlanned(b, "place ", pos)) return null; // design's planned spot first; the checks below judge it from there
				boolean inside = p.getBoundingBox().intersects(new net.minecraft.world.phys.AABB(pos));
				// O89f: a click with design's aim margin from here, else a spot that has one; only when neither exists, the
				// clear ray alone from here, else a spot by that rule (a30/a31: design's stand had ~1.6 deg to spare and the
				// crosshair landed on the support stair's front; other clicks with less margin passed live, so the old rule
				// stays the fallback)
				if (walks > 0) opBad.add(spot(p)); // walked there and it still does not work: pick another spot
				boolean seals = !repair && strands(lv, p, pos); // O94ah re-review F2: never place her only walk down shut
				for (boolean steady : new boolean[] {true, false}) {
					for (Plan.Option o : pl.options())
						for (Plan.Dir f : faces(o)) {
							BlockPos sup = support(lv, pos, f);
							if (sup == null) continue;
							if (!inside && !moveOn && !seals && clickable(lv, p, p.position(), pos, sup, f, o.look(), EYE_SNEAK, halfOf(cur.target), steady)) {
								EveBuilder.LOG.info("eve-builder: place {} at {} clicking {} face {} from {}{}", pl.item(), pos.toShortString(), sup.toShortString(), f, p.position(), steady ? "" : " (no spot keeps the aim margin)");
								opt = o;
								face = f;
								stage = 2;
								return null;
							}
						}
					if (walks >= 2 || (steady && walks > 0)) continue; // one walk for the margin at most
					for (Plan.Option o : pl.options())
						for (Plan.Dir f : faces(o)) {
							BlockPos sup = support(lv, pos, f);
							if (sup == null) continue;
							BlockPos feet = stand(b, pos, sup, f, o.look(), EYE_SNEAK, steady);
							if (feet != null) {
								EveBuilder.LOG.info("eve-builder: walk to {} to click {} face {} for {} (walk {}{})", feet.toShortString(), sup.toShortString(), f, pos.toShortString(), walks + 1, steady ? "" : ", no spot keeps the aim margin");
								walks++;
								return walk(b, feet); // then stage 1 again, judged from where she really stands
							}
						}
				}
				return giveUp(pos, walks >= 2 ? "no spot found from which the face to click is visible and in reach" : "no standing spot sees a support face of it");
			}
			case 2 -> {
				if (crouchFirst(p)) return Step.running(Input.EMPTY, true);
				JsonObject pp = new JsonObject();
				pp.add("pos", pos(pos));
				pp.addProperty("item", pl.item());
				// v2 checks the state against the item's own block: a torch on a wall (wall_torch) is a v1 face click
				Map<String, String> want = v2 && Plan.State.parse(cur.target).id().equals(pl.item()) ? Plan.v2State(cur.target) : Map.of();
				if (want.isEmpty()) pp.addProperty("face", Direction.byName(face.name()).getName());
				else { // v2: the body picks face and hit point from where she stands; the spot was chosen for look and sight
					JsonObject st = new JsonObject();
					want.forEach(st::addProperty);
					pp.add("state", st);
				}
				childKind = "place";
				child = b.run("place_block", pp);
				stage = 3;
				return null;
			}
			case 5 -> { // v2 second click: a single slab into a double
				if (crouchFirst(p)) return Step.running(Input.EMPTY, true);
				JsonObject pp = new JsonObject(), st = new JsonObject();
				pp.add("pos", pos(pos));
				pp.addProperty("item", pl.item());
				st.addProperty("type", "double");
				pp.add("state", st);
				childKind = "place";
				child = b.run("place_block", pp);
				stage = 6;
				return null;
			}
			case 6 -> {
				String now = canonical(lv.getBlockState(pos));
				String type = Plan.State.parse(now).get("type");
				if (!"double".equals(type)) {
					// the spot for the first slab may not see the face the second click needs (CP-50 a11-a13: the roof's corner
					// double slabs, state_unreachable from there): walk once to a spot that can click that face, then retry
					if (slabWalks++ == 0) {
						Plan.Dir f2 = "top".equals(type) ? Plan.Dir.down : Plan.Dir.up;
						BlockPos feet = stand(b, pos, pos, f2, null, EYE_SNEAK);
						if (feet == null) feet = stand(b, pos, pos, f2, null, EYE_SNEAK, false);
						if (feet != null) {
							EveBuilder.LOG.info("eve-builder: second slab at {}: walk to {} and click again", pos.toShortString(), feet.toShortString());
							stage = 5;
							return walk(b, feet);
						}
					}
					return giveUp(pos, "second slab not placed");
				}
				ledger(pos, cur.target, Map.of(pl.item(), 1), false);
				return endOp();
			}
			case 3 -> { // after place_block (see afterChild): open it if the design wants it open
				touched.add(pos);
				String now = canonical(lv.getBlockState(pos));
				if (Plan.isFarmland(cur.target) && Plan.tillable(Plan.State.parse(now).id())) { // dirt placed: now the hoe
					if (phase == Phase.BUILD) placed++;
					else fixed++;
					ledger(pos, cur.target, Map.of(pl.item(), 1), false);
					queue.addFirst(new Op("till", pos.getX(), pos.getY(), pos.getZ(), cur.target, Map.of(), null));
					return endOp();
				}
				if (Plan.State.parse(now).id().equals(Plan.State.parse(cur.target).id())) {
					if (phase == Phase.BUILD) placed++;
					else fixed++;
					ledger(pos, cur.target, Map.of(pl.item(), 1), false); // one item per click: a double slab books its second click below
					if (opt.manual() != null) manual.put(pos, opt.manual());
					retryNeighbours(pos);
					if (v2 && "double".equals(Plan.State.parse(cur.target).get("type")) && !"double".equals(Plan.State.parse(now).get("type"))) {
						stage = 5;
						return null;
					}
					if (pl.useAfter() && "false".equals(Plan.State.parse(now).get("open")))
						queue.addFirst(new Op("use", pos.getX(), pos.getY(), pos.getZ(), cur.target, cur.items, null));
					return endOp();
				}
				if (++attempts < 2) {
					stage = 0;
					return null;
				}
				manual.put(pos, "placing " + pl.item() + " gave " + now + (lastChild != null ? "; last: " + lastChild : ""));
				return endOp();
			}
			default -> {
				return endOp();
			}
		}
	}

	// ---- use op: right-click a door/trapdoor/gate until its `open` is the design's ----

	Step useOp(BodyServices b) {
		LocalPlayer p = b.player();
		ClientLevel lv = b.minecraft().level;
		BlockPos pos = cur.pos();
		String want = Plan.State.parse(cur.target).get("open");
		switch (stage) {
			case 0 -> {
				String now = Plan.State.parse(canonical(lv.getBlockState(pos))).get("open");
				if (now == null || now.equals(want)) return endOp();
				// aim at the top face: a closed bottom trapdoor is 3/16 thick, so its centre is empty space (CP-49 lane B attempt 2)
				// use_item aims at the block centre: a closed bottom trapdoor (3/16 thick) is hit only from nearly above
				if (seesBlock(lv, p, p.position(), pos, EYE)) {
					JsonObject up = new JsonObject();
					up.add("target_pos", pos(pos));
					childKind = "use";
					child = b.run("use_item", up);
					stage = 1;
					return null;
				}
				BlockPos feet = walks < 2 ? stand(b, pos, null, null, null, EYE) : null;
				if (feet == null) {
					manual.put(pos, "open=" + want + ": no spot from which use_item's aim (the shape's centre) hits it" + (lastChild != null ? "; last: " + lastChild : ""));
					return endOp();
				}
				walks++;
				return walk(b, feet);
			}
			case 1 -> {
				String now = Plan.State.parse(canonical(lv.getBlockState(pos))).get("open");
				if (!want.equals(now) && ++attempts < 2) {
					stage = 0;
					return null;
				}
				if (!want.equals(now)) manual.put(pos, "use did not set open=" + want + (lastChild != null ? "; last: " + lastChild : ""));
				return endOp();
			}
			default -> {
				return endOp();
			}
		}
	}

	// ---- till op: a hoe on dirt / grass for farmland (repair_zone; farmland is not placeable as an item) ----

	/** Bare dry farmland turns back to dirt within moments (CP-52 lane B attempt 5: 6 seed clicks hit dirt, the fix round
	 *  broke and re-placed the dirt and ran out): plant the design's crop on it right after tilling. */
	void plantAbove(BlockPos pos) {
		int x = pos.getX() - ox, y = pos.getY() + 1 - oy, z = pos.getZ() - oz;
		Design.Grid g = d.grid;
		if (x < 0 || z < 0 || y >= g.sy() || x >= g.sx() || z >= g.sz()) return;
		String want = g.state(x, y, z);
		if (Plan.SEEDS.containsKey(Plan.State.parse(want).id()))
			queue.addFirst(new Op("place", pos.getX(), pos.getY() + 1, pos.getZ(), want, itemsFor(want), null));
	}

	Step tillOp(BodyServices b) {
		LocalPlayer p = b.player();
		ClientLevel lv = b.minecraft().level;
		BlockPos pos = cur.pos();
		String now = Plan.State.parse(canonical(lv.getBlockState(pos))).id();
		switch (stage) {
			case 0 -> {
				if (now.equals("minecraft:farmland")) return endOp();
				if (!Plan.tillable(now)) {
					manual.put(pos, "farmland wanted, " + now + " there: not tillable");
					return endOp();
				}
				if (!lv.getBlockState(pos.above()).isAir()) {
					manual.put(pos, "farmland wanted but the block above is not air");
					return endOp();
				}
				if (hoeSlot(p) < 0) {
					int inv = -1;
					for (int i = 9; i < 36; i++)
						if (BuiltInRegistries.ITEM.getKey(p.getInventory().getItem(i).getItem()).toString().endsWith("_hoe")) {
							inv = i;
							break;
						}
					if (inv < 0) {
						shortItems.merge("minecraft:wooden_hoe", 1, Integer::sum);
						manual.put(pos, "no hoe to till it");
						return endOp();
					}
					b.clickSlot(inv, 8, ContainerInput.SWAP);
					waitTicks = 0;
					stage = 10;
					return null;
				}
				stage = 1;
				return null;
			}
			case 10 -> {
				if (hoeSlot(p) >= 0) stage = 1;
				else if (++waitTicks > 20) {
					manual.put(pos, "could not move a hoe to the hotbar");
					return endOp();
				}
				return Step.running();
			}
			case 1 -> { // use_item aims at the top face centre (target_pos + face up), standing eye
				if (clickable(lv, p, p.position(), pos, pos, Plan.Dir.up, null, EYE, false)) { // till: the clear ray, as before
					stage = 2;
					return null;
				}
				if (walks >= 2) {
					manual.put(pos, "no spot from which to till it" + (lastChild != null ? "; last: " + lastChild : ""));
					return endOp();
				}
				if (walks > 0) opBad.add(spot(p));
				BlockPos feet = stand(b, pos, pos, Plan.Dir.up, null, EYE, false);
				if (feet == null) {
					manual.put(pos, "no standing spot sees its top to till it");
					return endOp();
				}
				walks++;
				return walk(b, feet);
			}
			case 2 -> {
				b.selectHotbar(hoeSlot(p));
				JsonObject up = new JsonObject();
				up.add("target_pos", pos(pos));
				up.addProperty("face", "up");
				childKind = "use";
				child = b.run("use_item", up);
				stage = 3;
				return null;
			}
			case 3 -> {
				touched.add(pos);
				if (now.equals("minecraft:farmland")) {
					plantAbove(pos);
					return endOp();
				}
				if (++attempts < 2) {
					stage = 1;
					return null;
				}
				manual.put(pos, "tilling left " + now + (lastChild != null ? "; last: " + lastChild : ""));
				return endOp();
			}
			default -> {
				return endOp();
			}
		}
	}

	/** Hotbar slot (0-8) holding any hoe, or -1. */
	static int hoeSlot(LocalPlayer p) {
		for (int i = 0; i < 9; i++)
			if (BuiltInRegistries.ITEM.getKey(p.getInventory().getItem(i).getItem()).toString().endsWith("_hoe")) return i;
		return -1;
	}

	// ---- break op ----

	Step breakOp(BodyServices b) {
		LocalPlayer p = b.player();
		ClientLevel lv = b.minecraft().level;
		BlockPos pos = cur.pos();
		switch (stage) {
			case 0 -> {
				BlockState s = lv.getBlockState(pos);
				if (s.isAir()) return endOp();
				if (cur.expect != null && !cur.expect.equals(BuiltInRegistries.BLOCK.getKey(s.getBlock()).toString())) {
					manual.put(pos, "holds " + canonical(s) + ", not " + cur.expect + " (changed since the plan); left alone");
					return endOp();
				}
				if (goPlanned(b, "break ", pos)) return null; // judged again (stage 0) from design's planned spot
				stage = 1;
				// standing ON it = her feet rest on its top (beside it, her 0.6-wide box may overhang the column: CP-51 attempt 6)
				net.minecraft.world.phys.AABB bb = p.getBoundingBox();
				boolean onIt = Math.abs(bb.minY - (pos.getY() + 1)) < 0.01
						&& bb.intersects(new net.minecraft.world.phys.AABB(pos.getX(), pos.getY(), pos.getZ(), pos.getX() + 1, pos.getY() + 1.01, pos.getZ() + 1));
				boolean strands = !repair && strands(lv, p, pos); // O89c, O94ah: off the ground, her walk down must survive the break
				if (onIt || moveOn || strands || !seesBlock(lv, p, p.position(), pos, EYE)) { // mine_block aims at the centre; never break what she stands on
					BlockPos feet = walks < 2 ? stand(b, pos, null, null, null, EYE) : null;
					if (feet == null && moveOn && !onIt && !strands && seesBlock(lv, p, p.position(), pos, EYE)) {
						moveOn = false; // O89f: obstructed here and no other spot: once more from here (attempts still caps it)
						return null;
					}
					if (feet == null) {
						manual.put(pos, walks >= 2 ? "no spot from which to break it" + (lastChild != null ? "; last: " + lastChild : "") : "no standing spot found to break it");
						return endOp();
					}
					walks++;
					stage = 0; // judged again from where she really stops
					return walk(b, feet);
				}
				return null;
			}
			case 1 -> {
				JsonObject mp = new JsonObject();
				mp.add("pos", pos(pos));
				childKind = "break";
				child = b.run("mine_block", mp);
				stage = 2;
				return null;
			}
			case 2 -> {
				touched.add(pos);
				if (lv.getBlockState(pos).isAir()) {
					if (phase == Phase.REMOVE) removed++;
					ledger(pos, null, Map.of(), true);
				} else if (++attempts < 2) {
					stage = 0;
					return null;
				} else manual.put(pos, "could not break " + canonical(lv.getBlockState(pos)));
				return endOp();
			}
			default -> {
				return endOp();
			}
		}
	}

	/** A sub-action ended: a server refusal ends the whole build; anything else is judged by the world in the next stage. */
	Step afterChild(BodyServices b, JsonObject res) {
		String status = str(res, "status"), code = str(res, "code");
		EveBuilder.LOG.info("eve-builder: {} for {} -> {}/{} {}", childKind, cur.pos().toShortString(), status, code, str(res, "note"));
		if (!"ok".equals(status)) lastChild = childKind + " " + status + "/" + code + (str(res, "note") != null ? " (" + str(res, "note") + ")" : "");
		if ("failed".equals(status) && "zone_denied".equals(code)) {
			String id = BuiltInRegistries.BLOCK.getKey(b.minecraft().level.getBlockState(cur.pos()).getBlock()).getPath();
			String tree = treeRefusal(childKind, phase == Phase.REMOVE, id);
			if (tree != null) { // kickoff: H-trees on a FIX break -> manual, build on
				manual.put(cur.pos(), tree);
				return endOp();
			}
			return fail(b, "zone_denied", childKind + " at " + cur.pos().toShortString() + " refused by the server: " + str(res, "note"));
		}
		if ("place".equals(childKind) && v2 && "precondition_failed".equals(code) && str(res, "note") != null && str(res, "note").contains("state")) {
			v2 = false; // the body's schema has no `state`: v1 from here on
			EveBuilder.LOG.info("eve-builder: body has no place_block v2 ({}); placing with v1", str(res, "note"));
			stage = 0;
			lastChild = null;
			return null;
		}
		if (("place".equals(childKind) && "state_unreachable".equals(code)) || (("place".equals(childKind) || "break".equals(childKind)) && "obstructed".equals(code))) {
			// nothing was spent; try from another spot (this op only). O89f: obstructed too (a30/a31: the crosshair on the
			// support's other face, twice from design's stand; a11 (26,153,-39): a break's crosshair on the next dirt)
			opBad.add(spot(b.player()));
			moveOn = true;
		}
		if ("walk".equals(childKind) && "failed".equals(status)) {
			if (walkGoal != null) badSpots.add(walkGoal);
			// could not reach the spot: try the action from where she is; the primitive reports out_of_range if it cannot
			EveBuilder.LOG.info("eve-builder: walk_to for {} ended {} {}", cur.pos().toShortString(), code, str(res, "note"));
			if (walkGoal != null) logAround(b, walkGoal); // what Baritone saw vs. the design's replay (pillar_tall a1-a3)
		}
		if ("walk".equals(childKind)) return strandGuard(b, "after_walk");
		return null;
	}

	/**
	 * O94ah: the stranding guard, after every walk (finished or not) and at the start of every op. With no walk down to the
	 * ground (y <= the site origin, or off the site: {@link #ground}) from where she stands, the build stops: unreachable, with
	 * evidence.stranded. A perch or a
	 * pocket Baritone stepped her into must not fail op after op (CP-49 lane C a16: 694 of 821 unreachable walks from one
	 * pocket). Off the walk model (outside the box, or a cell it cannot stand on) nothing is known, so nothing stops;
	 * repair_zone has no site floor (its ground is not the origin), so it is not guarded.
	 */
	Step strandGuard(BodyServices b, String when) {
		if (repair) return null;
		Walk w = walk(b.minecraft().level);
		Walk.P here = walkSpot(w, b.player());
		if (here == null || w.reaches(here, this::ground)) return null;
		BlockPos at = new BlockPos(here.x(), here.y(), here.z());
		EveBuilder.LOG.warn("eve-builder: stranded at {} (no walk down to the ground) at {} for {}; build stopped", at.toShortString(), when,
				cur == null ? "-" : cur.pos().toShortString());
		JsonObject st = new JsonObject();
		st.add("spot", pos(at));
		st.addProperty("when", when);
		st.addProperty("phase", phase.name().toLowerCase());
		if (cur != null) st.add("op_target", pos(cur.pos()));
		if (walkGoal != null) st.add("walk_goal", pos(walkGoal));
		b.evidence().add("stranded", st);
		if (run != null) {
			run.status = "stopped";
			dirty = true;
			saveRun();
		}
		return report(b, null, null, "unreachable", "stranded at " + at.toShortString() + ": no walk down to the ground from there; build stopped (O94ah)");
	}

	/**
	 * Sense's H-trees (0.1.23): Eve may break logs and leaves only of real trees; a player-placed one is denied
	 * placed_block:<why> outside a fresh zone.repair remove step. The client sees only zone_denied (the block came back), not
	 * sense's rule_id, so a refused non-scaffold break of a log, wood, stem or leaves block is taken as that denial: the cell
	 * goes manual and the build goes on (kickoff, next line after 7d8b2385). Every other zone_denied still ends the build.
	 */
	static String treeRefusal(String kind, boolean removing, String id) {
		return "break".equals(kind) && !removing && treeBlock(id)
				? "the server refused breaking a player-placed " + id + " (sense H-trees, placed_block); left as it is" : null;
	}

	static boolean treeBlock(String id) {
		return id.endsWith("_log") || id.endsWith("_wood") || id.endsWith("_stem") || id.endsWith("_hyphae") || id.endsWith("_leaves");
	}

	/** The half a click must land in for this target, or null: stairs/trapdoors by `half`, single slabs by `type`. */
	static String halfOf(String target) {
		Plan.State st = Plan.State.parse(target);
		String id = st.id();
		if (id.endsWith("_stairs") || id.endsWith("_trapdoor")) return st.get("half");
		if (id.endsWith("_slab") && !"double".equals(st.get("type"))) return st.get("type");
		return null;
	}

	/** Diagnostic: her exact position and the non-air blocks in a 5x5x5 box around a walk goal she could not reach. */
	void logAround(BodyServices b, BlockPos g) {
		ClientLevel lv = b.minecraft().level;
		StringBuilder sb = new StringBuilder();
		for (int y = g.getY() - 2; y <= g.getY() + 2; y++)
			for (int z = g.getZ() - 2; z <= g.getZ() + 2; z++)
				for (int x = g.getX() - 2; x <= g.getX() + 2; x++) {
					var st = lv.getBlockState(new BlockPos(x, y, z));
					if (!st.isAir()) sb.append(' ').append(x).append(',').append(y).append(',').append(z).append('=').append(BuiltInRegistries.BLOCK.getKey(st.getBlock()).getPath());
				}
		EveBuilder.LOG.info("eve-builder: walk goal {} unreached from {}; solid around:{}", g.toShortString(), b.player().position(), sb);
	}

	/** Walk once to design's planned standing spot for this op (contract Amendments 3), unless she is already on it. */
	boolean goPlanned(BodyServices b, String kind, BlockPos pos) {
		BlockPos s = planned.get(kind + pos);
		if (s == null || plannedTried) return false;
		plannedTried = true;
		if (spot(b.player()).equals(s)) return false;
		Walk w = walk(b.minecraft().level);
		Walk.P from = walkSpot(w, b.player());
		Walk.P goal = new Walk.P(s.getX(), s.getY(), s.getZ());
		if (from != null && !w.reaches(from, q -> q.equals(goal))) { // O94ah: never hand Baritone an unreachable goal
			EveBuilder.LOG.info("eve-builder: design's planned spot {} for {} {} is not reachable from {} (Walk): own stand search instead",
					s.toShortString(), kind.trim(), pos.toShortString(), spot(b.player()).toShortString());
			return false;
		}
		if (!repair && !ground(goal) && !afterOp(w, pos).reaches(goal, this::ground)) { // re-review F2: no walk down after the op
			EveBuilder.LOG.info("eve-builder: design's planned spot {} for {} {} has no walk down to the ground after the op (Walk): own stand search instead",
					s.toShortString(), kind.trim(), pos.toShortString());
			return false;
		}
		EveBuilder.LOG.info("eve-builder: walk to design's planned spot {} for {} {}", s.toShortString(), kind.trim(), pos.toShortString());
		walk(b, s);
		return true;
	}

	Step walk(BodyServices b, BlockPos feet) {
		walkGoal = feet;
		moveOn = false;
		JsonObject goal = new JsonObject();
		goal.addProperty("x", feet.getX() + 0.5);
		goal.addProperty("y", feet.getY());
		goal.addProperty("z", feet.getZ() + 0.5);
		JsonObject wp = new JsonObject();
		wp.add("pos", goal);
		wp.addProperty("tolerance", 1.0); // Baritone ends anywhere in the goal block (~0.7 from its centre on step tops: hut5_sim lane B attempt 6)
		childKind = "walk";
		child = b.run("walk_to", wp);
		return null;
	}

	/** A placement with no workable spot now is tried once more at the end (CP-49 lane C attempt 5: a half=top stair placed
	 *  first in its layer had no side neighbour yet whose upper half could be clicked); after that it goes under manual. */
	Step giveUp(BlockPos pos, String why) {
		if (tries.merge(pos, 1, Integer::sum) <= 7) deferred.add(cur); // one per new neighbour (<= 6) + the end-of-queue try
		else manual.put(pos, why + (lastChild != null ? "; last: " + lastChild : ""));
		return endOp();
	}

	final Map<BlockPos, Integer> tries = new HashMap<>();

	/** A block just placed next to a deferred placement may be the support it lacked: try that one now, while its other sides
	 *  are still open (CP-52 lane B attempt 3: the stone under a chest had only the chest's underside at first, was deferred,
	 *  and by the end of the queue its four neighbours had boxed in every face she could click). */
	void retryNeighbours(BlockPos at) {
		for (var it = deferred.iterator(); it.hasNext(); ) {
			Op o = it.next();
			if (Math.abs(o.x() - at.getX()) + Math.abs(o.y() - at.getY()) + Math.abs(o.z() - at.getZ()) == 1) {
				it.remove();
				queue.addFirst(o);
			}
		}
	}

	/** place_block plans its v2 click from her eye on its first tick and clicks sneaking: crouch first (≤10 ticks), so the
	 *  plan is made from the eye that clicks (CP-50 a18-a21: planned from 1.62, clicked from 1.27, the re-check landed in
	 *  the other half or on the wall). True while still waiting. */
	boolean crouchFirst(LocalPlayer p) {
		if (p.isCrouching() || crouchTicks >= 10) {
			crouchTicks = 0;
			return false;
		}
		crouchTicks++;
		return true;
	}

	int crouchTicks;

	Step endOp() {
		opsSinceSave++;
		saveRun(false);
		opBad.clear();
		slabWalks = 0;
		cur = null;
		moveOn = false;
		opsDone++;
		return null;
	}

	// ---- finish: compare everything ----

	Step finish(BodyServices b) {
		ClientLevel lv = b.minecraft().level;
		JsonArray mism = new JsonArray();
		int matched = 0, bad = 0;
		Design.Grid g = d.grid;
		for (int y = 0; y < g.sy(); y++)
			for (int z = 0; z < g.sz(); z++)
				for (int x = 0; x < g.sx(); x++) {
					BlockPos p = new BlockPos(ox + x, oy + y, oz + z);
					String want = g.state(x, y, z);
					if (want.equals(Design.ANY)) continue; // repair_zone: a cell no step names
					String have = canonical(lv.getBlockState(p));
					String c = Plan.classify(want, have);
					if (c.equals("match")) {
						if (!Plan.isAir(want)) matched++;
						manual.remove(p); // it came out right after all
						continue;
					}
					if (!c.equals("derived_mismatch")) bad++;
					if (mism.size() < 200) mism.add(cell(p, want, have, c));
				}
		if (d.scaffold != null) { // scaffold must be gone (cells where the design itself has air)
			Design.Grid s = d.scaffold;
			for (int y = 0; y < s.sy(); y++)
				for (int z = 0; z < s.sz(); z++)
					for (int x = 0; x < s.sx(); x++) {
						if (Plan.isAir(s.state(x, y, z))) continue;
						int dx = s.ox() - g.ox() + x, dy = s.oy() - g.oy() + y, dz = s.oz() - g.oz() + z;
						if (g.in(dx, dy, dz)) continue; // inside the footprint: the loop above judged it
						BlockPos p = new BlockPos(ox + dx, oy + dy, oz + dz);
						String have = canonical(lv.getBlockState(p));
						if (!Plan.isAir(have) && Plan.State.parse(have).id().equals(Plan.State.parse(s.state(x, y, z)).id())) {
							bad++;
							if (mism.size() < 200) mism.add(cell(p, Design.AIR, have, "scaffold_left"));
						}
					}
		}
		if (run != null) {
			if (!phases.isEmpty()) { // the last phase's cells, then each scaffold cell as it ends (removed: observed air)
				Phases.Phase last = phases.get(phases.size() - 1);
				addRows(Phases.cellRows(bsteps, last.from(), last.to(), ox, oy, oz, s -> canonical(lv.getBlockState(new BlockPos(ox + s.x(), oy + s.y(), oz + s.z()))),
						s -> classifyStep(lv, s).equals("match"), run.placedAt));
				List<Design.Step> sc = new ArrayList<>();
				for (int i = 0; i < last.from(); i++) if (bsteps.get(i).layer().equals("scaffold")) sc.add(bsteps.get(i));
				addRows(Phases.cellRows(sc, 0, sc.size(), ox, oy, oz, s -> canonical(lv.getBlockState(new BlockPos(ox + s.x(), oy + s.y(), oz + s.z()))),
						s -> classifyStep(lv, s).equals("match"), run.placedAt));
			}
			run.phase = phases.size();
			run.status = bad == 0 ? "done" : "failed";
			dirty = true;
			saveRun();
		}
		return report(b, matched, mism, bad == 0 ? null : "partial", bad + " cell(s) not as designed");
	}

	Step fail(BodyServices b, String code, String note) {
		return report(b, null, null, code, note);
	}

	Step report(BodyServices b, Integer matched, JsonArray mism, String code, String note) {
		JsonObject ev = b.evidence();
		if (run != null) {
			runEvidence(b);
			ev.add("cells", cellRows);
			if (cellRows.size() >= MAX_ROWS) ev.addProperty("cells_truncated", true);
		}
		ev.addProperty("cells_total", total);
		ev.addProperty("already_present", already);
		ev.addProperty("placed", placed);
		ev.addProperty("fixed", fixed);
		ev.addProperty("scaffold_removed", removed);
		if (matched != null) ev.addProperty("matched", matched);
		if (mism != null) ev.add("mismatches", mism);
		ev.add("manual", manualRows(manual));
		if (!shortItems.isEmpty()) { // ran out during the build (the start check passed): only what is missing now is known
			JsonObject df = new JsonObject(), m = new JsonObject();
			shortItems.forEach(m::addProperty);
			df.add("missing", m);
			df.addProperty("note", "ran out during the build");
			ev.add("deficit", df);
		}
		if (code == null) return Step.ok();
		return Step.failed(code, note);
	}

	/** evidence.manual: one {pos, why} per cell left for a person, in the order they were left. */
	static JsonArray manualRows(Map<BlockPos, String> manual) {
		JsonArray man = new JsonArray();
		manual.forEach((p, why) -> {
			JsonObject m = new JsonObject();
			m.add("pos", pos(p));
			m.addProperty("why", why);
			man.add(m);
		});
		return man;
	}

	static JsonObject cell(BlockPos p, String want, String have, String cls) {
		JsonObject m = new JsonObject();
		m.add("pos", pos(p));
		m.addProperty("expected", want);
		m.addProperty("observed", have);
		m.addProperty("class", cls);
		return m;
	}

	// ---- world helpers ----

	static JsonObject pos(BlockPos p) {
		JsonObject o = new JsonObject();
		o.addProperty("x", p.getX());
		o.addProperty("y", p.getY());
		o.addProperty("z", p.getZ());
		return o;
	}

	/** id[props sorted]: the design palette's form. */
	static String canonical(BlockState s) {
		Map<String, String> props = new TreeMap<>();
		for (Property<?> pr : s.getProperties()) props.put(pr.getName(), value(s, pr));
		return new Plan.State(BuiltInRegistries.BLOCK.getKey(s.getBlock()).toString(), props).canonical();
	}

	static <T extends Comparable<T>> String value(BlockState s, Property<T> p) {
		return p.getName(s.getValue(p));
	}

	/** The block to click for this face (pos - face), or null when it is not there; face null = any solid neighbour. */
	static BlockPos support(ClientLevel lv, BlockPos pos, Plan.Dir face) {
		for (Plan.Dir f : face != null ? new Plan.Dir[] {face} : Plan.Dir.values()) {
			BlockPos s = pos.offset(-f.dx, -f.dy, -f.dz);
			if (!lv.getBlockState(s).canBeReplaced()) return s;
		}
		return null;
	}

	static Plan.Dir[] faces(Plan.Option o) {
		return o.face() != null ? new Plan.Dir[] {o.face()}
				: new Plan.Dir[] {Plan.Dir.up, Plan.Dir.north, Plan.Dir.south, Plan.Dir.west, Plan.Dir.east, Plan.Dir.down};
	}

	// eye heights (standing / sneaking: place_block always sneak-places, CP-49 lane B attempt 2 lantern) and a reach margin
	static final double EYE = 1.62, EYE_SNEAK = 1.27, REACH = 4.3;

	/** From feet position {@code at}: the support's face point within reach, Eve's look along {@code look} (if any), and the
	 *  ray from the eye hitting exactly that face of that block first (the body's own aim point and crosshair rule). */
	static boolean clickable(ClientLevel lv, LocalPlayer p, Vec3 at, BlockPos pos, BlockPos sup, Plan.Dir f, Plan.Dir look, double eyeH, boolean steady) {
		return clickable(lv, p, at, pos, sup, f, look, eyeH, null, steady);
	}

	/** half: "bottom"/"top" for a half-sensitive state (stairs, trapdoors, slabs) clicked on a side face, where vanilla takes the
	 *  half from the hit height: aim into that half, not at the face centre on the boundary (CP-50 a18/a19: from design's low
	 *  stand the clear rays only reached the upper half and the body refused half=top). */
	static boolean clickable(ClientLevel lv, LocalPlayer p, Vec3 at, BlockPos pos, BlockPos sup, Plan.Dir f, Plan.Dir look, double eyeH, String half, boolean steady) {
		Vec3 c = Vec3.atCenterOf(sup).add(f.dx * 0.49, f.dy * 0.49, f.dz * 0.49);
		steady &= f.dy == 0; // design keeps the margin for side clicks only (placecheck can_place: d[1] != 0 or _steady)
		if (half == null || f.dy != 0) return clickRay(lv, p, at, sup, f, look, eyeH, c, half, steady);
		// any clear point inside the wanted half, as the body's place_block v2 searches hit points (CP-50 lane B a5-a7: the
		// one centre-of-half point missed the low clicks that had worked from design's stand)
		double ux = f.dz != 0 ? 1 : 0, uz = f.dx != 0 ? 1 : 0, sgn = "top".equals(half) ? 1 : -1;
		for (double u : new double[] {0, 0.35, -0.35})
			for (double v : new double[] {0.25, 0.1, 0.4})
				if (clickRay(lv, p, at, sup, f, look, eyeH, c.add(ux * u, sgn * v, uz * u), half, steady)) return true;
		return false;
	}

	/** steady: the click must also keep design's aim margin ({@link #steady}); false = the clear ray alone (the old rule). */
	static boolean clickRay(ClientLevel lv, LocalPlayer p, Vec3 at, BlockPos sup, Plan.Dir f, Plan.Dir look, double eyeH, Vec3 pt, String half, boolean steady) {
		Vec3 eye = at.add(0, eyeH, 0);
		if (eye.distanceTo(pt) > REACH) return false;
		if (look != null) {
			double dx = pt.x - at.x, dz = pt.z - at.z;
			Plan.Dir d = Math.abs(dx) > Math.abs(dz) ? (dx > 0 ? Plan.Dir.east : Plan.Dir.west) : (dz > 0 ? Plan.Dir.south : Plan.Dir.north);
			if (d != look) return false;
		}
		Vec3 end = eye.add(pt.subtract(eye).scale(1 + 1.5 / eye.distanceTo(pt))); // a crosshair ray goes on past the aim point
		java.util.function.BiFunction<Vec3, Vec3, BlockHitResult> ray = (a, z) -> lv.clip(new ClipContext(a, z, ClipContext.Block.OUTLINE, ClipContext.Fluid.NONE, p));
		BlockHitResult h = ray.apply(eye, end);
		return h.getType() == HitResult.Type.BLOCK && h.getBlockPos().equals(sup) && h.getDirection() == Direction.byName(f.name())
				&& (!steady || steady(ray, eye, pt, sup, f, half, front(lv.getBlockState(sup), f)));
	}

	/** A stair support's front (the side its step faces, opposite its facing), or null: not a stair, or the clicked face is
	 *  that front (design's _front). */
	static Direction front(BlockState s, Plan.Dir clicked) {
		if (!(s.getBlock() instanceof net.minecraft.world.level.block.StairBlock)) return null;
		Direction fr = s.getValue(net.minecraft.world.level.block.StairBlock.FACING).getOpposite();
		return fr == Direction.byName(clicked.name()) ? null : fr;
	}

	// design's aim margin (placecheck.py AIM_TOL / AIM_TOL_SAME): the crosshair lands within ~1° of the aim
	static final double AIM_TOL = Math.toRadians(1.0), AIM_TOL_SAME = Math.toRadians(2.0);

	/**
	 * O89f, design's rule for a click (placecheck _steady, kept at "front only" by design 2026-10-21): rays turned ±1° about
	 * two axes across the aim still hit this face of the support first, within reach, inside the wanted half on a side face;
	 * and for a stair support, rays turned ±2° never land on its front (CP-50 a30/a31: ~1.6° to spare on the support stair's
	 * side, the crosshair landed on its front). Full-block supports with the same geometry passed live, so no 2° rule there.
	 * {@code ray} casts from-to and returns the first outline hit (a miss ends at {@code to}); front: {@link #front}.
	 */
	static boolean steady(java.util.function.BiFunction<Vec3, Vec3, BlockHitResult> ray, Vec3 eye, Vec3 pt, BlockPos sup, Plan.Dir f, String half, Direction front) {
		Direction face = Direction.byName(f.name());
		for (Vec3 q : turned(eye, pt, AIM_TOL)) {
			BlockHitResult h = ray.apply(eye, eye.add(q.scale(REACH + 1.5)));
			if (!onSup(h, sup) || h.getDirection() != face || h.getLocation().distanceTo(eye) > REACH) return false;
			double hy = h.getLocation().y - sup.getY();
			if (half != null && f.dy == 0 && ("top".equals(half) ? hy <= 0.5 : hy >= 0.5)) return false;
		}
		if (front != null)
			for (Vec3 q : turned(eye, pt, AIM_TOL_SAME)) {
				BlockHitResult h = ray.apply(eye, eye.add(q.scale(REACH + 1.5)));
				if (onSup(h, sup) && h.getDirection() == front) return false;
			}
		return true;
	}

	static boolean onSup(BlockHitResult h, BlockPos sup) {
		return h.getType() == HitResult.Type.BLOCK && h.getBlockPos().equals(sup);
	}

	/** The four ray directions turned by {@code tol} about two axes across the aim from eye to pt. */
	static Vec3[] turned(Vec3 eye, Vec3 pt, double tol) {
		Vec3 r = pt.subtract(eye).normalize();
		Vec3 a = r.cross(Math.abs(r.y) < 0.99 ? new Vec3(0, 1, 0) : new Vec3(1, 0, 0)).normalize(), c = r.cross(a);
		Vec3[] out = new Vec3[4];
		int i = 0;
		for (Vec3 axis : new Vec3[] {a, a.scale(-1), c, c.scale(-1)}) out[i++] = r.scale(Math.cos(tol)).add(axis.scale(Math.sin(tol)));
		return out;
	}

	/**
	 * Where to stand (feet block) to click {@code sup}'s face {@code f} for {@code pos} (break: sup null, just reach), nearest to
	 * where she is: any free spot around the footprint, never a cell the design fills. ponytail: ground and scaffold tops only;
	 * no reach simulation for tall designs beyond this search.
	 */
	BlockPos stand(BodyServices b, BlockPos pos, BlockPos sup, Plan.Dir f, Plan.Dir look, double eyeH) {
		return stand(b, pos, sup, f, look, eyeH, true);
	}

	/** steady: a click from the spot must keep design's aim margin (O89f); false = the clear ray alone (the old rule). */
	BlockPos stand(BodyServices b, BlockPos pos, BlockPos sup, Plan.Dir f, Plan.Dir look, double eyeH, boolean steady) {
		ClientLevel lv = b.minecraft().level;
		String half = sup != null && cur != null && "place".equals(cur.kind()) && !sup.equals(pos) ? halfOf(cur.target) : null;
		LocalPlayer p = b.player();
		BlockPos best = null;
		double bestD = Double.MAX_VALUE;
		Design.Grid g = d.grid;
		// O89c: scaffold removal may stand anywhere she can walk to now, if after the break she can still walk down to the
		// ground (taking steps out from above once stranded her over a >3 drop, hut5_sim lane B attempt 7; ground only left
		// hut5_tall's 15 room cells, CP-49 lane C a11). Outside the walk box: ground only, as before.
		// O94ah: in every phase a stand must be one she can walk to from where she stands (Walk), so Baritone never chases a
		// goal it cannot reach: its partial path once stepped her into a pocket she could not leave (CP-49 lane C a16)
		Walk after = null; // the walk once this op is done (a break takes the block out, a place puts it in): stands need a walk down there
		Set<Walk.P> reach = null;
		Walk walk = walk(lv);
		Walk.P from = walkSpot(walk, p);
		if (from != null) {
			reach = walk.reach(from);
			after = afterOp(walk, pos);
		}
		List<Candidates.Opt> opts = new ArrayList<>(); // O94f: the stands within reach of the target, for the candidates row
		Vec3 tc = Vec3.atCenterOf(pos);
		for (int x = ox - 4; x < ox + g.sx() + 4; x++)
			for (int z = oz - 4; z < oz + g.sz() + 4; z++)
				for (int y = pos.getY() + 2; y >= oy - 2; y--) {
					BlockPos feet = new BlockPos(x, y, z);
					if (feet.equals(pos) || feet.above().equals(pos) || feet.below().equals(pos)) continue;
					boolean near = Vec3.atBottomCenterOf(feet).distanceTo(tc) <= REACH + 2; // farther ones cannot reach: not logged
					if (badSpots.contains(feet) || opBad.contains(feet)) {
						if (near && standHeights(lv, feet).length > 0) opts.add(new Candidates.Opt(x, y, z, null, "bad_spot"));
						continue;
					}
					Walk.P c = new Walk.P(x, y, z);
					boolean offGround = phase == Phase.REMOVE && y != oy; // the exemptions stay on the origin's level; ground() is the target
					// removal keeps its ground spots without a walk check (O89c review #3); every other phase needs a walk to the
					// stand (O94ah). With her spot unknown (mid-air, on a pane or a bed) no filter, as before: a ground-only rule
					// there dropped every raised op while she stood on an edge (a18); off the box enterSite walks her in first
					boolean unreachable = phase == Phase.REMOVE ? offGround && (reach == null || !reach.contains(c)) : reach != null && !reach.contains(c);
					if (unreachable) {
						if (near && reach != null && standHeights(lv, feet).length > 0) opts.add(new Candidates.Opt(x, y, z, null, "unreachable"));
						continue;
					}
					double[] hs = standHeights(lv, feet);
					if (hs.length == 0) continue;
					boolean all = true; // on a stair she may end on the low or the high step: the click must work from both
					for (double h : hs) {
						Vec3 a = new Vec3(feet.getX() + 0.5, h, feet.getZ() + 0.5);
						if (sup != null ? !clickableAround(lv, p, a, pos, sup, f, look, eyeH, half, steady) : !seesBlockAround(lv, p, a, pos, eyeH)) all = false;
					}
					if (!all) {
						if (near) opts.add(new Candidates.Opt(x, y, z, null, sup != null ? (steady ? "no_steady_click" : "no_click") : "no_sight"));
						continue;
					}
					// a stand off the ground needs a walk down from it (removal: after the break), so she never walks herself into a
					// pocket she can only drop into (O94ah review #1); every such stand, so the row is true
					boolean needDown = phase == Phase.REMOVE ? offGround : !repair && reach != null && !ground(c);
					if (needDown && !after.reaches(c, this::ground)) {
						opts.add(new Candidates.Opt(x, y, z, null, "no_walk_down"));
						continue;
					}
					Vec3 at = new Vec3(feet.getX() + 0.5, hs[0], feet.getZ() + 0.5);
					double score = p.position().distanceTo(at) + 2.0 * Math.max(0, y - oy) // prefer the ground: high spots need a route up
							+ (offGround ? 100 : 0) // O89c: off the ground only when no ground spot works
							+ (designFills(feet) || designFills(feet.above()) ? 3.0 : 0); // a cell the design fills later: last resort
					opts.add(new Candidates.Opt(x, y, z, score, null));
					if (score < bestD) {
						bestD = score;
						best = feet;
					}
				}
		logStand(b, pos, sup, f, steady, opts, best);
		return best;
	}

	/** O94f: the stand choice as an eve-candidates/1 row, written before the walk is dispatched. Not a safety path. */
	void logStand(BodyServices b, BlockPos pos, BlockPos sup, Plan.Dir f, boolean steady, List<Candidates.Opt> opts, BlockPos best) {
		try {
			Map<String, Object> st = new HashMap<>();
			BlockPos me = spot(b.player());
			st.put("op", cur == null ? null : cur.kind());
			st.put("target", new int[] {pos.getX(), pos.getY(), pos.getZ()});
			st.put("support", sup == null ? null : new int[] {sup.getX(), sup.getY(), sup.getZ()});
			st.put("face", f == null ? null : f.name());
			st.put("her", new int[] {me.getX(), me.getY(), me.getZ()});
			st.put("steady", steady);
			st.put("bad_spots", badSpots.size() + opBad.size());
			String objective = run != null ? run.runId : repair ? "zone:" + params.get("zone_id").getAsString()
					: params.get("project_id").getAsString() + "@v" + params.get("version").getAsInt();
			String ph = phase.name().toLowerCase() + (run != null ? "/phase" + run.phase : "");
			Candidates.append(FabricLoader.getInstance().getGameDir().resolve("eve-builder").resolve("candidates.jsonl"),
					Candidates.row(objective, "stand@" + jarTag(), ph, b.minecraft().level.getGameTime(), st, opts,
							best == null ? null : best.getX() + "," + best.getY() + "," + best.getZ(), b.actionId(), Candidates.now(), pos(pos)));
		} catch (Exception e) {
			EveBuilder.LOG.warn("eve-builder: candidates row skipped: {}", e.toString());
		}
	}

	static String jarTag;

	/** The rule's version for the candidates row: the first 8 hex of this jar's sha256 (the hash every run already names). */
	static synchronized String jarTag() {
		if (jarTag == null) {
			jarTag = "dev";
			try {
				for (Path j : FabricLoader.getInstance().getModContainer("eve-builder").orElseThrow().getOrigin().getPaths())
					if (Files.isRegularFile(j))
						jarTag = "jar:" + java.util.HexFormat.of().formatHex(java.security.MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(j))).substring(0, 8);
			} catch (Exception e) {
				EveBuilder.LOG.warn("eve-builder: jar hash unknown: {}", e.toString());
			}
		}
		return jarTag;
	}

	/**
	 * Design-local walk box: the grid, every step and every planned stand, 5 out sideways, 2 below, 4 above the highest. The
	 * grid alone (the box before) cut off scaffold and stands up to 6 out, so the O94ah checks failed C3's horse ramp and tower
	 * (O94ah review #0); for a design whose steps stay in the grid it is the old box.
	 */
	static int[] walkBox(Design d) {
		Design.Grid g = d.grid;
		int[] b = {0, 0, 0, g.sx() - 1, g.sy() - 1, g.sz() - 1};
		for (Design.Step s : d.steps)
			for (int[] c : s.stand() == null ? new int[][] {{s.x(), s.y(), s.z()}} : new int[][] {{s.x(), s.y(), s.z()}, s.stand()})
				for (int i = 0; i < 3; i++) {
					b[i] = Math.min(b[i], c[i]);
					b[i + 3] = Math.max(b[i + 3], c[i]);
				}
		return new int[] {b[0] - 5, b[1] - 2, b[2] - 5, b[3] + 5, b[4] + 4, b[5] + 5};
	}

	/** Ground for the O94ah stranding checks: at or below the site origin's level, or on the walk box's 5-wide rim beyond every
	 *  grid cell, step and planned stand, from where she can walk away. The rim keeps an owner's site whose terrain stands
	 *  above the origin (a sunk foundation) from reading as stranded; a pocket or a roof perch reaches neither. walk() first. */
	boolean ground(Walk.P q) {
		return q.y() <= oy || q.x() < ox + wbox[0] + 5 || q.x() > ox + wbox[3] - 5 || q.z() < oz + wbox[2] + 5 || q.z() > oz + wbox[5] - 5;
	}

	/** The site as Baritone walks it (Walk's rules): free = no collision or a door, floor = Baritone's canWalkOn. */
	Walk walk(ClientLevel lv) {
		if (wbox == null) wbox = walkBox(d);
		return new Walk(new Walk.World() {
			public boolean free(int x, int y, int z) {
				BlockPos q = new BlockPos(x, y, z);
				BlockState s = lv.getBlockState(q);
				return s.getCollisionShape(lv, q).isEmpty() || s.getBlock() instanceof net.minecraft.world.level.block.DoorBlock;
			}

			/** Baritone's canWalkOn as the body's jar has it (baritone-api-fabric-1.20.0, MovementHelper; body's settings: bottom
			 *  slabs yes, magma no, vines no): a full collision cube (not magma, honey, a bubble column, nor the classes its
			 *  isBlockNormalCube leaves out), else azalea, ladder, farmland, dirt path, soul sand, the three chests, glass, stairs,
			 *  slabs. Water and lava (its MAYBE) are no floor here. Design's compare (O94ag) found isFaceSturdy both ways off:
			 *  top trapdoors, hoppers and magma were floors, chests and leaves were not. */
			public boolean floor(int x, int y, int z) {
				BlockPos q = new BlockPos(x, y, z);
				BlockState s = lv.getBlockState(q);
				net.minecraft.world.level.block.Block bl = s.getBlock();
				boolean odd = bl instanceof net.minecraft.world.level.block.BambooStalkBlock || bl instanceof net.minecraft.world.level.block.piston.MovingPistonBlock
						|| bl instanceof net.minecraft.world.level.block.ScaffoldingBlock || bl instanceof net.minecraft.world.level.block.ShulkerBoxBlock
						|| bl instanceof net.minecraft.world.level.block.PointedDripstoneBlock || bl instanceof net.minecraft.world.level.block.AmethystClusterBlock;
				if (!odd && net.minecraft.world.level.block.Block.isShapeFullBlock(s.getCollisionShape(lv, q)))
					return !s.is(net.minecraft.world.level.block.Blocks.MAGMA_BLOCK) && !s.is(net.minecraft.world.level.block.Blocks.BUBBLE_COLUMN)
							&& !s.is(net.minecraft.world.level.block.Blocks.HONEY_BLOCK);
				return bl instanceof net.minecraft.world.level.block.AzaleaBlock || s.is(net.minecraft.world.level.block.Blocks.LADDER)
						|| s.is(net.minecraft.world.level.block.Blocks.FARMLAND) || s.is(net.minecraft.world.level.block.Blocks.DIRT_PATH)
						|| s.is(net.minecraft.world.level.block.Blocks.SOUL_SAND) || s.is(net.minecraft.world.level.block.Blocks.ENDER_CHEST)
						|| s.is(net.minecraft.world.level.block.Blocks.CHEST) || s.is(net.minecraft.world.level.block.Blocks.TRAPPED_CHEST)
						|| s.is(net.minecraft.world.level.block.Blocks.GLASS) || bl instanceof net.minecraft.world.level.block.StainedGlassBlock
						|| bl instanceof net.minecraft.world.level.block.StairBlock || bl instanceof net.minecraft.world.level.block.SlabBlock;
			}

			public boolean door(int x, int y, int z) {
				return lv.getBlockState(new BlockPos(x, y, z)).getBlock() instanceof net.minecraft.world.level.block.DoorBlock;
			}
		}, ox + wbox[0], oy + wbox[1], oz + wbox[2], ox + wbox[3], oy + wbox[4], oz + wbox[5]);
	}

	/** Where the body's use_item and mine_block aim (Hands.aimPoint): the centre of the block's shape, the cell's centre when
	 *  it has none. An open door's centre is its panel, not the empty middle of its cell (CP-49 lane C a14: the cell centre
	 *  found no spot to close the door Baritone had opened, so it was left open). */
	static Vec3 aimPoint(ClientLevel lv, BlockPos pos) {
		var shape = lv.getBlockState(pos).getShape(lv, pos);
		return shape.isEmpty() ? Vec3.atCenterOf(pos) : Vec3.atLowerCornerOf(pos).add(shape.bounds().getCenter());
	}

	/** walk_to stops anywhere within 0.5 of the goal: the spot counts only if the click works from its centre and 4 offsets
	 *  (CP-49 lane B attempt 2: the door's spot passed at its centre and failed where she actually stopped). */
	static boolean clickableAround(ClientLevel lv, LocalPlayer p, Vec3 at, BlockPos pos, BlockPos sup, Plan.Dir f, Plan.Dir look, double eyeH, String half, boolean steady) {
		for (double[] o : new double[][] {{0, 0}, {0.35, 0}, {-0.35, 0}, {0, 0.35}, {0, -0.35}})
			if (!clickable(lv, p, at.add(o[0], 0, o[1]), pos, sup, f, look, eyeH, half, steady)) return false;
		return true;
	}

	/** The ray from the eye through the block centre (mine_block / use_item aim) hits that block first, within reach. */
	/** For choosing a spot: she may end up to 0.35 off its centre (walk_to tolerance), so every swayed eye must see it. */
	static boolean seesBlockAround(ClientLevel lv, LocalPlayer p, Vec3 at, BlockPos pos, double eyeH) {
		for (double[] o : new double[][] {{0, 0}, {0.35, 0}, {-0.35, 0}, {0, 0.35}, {0, -0.35}})
			if (!seesBlock(lv, p, at.add(o[0], 0, o[1]), pos, eyeH)) return false;
		return true;
	}

	/** From where she stands now: the one ray mine_block / use_item will cast, her eye to the block centre (pillar_tall a7:
	 *  judging her real position with the spot-choosing sway refused a break design's stand allowed, and the leftover block
	 *  cut the route to the next stand). */
	static boolean seesBlock(ClientLevel lv, LocalPlayer p, Vec3 at, BlockPos pos, double eyeH) {
		Vec3 eye = at.add(0, eyeH, 0), c = aimPoint(lv, pos);
		if (eye.distanceTo(c) > REACH) return false;
		BlockHitResult h = lv.clip(new ClipContext(eye, eye.add(c.subtract(eye).scale(1 + 1.5 / eye.distanceTo(c))),
				ClipContext.Block.OUTLINE, ClipContext.Fluid.NONE, p));
		return h.getType() == HitResult.Type.BLOCK && h.getBlockPos().equals(pos);
	}

	/** Rules agreed with design 2026-10-20. (c): F and F+1 free now; design cells not built yet are allowed, scored last;
	 *  stand() never picks the cell being placed (else foundation interiors wider than ~8 are unreachable). (a): she may
	 *  also stand on stairs and bottom slabs (roofs are walked on). Returns
	 *  the possible absolute feet heights at this spot (empty = not standable): a bottom slab puts her feet half a block
	 *  lower than a full block; a bottom stair can leave her on its low or high step, so both are checked. */
	static double[] standHeights(ClientLevel lv, BlockPos feet) {
		if (!lv.getBlockState(feet).getCollisionShape(lv, feet).isEmpty()
				|| !lv.getBlockState(feet.above()).getCollisionShape(lv, feet.above()).isEmpty()) return new double[0];
		BlockPos under = feet.below();
		BlockState s = lv.getBlockState(under);
		if (s.isFaceSturdy(lv, under, Direction.UP)) return new double[] {feet.getY()};
		if (s.getBlock() instanceof net.minecraft.world.level.block.SlabBlock) return new double[] {feet.getY() - 0.5}; // bottom slab
		if (s.getBlock() instanceof net.minecraft.world.level.block.StairBlock) return new double[] {feet.getY() - 0.5, feet.getY()};
		return new double[0];
	}

	/** The feet block she stands in as stand() names spots: on a bottom slab or a stair's low step her block position is the
	 *  slab's own cell, the spot is the one above it. */
	static BlockPos spot(LocalPlayer p) {
		return BlockPos.containing(p.getX(), p.getY() + 0.6, p.getZ());
	}

	/** Her spot as a walk cell, or null when the walk box does not hold her standing there. */
	static Walk.P walkSpot(Walk w, LocalPlayer p) {
		return spotOf(w, p.getX(), p.getY(), p.getZ(), p.getBbWidth() / 2);
	}

	/**
	 * The cell under her centre (spot's rule), else a cell her box overlaps, at that level or one down (mid-step on a stair or
	 * slab). She often stands on an edge, carried by a neighbour's top over air: Baritone ends anywhere within ~0.7 of a goal's
	 * centre. CP-49 lane C a18 stood at (29.55, 152.0, -43.20) over air at z -44, so the centre cell alone read her as off the
	 * walk model and every raised op was dropped (O94ah regression, 07:55).
	 */
	static Walk.P spotOf(Walk w, double x, double y, double z, double half) {
		int fy = (int) Math.floor(y + 0.6);
		double[] off = {0, -half + 0.01, half - 0.01};
		for (int dy = 0; dy >= -1; dy--)
			for (double dx : off)
				for (double dz : off) {
					Walk.P q = new Walk.P((int) Math.floor(x + dx), fy + dy, (int) Math.floor(z + dz));
					if (w.standable(q)) return q;
				}
		return null;
	}

	/** O89c: doing this op on pos from where she stands (a break; O94ah: a place too, see afterOp) would leave her no walk down
	 *  to the ground. */
	boolean strands(ClientLevel lv, LocalPlayer p, BlockPos pos) {
		Walk w = walk(lv);
		Walk.P here = walkSpot(w, p);
		return here != null && !ground(here) && !afterOp(w, pos).reaches(here, this::ground);
	}

	/** The walk after this op (O94ah): a break takes the block out; a place puts the target in (a door: passable on level moves
	 *  only; a torch, carpet, plate, button, sign, rail or plant: passable; else solid, a floor unless canWalkOn says not:
	 *  panes, bars, fences, walls, beds, lanterns, trapdoors, candles, pots); anything else leaves it. */
	Walk afterOp(Walk w, BlockPos pos) {
		Walk.P c = new Walk.P(pos.getX(), pos.getY(), pos.getZ());
		if (cur == null) return w;
		if ("break".equals(cur.kind)) return w.without(c);
		if (!"place".equals(cur.kind) || cur.target == null) return w;
		String id = Plan.State.parse(cur.target).id();
		if (id.endsWith("_door")) return w.withDoor("upper".equals(Plan.State.parse(cur.target).get("half")) ? c.up(-1) : c);
		if (id.contains("torch") || id.endsWith("_carpet") || id.endsWith("pressure_plate") || id.endsWith("_button") || id.endsWith("_sign")
				|| id.endsWith("rail") || id.endsWith("_sapling") || id.endsWith("flower") || id.endsWith("_tulip") || id.endsWith("grass")) return w;
		boolean floor = !(id.endsWith("_pane") || id.endsWith("_bars") || id.contains("fence") || id.endsWith("_wall") || id.endsWith("_bed")
				|| id.contains("lantern") || id.endsWith("_trapdoor") || id.contains("candle") || id.endsWith("flower_pot"));
		return w.with(c, floor);
	}

	/**
	 * Off the walk box nothing about her is known (no reach, no guard): before the op, a walk to the nearest standable cell of
	 * the box at the origin's level, so the op is judged on the model (O94ah re-review F1: a CP-75 re-dispatch from storage
	 * found every op of a high phase skipped and then manual). Once per op; repair_zone and leave ops excepted; nothing when no
	 * such cell exists (the ground-only fallback then applies).
	 */
	boolean enterSite(BodyServices b) {
		if (repair || "leave".equals(cur.kind) || entries >= 2) return false;
		Walk w = walk(b.minecraft().level);
		BlockPos at = spot(b.player());
		if (w.in(new Walk.P(at.getX(), at.getY(), at.getZ()))) return false;
		BlockPos best = null;
		double bd = Double.MAX_VALUE;
		for (int y = w.y0; y <= w.y1; y++) // any ground cell (y <= origin or the rim: a sunk origin has none at the origin's level)
			for (int x = w.x0; x <= w.x1; x++)
				for (int z = w.z0; z <= w.z1; z++) {
					BlockPos q = new BlockPos(x, y, z);
					double dd = at.distSqr(q);
					Walk.P c = new Walk.P(x, y, z);
					if (dd < bd && !badSpots.contains(q) && ground(c) && w.standable(c)) {
						best = q;
						bd = dd;
					}
				}
		if (best == null) return false;
		entries++; // twice per dispatch at most: a site Baritone cannot enter must not cost a walk per op (re-review)
		EveBuilder.LOG.info("eve-builder: {} is off the walk box: walk onto the site's ground at {} before {} {}", at.toShortString(), best.toShortString(), cur.kind, cur.pos().toShortString());
		walk(b, best);
		return true;
	}

	boolean designFills(BlockPos w) {
		String st = d.grid.state(w.getX() - ox, w.getY() - oy, w.getZ() - oz);
		return !Plan.isAir(st) && !st.equals(Design.ANY);
	}

	/** Main-inventory index (9..35) holding the item, or -1. */
	static int invSlot(LocalPlayer p, String item) {
		for (int i = 9; i < 36; i++) {
			ItemStack s = p.getInventory().getItem(i);
			if (!s.isEmpty() && BuiltInRegistries.ITEM.getKey(s.getItem()).toString().equals(item)) return i;
		}
		return -1;
	}
}
