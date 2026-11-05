package eve.combat;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

/** O102d (3), c_rush #6: at 4.8 HP under fight_ranged's sword finish (floor 6) the fight must not quit while the body hands it back. */
class FloorHandBackTest {
	@Test
	void belowTheFloorSheDisengages() {
		assertTrue(FightMelee.disengage(4.8f, 6f, false));
		assertFalse(FightMelee.disengage(6f, 6f, false), "at the floor is not below it");
	}

	@Test
	void handedBackSheFightsOn() {
		assertFalse(FightMelee.disengage(4.8f, 6f, true));
		assertFalse(FightMelee.disengage(1.5f, 8f, true));
	}
}
