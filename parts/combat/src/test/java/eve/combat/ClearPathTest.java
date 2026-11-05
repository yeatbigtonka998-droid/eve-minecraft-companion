package eve.combat;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.function.BiPredicate;

import static org.junit.jupiter.api.Assertions.*;

/**
 * O116, body's floor_pen (CP-20 attempt 105): Eve inside a 1.5-high oak-fence pen, the fence ~2.5 out, a skeleton 9 out on the
 * same floor. Heights are above the release point (her eye 1.62 - 0.1 = 1.52 above the floor).
 */
class ClearPathTest {
	static final double RELEASE = 1.52;

	/** A fence post band at horizontal 2.4..2.65, collider up to 1.5 above the floor. */
	static final BiPredicate<Double, Double> FENCE = (x, y) -> x >= 2.4 && x <= 2.65 && y + RELEASE < 1.5;
	/** A 3-high wall at the same place. */
	static final BiPredicate<Double, Double> WALL = (x, y) -> x >= 2.4 && x <= 3.4 && y + RELEASE < 3.0;

	/** Skeleton aim points above the release point: centre (1.0), 0.75 of its 1.99 height (1.49), eye (1.74). */
	static final List<Double> SKELETON = List.of(1.0 - RELEASE, 1.49 - RELEASE, 1.74 - RELEASE);

	@Test
	void theCentreShotAtSixClipsTheFence() {
		var s = Tactics.solve(6, SKELETON.get(0));
		assertNotNull(s);
		assertTrue(s.elevation() < 0, "close in, the centre is below her eye: a falling shot");
		assertFalse(Tactics.clearPath(s.elevation(), 6, FENCE), "attempt 105: those arrows hit the fence");
	}

	@Test
	void aHigherAimPointClearsTheFence() {
		int i = Tactics.firstClear(SKELETON, 6, FENCE);
		assertTrue(i > 0, "centre blocked, a higher aim point clears (got " + i + ")");
		assertEquals(0, Tactics.firstClear(SKELETON, 6, (x, y) -> false), "open ground: the centre, as before");
	}

	@Test
	void aThreeHighWallLeavesNoClearShot() {
		assertEquals(-1, Tactics.firstClear(SKELETON, 6, WALL), "no release: reposition, then no_clear_shot");
		assertEquals(-1, Tactics.firstClear(SKELETON, 9, WALL));
	}

	@Test
	void theTargetsOwnCellIsNotAnObstacle() {
		// a block right at the target (its feet's cell) is ignored: the last 0.3 of the path is the target itself
		assertTrue(Tactics.clearPath(Tactics.solve(9, SKELETON.get(0)).elevation(), 9, (x, y) -> x > 8.75));
	}
}
