package eve.combat;

import org.junit.jupiter.api.Test;

import static eve.combat.Tactics.ArcherMove.*;
import static org.junit.jupiter.api.Assertions.*;

/** O102v option A: against an archer, guard while it draws and rush once it has released; everything else keeps the old approach. */
class ArcherMoveTest {
	@Test
	void drawnBowHoldsReleasedBowRushes() {
		assertEquals(HOLD, Tactics.archer(true, true, 8, 7.5, true), "its bow is drawn: guard up, facing it");
		assertEquals(RUSH, Tactics.archer(true, true, 8, 7.5, false), "it has released: sprint in before the next draw");
	}

	@Test
	void onlyAnArcherInSightOutOfReachAndInRange() {
		assertEquals(NONE, Tactics.archer(false, true, 8, 7.5, false), "a zombie: the usual band and swing");
		assertEquals(NONE, Tactics.archer(true, false, 8, 7.5, true), "out of sight: walk_to finds it");
		assertEquals(NONE, Tactics.archer(true, true, 2.2, 2.0, true), "in swing reach: swing, as before");
		assertEquals(NONE, Tactics.archer(true, true, 20, 19.5, false), "beyond the rush range: walk_to first");
	}
}
