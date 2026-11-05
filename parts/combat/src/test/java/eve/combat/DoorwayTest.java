package eve.combat;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

/** O98, with CP-31 a16 night 1's site: door cell (0, 200, -17), outside toward +z (south), room at z < -17. */
class DoorwayTest {
	private final Doorway d = Doorway.of(0, 200, -17, "south");

	@Test
	void a16DoorwaySkeletonIsOutsideAndNotEngageable() {
		// the skeleton stood ~1.3 outside the opening: z = -16.5 + 1.3
		assertFalse(d.engageable(0.5, -15.2), "outside the door cell: hold, do not step out to it");
		assertTrue(d.engageable(0.5, -16.6), "in the doorway cell: engage");
		assertTrue(d.engageable(1.2, -21.5), "inside the room: engage");
	}

	@Test
	void a16DeathSpotWasInTheLineOfFireAndTheHoldSpotIsNot() {
		assertTrue(d.inLineOfFire(0.5, -15.2, 0.5, -21.5), "straight back from the door (the a16 retreat's back wall)");
		// on the axis: Eve's own side (she died at x 1.126, east of the axis)
		double[] h = d.holdSpot(d.sideFor(0.5, -15.2, 1.126, -21.526));
		assertEquals(2.5, h[0], 1e-9, "east of the axis by 2");
		assertEquals(-18.0, h[1], 1e-9, "1.5 inside the door's centre");
		assertFalse(d.inLineOfFire(0.5, -15.2, h[0], h[1]), "behind the door wall");
		// off the axis: the target's side, so the wall is between them
		int side = d.sideFor(-0.8, -14.0, 1.126, -21.526);
		double[] w = d.holdSpot(side);
		assertEquals(-1.5, w[0], 1e-9, "a skeleton west of the axis: hold west, behind the west wall");
		assertFalse(d.inLineOfFire(-0.8, -14.0, w[0], w[1]));
		assertTrue(d.inLineOfFire(-0.8, -14.0, h[0], h[1]), "the far-side spot is exposed diagonally: why the side follows the target");
	}

	@Test
	void eveDriftIntoTheOpeningIsDetected() {
		assertTrue(d.eveTooFarOut(0.5, -16.5), "standing in the doorway cell");
		assertTrue(d.eveTooFarOut(0.5, -17.1), "half a body in the opening");
		assertFalse(d.eveTooFarOut(2.5, -18.0), "at the hold spot");
	}

	@Test
	void everyFacingWorks() {
		for (String out : new String[] {"north", "south", "east", "west"}) {
			var e = Doorway.of(10, 64, 10, out);
			double[] h = e.holdSpot(1);
			assertTrue(e.signed(h[0], h[1]) < -1.0, out + ": the hold spot is inside");
			assertEquals(2.0, Math.abs(e.lateral(h[0], h[1])), 1e-9, out + ": two to the side");
			double ox = e.ox() * 1.3, oz = e.oz() * 1.3; // a target 1.3 outside the opening's centre
			assertFalse(e.engageable(10.5 + ox, 10.5 + oz), out);
			assertFalse(e.inLineOfFire(10.5 + ox, 10.5 + oz, h[0], h[1]), out);
		}
		assertThrows(IllegalArgumentException.class, () -> Doorway.of(0, 0, 0, "up"));
	}
}
