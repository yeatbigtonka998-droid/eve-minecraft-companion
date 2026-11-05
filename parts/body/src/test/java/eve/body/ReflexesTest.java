package eve.body;

import net.minecraft.core.BlockPos;
import net.minecraft.world.phys.Vec3;
import org.junit.jupiter.api.Test;

import java.util.function.BiPredicate;
import java.util.function.Predicate;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** O98, offline: the CP-31 a16 night-1 room (stand 0,200,-20, shell x -3..3 z -23..-17 y 199..204, doorway 0,-17) and its skeleton. */
class ReflexesTest {
	static final Vec3 SKELETON_EYE = new Vec3(1.0, 201.74, -15.7); // 1.3 outside the doorway, where it shot from for ~45 s
	static final Vec3 DIED_AT = new Vec3(1.126, 200.0, -21.526);

	static boolean wall(int x, int y, int z) {
		boolean shell = x >= -3 && x <= 3 && z >= -23 && z <= -17 && y >= 199 && y <= 204
				&& (x == -3 || x == 3 || z == -23 || z == -17 || y == 199 || y == 204);
		return shell && !(x == 0 && z == -17 && (y == 200 || y == 201));
	}

	/** A clear line: no wall cell at any point 1/400 of the way along it. */
	static final BiPredicate<Vec3, Vec3> CLEAR = (a, b) -> {
		for (int k = 0; k <= 400; k++) {
			Vec3 q = a.lerp(b, k / 400.0);
			if (wall((int) Math.floor(q.x), (int) Math.floor(q.y), (int) Math.floor(q.z))) return false;
		}
		return true;
	};

	static final Predicate<BlockPos> IN_ROOM = c -> c.getY() == 200 && c.getX() >= -2 && c.getX() <= 2 && c.getZ() >= -22 && c.getZ() <= -18;

	@Test
	void aimedAt() {
		Vec3 her = DIED_AT.add(0, 1.62, 0), at = her.subtract(SKELETON_EYE);
		assertTrue(Reflexes.aimedAt(SKELETON_EYE, at, her));
		assertTrue(Reflexes.aimedAt(SKELETON_EYE, at.yRot((float) Math.toRadians(20)), her), "within 25 deg");
		assertFalse(Reflexes.aimedAt(SKELETON_EYE, at.yRot((float) Math.toRadians(40)), her), "looking past her");
		assertFalse(Reflexes.aimedAt(SKELETON_EYE, at.scale(-1), her), "looking away");
	}

	@Test
	void shieldFacesBackAlongTheArrow() {
		Vec3 eye = DIED_AT.add(0, 1.62, 0), v = eye.subtract(SKELETON_EYE).normalize().scale(1.6);
		Vec3 beside = eye.add(0.4, 0, 0.2); // the arrow's last tick, already beside her: its position is off the shooter's line
		Vec3 at = Reflexes.alongPath(eye, beside, v);
		assertTrue(at.subtract(eye).normalize().dot(SKELETON_EYE.subtract(eye).normalize()) > 0.999, "toward the shooter: " + at);
		assertEquals(beside, Reflexes.alongPath(eye, beside, Vec3.ZERO), "a still projectile: its own position");
	}

	@Test
	void whereSheDiedIsInTheLineOfFire() {
		assertTrue(Reflexes.exposed(SKELETON_EYE, DIED_AT, CLEAR));
	}

	@Test
	void coverIsBesideTheDoorWall() {
		Predicate<BlockPos> seen = c -> Reflexes.exposed(SKELETON_EYE, Vec3.atBottomCenterOf(c), CLEAR);
		BlockPos c = Reflexes.coverCell(BlockPos.containing(DIED_AT), 5, IN_ROOM, seen);
		assertNotNull(c, "a hidden cell in the room");
		assertFalse(seen.test(c));
		assertEquals(-18, c.getZ(), "against the doorway's own wall: " + c);
		assertTrue(Math.abs(c.getX()) == 2, "in a corner beside it, out of the door's line: " + c);
	}

	@Test
	void aSealCellOrItsNeighbourOnTheAimLine() {
		Vec3 archer = new Vec3(0.5, 201.74, -15.2); // CP-31 a22: the skeleton summoned at 0.5 200 -15.2 (O98o (2))
		for (Vec3 eye : new Vec3[] {new Vec3(0.5, 201.62, -19.5), new Vec3(0.5, 201.27, -19.5), new Vec3(0.5, 201.62, -18.2)}) {
			assertTrue(Reflexes.sealsLine(new BlockPos(0, 201, -17), archer, eye), "the head cell is on the line: " + eye);
			assertTrue(Reflexes.sealsLine(new BlockPos(0, 200, -17), archer, eye), "the feet cell, through the head cell above it: " + eye);
			assertFalse(Reflexes.sealsLine(new BlockPos(1, 201, -17), archer, eye), "beside the opening: " + eye);
			assertFalse(Reflexes.sealsLine(new BlockPos(0, 200, -22), archer, eye), "behind her: " + eye);
		}
	}

	@Test
	void shieldPressesAgainUntilItTakes() {
		assertEquals("press", Reflexes.Shield.pressStep(false, false, 0, 0), "the first raise");
		assertNull(Reflexes.Shield.pressStep(true, true, 50, 1), "using it: nothing to do");
		assertNull(Reflexes.Shield.pressStep(true, false, 2, 1), "a press needs a few ticks to take");
		assertEquals("press", Reflexes.Shield.pressStep(true, false, 4, 1), "O102d c_rush #6: not using it 4 ticks after the press");
		assertEquals("no_block", Reflexes.Shield.pressStep(true, false, 4, 3), "three presses never took: hand the controls back");
	}

	@Test
	void aPausedFightKeepsItsGuardClaim() {
		assertTrue(Providers.ProviderAct.claimed(false, 0, 100, 99), "running: claimed last tick");
		assertFalse(Providers.ProviderAct.claimed(false, 0, 102, 99), "running: a lapsed claim");
		// O102d c_rush #6: paused at tick 100 by an arrow's shield; 391 ticks later the claim still stands, so the zombie stays the fight's
		assertTrue(Providers.ProviderAct.claimed(true, 100, 491, 99), "paused: judged as of the pause");
		assertFalse(Providers.ProviderAct.claimed(true, 100, 491, 90), "paused: no claim before the pause, none during it");
	}

	@Test
	void theFloorHandBackOpensOnlyForAFightAgainstAnArcher() {
		// O102d (3) c_rush #6 at 25.5 s: 4.8 HP, a lone skeleton ~10 out, fight_ranged paused
		assertTrue(Reflexes.handBackOpens(4.8f, 10.0, true, true));
		assertFalse(Reflexes.handBackOpens(7.0f, 10.0, true, true), "above the floor: the reflexes as before");
		assertFalse(Reflexes.handBackOpens(4.8f, 2.0, true, true), "a mob in melee reach: the cell is contested");
		assertFalse(Reflexes.handBackOpens(4.8f, 10.0, false, true), "no ranged hostile in sight");
		assertFalse(Reflexes.handBackOpens(4.8f, 10.0, true, false), "no fight to hand back to");
	}

	@Test
	void theHandBackLastsEightSecondsAndCoversTheNextFightCall() {
		long s = 1_000_000_000L, until = 8 * s;
		assertTrue(Reflexes.handBackOpen(3 * s, until, true, 3 * s), "a fight inside the window");
		assertTrue(Reflexes.handBackOpen(3 * s + s / 2, until, false, 3 * s), "between two fight calls (one target each): still open");
		assertFalse(Reflexes.handBackOpen(5 * s, until, false, 3 * s), "no fight for 2 s: closed");
		assertFalse(Reflexes.handBackOpen(8 * s, until, true, 8 * s), "8 s: closed");
	}

	@Test
	void aSecondShieldPreemptedFloorRetreatWithinTenSecondsOpensTheHandBack() {
		long s = 1_000_000_000L;
		assertFalse(Reflexes.secondPreempt(5 * s, 0), "the first one only starts the count");
		assertTrue(Reflexes.secondPreempt(12 * s, 5 * s), "O102o floor_pen a101: a second one within 10 s");
		assertFalse(Reflexes.secondPreempt(16 * s, 5 * s), "11 s apart: not the cycle");
	}

	@Test
	void aFightOwnsMeleeAndTheDrawnBowButNotArrows() {
		// O102ab fix A (lane F c_rush #18: a melee shield held fight_ranged paused 50 s)
		assertEquals("projectile", Reflexes.shieldCause(true, true, true, true, false), "an arrow predicted to hit: always");
		assertNull(Reflexes.shieldCause(false, true, true, true, false), "over a fight: no drawn-bow or melee shield");
		assertEquals("drawn_bow", Reflexes.shieldCause(false, true, true, false, false), "no fight: the drawn bow first, as before");
		assertEquals("melee", Reflexes.shieldCause(false, true, true, false, true), "a seal against the archer: melee still raises it");
		assertEquals("melee", Reflexes.shieldCause(false, false, true, false, false));
		assertNull(Reflexes.shieldCause(false, false, false, false, false));
	}

	@Test
	void noMealOverAFight() {
		assertTrue(Reflexes.eatAllowed(true, true, false));
		assertFalse(Reflexes.eatAllowed(true, true, true), "O102ab c_rush #18: an Eat paused the fight inside the hand-back");
		assertFalse(Reflexes.eatAllowed(false, true, false), "not safe");
	}

	@Test
	void noCoverInOpenGround() {
		BiPredicate<Vec3, Vec3> open = (a, b) -> true;
		assertEquals(null, Reflexes.coverCell(new BlockPos(0, 200, 0), 5, c -> true, c -> Reflexes.exposed(SKELETON_EYE, Vec3.atBottomCenterOf(c), open)));
	}

	@Test
	void atTheFloorAShooterNearestIsTheShieldsNotARetreat() { // S28: live death 13:26:56, retreat and shield pre-empted each other
		assertTrue(Reflexes.floorShield(4f, true, 1.3, true, true), "4 HP, the skeleton at 1.3 m in sight, a shield: shield-walk");
		assertTrue(Reflexes.floorShield(6f, true, 16, true, true), "at the floor itself, out to ARCHER_HOLD");
		assertFalse(Reflexes.floorShield(6.5f, true, 5, true, true), "above the floor: the usual shields");
		assertFalse(Reflexes.floorShield(4f, false, 2, true, true), "a zombie nearest: retreat as before");
		assertFalse(Reflexes.floorShield(4f, true, 17, true, true), "beyond ARCHER_HOLD");
		assertFalse(Reflexes.floorShield(4f, true, 8, false, true), "not in sight: retreat to cover");
		assertFalse(Reflexes.floorShield(4f, true, 8, true, false), "no shield in hand: retreat");
	}

	@Test
	void theFloorChoiceHoldsTwoSeconds() { // S28 second live death 15:14:33: shield/retreat flipped 9x in 9 s
		long now = 5_000_000_000L, until = now + Reflexes.FLOOR_LATCH_NS;
		assertTrue(Reflexes.latchOpen(now + 1_999_000_000L, until), "inside 2 s: the choice stands");
		assertFalse(Reflexes.latchOpen(now + 2_000_000_000L, until), "after 2 s: chosen again");
		assertEquals(2_000_000_000L, Reflexes.FLOOR_LATCH_NS);
	}

	@Test
	void aCalmCreeperWalkingAtHerLeavesTheFightUntilSix() { // O280, S96 lane C x_creeper: 315 flees at 6.6-7 m, 0 swings
		long never = Long.MAX_VALUE;
		for (double d : new double[] {7.0, 6.8, 6.6, 6.1}) assertFalse(Reflexes.creeperFlees(false, d, 1.25, never, false), "calm at " + d + ": she keeps swinging");
		assertTrue(Reflexes.creeperFlees(false, 6.0, 1.25, never, false), "advancing within 6: run");
		assertFalse(Reflexes.creeperFlees(false, 3.5, 0.0, never, false), "close but not advancing, not primed: no");
	}

	@Test
	void aPrimedCreeperIsFledAtOnce() {
		assertTrue(Reflexes.creeperFlees(true, 3.0, 0.0, 0, false), "primed at 3, even just after a safe flee");
		assertTrue(Reflexes.creeperFlees(true, 11.0, 0.0, Long.MAX_VALUE, false), "primed within 12");
		assertFalse(Reflexes.creeperFlees(true, 12.5, 0.0, Long.MAX_VALUE, false), "primed beyond 12: no");
	}

	@Test
	void aSafeFleeHoldsOffUntilFive() {
		assertFalse(Reflexes.creeperFlees(false, 5.9, 1.25, 20, false), "1 s after a safe flee, at 5.9");
		assertTrue(Reflexes.creeperFlees(false, 5.0, 1.25, 20, false), "within 5 the lockout gives way");
		assertTrue(Reflexes.creeperFlees(false, 5.9, 1.25, 60, false), "3 s: 6 again");
	}

	/** O280: a creeper at 2.73 m/s (a116) walks at her from 7 m while she fights; she sprints at 5.6 m/s, safe = 9 m held for 11 ticks. */
	@Test
	void sheFightsBetweenRetreatsAndItNeverGetsInsideThreeAndAHalf() {
		double d = 7, min = 7;
		final double creeper = 0.1365, sprint = 0.28;
		long sinceSafe = Long.MAX_VALUE / 2;
		int fightTicks = 0, retreats = 0, calm = 0;
		boolean fleeing = false;
		double firstRetreat = -1;
		for (int t = 0; t < 1200; t++) { // 60 s
			if (fleeing) {
				d += sprint - creeper;
				calm = d >= 9 ? calm + 1 : 0;
				if (calm > 10) {
					fleeing = false;
					sinceSafe = 0;
					calm = 0;
				}
			} else {
				d -= creeper;
				fightTicks++;
				if (Reflexes.creeperFlees(false, d, creeper * 10, sinceSafe, false)) {
					fleeing = true;
					retreats++;
					if (firstRetreat < 0) firstRetreat = d;
				}
			}
			sinceSafe++;
			min = Math.min(min, d);
		}
		assertTrue(firstRetreat <= 6 && firstRetreat > 5.8, "she swings until 6: " + firstRetreat);
		assertTrue(min > 3.5, "it never gets inside 3.5: " + min);
		assertTrue(retreats >= 5, "she retreats again and again: " + retreats);
		assertTrue(fightTicks > 500, "and fights between them: " + fightTicks + " of 1200 ticks");
	}

	@Test
	void herOwnWalkIsNotACreeperAdvancing() {
		assertEquals(0.0, Reflexes.creeperAdvance(0, 0, 0, 0, 6, 0), 1e-9, "it stands still while she walks");
		assertEquals(1.0, Reflexes.creeperAdvance(-1, 0, 0, 0, 6, 0), 1e-9, "it walks 1 toward her");
		assertEquals(0.0, Reflexes.creeperAdvance(0, -1, 0, 0, 6, 0), 1e-9, "it walks across");
		assertEquals(-1.0, Reflexes.creeperAdvance(1, 0, 0, 0, 6, 0), 1e-9, "it backs off");
	}

	@Test
	void stoppedKeepsHerDefencesOnly() { // O218: third live death, STOPPED cancelled her shield
		for (String d : new String[] {"shield", "dodge", "retreat", "creeper_flee", "eat", "fall_rescue", "hazard"})
			assertTrue(Reflexes.allowedStopped(d), d);
		for (String n : new String[] {"sleep", "darkness"})
			assertFalse(Reflexes.allowedStopped(n), n + " waits for a resume");
	}

	@Test
	void aTopUpBringsFoodBackToRegeneration() { // O227: HP 15 sat for 40 min at food 17
		assertTrue(Reflexes.hungry(15f, 17), "hurt, food below regen's 18: top up");
		assertTrue(Reflexes.hungry(19f, 17));
		assertFalse(Reflexes.hungry(20f, 17), "full health, food 17: no need");
		assertFalse(Reflexes.hungry(15f, 18), "food 18 regenerates on its own");
		assertTrue(Reflexes.hungry(20f, 14), "the old hunger rule stands");
	}

	@Test
	void sheWalksNotSprintsNearADrop() { // O237: a creeper flee ran her off the doorway lip, 77 -> 70
		assertEquals(0, Reflexes.fleeGait(0.5), "unsafe a block ahead: not that way");
		assertEquals(1, Reflexes.fleeGait(1.0), "a block of footing: walk");
		assertEquals(1, Reflexes.fleeGait(2.5), "a drop within 3: walk, a sprint would carry her over");
		assertEquals(2, Reflexes.fleeGait(3.0), "3 clear: sprint");
	}

	/** O294: her fight walks her at it after each safe flee (0.2 a tick, lane C closed 4 m in 0.85 s): no re-fire inside the 3 s unless primed. */
	@Test
	void herFightWalkingBackDoesNotRefireTheFleeInsideTheLockout() {
		assertFalse(Reflexes.creeperFlees(false, 4.8, 1.25, 20, true), "walking at it, 1 s after a safe flee, calm: fight on");
		assertTrue(Reflexes.creeperFlees(false, 4.8, 1.25, 20, false), "standing: the 5 m floor holds");
		assertTrue(Reflexes.creeperFlees(true, 3.0, 0.0, 20, true), "primed: run, whatever she is doing");
		double d = 9, min = 9;
		final double creeper = 0.1365, walk = 0.2, sprint = 0.28;
		long sinceSafe = 0;
		int calm = 0, flees = 0, lastEnd = 0, shortest = 9999;
		boolean fleeing = false;
		for (int t = 0; t < 1200; t++) {
			if (fleeing) {
				d += sprint - creeper;
				calm = d >= 9 ? calm + 1 : 0;
				if (calm > 10) {
					fleeing = false;
					sinceSafe = 0;
					calm = 0;
					lastEnd = t;
				}
			} else {
				d -= creeper + walk;
				if (Reflexes.creeperFlees(d <= 3, d, creeper * 10, sinceSafe, true)) {
					fleeing = true;
					flees++;
					if (lastEnd > 0) shortest = Math.min(shortest, t - lastEnd);
				}
			}
			sinceSafe++;
			min = Math.min(min, d);
		}
		assertTrue(flees <= 20, "not the 26-28 of a 110 s trial in 60 s: " + flees);
		assertTrue(shortest >= 17, "she gets near 1 s to fight each time, against 0.85 s of no reach: " + shortest);
		assertTrue(min > 2.5, "it is primed at 3, and she runs from there: " + min);
	}

	@Test
	void aClaimLapsesFiveTicksAfterItsLastCall() {
		assertTrue(Reflexes.creeperClaimed(7, 7, 105, 105), "renewed at 100, still held at 105");
		assertFalse(Reflexes.creeperClaimed(7, 7, 105, 106), "lapsed");
		assertFalse(Reflexes.creeperClaimed(8, 7, 105, 100), "any other creeper is not claimed");
	}

	/** O308: she walks 0.2 a tick at a drop for the 80 collect ticks, waiting while the ring of an idle creeper at (cx, 0) blocks it. */
	private static boolean collects(double dropX, double dropZ, double cx) {
		double x = 0, z = 0;
		for (int t = 0; t < 80; t++) {
			double d = Math.hypot(dropX - x, dropZ - z);
			if (d < 1) return true;
			if (Reflexes.ringBlocks(x, z, dropX, dropZ, cx, 0)) continue;
			x += (dropX - x) / d * 0.2;
			z += (dropZ - z) / d * 0.2;
		}
		return false;
	}

	@Test
	void aDropBesideAnIdleCreeperIsLeftAndOneClearOfItIsCollected() {
		assertFalse(collects(6, 0, 8), "2 m from the creeper: not collected");
		assertTrue(collects(6, 8, 6), "6 m to the side of it, the line clear: collected");
		assertFalse(collects(12, 0, 6), "beyond it, the straight line crosses the ring: not walked");
		assertFalse(Reflexes.ringBlocks(0, 0, -3, 0, 2, 0), "inside the ring, walking out of it: allowed");
		assertTrue(Reflexes.ringBlocks(0, 0, 1, 0, 2, 0), "inside, walking further in: no");
	}

	@Test
	void aCreepersFirstStepAtHerWithinFiveCountsAtOnce() { // O314, a135: 0.45 s from its first step to the flee, at 3.15 m
		assertTrue(Reflexes.creeperFlees(false, 3.5, Reflexes.wakeAdvance(0.0, 0.14, 3.5), Long.MAX_VALUE, false), "first step: run");
		assertFalse(Reflexes.creeperFlees(false, 5.5, Reflexes.wakeAdvance(0.0, 0.14, 5.5), Long.MAX_VALUE, false), "first step beyond 5: the window decides");
		assertFalse(Reflexes.creeperFlees(false, 3.5, Reflexes.wakeAdvance(0.0, 0.02, 3.5), Long.MAX_VALUE, false), "a client jitter: no");
		assertFalse(Reflexes.creeperFlees(false, 3.5, Reflexes.wakeAdvance(0.0, -0.14, 3.5), Long.MAX_VALUE, false), "stepping away: no");
		assertEquals(1.2, Reflexes.wakeAdvance(1.2, 0.14, 3.5), 1e-9, "a real window value stays");
	}

	/** O324: she walked east down a 1-wide tunnel, x 0 to 8; it records a point every 0.5. */
	private static java.util.ArrayDeque<net.minecraft.world.phys.Vec3> tunnel() {
		var t = new java.util.ArrayDeque<net.minecraft.world.phys.Vec3>();
		for (int i = 0; i <= 16; i++) t.addLast(new net.minecraft.world.phys.Vec3(i * 0.5, 0, 0));
		return t;
	}

	@Test
	void inATunnelSheBacksOutTheWaySheCame() { // O324, lane D: two cave deaths, every flee "cornered"
		var her = new net.minecraft.world.phys.Vec3(8, 0, 0);
		var ahead = new net.minecraft.world.phys.Vec3(13, 0, 0); // the creeper in front of her, further down the tunnel
		var trail = tunnel();
		var q = Reflexes.trailTarget(trail, her, ahead);
		assertNotNull(q, "a way back");
		assertEquals(6.5, q.x, 1e-9, "the newest point at least 1.5 back");
		// she walks it: each reached point is used up, the next is farther back
		for (double x = 8; x > 1.6; x -= 0.25) q = Reflexes.trailTarget(trail, new net.minecraft.world.phys.Vec3(x, 0, 0), ahead);
		assertTrue(q == null || q.x < 1.6, "down to the tunnel mouth: " + q);
		var behind = new net.minecraft.world.phys.Vec3(5, 0, 0); // it came up the tunnel after her
		assertNull(Reflexes.trailTarget(tunnel(), her, behind), "the trail leads into it: not that way");
	}

	@Test
	void aTeleportClearsTheTrail() {
		Reflexes.TRAIL.clear();
		Reflexes.recordTrail(new net.minecraft.world.phys.Vec3(0, 0, 0), 0);
		Reflexes.recordTrail(new net.minecraft.world.phys.Vec3(1, 0, 0), 5);
		assertEquals(2, Reflexes.TRAIL.size());
		Reflexes.recordTrail(new net.minecraft.world.phys.Vec3(30, 0, 0), 10);
		assertEquals(1, Reflexes.TRAIL.size(), "tp'd: the old trail is not hers to walk back");
		Reflexes.TRAIL.clear();
	}

	@Test
	void sheBacksAwayFacingIt() { // O326 (a): yaw 0 faces +z; the way out is -z: backwards, no strafe
		var in = Reflexes.facingMove(0f, 0, -3);
		assertTrue(in.backward() && !in.forward() && !in.left() && !in.right(), in.toString());
		var side = Reflexes.facingMove(0f, 3, 0); // +x is to her left at yaw 0
		assertTrue(side.left() && !side.right(), side.toString());
	}

	@Test
	void fromACreeperSheSprintsWithTheShieldDownAtAnyHealth() { // O335
		assertFalse(Reflexes.fleeShieldUp(true, 3f), "a creeper at the floor: sprint, shield down");
		assertFalse(Reflexes.fleeShieldUp(true, 20f), "a creeper at full health: sprint");
		assertTrue(Reflexes.fleeShieldUp(false, 3f), "a melee or ranged mob at the floor: shield up (S28-2)");
		assertFalse(Reflexes.fleeShieldUp(false, 12f), "above the floor: no");
	}

	@Test
	void aCreeperOutranksTheArcherAtTheFloor() { // O335: both on her at the floor: the creeper wins
		assertTrue(Reflexes.CREEPER_FLEE_PRIORITY > Reflexes.SHIELD_PRIORITY);
		assertTrue(Reflexes.CREEPER_FLEE_PRIORITY > Reflexes.RETREAT_PRIORITY);
	}

	@Test
	void confinedSheBacksOutOrShieldsOrRunsNeverAWall() { // O347: 0 of 5 walls landed on lane C; no-shield #2 took 19.04 from behind
		assertEquals("trail", Reflexes.confinedPlan(true, true), "a way back that leads away: back out, shield toward it");
		assertEquals("shield", Reflexes.confinedPlan(true, false), "no way back: the shield toward it");
		assertEquals("trail", Reflexes.confinedPlan(false, true), "no shield: back out if it leads away");
		assertEquals("run", Reflexes.confinedPlan(false, false), "no shield, no way back: the best direction at once, never standing");
	}

	@Test
	void theWallGoesOnTheSideFacingTheCreeper() { // O347 revised, no-shield kit only
		assertEquals(net.minecraft.core.Direction.EAST, Reflexes.towardCreeper(0, 0, 4, 1));
		assertEquals(net.minecraft.core.Direction.NORTH, Reflexes.towardCreeper(0, 0, 1, -4));
		assertEquals(net.minecraft.core.Direction.WEST, Reflexes.towardCreeper(0, 0, -3, 2));
	}

	@Test
	void everySubActAReflexDrivesGetsTheKeysItAskedFor() { // O355: the crouch for a place, the sneak for a dismount
		com.google.gson.JsonObject prm = new com.google.gson.JsonObject(), pos = new com.google.gson.JsonObject();
		pos.addProperty("x", 0);
		pos.addProperty("y", 0);
		pos.addProperty("z", 0);
		prm.add("pos", pos);
		prm.addProperty("item", "minecraft:dirt");
		var place = new Interact.PlaceBlock("t", prm, 0, 0);
		assertEquals(Boolean.TRUE, place.sneak, "a place asks for its crouch");
		assertTrue(Reflexes.withSubActKeys(net.minecraft.world.entity.player.Input.EMPTY, place).shift(), "and the reflex passes it on");
		var out = new Interact.Dismount("d", new com.google.gson.JsonObject(), 0, 0);
		assertTrue(Reflexes.withSubActKeys(net.minecraft.world.entity.player.Input.EMPTY, out).shift(), "a dismount's sneak is passed on");
		var none = new Interact.AttackEntity("a", new com.google.gson.JsonObject(), 0, 0);
		assertEquals(none.sneak != null && none.sneak, Reflexes.withSubActKeys(net.minecraft.world.entity.player.Input.EMPTY, none).shift(), "no ask: as given");
	}

	@Test
	void atTheFloorTheShieldTakesOverARetreatOnlyForAProjectile() { // 45L 17:16:03: 7 shield/retreat swaps on phantoms
		assertFalse(Reflexes.shieldMayPreemptRetreat(false), "a mob in reach: the retreat keeps going");
		assertTrue(Reflexes.shieldMayPreemptRetreat(true), "an arrow on its way in: shield");
	}

	@Test
	void theRetreatLeansTowardHome() { // 45L 17:16: 30 m from the shelter while go_home waited
		var away = new net.minecraft.world.phys.Vec3(1, 0, 0);
		var north = new net.minecraft.world.phys.Vec3(0, 0, -1);
		var r = Reflexes.retreatWay(away, north, false);
		assertTrue(r.x > 0.5 && r.z < -0.5, "away from the mob and toward home: " + r);
		assertEquals(away, Reflexes.retreatWay(away, new net.minecraft.world.phys.Vec3(-1, 0, 0), false), "home past the mob: straight away");
		assertEquals(away, Reflexes.retreatWay(away, null, false), "no home, no owner: away");
		assertEquals(north, Reflexes.retreatWay(away, north, true), "a phantom at night: home outright");
	}

	@Test
	void phantomCoverFiresOnTheFirstHitUnderOpenSky() { // S105 kickoff ruling
		assertTrue(Reflexes.phantomCoverDue(true, true, 20f, true), "hit by a phantom under open sky at full health: cover");
		assertTrue(Reflexes.phantomCoverDue(true, false, 12f, true), "hurt with a phantom near: cover");
		assertFalse(Reflexes.phantomCoverDue(true, false, 18f, true), "near but no hit, healthy: no");
		assertFalse(Reflexes.phantomCoverDue(false, true, 8f, true), "already under a roof: no");
	}

	@Test
	void aHurtMealIsNotBrokenOffForAnAutoFight() { // S105 arbiter fix, 45L 17:12:26
		assertFalse(Reflexes.autoFightMayInterruptMeal(12f));
		assertTrue(Reflexes.autoFightMayInterruptMeal(18f));
	}

	@Test
	void theMostEnclosedCoverWins() { // kickoff (a): a163, a 3x3 roof 3 up let side dives in (0, 1 and 5 hits)
		assertTrue(Reflexes.coverScore(6, 2) > Reflexes.coverScore(0, 3), "walled with a ceiling at head+1 beats an open roof 3 up");
		assertTrue(Reflexes.coverScore(0, 2) > Reflexes.coverScore(0, 3), "a lower ceiling is better");
		assertTrue(Reflexes.coverScore(2, 3) > Reflexes.coverScore(0, 3), "each wall counts");
	}

	@Test
	void aPhantomIsNeverAFloorShieldFlyer() { // watch R1 on fb07484c: the floor shield still rose on phantoms, no retreat
		assertFalse(Reflexes.floorShield(5f, false, 3.0, true, true), "a phantom is not a shooter or a floor-shield flyer: no floor shield");
		assertTrue(Reflexes.floorShield(5f, true, 3.0, true, true), "a shooter or another flyer (blaze, ghast, vex, breeze): the floor shield");
	}
}
