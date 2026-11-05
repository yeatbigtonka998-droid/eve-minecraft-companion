package eve.body;

import java.util.function.BiPredicate;

/**
 * H-boat (Hunter: riding with him is "extremely important"), the pure half of follow_boat: which phase she is in, how to steer a
 * boat toward the owner's, and the nearest cell around her that passes a test. No game types, so it is unit-tested with a fake
 * world and owner track (BoatPlanTest); FollowBoat is the live half.
 */
final class BoatPlan {
	enum Phase { LAUNCH, SAIL, LAND, RECOVER, DONE }

	/**
	 * Where she is in the follow. ownerOnWater: in a boat or in water. nearShoreBoat: the boat she launched still lies near her
	 * (to break and pick up). LAUNCH needs no boat yet: FollowBoat refuses no_boat itself.
	 */
	static Phase phase(boolean inBoat, boolean ownerOnWater, boolean boatLeftBehind) {
		if (inBoat) return ownerOnWater ? Phase.SAIL : Phase.LAND;
		if (ownerOnWater) return Phase.LAUNCH;
		return boatLeftBehind ? Phase.RECOVER : Phase.DONE;
	}

	/** Boat keys: forward, left, right. */
	record Keys(boolean forward, boolean left, boolean right) {
		static final Keys NONE = new Keys(false, false, false);
	}

	/**
	 * Steer a boat (yaw in degrees, MC convention: facing (-sin yaw, cos yaw)) toward (tx, tz), staying keepMin..keepMax behind.
	 * Vanilla's boat turns left on its left key (yaw down) and right on its right key. Turn while the bearing is off by more than
	 * 8 deg; paddle when farther than keepMax and roughly facing (60 deg), or farther than the middle of the band and well facing
	 * (20 deg); inside keepMin: no paddle, only turn (it coasts to a stop).
	 */
	static Keys steer(double x, double z, float yaw, double tx, double tz, double keepMin, double keepMax) {
		double dx = tx - x, dz = tz - z, dist = Math.hypot(dx, dz);
		if (dist < 1e-6) return Keys.NONE;
		double bearing = Math.toDegrees(Math.atan2(dz, dx)) - 90.0;
		double err = wrap(bearing - yaw);
		boolean left = err < -8, right = err > 8;
		boolean fwd = dist > keepMax && Math.abs(err) < 60 || dist > (keepMin + keepMax) / 2 && Math.abs(err) < 20;
		if (dist < keepMin) fwd = false;
		return new Keys(fwd, left, right);
	}

	static double wrap(double deg) {
		double d = deg % 360;
		if (d >= 180) d -= 360;
		if (d < -180) d += 360;
		return d;
	}

	/** The nearest (dx, dz) with |dx|, |dz| <= r for which ok holds (ties: the first found), or null. */
	static int[] nearest(int r, BiPredicate<Integer, Integer> ok) {
		int[] best = null;
		int bd = Integer.MAX_VALUE;
		for (int dx = -r; dx <= r; dx++)
			for (int dz = -r; dz <= r; dz++) {
				int d = dx * dx + dz * dz;
				if (d < bd && ok.test(dx, dz)) {
					bd = d;
					best = new int[] {dx, dz};
				}
			}
		return best;
	}

	/** Like nearest, but nearest to the point (tx, tz) rather than to her: the shore cell closest to the owner. */
	static int[] nearestTo(int r, BiPredicate<Integer, Integer> ok, int tx, int tz) {
		int[] best = null;
		long bd = Long.MAX_VALUE;
		for (int dx = -r; dx <= r; dx++)
			for (int dz = -r; dz <= r; dz++) {
				long d = (long) (dx - tx) * (dx - tx) + (long) (dz - tz) * (dz - tz);
				if (d < bd && ok.test(dx, dz)) {
					bd = d;
					best = new int[] {dx, dz};
				}
			}
		return best;
	}

	/** Half the boat's box width (1.375 wide). */
	static final double BOAT_HALF = 0.6875;

	/**
	 * Vanilla's BoatItem rule at water level: a boat placed at (x, z) needs its box clear of blocks, so every cell the box overlaps must
	 * be water. S96 a113: the offline lake had no such rule and passed the shore cell the game refused 10 of 10 times.
	 */
	static boolean boxFits(double x, double z, BiPredicate<Integer, Integer> water) {
		for (int cx = (int) Math.floor(x - BOAT_HALF); cx <= (int) Math.floor(x + BOAT_HALF); cx++)
			for (int cz = (int) Math.floor(z - BOAT_HALF); cz <= (int) Math.floor(z + BOAT_HALF); cz++)
				if (!water.test(cx, cz)) return false;
		return true;
	}

	/** A boat fits on the centre of cell (dx, dz). */
	static boolean clearOfShore(int dx, int dz, BiPredicate<Integer, Integer> water) {
		return boxFits(dx + 0.5, dz + 0.5, water);
	}

	/** Kickoff's "more than 3 blocks from shore": no non-water cell within 3 of (x, z) on its level. */
	static boolean offShore(BiPredicate<Integer, Integer> water) {
		return nearest(3, (dx, dz) -> !water.test(dx, dz)) == null;
	}
}
