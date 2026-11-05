package eve.combat;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

/** S28(a): the fight's shield goes up on the archer's draw (a shield blocks from its 5th tick: an arrow 2 ticks out is too late). */
class DrawGuardTest {
	@Test
	void meleeGuardsOnTheDrawAboveTheFloor() {
		assertTrue(Tactics.guardOnDraw(true, 14f, false), "an archer draws at her: shield up now, 20 ticks before its release");
		assertFalse(Tactics.guardOnDraw(false, 14f, false), "nobody drawing: the fight's own guard logic as before");
		assertFalse(Tactics.guardOnDraw(true, 6f, false), "at body's floor: its floor shield owns the controls");
		assertFalse(Tactics.guardOnDraw(true, 14f, true), "the swing's click tick keeps the shield down");
	}

	@Test
	void rangedDuelShootsFirstOnlyWithTimeToRaise() {
		assertTrue(Tactics.shieldInsteadOfDraw(true, 14f, -1, 0), "not drawing, it starts: 21 + 5 > 20, shield");
		assertTrue(Tactics.shieldInsteadOfDraw(true, 14f, 10, 8), "she needs 11 more, it needs 12: no time to raise after, shield");
		assertFalse(Tactics.shieldInsteadOfDraw(true, 14f, 18, 0), "3 ticks to her release, 20 to its: shoot, then shield in time");
		assertFalse(Tactics.shieldInsteadOfDraw(false, 14f, 5, 0), "nobody drawing: keep drawing");
		assertFalse(Tactics.shieldInsteadOfDraw(true, 5f, -1, 0), "below body's floor: not the fight's call");
	}
}
