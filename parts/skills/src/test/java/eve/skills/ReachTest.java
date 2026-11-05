package eve.skills;

import com.google.gson.JsonParser;
import eve.body.api.Step;
import net.minecraft.core.BlockPos;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

/** O125: a target she cannot walk to gets one dug path, then is skipped; only 8 skipped targets end mine_resource unreachable. */
class ReachTest {
	static MineResource stone() {
		return new MineResource(JsonParser.parseString("{\"block\":\"minecraft:stone\",\"count\":10}").getAsJsonObject());
	}

	@Test
	void walkThenDugPathThenSkip() {
		MineResource m = stone();
		Step noRoute = Step.failed("unreachable", "Baritone stopped 3.1 from the goal");
		assertEquals("dig", m.reach(false, noRoute, false));      // first failure: try a dug path
		assertEquals("skip", m.reach(true, noRoute, false));      // the dug path failed too: another target
		for (int i = 0; i < 7; i++) m.skipped.add("t" + i);
		assertEquals("skip", m.reach(true, noRoute, false));      // ad0bae41: still another target; only "no candidate left" ends it
	}

	@Test
	void noCountMeansUntilNone() { // O133: gather wood with count 0 or absent runs until no tree is left, bounded by max_blocks
		MineResource m = new MineResource(JsonParser.parseString("{\"tag\":\"minecraft:logs\"}").getAsJsonObject());
		assertEquals(0, m.want);
		assertEquals(MineResource.UNTIL_NONE_BLOCKS, m.maxBlocks);
		assertTrue(m.logs());
		Gather g = new Gather(JsonParser.parseString("{\"product\":\"wood\",\"count\":0}").getAsJsonObject());
		assertEquals(0, g.wood.want);
		assertEquals(24, stone().maxBlocks); // a counted call keeps count*2+4
	}

	@Test
	void aTargetBelowHerGetsNoDugPath() { // CP-86 run 2: body's dug path never digs down - 8 stone 7-9 below, 17 tries, 0 mined
		MineResource m = stone();
		Step noRoute = Step.failed("unreachable", "Baritone stopped 8.2 from the goal");
		assertEquals("skip", m.reach(false, noRoute, true));
		for (int i = 0; i < 7; i++) m.skipped.add("t" + i);
		assertEquals("skip", m.reach(false, noRoute, true));
	}

	@Test
	void aDriftingWalkerStopsAfterFourBelowTries() { // S93 / CP-86 run 4: every failed Baritone walk moved her ~2 blocks west
		MineResource m = stone();
		BlockPos her = new BlockPos(-20, 71, -1), start = her;
		Step noRoute = Step.failed("unreachable", "Baritone stopped from the goal");
		String r = "next";
		int walks = 0;
		while (!r.startsWith("unreachable:") && walks < 64) {
			m.target = new BlockPos(-18 - walks, 62 + walks % 3, 0); // cave stone 7-9 below her, nothing reachable
			m.reachableTarget = false;
			r = m.failedWalk(her, noRoute);
			walks++;
			her = her.offset(-2, 0, 0); // the drift
		}
		assertEquals(MineResource.BELOW_TRIES, walks, "stopped after 4 walks, not 64");
		assertTrue(r.startsWith("unreachable:below her: dig down (prospect); "), r);
		assertTrue(Math.abs(her.getX() - start.getX()) <= 2 * MineResource.BELOW_TRIES, "drift bounded to ~8 blocks (run 4: 33)");
		assertTrue(r.contains(MineResource.BELOW_TRIES + " attempts"), r);
	}

	@Test
	void aLevelTargetStillGetsItsDugPath() {
		MineResource m = stone();
		m.target = new BlockPos(5, 70, 0);
		m.reachableTarget = true;
		assertEquals("dig", m.failedWalk(new BlockPos(0, 70, 0), Step.failed("unreachable", null)));
		assertEquals("next", m.failedWalk(new BlockPos(0, 70, 0), Step.failed("unreachable", null)), "the dug path failed too: another target");
		assertNull(m.target);
	}

	/** The radius bounds a registry draft serves for one skill (body's Registry.check enforces minimum/maximum on dispatch). */
	static int[] radiusBounds(String draft, String skill) throws Exception {
		var reg = JsonParser.parseString(java.nio.file.Files.readString(java.nio.file.Path.of("../../contracts/skills/drafts/" + draft))).getAsJsonObject();
		for (var s : reg.getAsJsonArray("skills")) {
			var o = s.getAsJsonObject();
			if (!o.get("name").getAsString().equals(skill)) continue;
			var r = o.getAsJsonObject("params").getAsJsonObject("properties").getAsJsonObject("radius");
			return new int[] {r.get("minimum").getAsInt(), r.get("maximum").getAsInt()};
		}
		throw new AssertionError(skill + " not in " + draft);
	}

	@Test
	void radius100IsServedBy0153AndTakenAsIs() throws Exception { // kickoff 17684cfa / H-range100: the jar needs no 0.15.3 build
		for (String skill : new String[] {"mine_resource", "gather"}) {
			int[] b = radiusBounds("registry-0.15.3.json", skill);
			assertTrue(b[0] <= 100 && 100 <= b[1], skill + " 0.15.3 radius " + b[0] + ".." + b[1]);
			assertTrue(100 > radiusBounds("registry-0.15.2.json", skill)[1], skill + ": 0.15.2 still refuses 100, so the gate is the registry");
		}
		assertEquals(100, new MineResource(JsonParser.parseString("{\"block\":\"minecraft:stone\",\"count\":12,\"radius\":100}").getAsJsonObject()).radius);
		Gather g = new Gather(JsonParser.parseString("{\"product\":\"wood\",\"count\":8,\"radius\":100}").getAsJsonObject());
		assertEquals(100, g.radius);
		assertEquals(100, g.wood.radius, "gather wood hands its radius to mine_resource unclamped");
	}

	@Test
	void aRefusedZoneIsNeverDugThrough() {
		MineResource m = stone();
		assertEquals("skip", m.reach(false, Step.failed("zone_denied", "server denied"), false));
	}

	@Test
	void dugPathsAreBounded() {
		MineResource m = stone();
		m.digPaths = 2 * MineResource.REACH_SKIPS;
		assertEquals("skip", m.reach(false, Step.failed("unreachable", null), false));
	}
}
