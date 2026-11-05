package eve.combat;

/**
 * O98: fighting a target at a doorway from inside (CP-31 a16 night 1: a skeleton ~1.3 outside the open doorway shot her
 * through it for ~45 s). Pure geometry, block-centre based. The door cell is (x, z); `out` is the direction from inside to
 * outside. "Signed" = how far a point is past the door's centre toward the outside; "lateral" = sideways from the door's axis.
 */
record Doorway(int x, int y, int z, int ox, int oz) {
	static Doorway of(int x, int y, int z, String out) {
		return switch (out) {
			case "north" -> new Doorway(x, y, z, 0, -1);
			case "south" -> new Doorway(x, y, z, 0, 1);
			case "east" -> new Doorway(x, y, z, 1, 0);
			case "west" -> new Doorway(x, y, z, -1, 0);
			default -> throw new IllegalArgumentException("doorway.out must be north|south|east|west, got " + out);
		};
	}

	double signed(double px, double pz) {
		return (px - (x + 0.5)) * ox + (pz - (z + 0.5)) * oz;
	}

	double lateral(double px, double pz) {
		return (px - (x + 0.5)) * -oz + (pz - (z + 0.5)) * ox;
	}

	/** The target may be fought: in the doorway cell or inside (not past the door cell's outer face). */
	boolean engageable(double tx, double tz) {
		return signed(tx, tz) <= 0.5;
	}

	/** Eve has drifted into the opening or out (she holds from inside, at least a body width behind the door's inner face). */
	boolean eveTooFarOut(double ex, double ez) {
		return signed(ex, ez) > -0.8;
	}

	/** The hold spot: 1.5 inside the door's centre and 2 to the side (sideSign +1 / -1), behind the door wall. {x, z}. */
	double[] holdSpot(int sideSign) {
		double cx = x + 0.5 - ox * 1.5, cz = z + 0.5 - oz * 1.5;
		return new double[] {cx + -oz * 2.0 * sideSign, cz + ox * 2.0 * sideSign};
	}

	/** The side Eve is on now (+1 / -1; +1 on the axis). */
	int sideOf(double ex, double ez) {
		return lateral(ex, ez) < 0 ? -1 : 1;
	}

	/**
	 * Which side to hold on: the TARGET's side, so the door wall is between them (a shooter on the far side sees a near-side
	 * spot diagonally through the opening); on the axis (|lateral| < 0.3) Eve's own side, the shorter walk.
	 */
	int sideFor(double tx, double tz, double ex, double ez) {
		double l = lateral(tx, tz);
		return Math.abs(l) < 0.3 ? sideOf(ex, ez) : l < 0 ? -1 : 1;
	}

	/**
	 * Is Eve in the line of fire from a target outside, through the opening? The straight line from the target to Eve crosses
	 * the door plane within the opening's half-width (0.5) plus an arrow's slack (0.3). Only meaningful while the target is
	 * outside and Eve inside.
	 */
	boolean inLineOfFire(double tx, double tz, double ex, double ez) {
		double st = signed(tx, tz), se = signed(ex, ez);
		if (st <= 0 || se >= 0) return false;
		double f = st / (st - se); // where the segment target -> Eve crosses the door plane
		double lat = lateral(tx, tz) + (lateral(ex, ez) - lateral(tx, tz)) * f;
		return Math.abs(lat) <= 0.8;
	}
}
