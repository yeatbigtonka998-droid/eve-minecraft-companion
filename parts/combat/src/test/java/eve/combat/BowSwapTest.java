package eve.combat;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

/** 547236a4 lane C: her live bow is in slot 13; fight_ranged swaps it into a hotbar slot that is not her sword's. */
class BowSwapTest {
	@Test
	void firstEmptyHotbarSlotNeverTheSwords() {
		boolean[] e = new boolean[9];
		e[0] = true; e[5] = true;
		assertEquals(5, FightRanged.swapTarget(e, 0), "slot 0 is empty but kept for the sword");
		assertEquals(0, FightRanged.swapTarget(e, 3));
	}

	@Test
	void fullHotbarUsesTheLastSlotUnlessTheSwordIsThere() {
		boolean[] full = new boolean[9];
		assertEquals(8, FightRanged.swapTarget(full, 0));
		assertEquals(7, FightRanged.swapTarget(full, 8));
	}
}
