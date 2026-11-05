package eve.combat;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

/**
 * O174 (sense's hit logger, x_archer on F: 53 of 62 arrows landed 0-4 ticks after the shield use started; vanilla blocks from the
 * 5th): the guard stays up from the draw through the arrow's flight, instead of dropping at the release.
 */
class ArrowHoldTest {
	@Test
	void theGuardOutlivesTheDraw() {
		assertTrue(Tactics.holdForArrow(0, false), "drawing now");
		assertTrue(Tactics.holdForArrow(Tactics.POST_RELEASE_HOLD - 1, false), "just released: the arrow is in the air");
		assertFalse(Tactics.holdForArrow(Tactics.POST_RELEASE_HOLD, false), "long enough after: rush");
		assertTrue(Tactics.holdForArrow(1000, true), "an arrow flying at her, whoever shot it");
	}

	@Test
	void theRushWaitsForTheArrow() {
		// option A's move uses `drawing || rangedThreat`: right after the release it must still HOLD
		assertEquals(Tactics.ArcherMove.HOLD, Tactics.archer(true, true, 10, 9.5, Tactics.holdForArrow(3, false)));
		assertEquals(Tactics.ArcherMove.RUSH, Tactics.archer(true, true, 10, 9.5, Tactics.holdForArrow(12, false)));
	}
}
