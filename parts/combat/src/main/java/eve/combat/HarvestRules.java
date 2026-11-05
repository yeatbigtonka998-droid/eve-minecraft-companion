package eve.combat;

/**
 * harvest_animal's pure rules (O94 CP-39): which granted animal may be killed, the keep floor, and the zone box it never leaves.
 * Refusals are rule ids, as skills' shear_sheep / milk_cow report them: sense's rule id verbatim where sense has the rule
 * (animal:baby, animal:leashed, protected:named, protected:pets), else a client: id for checks only the client can make.
 */
final class HarvestRules {
	private HarvestRules() {
	}

	/** What the client sees of one granted id. breedingStock = in exclude_ids (sense refused it). */
	record Animal(boolean alive, boolean animal, boolean wantedType, boolean baby, boolean named, boolean tamed, boolean leashed,
			boolean protectedE, boolean breedingStock, boolean inZone) {
	}

	/** The reason this id must not be hit, or null when it may. Order: the cheapest certain refusals first. */
	static String refusal(Animal a) {
		if (!a.alive()) return "client:gone";
		if (!a.animal() || !a.wantedType()) return "client:wrong_type";
		if (a.breedingStock()) return "client:excluded"; // the mind's exclude_ids (e.g. ids sense refused as animal:keep_min)
		if (a.baby()) return "animal:baby";
		if (a.named()) return "protected:named";
		if (a.tamed()) return "protected:pets";
		if (a.leashed()) return "animal:leashed";
		if (a.protectedE()) return "protected:pets"; // body's isProtected when none of the above (skills maps it the same)
		if (!a.inZone()) return "client:out_of_zone";
		return null;
	}

	/**
	 * The keep floor (skills' second guard on breeding stock): with `adults` same-species adults the client sees alive in the
	 * zone (this one included), a kill is allowed only if at least `keep` are left after it. The client sees at most as many
	 * as exist, so this stops early, never late. keep < 0 = no floor.
	 */
	static boolean keepAllows(int adults, int keep) {
		return keep < 0 || adults - 1 >= keep;
	}

	/** The zone box, inclusive block bounds: a position is inside when its block is. */
	record Zone(int x0, int y0, int z0, int x1, int y1, int z1) {
		static Zone of(int[] a, int[] b) {
			return new Zone(Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.min(a[2], b[2]),
					Math.max(a[0], b[0]), Math.max(a[1], b[1]), Math.max(a[2], b[2]));
		}

		boolean contains(double x, double y, double z) {
			int bx = (int) Math.floor(x), by = (int) Math.floor(y), bz = (int) Math.floor(z);
			return bx >= x0 && bx <= x1 && by >= y0 && by <= y1 && bz >= z0 && bz <= z1;
		}

		/** The nearest point inside the box (half a block in from the faces): every walk_to target is clamped here. */
		double[] clamp(double x, double y, double z) {
			return new double[] {Math.max(x0 + 0.5, Math.min(x1 + 0.5, x)), Math.max(y0, Math.min(y1, y)), Math.max(z0 + 0.5, Math.min(z1 + 0.5, z))};
		}
	}
}
