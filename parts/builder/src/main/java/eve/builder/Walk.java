package eve.builder;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.function.Predicate;

/**
 * O89c: where she can walk, as the body's Baritone does it (no breaking, no placing, no parkour): one step sideways on the
 * same level, a step up of one (with head room two above the cell she leaves: Baritone's ascend), or a drop of up to three
 * (maxFallHeightNoWater). A door cell is passable on a level move only: Baritone opens doors as it walks through, but it
 * does not step up into a door cell nor drop out of one (CP-49 lane C a12/a13: hut5_tall's door on the room floor, one up
 * from the plot, and no route into the room). Plain coordinates, no game classes, so the dev fixture can run it on a design
 * without a world. Bounded to a box around the site.
 */
final class Walk {
	interface World {
		boolean free(int x, int y, int z); // feet/head may be here (air, doors, plants)

		boolean floor(int x, int y, int z); // she can stand on top of it

		boolean door(int x, int y, int z); // a door cell (free, but only on a level move)
	}

	record P(int x, int y, int z) {
		P up(int k) {
			return new P(x, y + k, z);
		}
	}

	static final int[][] SIDES = {{1, 0}, {-1, 0}, {0, 1}, {0, -1}};
	final World w;
	final int x0, y0, z0, x1, y1, z1;

	Walk(World w, int x0, int y0, int z0, int x1, int y1, int z1) {
		this.w = w;
		this.x0 = x0;
		this.y0 = y0;
		this.z0 = z0;
		this.x1 = x1;
		this.y1 = y1;
		this.z1 = z1;
	}

	boolean in(P p) {
		return p.x >= x0 && p.x <= x1 && p.y >= y0 && p.y <= y1 && p.z >= z0 && p.z <= z1;
	}

	boolean free(P p) {
		return w.free(p.x, p.y, p.z);
	}

	boolean standable(P f) {
		return in(f) && free(f) && free(f.up(1)) && w.floor(f.x, f.y - 1, f.z);
	}

	boolean door(P p) {
		return w.door(p.x, p.y, p.z);
	}

	/** Her feet or head cell is a door. */
	boolean inDoor(P f) {
		return door(f) || door(f.up(1));
	}

	List<P> next(P f) {
		List<P> out = new ArrayList<>(4);
		boolean vertical = !inDoor(f); // never up or down out of a door cell
		for (int[] s : SIDES) {
			P n = new P(f.x + s[0], f.y, f.z + s[1]);
			if (standable(n)) {
				out.add(n);
				continue;
			}
			if (!vertical) continue;
			P up = n.up(1);
			if (free(f.up(2)) && standable(up) && !inDoor(up)) {
				out.add(up);
				continue;
			}
			if (free(n) && free(n.up(1)) && !inDoor(n))
				for (int k = 1; k <= 3; k++) { // step off into the column and fall to the first floor
					P dn = n.up(-k);
					if (!in(dn) || !free(dn) || door(dn)) break;
					if (standable(dn)) {
						out.add(dn);
						break;
					}
				}
		}
		return out;
	}

	/** Every cell reachable on foot from {@code from}. */
	Set<P> reach(P from) {
		Set<P> seen = new HashSet<>();
		search(from, q -> false, seen);
		return seen;
	}

	/** A walk from {@code from} reaches a cell {@code goal} accepts (stops at the first). */
	boolean reaches(P from, Predicate<P> goal) {
		return find(from, goal) != null;
	}

	/** The nearest cell (fewest moves) a walk from {@code from} reaches that {@code goal} accepts, or null. */
	P find(P from, Predicate<P> goal) {
		return search(from, goal, new HashSet<>());
	}

	private P search(P from, Predicate<P> goal, Set<P> seen) {
		ArrayDeque<P> q = new ArrayDeque<>();
		if (!standable(from)) return null;
		seen.add(from);
		q.add(from);
		while (!q.isEmpty()) {
			P c = q.poll();
			if (goal.test(c)) return c;
			for (P n : next(c))
				if (seen.add(n)) q.add(n);
		}
		return null;
	}

	/** The same walk with one cell filled (the block she is about to place): not free, no door, a floor when the block is
	 *  one to stand on (a pane, fence, wall, bed or lantern is not). */
	Walk with(P solid, boolean isFloor) {
		return new Walk(new World() {
			public boolean free(int x, int y, int z) {
				return !solid.equals(new P(x, y, z)) && w.free(x, y, z);
			}

			public boolean floor(int x, int y, int z) {
				return solid.equals(new P(x, y, z)) ? isFloor : w.floor(x, y, z);
			}

			public boolean door(int x, int y, int z) {
				return !solid.equals(new P(x, y, z)) && w.door(x, y, z);
			}
		}, x0, y0, z0, x1, y1, z1);
	}

	/** The same walk with a door placed at {@code lower} (and its upper half above): passable, but level moves only. */
	Walk withDoor(P lower) {
		return new Walk(new World() {
			public boolean free(int x, int y, int z) {
				return w.free(x, y, z);
			}

			public boolean floor(int x, int y, int z) {
				return !(lower.equals(new P(x, y, z)) || lower.up(1).equals(new P(x, y, z))) && w.floor(x, y, z);
			}

			public boolean door(int x, int y, int z) {
				return lower.equals(new P(x, y, z)) || lower.up(1).equals(new P(x, y, z)) || w.door(x, y, z);
			}
		}, x0, y0, z0, x1, y1, z1);
	}

	/** The same walk with one block gone (the one she is about to break). */
	Walk without(P gone) {
		return new Walk(new World() {
			public boolean free(int x, int y, int z) {
				return gone.equals(new P(x, y, z)) || w.free(x, y, z);
			}

			public boolean floor(int x, int y, int z) {
				return !gone.equals(new P(x, y, z)) && w.floor(x, y, z);
			}

			public boolean door(int x, int y, int z) {
				return !gone.equals(new P(x, y, z)) && w.door(x, y, z);
			}
		}, x0, y0, z0, x1, y1, z1);
	}
}
