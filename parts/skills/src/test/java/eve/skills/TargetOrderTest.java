package eve.skills;

import net.minecraft.core.BlockPos;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.*;

/** O129: the nearest reachable exposed stone first (walking cost, not straight-line); a buried block is never the target. */
class TargetOrderTest {
	static final BlockPos HER = new BlockPos(0, 64, 0);

	static BlockPos pick(List<BlockPos> stone, Set<BlockPos> buried) {
		return MineResource.best(HER, stone, q -> true, q -> !buried.contains(q));
	}

	@Test
	void nearestExposedStoneFirst() {
		BlockPos near = new BlockPos(2, 64, 1), far = new BlockPos(9, 64, -6);
		assertEquals(near, pick(List.of(far, near), Set.of()));
	}

	@Test
	void walkingCostNotStraightLine() {
		BlockPos up = new BlockPos(0, 68, 1);   // straight-line ~4.1, but a climb of 4: cost 9
		BlockPos flat = new BlockPos(5, 64, 0); // straight-line 5, cost 5
		assertEquals(flat, pick(List.of(up, flat), Set.of()));
	}

	@Test
	void buriedSkippedWhileAnExposedOneExists() {
		BlockPos buried = new BlockPos(1, 63, 0), exposed = new BlockPos(6, 64, 2);
		assertEquals(exposed, pick(List.of(buried, exposed), Set.of(buried)));
		assertNull(pick(List.of(buried), Set.of(buried)), "only buried stone: nothing to target");
	}

	@Test
	void reSortedFromWhereSheStands() {
		BlockPos a = new BlockPos(3, 64, 0), b = new BlockPos(-3, 64, 0);
		assertEquals(a, MineResource.best(new BlockPos(2, 64, 0), List.of(b, a), q -> true, q -> true));
		assertEquals(b, MineResource.best(new BlockPos(-2, 64, 0), List.of(a, b), q -> true, q -> true));
	}

	@Test
	void radius100ScanMatchesTheFullCubeAndReadsLittle() { // O163: section scan = brute force, at a fraction of the reads
		java.util.Random rnd = new java.util.Random(163);
		for (int round = 0; round < 20; round++) {
			Set<BlockPos> ore = new java.util.HashSet<>(), buried = new java.util.HashSet<>();
			for (int i = 0; i < 30; i++) {
				BlockPos q = new BlockPos(rnd.nextInt(241) - 120, rnd.nextInt(241) - 120 + 64, rnd.nextInt(241) - 120);
				ore.add(q);
				if (rnd.nextInt(4) == 0) buried.add(q);
			}
			BlockPos from = new BlockPos(rnd.nextInt(21) - 10, 64 + rnd.nextInt(21) - 10, rnd.nextInt(21) - 10);
			int r = 100;
			BlockPos brute = MineResource.best(from, ore.stream().filter(q -> Math.abs(q.getX() - from.getX()) <= r
					&& Math.abs(q.getY() - from.getY()) <= r && Math.abs(q.getZ() - from.getZ()) <= r).toList(), q -> true, q -> !buried.contains(q));
			int[] reads = {0};
			BlockPos got = MineResource.scan(from, r, (sx, sy, sz) -> ore.stream().anyMatch(q -> q.getX() >> 4 == sx && q.getY() >> 4 == sy && q.getZ() >> 4 == sz),
					q -> { reads[0]++; return ore.contains(q); }, q -> !buried.contains(q));
			if (brute == null) assertNull(got);
			else assertEquals(MineResource.cost(from, brute), MineResource.cost(from, got), "round " + round);
			assertTrue(reads[0] <= 30 * 4096, "only sections that may hold ore are read: " + reads[0]); // the cube is 8.1M
		}
		assertNull(MineResource.scan(HER, 100, (x, y, z) -> false, q -> true, q -> true), "no section holds it: nothing read, null");
	}

	@Test
	void cliffPrefersTheLevelStoneOverTheNearDrop() { // kickoff ad0bae41 / CP-86 run 2: she on a ledge at y 71
		BlockPos her = new BlockPos(0, 71, 0);
		java.util.Set<BlockPos> solid = new java.util.HashSet<>();
		for (int x = -3; x <= 25; x++) for (int z = -3; z <= 3; z++) solid.add(new BlockPos(x, 70, z)); // the ledge she stands on
		BlockPos below = new BlockPos(2, 64, 0); // exposed stone 7 below, in a cave under the ledge: cost 2 + 14 = 16
		solid.add(below);
		for (int z = -3; z <= 3; z++) solid.add(new BlockPos(1, 63, z)); // the cave floor beside it (a stand, but 7 below her)
		BlockPos level = new BlockPos(20, 71, 0); // a boulder on the ledge 20 away at her height: cost 20
		solid.add(level);
		java.util.function.Predicate<BlockPos> isStone = q -> q.equals(below) || q.equals(level);
		BlockPos got = MineResource.choose(her, 32, (x, y, z) -> true, isStone, q -> !solid.contains(q.above()) || !solid.contains(q.east()), solid::contains);
		assertEquals(level, got, "the reachable level stone, not the nearer one under the drop");
		assertNotNull(MineResource.standFor(solid::contains, her, level));
		assertEquals(below, MineResource.choose(her, 32, (x, y, z) -> true, below::equals, q -> true, solid::contains), "only the drop left: still tried");
	}
}
