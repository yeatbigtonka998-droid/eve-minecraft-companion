package eve.body;

import com.google.gson.JsonObject;
import org.junit.jupiter.api.Test;

import java.util.function.BiPredicate;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** H-boat, offline: a fake lake and owner track (owner crosses, owner lands, she has / has no boat). */
class BoatPlanTest {
	/** A lake: water for x in [2, 30], any z; land elsewhere. She stands on the shore at x = 0..1. */
	static boolean lake(int x, int z) {
		return x >= 2 && x <= 30;
	}

	@Test
	void theOwnerCrossesTheLakeAndLands() {
		// he paddles off: she is ashore, he is on water -> launch
		assertEquals(BoatPlan.Phase.LAUNCH, BoatPlan.phase(false, true, false));
		// she is in her boat, he still on water -> sail
		assertEquals(BoatPlan.Phase.SAIL, BoatPlan.phase(true, true, false));
		// he steps ashore on the far side -> land
		assertEquals(BoatPlan.Phase.LAND, BoatPlan.phase(true, false, false));
		// she is out, her boat still lies at the shore -> recover; then done
		assertEquals(BoatPlan.Phase.RECOVER, BoatPlan.phase(false, false, true));
		assertEquals(BoatPlan.Phase.DONE, BoatPlan.phase(false, false, false));
	}

	@Test
	void sheLaunchesFromTheNearestWaterNextToHer() {
		// standing at x = 0: the nearest water cell is 2 east
		assertArrayEquals(new int[] {2, 0}, BoatPlan.nearest(4, (dx, dz) -> lake(dx, dz)));
		// S96 a113: the game's rule in the fake lake. The old choice (nearest water) is refused; the new one is what vanilla accepts
		int[] old = BoatPlan.nearest(4, (dx, dz) -> lake(dx, dz));
		assertFalse(BoatPlan.boxFits(old[0] + 0.5, old[1] + 0.5, BoatPlanTest::lake), "the shore cell: the 1.375-wide box touches the bank");
		int[] chosen = BoatPlan.nearest(4, (dx, dz) -> BoatPlan.clearOfShore(dx, dz, BoatPlanTest::lake));
		assertArrayEquals(new int[] {3, 0}, chosen);
		assertTrue(BoatPlan.boxFits(chosen[0] + 0.5, chosen[1] + 0.5, BoatPlanTest::lake));
		// a one-wide channel never fits a boat
		assertNull(BoatPlan.nearest(4, (dx, dz) -> BoatPlan.clearOfShore(dx, dz, (x, z) -> x == 2)));
		// no water within reach: no_water
		assertNull(BoatPlan.nearest(4, (dx, dz) -> lake(dx + 100, dz)));
	}

	@Test
	void theDismountsSneakReachesTheArbiter() { // S96 a115: the key press stayed inside the sub-act and she never left the boat
		assertEquals(Boolean.TRUE, FollowBoat.outerSneak(new Interact.Dismount("d", new JsonObject(), 0, 0)), "out of the boat: sneak on");
		assertEquals(Boolean.FALSE, FollowBoat.outerSneak(new Interact.Mount("m", mountParams(), 0, 0)), "into it: sneak off (sneak + use never mounts)");
		assertNull(FollowBoat.outerSneak(null), "no sub-act: her held sneak");
	}

	private static JsonObject mountParams() {
		JsonObject p = new JsonObject();
		p.addProperty("entity_id", 1);
		return p;
	}

	@Test
	void sheLandsOnTheShoreNearestTheOwner() { // S96 a115: she took the nearest bank and stopped 10+ off him
		// her boat at x 25 mid-lake; shores run along z = +-9 and past x = 30; he stands at (33, 3)
		BiPredicate<Integer, Integer> shore = (dx, dz) -> Math.abs(dz) >= 4 || 25 + dx > 30;
		assertArrayEquals(new int[] {6, 3}, BoatPlan.nearestTo(6, shore, 8, 3), "east, toward him, not the side bank");
		assertArrayEquals(new int[] {0, -4}, BoatPlan.nearestTo(6, shore, 0, 0), "no owner (winding down): the nearest bank");
	}

	@Test
	void offShoreMeansNoLandWithinThree() {
		assertTrue(BoatPlan.offShore((dx, dz) -> lake(15 + dx, dz)), "mid-lake");
		assertFalse(BoatPlan.offShore((dx, dz) -> lake(3 + dx, dz)), "1 off the near shore: land within 3");
	}

	@Test
	void sheSteersAfterHisBoatAndKeepsFourToEightBehind() {
		// her boat at the origin facing +z (yaw 0); he is 12 ahead: paddle, no turn
		assertEquals(new BoatPlan.Keys(true, false, false), BoatPlan.steer(0, 0, 0f, 0, 12, 4, 8));
		// he is 6 ahead (inside the band, well faced): paddle only past the middle (6 = middle): coast
		assertEquals(new BoatPlan.Keys(false, false, false), BoatPlan.steer(0, 0, 0f, 0, 6, 4, 8));
		// he is 3 ahead: too close, no paddle
		assertFalse(BoatPlan.steer(0, 0, 0f, 0, 3, 4, 8).forward());
		// he is 12 off to her right (+x is at yaw -90 in MC: facing (-sin, cos)): turn left, toward yaw -90, no paddle yet
		BoatPlan.Keys k = BoatPlan.steer(0, 0, 0f, 12, 0, 4, 8);
		assertTrue(k.left() && !k.right() && !k.forward(), k.toString());
		// he is 12 at her left (-x, yaw +90): turn right
		BoatPlan.Keys k2 = BoatPlan.steer(0, 0, 0f, -12, 0, 4, 8);
		assertTrue(k2.right() && !k2.left(), k2.toString());
	}

	@Test
	void aTrackAcrossTheLakeStaysInTheBand() {
		// a crude sim: he moves +z at 0.3/tick; her boat moves 0.35/tick along its yaw when paddling, turns 3 deg/tick
		double x = 0, z = 0, hx = 0, hz = 8;
		float yaw = 0;
		double maxGap = 0;
		for (int t = 0; t < 400; t++) {
			hz += 0.3;
			BoatPlan.Keys k = BoatPlan.steer(x, z, yaw, hx, hz, 4, 8);
			if (k.left()) yaw -= 3;
			if (k.right()) yaw += 3;
			if (k.forward()) {
				x += -Math.sin(Math.toRadians(yaw)) * 0.35;
				z += Math.cos(Math.toRadians(yaw)) * 0.35;
			}
			if (t > 40) maxGap = Math.max(maxGap, Math.hypot(hx - x, hz - z));
		}
		assertTrue(maxGap <= 9.0, "she never falls more than ~9 behind: " + maxGap);
	}
}
