package eve.builder;

import org.junit.jupiter.api.Test;

import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.*;

/** O89c dev fixture: hut5_tall's room with the 15 scaffold cells ground-only removal left (CP-49 lane C a11), on a flat plot.
 *  Without a level way in (5d03731) the room is closed to Baritone (a12/a13); with design's doorstep (abb5260d) all 15 come out. */
class WalkTest {
	static Walk.World world(Map<Walk.P, String> blocks) {
		return new Walk.World() {
			public boolean free(int x, int y, int z) {
				String s = blocks.get(new Walk.P(x, y, z));
				return y >= 0 && (s == null || s.contains("door") || s.contains("torch"));
			}

			public boolean floor(int x, int y, int z) {
				String s = blocks.get(new Walk.P(x, y, z));
				return y == -1 || s != null && !s.contains("door") && !s.contains("torch") && !s.contains("chest") && !s.contains("pane");
			}

			public boolean door(int x, int y, int z) {
				String s = blocks.get(new Walk.P(x, y, z));
				return s != null && s.contains("door");
			}
		};
	}

	/** The eye-to-centre ray, sampled: no other block in the way (torches aside), within reach. */
	static boolean sees(Map<Walk.P, String> blocks, Walk.P feet, Walk.P t) {
		double ex = feet.x() + 0.5, ey = feet.y() + Build.EYE, ez = feet.z() + 0.5, cx = t.x() + 0.5, cy = t.y() + 0.5, cz = t.z() + 0.5;
		if (Math.sqrt((cx - ex) * (cx - ex) + (cy - ey) * (cy - ey) + (cz - ez) * (cz - ez)) > Build.REACH) return false;
		for (int i = 0; i < 64; i++) {
			double k = i / 64.0;
			Walk.P c = new Walk.P((int) Math.floor(ex + k * (cx - ex)), (int) Math.floor(ey + k * (cy - ey)), (int) Math.floor(ez + k * (cz - ez)));
			if (c.equals(t)) return true;
			String s = blocks.get(c);
			if (s != null && !s.contains("torch")) return false;
		}
		return true;
	}

	/** hut5_tall's blocks (design-local) with the 15 room scaffold cells in place; the cells in design order. */
	static Map<Walk.P, String> room(List<Walk.P> left) throws Exception {
		Design d = Design.load(DesignTest.OUT.resolve("hut5_tall"));
		Design.Grid g = d.grid;
		Map<Walk.P, String> blocks = new HashMap<>();
		for (int y = 0; y < g.sy(); y++)
			for (int z = 0; z < g.sz(); z++)
				for (int x = 0; x < g.sx(); x++)
					if (!Plan.isAir(g.state(x, y, z))) blocks.put(new Walk.P(x, y, z), g.state(x, y, z));
		for (Design.Step s : d.steps)
			if (s.layer().equals("scaffold_removal") && s.x() >= 1 && s.x() < g.sx() - 1 && s.z() >= 1 && s.z() < g.sz() - 1)
				left.add(new Walk.P(s.x(), s.y(), s.z()));
		assertEquals(15, left.size());
		for (Walk.P c : left) blocks.put(c, "minecraft:dirt");
		return blocks;
	}

	static Walk walk(Map<Walk.P, String> blocks) {
		return new Walk(world(blocks), -5, -2, -5, 5 + 4, 8 + 3, 6 + 4); // hut5_tall 5x8x6 (abb5260d)
	}

	@Test
	void hut5TallRoomNeedsItsDoorstep() throws Exception {
		Map<Walk.P, String> blocks = room(new ArrayList<>());
		assertTrue(walk(blocks).reach(new Walk.P(2, 0, 6)).contains(new Walk.P(2, 1, 3)), "abb5260d: in over the doorstep, level");
		// a12/a13 (5d03731, no doorstep): the door (2,1,4) stands on the room floor, one up from the plot: a step up into a
		// door cell, never a route
		blocks.remove(new Walk.P(2, 0, 5));
		assertFalse(walk(blocks).reach(new Walk.P(2, 0, 6)).contains(new Walk.P(2, 1, 3)));
	}

	@Test
	void hut5TallRoomLeftoversComeOutFromReachableStands() throws Exception {
		List<Walk.P> left = new ArrayList<>();
		Map<Walk.P, String> blocks = room(left);
		assertTrue(blocks.get(new Walk.P(2, 0, 5)).contains("cobblestone"), "design's doorstep at floor height (abb5260d)");
		Walk walk = walk(blocks);
		Walk.P her = new Walk.P(2, 0, 6); // outside, in front of the doorstep
		boolean first = true;
		for (Walk.P t : left) {
			// as Build's break: from where she stands when she sees it and her walk down survives the break (8 of the 15)
			if (!first && !her.up(-1).equals(t) && sees(blocks, her, t) && walk.without(t).reaches(her, q -> q.y() <= 0)) {
				blocks.remove(t);
				continue;
			}
			Walk.P best = null;
			double bestD = Double.MAX_VALUE;
			boolean ground = false;
			for (Walk.P c : walk.reach(her)) {
				if (c.equals(t) || c.up(1).equals(t) || c.up(-1).equals(t) || !sees(blocks, c, t)) continue;
				ground |= c.y() == 0;
				double dd = Math.abs(c.x() - her.x()) + Math.abs(c.y() - her.y()) + Math.abs(c.z() - her.z());
				double score = dd + 2.0 * Math.max(0, c.y()) + (c.y() != 0 ? 100 : 0);
				if (score < bestD && walk.without(t).reaches(c, q -> q.y() <= 0)) {
					bestD = score;
					best = c;
				}
			}
			if (first) assertFalse(ground, "a ground spot sees " + t + ": the fixture would not reproduce a11"); // a11's rule left it
			first = false;
			assertNotNull(best, "no reachable stand with a route down for " + t);
			blocks.remove(t);
			her = best;
		}
		assertTrue(walk.reaches(her, q -> q.y() <= 0), "stranded at " + her);
	}

	@Test
	void noRouteDownFromAFiveHighPillar() {
		Map<Walk.P, String> blocks = new HashMap<>();
		for (int y = 0; y < 5; y++) blocks.put(new Walk.P(0, y, 0), "minecraft:dirt");
		Walk walk = new Walk(world(blocks), -3, -2, -3, 3, 8, 3);
		assertFalse(walk.reaches(new Walk.P(0, 5, 0), q -> q.y() <= 0)); // a drop of 5: Baritone falls 3 at most
		blocks.remove(new Walk.P(0, 4, 0));
		blocks.remove(new Walk.P(0, 3, 0));
		assertTrue(walk.reaches(new Walk.P(0, 3, 0), q -> q.y() <= 0)); // 3 high: drops to the ground
		assertTrue(walk.reach(new Walk.P(2, 0, 2)).contains(new Walk.P(0, 3, 0)) == false); // 3 up is no step
	}

	/** O94ah, CP-49 lane C a16's trap: a 2-deep pocket in a 3-high platform. She can step down into it from the top, and from
	 *  there no walk leads anywhere. stand() rejects such a cell (no_walk_down, review #1); if she ends there anyway the
	 *  stranding guard stops the build, after the walk or at the next op's start (review #2). */
	@Test
	void pocketInAPlatformStrandsHer() {
		Map<Walk.P, String> blocks = new HashMap<>();
		for (int x = -2; x <= 2; x++)
			for (int z = -2; z <= 2; z++)
				for (int y = 0; y < 3; y++) blocks.put(new Walk.P(x, y, z), "minecraft:dirt");
		blocks.remove(new Walk.P(0, 2, 0));
		blocks.remove(new Walk.P(0, 1, 0));
		Walk walk = new Walk(world(blocks), -6, -2, -6, 6, 6, 6);
		Walk.P top = new Walk.P(1, 3, 0), pocket = new Walk.P(0, 1, 0);
		assertTrue(walk.reaches(top, q -> q.y() <= 0), "the top drops 3 to the ground");
		assertTrue(walk.reach(top).contains(pocket), "a 2-block step down into the pocket");
		assertFalse(walk.reaches(pocket, q -> q.y() <= 0), "stranded in the pocket");
		assertEquals(Set.of(pocket), walk.reach(pocket));
	}

	/** O94ah offline check: replaying design's order on a flat plot (Phases' world), every planned stand of the designs that
	 *  passed on lane C is reachable from the one before (the stand filter never drops design's spot) and has a walk down at
	 *  its turn (the stranding guard never stops them). */
	@Test
	void passingDesignsKeepEveryStand() throws Exception {
		for (String name : List.of("hut5_sim", "hut5_tall", "pillar_tall")) {
			List<Design.Step> steps = Design.load(DesignTest.OUT.resolve(name)).steps;
			Map<Walk.P, String> cells = new HashMap<>();
			int[] box = Phases.box(steps);
			Walk w = Phases.walk(cells, box);
			Walk.P her = new Walk.P(box[0] + 1, 0, box[2] + 1); // off the site, on the plot
			int n = 0;
			for (Design.Step s : steps) {
				if (s.stand() != null) {
					Walk.P f = new Walk.P(s.stand()[0], s.stand()[1], s.stand()[2]), from = her;
					assertTrue(w.reaches(from, q -> q.equals(f)), name + ": stand " + f + " not reachable from " + from);
					assertTrue(w.reaches(f, q -> q.y() <= 0), name + ": stranded on " + f);
					her = f;
					n++;
				}
				Walk.P p = new Walk.P(s.x(), s.y(), s.z());
				if (s.layer().equals("scaffold_removal")) cells.remove(p);
				else cells.put(p, s.state());
				final Walk.P at = her; // the next op starts where she stands: the op-start guard (review #2) must stay quiet
				assertTrue(!w.standable(at) || w.reaches(at, q -> q.y() <= 0), name + ": stranded on " + at + " after placing " + p);
			}
			assertEquals(steps.size(), n, name + ": every step has design's stand");
		}
	}

	/** O94ah re-review F2: placing a block can shut her only walk down. On a 3-high pillar walled on three sides at feet and
	 *  head height, the open side drops 3 to the plot; with that cell filled (Walk.with) no walk leads down, so she must not
	 *  place it from there. */
	@Test
	void placingHerOnlyWayDownStrandsHer() {
		Map<Walk.P, String> blocks = new HashMap<>();
		for (int y = 0; y < 3; y++) blocks.put(new Walk.P(0, y, 0), "minecraft:dirt");
		for (int y = 3; y < 5; y++)
			for (Walk.P wall : List.of(new Walk.P(-1, y, 0), new Walk.P(0, y, 1), new Walk.P(0, y, -1))) blocks.put(wall, "minecraft:dirt");
		for (int y = 0; y < 3; y++) // the wall columns stand on pillars of their own, so their tops are no way down either
			for (Walk.P col : List.of(new Walk.P(-1, y, 0), new Walk.P(0, y, 1), new Walk.P(0, y, -1))) blocks.put(col, "minecraft:dirt");
		Walk walk = new Walk(world(blocks), -6, -2, -6, 6, 8, 6);
		Walk.P top = new Walk.P(0, 3, 0);
		assertTrue(walk.reaches(top, q -> q.y() <= 0), "east: a drop of 3 to the plot");
		assertFalse(walk.with(new Walk.P(1, 3, 0), true).reaches(top, q -> q.y() <= 0), "the east cell filled: stranded");
		assertFalse(walk.with(new Walk.P(1, 3, 0), false).reaches(top, q -> q.y() <= 0), "a pane there: no floor either, stranded");
		assertTrue(walk.with(new Walk.P(1, 0, 0), true).reaches(top, q -> q.y() <= 0), "a block on the plot below only shortens the drop");
		assertFalse(walk.withDoor(new Walk.P(1, 3, 0)).reaches(top, q -> q.y() <= 0), "a door there: no drop out of a door cell");
	}

	/** O94ah, CP-49 lane C a18: she stood at (29.55, 152.0, -43.20), on the edge of the block at z -43 with her centre over
	 *  air at z -44. Her spot is the cell her box stands in, not "off the walk model"; mid-step one level down counts too. */
	@Test
	void spotOnAnEdgeIsTheCellThatCarriesHer() {
		Map<Walk.P, String> blocks = new HashMap<>();
		blocks.put(new Walk.P(29, 151, -43), "minecraft:dirt"); // the step she stands on; (29,151,-44) is air
		for (int x = 27; x <= 31; x++)
			for (int z = -46; z <= -41; z++) blocks.put(new Walk.P(x, 149, z), "minecraft:stone");
		Walk walk = new Walk(world(blocks), 20, 140, -50, 40, 160, -30);
		assertFalse(walk.standable(new Walk.P(29, 152, -44)), "the centre cell is over air");
		assertEquals(new Walk.P(29, 152, -43), Build.spotOf(walk, 29.546, 152.0, -43.196, 0.3));
		assertEquals(new Walk.P(28, 150, -43), Build.spotOf(walk, 28.5, 150.4, -42.5, 0.3), "mid-step up off the stone: one level down");
		assertNull(Build.spotOf(walk, 25.5, 156.0, -43.5, 0.3), "mid-air over nothing: unknown");
	}

	/** O94ah review #0: the live walk box holds every step and planned stand with its margin (C3's horse: scaffold and stands
	 *  6 west of the grid) and is never smaller than the grid box it replaces. */
	@Test
	void walkBoxHoldsScaffoldAndStands() throws Exception {
		for (Path dir : List.of(DesignTest.OUT.resolve("hut5_sim"), DesignTest.OUT.resolve("../../organic/examples/cp69-horse-triposr").normalize())) {
			Design d = Design.load(dir);
			int[] b = Build.walkBox(d);
			int out = 0;
			for (Design.Step s : d.steps)
				for (int[] c : s.stand() == null ? new int[][] {{s.x(), s.y(), s.z()}} : new int[][] {{s.x(), s.y(), s.z()}, s.stand()})
					for (int i = 0; i < 3; i++)
						if (c[i] < b[i] + (i == 1 ? 2 : 5) || c[i] > b[i + 3] - (i == 1 ? 4 : 5)) out++;
			assertEquals(0, out, dir + ": cells closer to the box edge than its margin");
			assertTrue(b[0] <= -5 && b[1] <= -2 && b[2] <= -5 && b[3] >= d.grid.sx() + 4 && b[4] >= d.grid.sy() + 3 && b[5] >= d.grid.sz() + 4,
					dir + ": never smaller than the grid box it replaces");
		}
		Design horse = Design.load(DesignTest.OUT.resolve("../../organic/examples/cp69-horse-triposr").normalize());
		assertTrue(Build.walkBox(horse)[0] < -5, "the horse's west scaffold widens the box past the grid's");
	}
}
