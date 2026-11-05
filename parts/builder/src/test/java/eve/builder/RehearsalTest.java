package eve.builder;

import org.junit.jupiter.api.Test;

import java.net.URISyntaxException;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;

import static org.junit.jupiter.api.Assertions.*;

/**
 * S93: build_schematic rehearsed without a lane, like skills' ProspectWorldTest. A design and its build_order go into a fake
 * world (Phases' flat plot, top at y = -1) with a fake body (walks on Walk, places with support and reach, carries an exact
 * kit) and a fake sense (refuses breaking player-placed tree blocks, H-trees). The rehearsal follows Build's own order: the
 * build steps (final + scaffold, design's order), a FIX pass by Plan.classify over the grid, then scaffold removal; manual
 * notes come from Build.treeRefusal. house7 v1/v2 are design's 333e5829 files (not on main yet), copied to test resources.
 */
class RehearsalTest {
	static Path house7(String dir) throws URISyntaxException {
		return Path.of(RehearsalTest.class.getResource("designs/" + dir).toURI());
	}

	/** What a rehearsal saw: the ops in order, and every rule it broke (each list must stay empty). */
	static final class Run {
		final List<String> ops = new ArrayList<>(), unsupported = new ArrayList<>(), unreachable = new ArrayList<>(),
				stranded = new ArrayList<>(), outOfReach = new ArrayList<>(), short_ = new ArrayList<>(), replanned = new ArrayList<>(), underFeet = new ArrayList<>();
		final Map<Walk.P, String> world, manual = new LinkedHashMap<>();
		final Map<String, Integer> inv;
		int stands;

		Run(Map<Walk.P, String> world, Map<String, Integer> inv) {
			this.world = world;
			this.inv = inv;
		}
	}

	/** Rehearse d on world (design-local cells; pre-set cells are someone else's) with kit inv; placedByPlayer = cells sense guards. */
	static Run rehearse(Design d, Map<Walk.P, String> world, Map<String, Integer> inv, Set<Walk.P> placedByPlayer) {
		Run r = new Run(world, inv);
		int[] box = Phases.box(d.steps);
		Walk w = Phases.walk(world, box);
		Walk.P[] her = {new Walk.P(box[0] + 1, 0, box[2] + 1)}; // off the site, on the plot
		Map<Walk.P, Map<String, Integer>> scaffoldItems = new HashMap<>();
		for (Design.Step s : Phases.buildSteps(d.steps)) {
			Walk.P p = new Walk.P(s.x(), s.y(), s.z());
			if (Build.secondHalf(s.state()) || world.containsKey(p)) continue; // placed with its lower half; occupied: the FIX pass
			stand(r, w, her, s, p);
			place(r, w, her[0], p, s.state(), s.items());
			if (s.layer().equals("scaffold")) scaffoldItems.put(p, s.items());
			String id = Plan.State.parse(s.state()).id(); // a door's upper half, a bed's head: placed with the first half
			for (int[] o : new int[][] {{1, 0, 0}, {-1, 0, 0}, {0, 1, 0}, {0, 0, 1}, {0, 0, -1}}) {
				String other = d.grid.state(s.x() + o[0], s.y() + o[1], s.z() + o[2]);
				if (Build.secondHalf(other) && Plan.State.parse(other).id().equals(id))
					world.put(new Walk.P(s.x() + o[0], s.y() + o[1], s.z() + o[2]), other);
			}
		}
		Map<Walk.P, Map<String, Integer>> finalItems = new HashMap<>();
		for (Design.Step s : d.steps) if (s.layer().equals("final")) finalItems.put(new Walk.P(s.x(), s.y(), s.z()), s.items());
		Design.Grid g = d.grid;
		Set<Walk.P> removal = Build.removalCells(d.steps);
		for (int y = 0; y < g.sy(); y++) // FIX: one round, Build.planFixes' classes
			for (int z = 0; z < g.sz(); z++)
				for (int x = 0; x < g.sx(); x++) {
					Walk.P p = new Walk.P(x, y, z);
					String want = g.state(x, y, z), have = world.getOrDefault(p, Design.AIR);
					if (want.equals(Design.ANY) || Build.secondHalf(want) || Build.ok(Plan.classify(want, have))) continue;
					if (Plan.isAir(want) && removal.contains(p)) continue; // left to its scaffold_removal row
					if (!Plan.isAir(have)) {
						r.ops.add("break " + p);
						underFeet(r, her[0], p);
						String note = placedByPlayer.contains(p) ? Build.treeRefusal("break", false, Plan.State.parse(have).id().replace("minecraft:", "")) : null;
						if (note != null) {
							r.manual.put(p, note);
							continue;
						}
						world.remove(p);
						scaffoldItems.getOrDefault(p, Map.of()).forEach((k, v) -> inv.merge(k, v, Integer::sum)); // its drop, picked up
						her[0] = fall(w, her[0], box);
					}
					if (!Plan.isAir(want)) place(r, w, her[0], p, want, finalItems.getOrDefault(p, Map.of()));
				}
		for (Design.Step s : d.steps) { // REMOVE: design's removal steps, in order; the scaffold block comes back to her
			if (!s.layer().equals("scaffold_removal")) continue;
			Walk.P p = new Walk.P(s.x(), s.y(), s.z());
			stand(r, w, her, s, p);
			r.ops.add("break " + p);
			underFeet(r, her[0], p);
			if (!world.containsKey(p) || g.in(s.x(), s.y(), s.z()) && !Plan.isAir(g.state(s.x(), s.y(), s.z()))) continue;
			world.remove(p);
			scaffoldItems.getOrDefault(p, Map.of()).forEach((k, v) -> inv.merge(k, v, Integer::sum));
			her[0] = fall(w, her[0], box);
		}
		return r;
	}

	/** O256: a block she breaks is never the one she stands on (no drop). */
	static void underFeet(Run r, Walk.P her, Walk.P p) {
		if (p.equals(new Walk.P(her.x(), her.y() - 1, her.z()))) r.underFeet.add(p + " under her at " + her);
	}

	/** A block broken from under her: she drops to the first spot she can stand on. */
	static Walk.P fall(Walk w, Walk.P her, int[] box) {
		Walk.P f = her;
		while (!w.standable(f) && f.y() > box[1]) f = new Walk.P(f.x(), f.y() - 1, f.z());
		return w.standable(f) ? f : her;
	}

	/** Walk to design's stand for s: reachable from where she is, with a walk down from it; within reach of the cell. */
	static void stand(Run r, Walk w, Walk.P[] her, Design.Step s, Walk.P p) {
		if (s.stand() == null) return; // Build looks for a spot live; nothing to rehearse
		r.stands++;
		Walk.P f = new Walk.P(s.stand()[0], s.stand()[1], s.stand()[2]), from = her[0];
		if (!w.reaches(from, q -> q.equals(f))) { // O94ah: Build drops design's stand and takes a reachable spot in reach (Candidates)
			Walk.P alt = w.reach(from).stream().filter(q -> !q.equals(p) && reach(q, p) <= Build.REACH && w.reaches(q, y -> y.y() <= 0))
					.min(java.util.Comparator.comparingDouble(q -> reach(q, p))).orElse(null);
			if (alt == null) r.unreachable.add(f + " from " + from + " for " + p);
			else {
				r.replanned.add(p + ": design's " + f + " not reachable from " + from + ", took " + alt);
				her[0] = alt;
				return;
			}
		}
		if (!w.reaches(f, q -> q.y() <= 0)) r.stranded.add(f + " for " + p);
		her[0] = f;
		if (reach(f, p) > Build.REACH) r.outOfReach.add(p + " from " + f);
	}

	/** Eye at feet f to the nearest point of cell p. */
	static double reach(Walk.P f, Walk.P p) {
		double ex = f.x() + 0.5, ey = f.y() + Build.EYE, ez = f.z() + 0.5;
		double dx = Math.max(0, Math.max(p.x() - ex, ex - p.x() - 1)), dy = Math.max(0, Math.max(p.y() - ey, ey - p.y() - 1)),
				dz = Math.max(0, Math.max(p.z() - ez, ez - p.z() - 1));
		return Math.sqrt(dx * dx + dy * dy + dz * dz);
	}

	static void place(Run r, Walk w, Walk.P her, Walk.P p, String state, Map<String, Integer> items) {
		r.ops.add("place " + p + " " + state);
		boolean sup = p.y() == 0;
		for (int[] o : new int[][] {{1, 0, 0}, {-1, 0, 0}, {0, 1, 0}, {0, -1, 0}, {0, 0, 1}, {0, 0, -1}})
			sup |= !Plan.isAir(r.world.getOrDefault(new Walk.P(p.x() + o[0], p.y() + o[1], p.z() + o[2]), Design.AIR));
		if (!sup) r.unsupported.add(p + " " + state);
		items.forEach((k, v) -> {
			if (r.inv.merge(k, -v, Integer::sum) < 0) r.short_.add(k + " at " + p);
		});
		r.world.put(p, state);
		if (w.standable(her) && !w.reaches(her, q -> q.y() <= 0)) r.stranded.add(her + " after placing " + p);
	}

	/** Every grid cell as designed (air included) and nothing else left on the plot. */
	static List<String> wrongCells(Design d, Map<Walk.P, String> world) {
		List<String> out = new ArrayList<>();
		Design.Grid g = d.grid;
		for (int y = 0; y < g.sy(); y++)
			for (int z = 0; z < g.sz(); z++)
				for (int x = 0; x < g.sx(); x++) {
					String want = g.state(x, y, z);
					if (!want.equals(Design.ANY) && !Build.ok(Plan.classify(want, world.getOrDefault(new Walk.P(x, y, z), Design.AIR))))
						out.add(x + "," + y + "," + z + " " + want + " != " + world.get(new Walk.P(x, y, z)));
				}
		world.forEach((p, s) -> {
			if (!g.in(p.x(), p.y(), p.z())) out.add("left outside the grid: " + p + " " + s);
		});
		return out;
	}

	static Map<String, Integer> layerItems(Design d, String layer) {
		Map<String, Integer> m = new TreeMap<>();
		for (Design.Step s : d.steps) if (s.layer().equals(layer)) s.items().forEach((k, v) -> m.merge(k, v, Integer::sum));
		return m;
	}

	static Map<String, Integer> kit(Design d) {
		Map<String, Integer> m = layerItems(d, "final");
		layerItems(d, "scaffold").forEach((k, v) -> m.merge(k, v, Integer::sum));
		return m;
	}

	static void assertClean(String name, Run r) {
		assertEquals(List.of(), r.unsupported, name + ": placed with nothing to click");
		assertEquals(List.of(), r.unreachable, name + ": design's stand not reachable");
		assertEquals(List.of(), r.stranded, name + ": no walk down");
		assertEquals(List.of(), r.outOfReach, name + ": cell out of reach of design's stand");
		assertEquals(List.of(), r.short_, name + ": ran short of items");
		assertEquals(List.of(), r.underFeet, name + ": broke the block under her own feet (O256: FIX leaves scaffold to its removal row)");
	}

	static List<Design> designs() throws Exception {
		return List.of(Design.load(house7("house7")), Design.load(house7("house7_v2")), Design.load(DesignTest.OUT.resolve("nether_portal")),
				Design.load(DesignTest.OUT.resolve("nether_portal_v3")), Design.load(DesignTest.OUT.resolve("hut5")));
	}

	/** Each design on an empty plot with its exact kit: design's order, scaffold up and gone, every cell right, O59 exact. */
	@Test
	void designsBuildCleanInDesignsOrder() throws Exception {
		for (Design d : designs()) {
			String name = d.json.get("project_id").getAsString() + " v" + d.json.get("version").getAsInt();
			Map<String, Integer> inv = kit(d);
			Run r = rehearse(d, new HashMap<>(), inv, Set.of());
			List<String> want = new ArrayList<>();
			for (Design.Step s : Phases.buildSteps(d.steps))
				if (!Build.secondHalf(s.state())) want.add("place " + new Walk.P(s.x(), s.y(), s.z()) + " " + s.state());
			for (Design.Step s : d.steps) if (s.layer().equals("scaffold_removal")) want.add("break " + new Walk.P(s.x(), s.y(), s.z()));
			assertEquals(want, r.ops, name + ": the op sequence is design's order, no FIX round (in-grid scaffold left to its removal row)");
			assertClean(name, r);
			assertEquals(List.of(), wrongCells(d, r.world), name + ": as designed, scaffold gone");
			Map<String, Integer> left = new TreeMap<>(inv);
			left.values().removeIf(v -> v == 0);
			assertEquals(layerItems(d, "scaffold"), left, name + ": O59: the finals spent exactly, every scaffold block back");
			assertEquals(r.stands, (int) d.steps.stream().filter(s -> s.stand() != null && !Build.secondHalf(s.state())).count());
			System.out.println("rehearsal " + name + ": " + r.ops.size() + " ops, " + r.stands + " stands, kit " + kit(d) + ", replanned "
					+ r.replanned.size() + " " + r.replanned);
		}
	}

	/** H-trees: a player-placed oak log where house7 v2 wants planks cannot be broken (sense refuses): that cell goes manual with
	 *  Build's note, the build goes on, and its planks stay in her inventory. A player's dirt in another wall cell is broken and fixed. */
	@Test
	void aPlayerPlacedLogGoesManualAndTheBuildGoesOn() throws Exception {
		Design d = Design.load(house7("house7_v2"));
		List<Walk.P> planks = new ArrayList<>();
		for (Design.Step s : d.steps)
			if (s.layer().equals("final") && s.state().equals("minecraft:oak_planks") && s.y() == 1) planks.add(new Walk.P(s.x(), s.y(), s.z()));
		Walk.P log = planks.get(0), dirt = planks.get(planks.size() - 1);
		Map<Walk.P, String> world = new HashMap<>(Map.of(log, "minecraft:oak_log[axis=y]", dirt, "minecraft:dirt"));
		Map<String, Integer> inv = kit(d);
		Run r = rehearse(d, world, inv, Set.of(log, dirt));
		assertClean("house7 v2 + log", r);
		assertEquals(Map.of(log, "the server refused breaking a player-placed oak_log (sense H-trees, placed_block); left as it is"), r.manual);
		assertTrue(r.ops.contains("break " + dirt) && r.ops.contains("place " + dirt + " minecraft:oak_planks"), "the dirt is fixed");
		assertFalse(r.ops.contains("place " + log + " minecraft:oak_planks"), "nothing placed over the log");
		List<String> wrong = wrongCells(d, r.world);
		assertEquals(1, wrong.size(), "only the log's cell differs: " + wrong);
		assertTrue(wrong.get(0).startsWith(log.x() + "," + log.y() + "," + log.z() + " minecraft:oak_planks"));
		Map<String, Integer> left = new TreeMap<>(inv);
		left.values().removeIf(v -> v == 0);
		Map<String, Integer> want = new TreeMap<>(layerItems(d, "scaffold"));
		want.merge("minecraft:oak_planks", 1, Integer::sum);
		assertEquals(want, left, "O59: the manual cell's planks unspent, nothing else");
	}

	@Test
	void treeRefusalIsOnlyABreakOutsideRemoval() {
		assertNull(Build.treeRefusal("break", true, "oak_log"), "a refused scaffold-removal break still ends the build");
		assertNull(Build.treeRefusal("place", false, "oak_log"));
		assertNull(Build.treeRefusal("break", false, "dirt"));
		for (String id : List.of("spruce_log", "stripped_oak_wood", "crimson_stem", "warped_hyphae", "azalea_leaves"))
			assertNotNull(Build.treeRefusal("break", false, id), id);
	}

	/** repair_zone: house7 v2 built, then a crater in its north-east wall top, a player's log on the roof and an owner edit (a
	 *  lantern for one window pane, absent from sense's steps). Sense's steps (removals top-down, then placements bottom-up) run
	 *  in sense's order (Build keeps it for a repair): every place supported, the house back but the owner's edit, items exact. */
	@Test
	void repairZoneRestoresACraterInSensesOrder() throws Exception {
		Design h = Design.load(house7("house7_v2"));
		Map<Walk.P, String> world = rehearse(h, new HashMap<>(), kit(h), Set.of()).world;
		Map<Walk.P, String> before = new HashMap<>(world);
		Walk.P pane = null;
		for (Map.Entry<Walk.P, String> e : world.entrySet()) if (e.getValue().contains("glass_pane")) pane = e.getKey();
		world.put(pane, "minecraft:lantern[hanging=false,waterlogged=false]"); // the owner's edit: kept
		List<Walk.P> crater = new ArrayList<>();
		for (Walk.P p : before.keySet())
			if (Math.abs(p.x() - 6) + Math.abs(p.z() - 0) + Math.abs(p.y() - 4) <= 2 && !p.equals(pane)) crater.add(p);
		crater.forEach(world::remove);
		int top = before.keySet().stream().filter(p -> p.x() == 3 && p.z() == 3).mapToInt(Walk.P::y).max().orElseThrow();
		Walk.P log = new Walk.P(3, top + 1, 3); // on the roof's ridge
		world.put(log, "minecraft:oak_log[axis=y]");
		com.google.gson.JsonArray steps = new com.google.gson.JsonArray();
		steps.add(step("remove", log, "minecraft:oak_log"));
		crater.sort(java.util.Comparator.comparingInt(Walk.P::y));
		for (Walk.P p : crater) steps.add(step("place", p, before.get(p)));
		Design r = Design.fromSteps(steps);
		Map<String, Integer> inv = new TreeMap<>(), kit = new TreeMap<>();
		for (Design.Step s : r.steps) s.items().forEach((k, v) -> kit.merge(k, v, Integer::sum));
		inv.putAll(kit);
		Run run = new Run(world, inv);
		Walk w = Phases.walk(world, Phases.box(h.steps));
		Walk.P away = new Walk.P(-100, 0, -100);
		for (Design.Step s : r.steps) {
			Walk.P p = new Walk.P(r.grid.ox() + s.x(), r.grid.oy() + s.y(), r.grid.oz() + s.z());
			if (s.layer().equals("remove")) {
				run.ops.add("break " + p);
				if (world.containsKey(p) && Plan.State.parse(world.get(p)).id().equals(Plan.State.parse(s.state()).id())) world.remove(p);
			} else if (!Build.ok(Plan.classify(s.state(), world.getOrDefault(p, Design.AIR)))) place(run, w, away, p, s.state(), s.items());
		}
		assertEquals(List.of(), run.unsupported, "a repair place with nothing to click");
		assertEquals(List.of(), run.short_);
		assertEquals("break " + log, run.ops.get(0), "the removal first");
		assertEquals(crater.size() + 1, run.ops.size(), "one op per step");
		Map<Walk.P, String> want = new HashMap<>(before);
		want.put(pane, "minecraft:lantern[hanging=false,waterlogged=false]");
		assertEquals(want, world, "the house as it was, the owner's lantern kept, the log gone");
		inv.values().removeIf(v -> v == 0);
		assertEquals(Map.of(), inv, "O59: the repair's items spent exactly");
		System.out.println("rehearsal repair_zone: " + run.ops.size() + " ops, kit " + kit);
	}

	static com.google.gson.JsonObject step(String op, Walk.P p, String block) {
		com.google.gson.JsonObject o = new com.google.gson.JsonObject(), pos = new com.google.gson.JsonObject();
		pos.addProperty("x", p.x());
		pos.addProperty("y", p.y());
		pos.addProperty("z", p.z());
		o.addProperty("op", op);
		o.add("pos", pos);
		o.addProperty("block", block);
		return o;
	}

	/** bridge: the line, a crossing on a fake gap (each cell placed against the one before), and the refusals before she steps out. */
	@Test
	void bridgeLineCrossingAndRefusals() {
		net.minecraft.core.BlockPos start = new net.minecraft.core.BlockPos(409, 63, 0);
		List<net.minecraft.core.BlockPos> line = Bridge.line(start, new net.minecraft.core.BlockPos(439, 63, 0), 40);
		assertEquals(30, line.size());
		assertNull(Bridge.line(start, new net.minecraft.core.BlockPos(439, 64, 0), 40), "not level");
		assertNull(Bridge.line(start, new net.minecraft.core.BlockPos(419, 63, 3), 40), "not straight");
		assertNull(Bridge.line(start, new net.minecraft.core.BlockPos(450, 63, 0), 40), "over max_blocks");
		assertEquals(start.offset(0, 0, -1), Bridge.line(start, start.offset(0, 0, -5), 40).get(0), "towards -z");
		Set<net.minecraft.core.BlockPos> solid = new java.util.HashSet<>(Set.of(start, line.get(12))); // an island mid-gap
		int gap = (int) line.stream().filter(c -> !solid.contains(c)).count();
		Map<String, Integer> inv = new HashMap<>(Map.of("minecraft:cobblestone", gap));
		assertNull(Bridge.noItem("minecraft:cobblestone", inv, gap));
		net.minecraft.core.BlockPos prev = start;
		for (net.minecraft.core.BlockPos c : line) {
			assertEquals(1, c.distManhattan(prev), "placed against the block she stands on");
			assertTrue(solid.contains(prev), "she stands on a solid block");
			if (solid.add(c)) inv.merge("minecraft:cobblestone", -1, Integer::sum);
			prev = c;
		}
		assertEquals(0, inv.get("minecraft:cobblestone"), "the gap's count spent exactly");
		var no = Bridge.noItem("minecraft:cobblestone", Map.of("minecraft:cobblestone", gap - 1), gap);
		assertEquals("no_item", no.code());
		assertEquals("needs 29 bridge blocks, has 28 minecraft:cobblestone", no.note());
		assertEquals("needs 29 bridge blocks", Bridge.noItem(null, Map.of(), gap).note());
		assertEquals("minecraft:ender_pearl", Bridge.recovery(Map.of("minecraft:ender_pearl", 1, "minecraft:water_bucket", 1), true, false));
		assertEquals("slow_falling", Bridge.recovery(Map.of("minecraft:water_bucket", 1), true, true));
		assertEquals("minecraft:water_bucket", Bridge.recovery(Map.of("minecraft:water_bucket", 1), false, false));
		assertNull(Bridge.recovery(Map.of("minecraft:water_bucket", 1), false, true), "water saves nothing over the void");
		assertNull(Bridge.noRecovery(false, false, null), "a shallow gap needs none");
		assertNull(Bridge.noRecovery(true, true, "minecraft:ender_pearl"));
		assertEquals("the gap is deeper than 3 blocks (open void) and she carries no ender pearl, slow-falling potion",
				Bridge.noRecovery(true, true, null).note());
		assertEquals("the gap is deeper than 3 blocks and she carries no ender pearl, slow-falling potion or water bucket",
				Bridge.noRecovery(true, false, null).note());
	}

	/** The manual-cell report: evidence.manual rows {pos, why} in the order cells were left, with each reason's exact words. */
	@Test
	void manualRowsReportTheReasonsInOrder() {
		Map<net.minecraft.core.BlockPos, String> manual = new LinkedHashMap<>();
		manual.put(new net.minecraft.core.BlockPos(-21, 191, 5), Build.treeRefusal("break", false, "oak_log"));
		manual.put(new net.minecraft.core.BlockPos(-20, 191, 5), "changed since this run placed it: kept (an edit by someone else, CP-76)");
		assertEquals("[{\"pos\":{\"x\":-21,\"y\":191,\"z\":5},\"why\":\"the server refused breaking a player-placed oak_log (sense H-trees, placed_block); left as it is\"},"
				+ "{\"pos\":{\"x\":-20,\"y\":191,\"z\":5},\"why\":\"changed since this run placed it: kept (an edit by someone else, CP-76)\"}]",
				Build.manualRows(manual).toString());
		assertEquals("[]", Build.manualRows(Map.of()).toString(), "none left: an empty list, not absent");
	}
}
