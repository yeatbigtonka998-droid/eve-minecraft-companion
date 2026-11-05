package eve.builder;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

import static org.junit.jupiter.api.Assertions.*;

/** CP-75 fake tests: phases, door-safe cuts, the run file, and exact continuation across a pause and a crash. */
class PhasesTest {
	static List<Design.Step> hut5TallSteps() throws Exception {
		return Phases.buildSteps(Design.load(DesignTest.OUT.resolve("hut5_tall")).steps);
	}

	@Test
	void phasesCoverTheBuildOnceInOrderWithTheirBudgets() throws Exception {
		List<Design.Step> steps = hut5TallSteps();
		List<Phases.Phase> ph = Phases.plan(steps, 40);
		boolean[] safe = Phases.safeCuts(steps);
		assertEquals(0, ph.get(0).from());
		assertEquals(steps.size(), ph.get(ph.size() - 1).to());
		Map<String, Integer> all = new TreeMap<>();
		for (int k = 0; k < ph.size(); k++) {
			Phases.Phase p = ph.get(k);
			if (k > 0) assertEquals(ph.get(k - 1).to(), p.from(), "contiguous");
			assertTrue(p.to() > p.from());
			assertTrue(p.to() == steps.size() || safe[p.to()], "phase " + k + " ends at a safe cut");
			p.budget().forEach((i, n) -> all.merge(i, n, Integer::sum));
		}
		assertEquals(Phases.budget(steps, 0, steps.size()), all, "the phase budgets add up to the build's items");
		assertTrue(ph.size() >= 6, "hut5_tall's 209 steps in phases of ~40");
	}

	@Test
	void aDoorWithoutALevelWayInShutsEveryCutAfterIt() throws Exception {
		List<Design.Step> steps = new ArrayList<>(hut5TallSteps());
		int door = -1;
		for (int i = 0; i < steps.size(); i++)
			if (Phases.isDoor(steps.get(i).state()) && steps.get(i).state().contains("half=lower")) door = i;
		assertTrue(door > 0, "hut5_tall places its door");
		boolean[] safe = Phases.safeCuts(steps);
		assertTrue(safe[door + 1], "abb5260d: the doorstep (2,0,5) keeps the door's way through");
		steps.removeIf(s -> s.x() == 2 && s.y() == 0 && s.z() == 5); // 5d03731: no doorstep, the door one up from the plot
		int door2 = door - 1;
		boolean[] shut = Phases.safeCuts(steps);
		for (int c = door2 + 1; c <= steps.size(); c++) assertFalse(shut[c], "after the door, cut " + c + " is shut");
		List<Phases.Phase> ph = Phases.plan(steps, 40);
		assertEquals(steps.size(), ph.get(ph.size() - 1).to());
		assertTrue(ph.get(ph.size() - 1).from() <= door2, "no pause between the door and the end: the last phase holds it");
	}

	@Test
	void theRunFileRoundTrips(@TempDir Path dir) throws Exception {
		Phases.Run r = new Phases.Run();
		r.runId = "hut5tall-0001";
		r.projectId = "hut5_tall";
		r.version = 1;
		r.designHash = "c65ebf701b17";
		r.origin = new int[] {23, 151, -45};
		r.phases.add(new int[] {0, 40});
		r.phases.add(new int[] {40, 80});
		r.phase = 1;
		r.status = "paused";
		r.pauseReason = "day_time";
		r.placed.put(Phases.Run.key(23, 151, -45), "minecraft:cobblestone");
		r.spent.put("minecraft:cobblestone", 1);
		r.phaseMatched.put("0", 38);
		r.updatedMs = 1234;
		r.save(dir);
		assertNull(Phases.Run.load(dir, "other-run-0001"));
		Phases.Run back = Phases.Run.load(dir, "hut5tall-0001");
		assertEquals(r.toJson(), back.toJson());
		assertFalse(dir.resolve("hut5tall-0001.json.tmp").toFile().exists(), "the temp file is renamed away");
	}

	/** A fake world over design-local cells: place = put; classify = match | missing | changed. */
	static String classify(Map<Walk.P, String> world, Design.Step s) {
		String have = world.get(new Walk.P(s.x(), s.y(), s.z()));
		return have == null ? "missing" : have.equals(s.state()) ? "match" : "changed";
	}

	@Test
	void aRunContinuesExactlyAcrossAPauseAndACrash(@TempDir Path dir) throws Exception {
		List<Design.Step> steps = hut5TallSteps();
		List<Phases.Phase> ph = Phases.plan(steps, 40);
		Map<Walk.P, String> world = new HashMap<>();
		Map<Integer, Integer> placedTimes = new TreeMap<>();
		List<Integer> order = new ArrayList<>();
		Phases.Run run = new Phases.Run();
		run.runId = "fake-0001";
		run.projectId = "hut5_tall";
		run.version = 1;
		run.designHash = "c65ebf701b17";
		run.origin = new int[] {0, 0, 0};
		for (Phases.Phase p : ph) run.phases.add(new int[] {p.from(), p.to()});
		run.updatedMs = 0;
		// dispatch 1: two phases, then pause (as for dusk)
		for (int k = 0; k < 2; k++) {
			for (int i : Phases.todo(steps, ph, run.phase, s -> classify(world, s))) place(steps, i, world, run, placedTimes, order);
			run.phase++;
		}
		run.status = "paused";
		run.save(dir);
		// overnight someone changes a done cell (kept, drift) and another goes missing (re-placed first)
		Design.Step changed = steps.get(ph.get(0).from()), gone = steps.get(ph.get(1).from());
		world.put(new Walk.P(changed.x(), changed.y(), changed.z()), "minecraft:gold_block");
		world.remove(new Walk.P(gone.x(), gone.y(), gone.z()));
		// dispatch 2: resume, crash a few placements into the phase
		run = Phases.Run.load(dir, "fake-0001");
		Phases.Recon rc = Phases.reconcile(steps, ph.get(run.phase).from(), s -> classify(world, s));
		assertEquals(List.of(ph.get(1).from()), rc.redo());
		assertEquals(List.of(ph.get(0).from()), rc.drift());
		List<Integer> todo = Phases.todo(steps, ph, run.phase, s -> classify(world, s));
		assertEquals(ph.get(1).from(), todo.get(0), "the missing cell first");
		for (int i : todo.subList(0, 6)) place(steps, i, world, run, placedTimes, order);
		run.save(dir); // the crash: the file holds what was placed
		// dispatch 3: resume to the end
		run = Phases.Run.load(dir, "fake-0001");
		for (; run.phase < ph.size(); run.phase++)
			for (int i : Phases.todo(steps, ph, run.phase, s -> classify(world, s))) place(steps, i, world, run, placedTimes, order);
		for (int i = 0; i < steps.size(); i++) {
			int want = i == ph.get(1).from() ? 2 : 1; // the cell taken overnight was placed again, once
			if (i == ph.get(0).from()) continue; // the owner's gold block stays
			assertEquals(want, placedTimes.getOrDefault(i, 0), "step " + i + " placed exactly " + want + " time(s)");
		}
		assertEquals("minecraft:gold_block", world.get(new Walk.P(changed.x(), changed.y(), changed.z())));
		List<Integer> fresh = new ArrayList<>(order);
		fresh.remove(fresh.lastIndexOf(ph.get(1).from())); // the re-placement after the night; its first placement stays in order
		for (int k = 1; k < fresh.size(); k++) assertTrue(fresh.get(k) > fresh.get(k - 1), "design's order kept");
		assertEquals(Phases.budget(steps, 0, steps.size()).values().stream().mapToInt(Integer::intValue).sum() + 1,
				run.spent.values().stream().mapToInt(Integer::intValue).sum(), "every item accounted for (+1 for the cell placed again)");
	}

	static void place(List<Design.Step> steps, int i, Map<Walk.P, String> world, Phases.Run run, Map<Integer, Integer> times, List<Integer> order) {
		Design.Step s = steps.get(i);
		world.put(new Walk.P(s.x(), s.y(), s.z()), s.state());
		run.placed.put(Phases.Run.key(s.x(), s.y(), s.z()), s.state());
		s.items().forEach((k, v) -> run.spent.merge(k, v, Integer::sum));
		times.merge(i, 1, Integer::sum);
		order.add(i);
	}

	@Test
	void cellRowsAreTheMindsBuildCells() throws Exception {
		List<Design.Step> steps = hut5TallSteps();
		Design.Step a = steps.get(0), b = steps.get(1);
		Map<String, Long> at = Map.of(Phases.Run.key(100 + a.x(), 64 + a.y(), 200 + a.z()), 1234L);
		var rows = Phases.cellRows(steps, 0, 2, 100, 64, 200, s -> "minecraft:gold_block", s -> s == a, at);
		var r0 = rows.get(0).getAsJsonArray();
		assertEquals(100 + a.x(), r0.get(0).getAsInt());
		assertEquals(1234L, r0.get(3).getAsLong(), "placed_ts from the ledger");
		assertEquals(1, r0.get(4).getAsInt(), "verified");
		assertTrue(r0.get(5).isJsonNull(), "right: observed null, the mind takes state_expected from the design");
		var r1 = rows.get(1).getAsJsonArray();
		assertEquals(0L, r1.get(3).getAsLong(), "not placed by this run");
		assertEquals(0, r1.get(4).getAsInt());
		assertEquals("minecraft:gold_block", r1.get(5).getAsString());
		assertEquals(b.layer().equals("scaffold") ? 1 : 0, r1.get(6).getAsInt());
		assertTrue(rows.toString().length() < 2 * 60, "about 30 bytes a row: " + rows);
	}

	@Test
	void pauseDayTimeIsDueFromTheTargetUntilDawn() {
		assertFalse(Phases.pauseDue(11000, 12000), "before dusk: build");
		assertTrue(Phases.pauseDue(12000, 12000));
		assertTrue(Phases.pauseDue(12100, 12000), "re-dispatched just after dusk (after gathering): still pause tonight");
		assertTrue(Phases.pauseDue(23900, 12000), "the night through");
		assertFalse(Phases.pauseDue(500, 12000), "morning: build until dusk");
		assertFalse(Phases.pauseDue(6000, 12000));
	}

	@Test
	void aPhaseSizeBelowOneIsOne() throws Exception {
		List<Design.Step> steps = hut5TallSteps();
		assertEquals(Phases.plan(steps, 1).size(), Phases.plan(steps, -2).size());
		assertTrue(Phases.plan(List.of(), 40).isEmpty());
	}
}
