package eve.combat;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.*;

class TacticsTest {
	@Test
	void picksNearestSwingableInSight() {
		var near = new Tactics.Cand(2, 20, false, true);
		var blocked = new Tactics.Cand(1, 20, true, true); // villager beside it: never first while another exists
		var hidden = new Tactics.Cand(3, 20, false, false);
		var weakFar = new Tactics.Cand(2.5, 4, false, true);
		assertEquals(0, Tactics.pick(List.of(near, blocked, hidden)));
		assertEquals(2, Tactics.pick(List.of(blocked, hidden, weakFar)), "one in sight beats a hidden one");
		assertEquals(0, Tactics.pick(List.of(blocked)), "only a blocked one: still chosen (attack_entity then holds)");
		assertEquals(-1, Tactics.pick(List.of()));
	}

	@Test
	void spacingHoldsTheBand() {
		var k = Tactics.spacing(1.5, false, true, 0);
		assertTrue(k.backward() && !k.forward(), "too close while recharging: back off");
		k = Tactics.spacing(4.0, false, false, 0);
		assertTrue(k.forward() && !k.backward(), "too far while recharging: close in");
		k = Tactics.spacing(2.7, false, true, 0);
		assertFalse(k.forward() || k.backward(), "inside the band: hold");
		assertTrue(k.left() ^ k.right(), "and strafe one way");
		assertNotEquals(k.left(), Tactics.spacing(2.7, false, true, Tactics.STRAFE_PERIOD).left(), "strafe flips");
		k = Tactics.spacing(3.4, true, false, 0);
		assertTrue(k.forward(), "ready and out of reach: step in");
		k = Tactics.spacing(10, true, false, 0);
		assertFalse(k.left() || k.right(), "no strafing on the long approach");
	}

	@Test
	void arrowSolutionsHitTheirMark() {
		for (double d : new double[] {4, 10, 20, 30, 45}) {
			for (double dy : new double[] {-3, 0, 4}) {
				Tactics.Shot s = Tactics.solve(d, dy);
				assertNotNull(s, d + "," + dy);
				assertEquals(dy, Tactics.heightAt(s.elevation(), d)[0], 0.02, "lands on the mark at " + d + "," + dy);
				assertTrue(s.ticks() >= d / Tactics.ARROW_SPEED, "flight time at least distance/speed");
			}
		}
		assertTrue(Tactics.solve(20, 0).elevation() > Tactics.solve(10, 0).elevation(), "farther = higher");
		assertTrue(Tactics.solve(10, 0).elevation() > 0, "gravity: aim above a level target");
		assertNull(Tactics.solve(400, 0), "out of range");
	}
}
