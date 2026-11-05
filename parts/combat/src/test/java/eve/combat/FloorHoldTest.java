package eve.combat;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

/** S28-DEATH-2: below fight_melee's floor, run only from a walker with room to run; otherwise hold the guard facing it. */
class FloorHoldTest {
	@Test
	void neverRunFromAFlyer() {
		assertTrue(Tactics.floorHold(true, true), "a phantom is faster than she runs: guard up, facing it");
	}

	@Test
	void noStepAwayMeansHold() {
		assertTrue(Tactics.floorHold(false, false), "a wall behind her: a back turned to the zombie is a free hit");
	}

	@Test
	void aWalkerWithRoomStillMeansRun() {
		assertFalse(Tactics.floorHold(false, true), "the disengage as before");
	}
}
