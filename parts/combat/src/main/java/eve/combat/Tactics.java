package eve.combat;

import java.util.List;

/** The fixed fighting rules, on plain numbers so they are checked without a game (TacticsTest). No model, no randomness. */
final class Tactics {
	/** Keep this far (horizontal centre distance) while the sword recharges: a zombie's reach ends near 1.4, Eve's near 3.3. */
	static final double BAND_NEAR = 2.4, BAND_FAR = 3.0;
	/** Swing at full attack strength: attack_entity waits for 1.0, and every tick it waits the shield is down (CP-28 attempt 8). */
	static final float READY = 1.0f;
	/** Flip strafe direction this often (ticks), so a mob's straight-line approach keeps missing the centre line. */
	static final int STRAFE_PERIOD = 30;

	private Tactics() {}

	/** One candidate target as Eve's client sees it. */
	record Cand(double dist, float health, boolean sweepBlocked, boolean sight) {}

	/**
	 * CP-28 target choice: never a target she may not swing at (a protected entity inside its sweep area) while another exists;
	 * then the nearest, in sight first (the one about to hit her), weakest as tie-break (fewest attackers soonest). -1 if none.
	 * ponytail: distance stands in for "is targeting me" (the client cannot see mob AI targets); add threat ranks per type with the
	 * mixed-threat suite.
	 */
	static int pick(List<Cand> cs) {
		int best = -1;
		double bestScore = Double.MAX_VALUE;
		for (int i = 0; i < cs.size(); i++) {
			Cand c = cs.get(i);
			double s = c.dist() + (c.sight() ? 0 : 4) + c.health() / 20.0 + (c.sweepBlocked() ? 1000 : 0);
			if (s < bestScore) {
				bestScore = s;
				best = i;
			}
		}
		return best;
	}

	/** Vanilla arrow: full-draw speed (blocks/tick), drag per tick in air, gravity per tick. AbstractArrow / BowItem, 26.3. */
	static final double ARROW_SPEED = 3.0, ARROW_DRAG = 0.99, ARROW_GRAVITY = 0.05;
	/** Ticks of draw for full power (BowItem.getPowerForTime reaches 1.0 at 20). */
	static final int FULL_DRAW = 20;

	/** A solved shot: launch elevation (degrees above horizontal; Minecraft pitch is its negative) and flight time in ticks. */
	record Shot(double elevation, int ticks) {}

	/**
	 * The low-arc elevation that puts a full-power arrow at horizontal distance {@code d} and height {@code dy} above the launch
	 * point, by bisection over the simulated flight (same update order as the arrow: move, then drag, then gravity). Null when
	 * out of range at every elevation.
	 */
	static Shot solve(double d, double dy) {
		double hi = Double.NaN, best = Double.NEGATIVE_INFINITY; // the elevation that gets highest at d bounds the low arc
		for (int e = -45; e <= 75; e++) {
			double[] h = heightAt(e, d);
			if (h != null && h[0] > best) {
				best = h[0];
				hi = e;
			}
		}
		if (Double.isNaN(hi) || best < dy) return null;
		double lo = -45;
		for (int i = 0; i < 40; i++) {
			double mid = (lo + hi) / 2;
			double[] h = heightAt(mid, d);
			if (h == null || h[0] < dy) lo = mid;
			else hi = mid;
		}
		double[] h = heightAt(hi, d);
		return new Shot(hi, (int) Math.ceil(h[1]));
	}

	/** {height, ticks} of the flight when it reaches horizontal distance d, or null if it never gets there within 200 ticks. */
	static double[] heightAt(double elevationDeg, double d) {
		double a = Math.toRadians(elevationDeg), vx = ARROW_SPEED * Math.cos(a), vy = ARROW_SPEED * Math.sin(a), x = 0, y = 0;
		for (int tick = 1; tick <= 200; tick++) {
			double nx = x + vx, ny = y + vy;
			if (nx >= d) {
				double f = (d - x) / (nx - x);
				return new double[] {y + f * (ny - y), tick - 1 + f};
			}
			x = nx;
			y = ny;
			vx *= ARROW_DRAG;
			vy = vy * ARROW_DRAG - ARROW_GRAVITY;
			if (vx < 1e-3) return null;
		}
		return null;
	}

	/**
	 * S28-DEATH-2 (live, 15:14:33: a phantom killed her while she ran): below fight_melee's floor she runs from a walker, but holds
	 * the guard facing the threat when it flies (faster than her, and a sprint has the shield down) or when there is no step away.
	 */
	static boolean floorHold(boolean flyer, boolean canStepAway) {
		return flyer || !canStepAway;
	}

	/** S28(a): body's numbers (Reflexes), mirrored: its floor, the archer radius and aim cone, and a shield's 5-tick raise. */
	static final float BODY_FLOOR = 6f;
	static final double ARCHER_RADIUS = 24, AIM_COS = Math.cos(Math.toRadians(25));
	static final int SHIELD_RAISE = 5, BOW_DRAW = 20; // a raised shield blocks from its 5th tick; a bow releases after 20 ticks of draw

	/**
	 * S28(a) (three of four fatal hits landed on a shield raised at impact_ticks 2): in fight_melee the fight's guard goes up on an
	 * archer's DRAW, not on the arrow, and stays up while it draws. Above body's floor only (below it body's floor shield owns the
	 * controls); never on the tick a swing clicks (the click needs the shield down).
	 */
	/** O174: ticks the guard stays up after an archer's draw ends: an arrow takes ~3-6 ticks to cross 8-16 blocks, plus margin. */
	static final int POST_RELEASE_HOLD = 10;

	/** O174: hold the guard for a shot: drawn within POST_RELEASE_HOLD ticks (the arrow is in the air) or an arrow coming at her. */
	static boolean holdForArrow(int ticksSinceDraw, boolean arrowIncoming) {
		return ticksSinceDraw < POST_RELEASE_HOLD || arrowIncoming;
	}

	static boolean guardOnDraw(boolean drawingAtHer, float health, boolean swingClicking) {
		return drawingAtHer && health > BODY_FLOOR && !swingClicking;
	}

	/**
	 * S28(a) in fight_ranged's duel: keep drawing only when her arrow leaves at least SHIELD_RAISE ticks before the archer's (she
	 * shoots first and still has the shield up in time); otherwise drop the draw and shield until it releases. myDraw = her ticks
	 * of draw (-1 = not drawing); archerUse = its ticks of draw so far.
	 */
	static boolean shieldInsteadOfDraw(boolean drawingAtHer, float health, int myDraw, int archerUse) {
		if (!drawingAtHer || health <= BODY_FLOOR) return false;
		int mine = (FULL_DRAW + 1) - Math.max(myDraw, 0); // ticks until her release (she releases once draw > FULL_DRAW)
		int theirs = Math.max(0, BOW_DRAW - archerUse);
		return mine + SHIELD_RAISE > theirs;
	}

	/**
	 * O136 (Hunter:"when I say to fight a mob she needs to override her protected rule, just until that mob is dead"):
	 * {@code owner_target} lifts the protected-type rule for the named entity only. Never a player, never without entity_id.
	 * Returns null when allowed, else the refusal (code then reason) for the fight to end with.
	 */
	static String[] ownerTarget(boolean flag, Integer entityId, Integer targetId, boolean targetIsPlayer, boolean targetProtected) {
		if (flag && entityId == null) return new String[] {"bad_params", "owner_target needs entity_id: the owner names one mob"};
		if (!targetProtected) return null;
		if (targetIsPlayer) return new String[] {"protected_entity", "target is a player: never, owner_target or not"};
		if (flag && targetId != null && targetId.equals(entityId)) return null;
		return new String[] {"protected_entity", "target is protected"};
	}

	/** O126: 5 s out of reach without getting a block closer = unreachable (Hunter live: a mob behind a wall, she froze silently). */
	static final int UNREACHABLE_TICKS = 100;

	/** O126: progress toward one target. close = in the fight already (within 4, or a swing in flight): never unreachable. */
	static final class Reach {
		double best = Double.MAX_VALUE;
		int stalled;

		/** True once the target has stayed out of reach UNREACHABLE_TICKS ticks without her getting at least a block closer. */
		/** A fresh window: after a reflex pause the distance before it is no baseline (it moved her, not the fight). */
		void reset() {
			best = Double.MAX_VALUE;
			stalled = 0;
		}

		boolean update(double dist, boolean close) {
			if (close || dist < best - 1.0) {
				best = dist;
				stalled = 0;
				return false;
			}
			return ++stalled > UNREACHABLE_TICKS;
		}
	}

	/** O116: sampling step (blocks) along the simulated arrow path; a fence post is 0.25 wide. */
	static final double CLIP_STEP = 0.1;

	/**
	 * O116 (body's floor_pen, Hunter: "shooting the fence"): does the arrow's own path, not the eye-to-eye line, reach horizontal
	 * distance {@code d} without entering a solid collider? {@code solid(x, y)} = a block collider at horizontal distance x along
	 * the shot and height y above the release point (eye - 0.1). Same flight update as heightAt. The last 0.3 is the target itself.
	 */
	static boolean clearPath(double elevationDeg, double d, java.util.function.BiPredicate<Double, Double> solid) {
		double a = Math.toRadians(elevationDeg), vx = ARROW_SPEED * Math.cos(a), vy = ARROW_SPEED * Math.sin(a), x = 0, y = 0;
		double end = Math.max(0, d - 0.3);
		for (int tick = 1; tick <= 200 && x < end; tick++) {
			double nx = x + vx, ny = y + vy;
			int n = (int) Math.ceil((nx - x) / CLIP_STEP);
			for (int i = 1; i <= n; i++) {
				double sx = x + (nx - x) * i / n;
				if (sx > end) break;
				if (solid.test(sx, y + (ny - y) * i / n)) return false;
			}
			x = nx;
			y = ny;
			vx *= ARROW_DRAG;
			vy = vy * ARROW_DRAG - ARROW_GRAVITY;
			if (vx < 1e-3) return false;
		}
		return true;
	}

	/**
	 * O116: the first aim height (in order: hitbox centre, 0.75 of its height, its eye) whose solved low-arc path is clear, or -1.
	 * {@code dys} are those aim points' heights above the release point; {@code d} the horizontal distance.
	 */
	static int firstClear(List<Double> dys, double d, java.util.function.BiPredicate<Double, Double> solid) {
		for (int i = 0; i < dys.size(); i++) {
			Shot s = solve(d, dys.get(i));
			if (s != null && clearPath(s.elevation(), d, solid)) return i;
		}
		return -1;
	}

	/** Movement keys this tick: forward/backward hold the spacing band (or close in when the swing is ready), strafe alternates. */
	/**
	 * O102v option A (CP-28 armed x_skeleton 7/20: crawling at ~20 % speed behind a raised shield never closed on a skeleton that
	 * backs off inside ~7.5). Against an archer in sight, out of swing reach and within RUSH_RANGE: HOLD (guard up, facing it) while
	 * its bow is drawn, RUSH (guard down, sprint straight in) once it has released. Otherwise NONE: the usual approach and band.
	 */
	enum ArcherMove { NONE, HOLD, RUSH }

	static final double RUSH_RANGE = 16;

	static ArcherMove archer(boolean ranged, boolean sight, double dist, double reach, boolean drawing) {
		if (!ranged || !sight || reach <= FightMelee.SWING_REACH || dist > RUSH_RANGE) return ArcherMove.NONE;
		return drawing ? ArcherMove.HOLD : ArcherMove.RUSH;
	}

	record Keys(boolean forward, boolean backward, boolean left, boolean right) {}

	static Keys spacing(double dist, boolean ready, boolean inReach, int tick) {
		boolean fwd, back;
		if (ready) {
			fwd = !inReach;
			back = dist < 1.6; // too close to swing cleanly: the mob's own reach
		} else {
			fwd = dist > BAND_FAR;
			back = dist < BAND_NEAR;
		}
		boolean left = (tick / STRAFE_PERIOD) % 2 == 0;
		boolean strafe = !fwd || dist < 4.5; // straight in from far away, strafe once close
		return new Keys(fwd, back, strafe && left, strafe && !left);
	}
}
