package eve.skills;

import com.google.gson.JsonParser;
import eve.body.api.Step;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.*;

/**
 * S93: prospect's step rules (stepCell, touching, stepHazard, floor, besideLava - the static functions its tick calls) driven
 * over synthetic worlds. The driver below stands in for the tick loop only: it digs the cells the tick digs (a step down:
 * feet, head and the head-room cell; level: feet and head), steps in, then runs the wall-ore pass. Body calls, walking,
 * seals, bridges and timing are not covered here - that is the live run's job.
 */
class ProspectWorldTest {
	static final String AIR = "minecraft:air", LAVA = "minecraft:lava", WATER = "minecraft:water";

	static final class World implements Prospect.Cells {
		final Map<BlockPos, String> set = new HashMap<>();
		final int surface; // stone (deepslate below y 0) at and under this y, air above

		World(int surface) { this.surface = surface; }

		public String id(BlockPos q) {
			return set.getOrDefault(q, q.getY() > surface ? AIR : q.getY() < 0 ? "minecraft:deepslate" : "minecraft:stone");
		}

		public boolean lava(BlockPos q) { return id(q).equals(LAVA); }

		public boolean water(BlockPos q) { return id(q).equals(WATER); }

		boolean solid(BlockPos q) { String i = id(q); return !i.equals(AIR) && !i.equals(LAVA) && !i.equals(WATER); }

		void put(String id, int x, int y, int z) { set.put(new BlockPos(x, y, z), id); }
	}

	/** The tick-loop stand-in: returns the stop reason; records what it broke, mined and left. */
	static final class Run {
		final World w;
		final Set<BlockPos> path = new HashSet<>(), opened = new HashSet<>(), lavaLeft = new HashSet<>();
		final List<BlockPos> ores = new ArrayList<>();
		BlockPos cursor;
		int broken;

		Run(World w) { this.w = w; }

		void dig(BlockPos q) {
			if (w.solid(q)) broken++;
			w.set.put(q.immutable(), AIR);
			opened.add(q.immutable());
		}

		String go(BlockPos start, Direction dir, Integer descendTo, String ore, int want, int maxBlocks) {
			cursor = start;
			path.add(start);
			for (int i = 0; i < 300; i++) {
				boolean down = descendTo != null && cursor.getY() > descendTo;
				BlockPos next = Prospect.stepCell(cursor, dir, descendTo, false);
				BlockPos[] t = Prospect.touching(cursor, dir, next, false);
				String h = Prospect.stepHazard(w, t, false, down, Set.of(), path, true);
				if (h != null) return h;
				if (!w.solid(t[5])) return "hazard:no_floor";
				for (BlockPos q : down ? new BlockPos[] {t[0], t[1], t[4]} : new BlockPos[] {t[0], t[1]}) dig(q);
				if (broken >= maxBlocks) return "max_blocks";
				cursor = next.immutable();
				path.add(cursor);
				BlockPos ahead = Prospect.stepCell(cursor, dir, descendTo, false);
				for (BlockPos q : BlockPos.betweenClosed(cursor.offset(-4, -2, -4), cursor.offset(4, 3, 4))) {
					if (!w.id(q).equals(ore) || !exposed(q) || Prospect.floor(path, cursor, ahead, q)) continue;
					if (Prospect.besideLava(w::lava, q)) { lavaLeft.add(q.immutable()); continue; }
					if (q.distSqr(cursor.above()) > 4.5 * 4.5) continue;
					dig(q);
					ores.add(q.immutable());
					if (ores.size() >= want) return "count";
				}
				assertTrue(w.solid(cursor.below()), "her floor stays under her at " + cursor.toShortString());
			}
			return "loop";
		}

		boolean exposed(BlockPos q) {
			for (Direction d : Direction.values()) if (w.id(q.relative(d)).equals(AIR)) return true;
			return false;
		}

		/** Lava never ends up beside a cell this run opened (she never opened into lava). */
		void neverOpenedIntoLava() {
			for (BlockPos q : opened) for (Direction d : Direction.values())
				assertFalse(w.lava(q.relative(d)), "opened " + q.toShortString() + " beside lava at " + q.relative(d).toShortString());
		}
	}

	@Test
	void ironLegDescendsToY16AndMinesTheWallOre() { // iron at y 16 under 10 blocks of stone
		World w = new World(26);
		w.put("minecraft:iron_ore", 1, 16, 13);
		w.put("minecraft:iron_ore", -1, 17, 14);
		w.put("minecraft:iron_ore", 1, 16, 15);
		Run r = new Run(w);
		String stop = r.go(new BlockPos(0, 27, 0), Direction.SOUTH, 16, "minecraft:iron_ore", 3, 64);
		assertEquals("count", stop, "ores " + r.ores + ", broken " + r.broken + ", at " + r.cursor);
		assertEquals(16, r.cursor.getY(), "the staircase reached descend_to");
		assertEquals(3, r.ores.size());
		assertTrue(r.broken < 64, "within max_blocks: " + r.broken);
		r.neverOpenedIntoLava();
	}

	@Test
	void diamondLegAtMinus58LeavesTheOreBesideLavaAndStopsAtTheLake() {
		World w = new World(100);
		w.put(AIR, 0, -48, 0);
		w.put(AIR, 0, -47, 0);
		w.put("minecraft:deepslate_diamond_ore", 1, -58, 12);  // a lava pocket behind it: left
		w.put(LAVA, 2, -58, 12);
		w.put("minecraft:deepslate_diamond_ore", -1, -58, 13); // clean: mined
		for (int x = -2; x <= 2; x++) for (int z = 16; z <= 19; z++) w.put(LAVA, x, -58, z); // a lava lake across the line
		Run r = new Run(w);
		String stop = r.go(new BlockPos(0, -48, 0), Direction.SOUTH, -58, "minecraft:deepslate_diamond_ore", 2, 64);
		assertTrue(stop.startsWith("hazard:lava"), stop);
		assertEquals(List.of(new BlockPos(-1, -58, 13)), r.ores);
		assertTrue(r.lavaLeft.contains(new BlockPos(1, -58, 12)));
		assertEquals(-58, r.cursor.getY());
		r.neverOpenedIntoLava();
	}

	@Test
	void maxBlocksWithNoOreStopsAndSaysHowDeep() {
		World w = new World(70);
		Run r = new Run(w);
		assertEquals("max_blocks", r.go(new BlockPos(0, 71, 0), Direction.SOUTH, 16, "minecraft:iron_ore", 3, 64));
		assertTrue(r.cursor.getY() > 16, "64 blocks is not enough for a 55-block staircase");
		Prospect p = new Prospect(JsonParser.parseString("{\"tag\":\"minecraft:iron_ores\",\"count\":3,\"descend_to\":16,\"max_blocks\":64}").getAsJsonObject());
		p.stop = "max_blocks";
		p.cursor = r.cursor;
		p.length = r.path.size() - 1;
		Step s = p.outcome();
		assertEquals("not_found", s.code());
		assertTrue(s.note().contains("at y " + r.cursor.getY()) && s.note().contains("descend_to 16 not reached"), s.note());
	}

	/** Every face of every cell a step opens, minus her own cells: lava there stops the step before any dig. */
	static void lavaOnEveryFace(boolean down) {
		BlockPos cursor = new BlockPos(0, 40, 0);
		Integer descendTo = down ? 10 : null;
		BlockPos next = Prospect.stepCell(cursor, Direction.SOUTH, descendTo, false);
		BlockPos[] t = Prospect.touching(cursor, Direction.SOUTH, next, false);
		Set<BlockPos> opened = down ? Set.of(t[0], t[1], t[4]) : Set.of(t[0], t[1]);
		Set<BlockPos> hers = Set.of(cursor, cursor.above(), cursor.below());
		int faces = 0;
		for (BlockPos o : opened)
			for (Direction d : Direction.values()) {
				BlockPos f = o.relative(d);
				if (opened.contains(f) || hers.contains(f)) continue;
				World w = new World(100);
				w.set.put(f, LAVA);
				assertNotNull(Prospect.stepHazard(w, t, false, down, Set.of(), Set.of(cursor), true), (down ? "down" : "level") + ": lava at " + f.toShortString());
				faces++;
			}
		assertEquals(down ? 11 : 8, faces);
	}

	@Test
	void lavaOnAnyFaceOfTheStepStops() {
		lavaOnEveryFace(false);
		lavaOnEveryFace(true); // the head-room cell's ceiling, sides and front were not looked at before S93
	}

	@Test
	void waterBesideStopsWaterInTheLineIsSealedNotStopped() {
		BlockPos cursor = new BlockPos(0, 40, 0), next = cursor.south();
		BlockPos[] t = Prospect.touching(cursor, Direction.SOUTH, next, false);
		World beside = new World(100);
		beside.set.put(t[6], WATER);
		assertTrue(Prospect.stepHazard(beside, t, false, false, Set.of(), Set.of(cursor), true).startsWith("hazard:water"));
		World over = new World(100);
		over.set.put(t[4], WATER);
		assertNotNull(Prospect.stepHazard(over, t, false, false, Set.of(), Set.of(cursor), true), "water over her head");
		World line = new World(100);
		line.set.put(t[2], WATER);
		assertNull(Prospect.stepHazard(line, t, false, false, Set.of(), Set.of(cursor), true), "the face beyond: fill() seals it");
		assertNull(Prospect.stepHazard(beside, t, false, false, Set.of(), Set.of(cursor), false), "dig_up keeps sealing");
	}

	@Test
	void aShortGravelColumnIsDugATallOneStops() {
		BlockPos cursor = new BlockPos(0, 40, 0), next = cursor.south();
		BlockPos[] t = Prospect.touching(cursor, Direction.SOUTH, next, false);
		World low = new World(100);
		for (int k = 0; k < 3; k++) low.set.put(t[4].above(k), "minecraft:gravel");
		assertNull(Prospect.stepHazard(low, t, false, false, Set.of(), Set.of(cursor), true), "3 gravel: falls in, dug again");
		World tall = new World(100);
		for (int k = 0; k < 6; k++) tall.set.put(t[4].above(k), "minecraft:sand");
		assertTrue(Prospect.stepHazard(tall, t, false, false, Set.of(), Set.of(cursor), true).startsWith("hazard:falling"));
		assertTrue(Prospect.falls("minecraft:red_concrete_powder"));
		assertFalse(Prospect.falls("minecraft:soul_sand"));
	}
}
