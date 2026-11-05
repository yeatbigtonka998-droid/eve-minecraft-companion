package eve.combat;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import eve.body.api.BodyServices;
import eve.body.api.Skill;
import eve.body.api.Step;
import eve.body.api.SubAction;
import net.minecraft.core.BlockPos;
import net.minecraft.util.Mth;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.monster.Enderman;
import net.minecraft.world.entity.NeutralMob;
import net.minecraft.world.entity.Mob;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.monster.Creeper;
import net.minecraft.world.entity.monster.Enemy;
import net.minecraft.world.entity.player.Input;
import net.minecraft.world.level.Level;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.Vec3;

import java.util.ArrayList;
import java.util.List;

/**
 * fight_melee (CP-26, CP-28): fight one entity, or (no entity_id) the hostiles within radius one at a time, chosen by
 * {@link Tactics#pick}. Swings only through the body's attack_entity (reach, crosshair, full cooldown, protected target and sweep
 * area checked there every swing); between swings it holds the spacing band and strafes. Far away or out of sight it walks
 * with the body's walk_to (Baritone). Below health_floor it disengages (runs) and ends failed health_floor. Server truth is the
 * harness's job; the evidence here is what the client saw, swing by swing.
 */
final class FightMelee implements Skill {
	static final int LOG_MAX = 64;
	/**
	 * Start a swing only within this eye-to-hitbox distance. Vanilla's crosshair ray to the centre is longer than the closest-point
	 * distance, so at 2.9-3.0 attack_entity reported obstructed/out_of_range 21 times in CP-26 attempt 1.
	 */
	static final double SWING_REACH = 2.6;

	final Integer entityId;
	final boolean ownerTarget; // O136: the owner named this mob; the protected-type rule is lifted for it alone (never a player)
	final double radius;
	final float floor;
	final int maxTargets;

	Entity target;
	SubAction swing, walk, equip; // equip: O126 equip_best {weapon} once at the start when no weapon is in the hotbar
	boolean equipTried;
	final Tactics.Reach reachTrack = new Tactics.Reach(); // O126: progress toward the current target
	final java.util.Set<Integer> unreachable = new java.util.HashSet<>(); // O126: skipped in group mode
	static final String UNREACHABLE_SAY = "it's behind a wall; open the door or I'll dig?";
	int walkTicks, holdTicks, fleeTicks = -1;
	int kills, ended, swings, hits, protectedHolds, targets, retargets, doorHoldTicks; // O280: kills = died after her hit; ended = any target that died
	final java.util.Set<Integer> struck = new java.util.HashSet<>(), creeperTried = new java.util.HashSet<>();
	FightRanged creeperBow; // O284: the bow, in-process, for a creeper in range; the sword fight waits
	Doorway door;
	private SubAction doorWalk;
	float healthStart = -1, healthMin = Float.MAX_VALUE, lastHealth = -1, dealt, taken, targetHpBefore;
	double maxReach;
	final JsonArray log = new JsonArray();
	JsonObject pending; // the swing being confirmed
	final int[] phase = new int[5]; // ticks: walk, band (recharging), approach (ready, not in reach), swing, flee
	int guardTicks, guardRefused, archerHoldTicks, archerRushTicks, drawGuardTicks, floorHoldTicks;
	boolean hadShield, guarded, guardCalled, wantGuard;
	int lastDrawSeen = -1000; // O174: the tick an archer was last seen drawing at her (the guard holds through the arrow's flight)

	FightMelee(JsonObject params) {
		entityId = params.has("entity_id") ? params.get("entity_id").getAsInt() : null;
		ownerTarget = params.has("owner_target") && params.get("owner_target").getAsBoolean();
		radius = params.has("radius") ? params.get("radius").getAsDouble() : 16;
		floor = params.has("health_floor") ? params.get("health_floor").getAsFloat() : 8f;
		maxTargets = entityId != null ? 1 : params.has("max_targets") ? params.get("max_targets").getAsInt() : 8;
		if (params.has("doorway")) { // O98: the shelter door, passed by the mind at night
			JsonObject d = params.getAsJsonObject("doorway"), pos = d.getAsJsonObject("pos"); // {pos: block_pos, out}
			door = Doorway.of(pos.get("x").getAsInt(), pos.get("y").getAsInt(), pos.get("z").getAsInt(), d.get("out").getAsString());
		}
	}

	@Override
	public boolean resumable() {
		return true; // after a shield/eat/retreat reflex the fight goes on (preconditions re-checked by the body)
	}

	@Override
	public void resume(BodyServices b) {
		swing = null; // the body cancelled the children when the reflex took over
		reachTrack.reset(); // O126 fix (F x_creeper 16:49): a reflex (creeper flee) moved her; progress restarts from here, not from before
		walk = null;
		doorWalk = null;
		pending = null;
		if (creeperBow != null) creeperBow.resume(b);
	}

	/** O325: a creeper in sight within 10 and no bow she can use. */
	private boolean creeperNoBow(BodyServices b) {
		var p = b.player();
		boolean bow = b.inventoryCounts().getOrDefault("minecraft:bow", 0) > 0; // 547236a4 lane C: her live bow sits in slot 13; fight_ranged swaps it in
		int arrows = b.inventoryCounts().getOrDefault("minecraft:arrow", 0);
		for (Entity e : p.level().getEntities(p, p.getBoundingBox().inflate(AutoEngage.BREAK_OFF))) {
			if (e instanceof Creeper && e.isAlive() && !b.isProtected(e) && p.hasLineOfSight(e)
					&& AutoEngage.breakOffForCreeper(e.distanceTo(p), bow, arrows)) return true;
		}
		return false;
	}

	/** O284: a creeper in sight she can shoot, not tried yet in this fight. Null when the fight names its target or body has no claim. */
	private Entity creeperToShoot(BodyServices b) {
		if (EveCombat.api < 5 || entityId != null) return null;
		var p = b.player();
		int arrows = b.inventoryCounts().getOrDefault("minecraft:arrow", 0);
		boolean bow = b.inventoryCounts().getOrDefault("minecraft:bow", 0) > 0; // 547236a4 lane C: her live bow sits in slot 13; fight_ranged swaps it in
		for (Entity e : p.level().getEntities(p, p.getBoundingBox().inflate(AutoEngage.CREEPER_BOW_MAX))) {
			if (e instanceof Creeper && e.isAlive() && !b.isProtected(e) && !creeperTried.contains(e.getId())
					&& AutoEngage.creeperFirst(e.distanceTo(p), bow, arrows) && p.hasLineOfSight(e)) return e;
		}
		return null;
	}

	/** Best first. ponytail: a fixed order instead of reading attack-damage attributes; enough for vanilla tiers. */
	static final String[] WEAPONS = {"netherite_sword", "diamond_sword", "iron_sword", "stone_sword", "golden_sword", "wooden_sword",
			"netherite_axe", "diamond_axe", "iron_axe", "stone_axe", "golden_axe", "wooden_axe"};
	String weapon;

	/**
	 * Hold the best hotbar weapon (selected every tick; it is sent with the tick). CP-28 attempt 4: the selected slot was the
	 * bread, so she hit with bread and the body refused the shield (food in the main hand), 0 kills.
	 */
	/** The best hotbar weapon id, or null. */
	/** O126: any melee weapon anywhere in her inventory (equip_best moves the best one to her hand). */
	static boolean hasWeapon(BodyServices b) {
		var c = b.inventoryCounts();
		for (String w : WEAPONS) if (c.getOrDefault("minecraft:" + w, 0) > 0) return true;
		return false;
	}

	static String bestWeapon(BodyServices b) {
		for (String w : WEAPONS) if (b.hotbarSlot("minecraft:" + w) >= 0) return "minecraft:" + w;
		return null;
	}

	private void arm(BodyServices b) {
		for (String w : WEAPONS) {
			int s = b.hotbarSlot("minecraft:" + w);
			if (s >= 0) {
				if (b.player().getInventory().getSelectedSlot() != s) b.selectHotbar(s);
				weapon = "minecraft:" + w;
				return;
			}
		}
	}

	@Override
	public Step tick(BodyServices b) {
		var p = b.player();
		if (ownerTarget && entityId == null) return Step.failed("bad_params", "owner_target needs entity_id: the owner names one mob"); // O136
		if (creeperBow == null) {
			Entity c = creeperToShoot(b);
			if (c != null) {
				creeperTried.add(c.getId()); // one go per creeper: a miss-out falls back to the sword fight and body's flee
				cancelChildren();
				JsonObject cp = new JsonObject();
				cp.addProperty("entity_id", c.getId());
				cp.addProperty("radius", 16);
				cp.addProperty("max_arrows", 16);
				creeperBow = new FightRanged(cp);
			}
		}
		if (creeperBow != null) {
			Step cs = creeperBow.tick(b);
			if (!cs.done()) return cs;
			creeperBow = null;
		}
		if (creeperNoBow(b)) return Step.failed("interrupted", AutoEngage.BREAK_OFF_SAY); // O325: the mind does not re-send it
		// O126 (Hunter live): the sword first. A weapon only in the main inventory never reached her hand (arm() reads the hotbar)
		if (!equipTried && bestWeapon(b) == null && hasWeapon(b)) {
			JsonObject ep = new JsonObject();
			ep.add("slots", new JsonArray());
			ep.addProperty("weapon", true);
			equip = b.run("equip_best", ep);
			equipTried = true;
		}
		if (equip != null) {
			if (!equip.tick().done()) return Step.running();
			equip = null;
		}
		equipTried = true;
		arm(b);
		float hp = p.getHealth();
		if (healthStart < 0) healthStart = hp;
		if (lastHealth >= 0 && hp < lastHealth) taken += lastHealth - hp;
		lastHealth = hp;
		healthMin = Math.min(healthMin, hp);
		guardCalled = false;
		Step s = decide(b);
		// S28(a): an archer drawing at her raises the fight's own guard now, on the draw (a shield blocks from its 5th tick: raised
		// for the arrow at impact_ticks 2 it was too late, three of four fatal hits); held while it draws, any phase of the fight
		// O174: and it stays up through the arrow's flight: POST_RELEASE_HOLD ticks after the draw ends, or while an arrow flies at her
		if (!s.done() && !(guardCalled && wantGuard) && Tactics.guardOnDraw(rangedThreat(b), hp, swing != null && !clicked(b))) {
			shield(b, true);
			drawGuardTicks++;
		}
		// claim the shield on EVERY tick of the fight (down unless decide raised it): one lapsed tick handed the shield to the
		// body's melee reflex, which then held it 417 ticks while she was flanked and killed (CP-28 attempt 6)
		if (!guardCalled && !s.done()) shield(b, false);
		if (!s.done()) applyGuard(b);
		fill(b);
		return s;
	}

	private Step decide(BodyServices b) {
		var p = b.player();
		// O102d (3): the body handed the fight back at the floor (cornered by an archer; reflexes stand back 8 s): fight, never run
		if (b.handedBack()) fleeTicks = -1;
		else if (fleeTicks >= 0) {
			phase[4]++;
			return flee(b);
		}
		if (disengage(p.getHealth(), floor, b.handedBack())) {
			fleeTicks = 0;
			cancelChildren();
			return flee(b);
		}

		// the current target: dead -> count it, then the next one (group mode) or done
		if (target != null && dead(target)) {
			ended++;
			if (swing != null && pending != null) { // the killing swing: attack_entity had not reported yet
				hits++;
				struck.add(target.getId());
				dealt += targetHpBefore;
				pending.addProperty("status", "kill");
				pending.addProperty("hp_before", targetHpBefore);
				pending.addProperty("hp_after", 0);
				if (log.size() < LOG_MAX) log.add(pending);
				pending = null;
			}
			if (struck.contains(target.getId())) kills++;
			target = null;
			cancelChildren();
		}
		if (target != null && (target.isRemoved() || target.distanceTo(p) > radius + 8)) {
			if (entityId != null) return Step.failed("lost_target", "target left the client's view without dying");
			target = null;
			cancelChildren();
		}
		if (target == null) {
			if (ended >= maxTargets) return Step.ok();
			target = entityId != null ? named(b) : choose(b);
			if (target == null) {
				if (entityId != null && ended == 0) return Step.failed("no_target", "entity " + entityId + " not in the client world");
				if (ended > 0) return Step.ok();
				return unreachable.isEmpty() ? Step.failed("no_target", "no hostile within " + radius) : Step.failed("unreachable", UNREACHABLE_SAY);
			}
			targets++;
			String[] no = Tactics.ownerTarget(ownerTarget, entityId, target.getId(), target instanceof net.minecraft.world.entity.player.Player, b.isProtected(target));
			if (no != null) return Step.failed(no[0], no[1]);
			reachTrack.best = Double.MAX_VALUE;
			reachTrack.stalled = 0;
		}

		// O126: never a silent freeze. Out of reach for 5 s without getting a block closer (walk_to found no way: a wall, a shut
		// door, a gap) = unreachable: one named target ends the fight with a line the mind relays; in group mode it is skipped.
		// Not while holding a doorway (holding is the point there).
		double td = target.distanceTo(p);
		boolean engaged = td <= 4 || swing != null // in the fight, or an archer in sight that option A is closing on (it kites)
				|| (ranged(target) && p.hasLineOfSight(target) && td <= Tactics.RUSH_RANGE);
		if (door == null && reachTrack.update(td, engaged)) {
			cancelChildren();
			walk = null;
			if (entityId != null) return Step.failed("unreachable", UNREACHABLE_SAY);
			unreachable.add(target.getId());
			target = null;
			return Step.running();
		}

		// a swing in flight. Until the click, stand still: attack_entity clicks only once its aim is within 0.5 deg, which strafing
		// never lets it reach (CP-26 attempt 2: 468 ticks in one swing). After the click (attack strength resets) keep moving while
		// it confirms the server's damage.
		if (swing != null) {
			Step st = swing.tick();
			phase[3]++;
			if (st.done()) {
				endSwing(st);
			} else if (clicked(b)) {
				shield(b, true); // the click is out: guard again while attack_entity confirms
				return Step.running(move(b, false));
			} else {
				shield(b, false);
				// keep the aim snapped on the hitbox centre until the click: jostling breaks attack_entity's 0.5-deg check in
				// a tick (CP-28 attempt 8: the two floor disengages spent 12-20 ticks per swing here with the shield down)
				if (EveCombat.api >= 3 && target != null && !dead(target)) b.aim(target.getBoundingBox().getCenter(), 180f);
				return st;
			}
		}

		// O98: a target outside the door is never followed out. Hold beside the door wall, on the target's side, out of the line
		// through the opening, shield up and facing the door, until it steps into the doorway cell (CP-31 a16 night 1: a
		// skeleton 1.3 outside shot her through the doorway for ~45 s while she stood straight back from it).
		if (door != null) {
			Vec3 t = target.position(), e = p.position();
			if (!door.engageable(t.x, t.z) || door.eveTooFarOut(e.x, e.z)) {
				if (walk != null) {
					walk.cancel();
					walk = null;
				}
				return holdAtDoor(b, t);
			}
			if (doorWalk != null) {
				doorWalk.cancel();
				doorWalk = null;
			}
		}

		// O102v A: an archer in sight is closed on in rushes between its shots, not walked or crawled at behind the shield
		Tactics.ArcherMove am = Tactics.archer(ranged(target), p.hasLineOfSight(target), target.distanceTo(p), reach(p, target), drawing(target) || rangedThreat(b));
		if (am != Tactics.ArcherMove.NONE) {
			if (walk != null) {
				walk.cancel();
				walk = null;
			}
			if (EveCombat.api >= 3) b.aim(target.getBoundingBox().getCenter(), 40f);
			else b.steerTo(target.position());
			if (am == Tactics.ArcherMove.HOLD) {
				shield(b, true);
				archerHoldTicks++;
				return Step.running();
			}
			float yaw = p.getYRot() * Mth.DEG_TO_RAD;
			if (footing(p.level(), p.position().add(new Vec3(-Mth.sin(yaw), 0, Mth.cos(yaw))))) {
				shield(b, false);
				archerRushTicks++;
				return Step.running(new Input(true, false, false, false, false, false, true));
			}
			// no footing ahead (a gap or a wall): the usual walk_to finds the way
		}

		if (walk != null) {
			Step st = walk.tick();
			boolean close = target.distanceTo(p) < 5 && p.hasLineOfSight(target);
			if (st.done() || close || ++walkTicks > 60) {
				walk.cancel();
				walk = null;
			} else {
				phase[0]++;
				return st;
			}
		}
		if (target.distanceTo(p) > 8 || !p.hasLineOfSight(target)) {
			walk = b.run("walk_to", posParams(target.position()));
			walkTicks = 0;
			return Step.running();
		}

		// face it: a shield blocks from the front only (body API v3 aim turns yaw and pitch); v2 bodies: steerTo's yaw turn
		if (EveCombat.api >= 3) b.aim(target.getBoundingBox().getCenter(), 40f);
		else b.steerTo(target.position());
		boolean ready = p.getAttackStrengthScale(0.5f) >= Tactics.READY;
		boolean inReach = reach(p, target) <= SWING_REACH;
		if (holdTicks > 0) holdTicks--;
		// in a group the crosshair ray often meets another hostile in front of the target (CP-28 attempt 2: 36 of 45 swings
		// obstructed): that one is the nearer threat anyway, so it becomes the target
		if (b.minecraft().hitResult instanceof net.minecraft.world.phys.EntityHitResult eh && eh.getEntity() != target
				&& hostile(eh.getEntity()) && eh.getEntity() instanceof LivingEntity && !dead(eh.getEntity()) && !b.isProtected(eh.getEntity())
				&& !(eh.getEntity() instanceof Creeper) && entityId == null && reach(p, eh.getEntity()) <= SWING_REACH) {
			target = eh.getEntity();
			retargets++;
			inReach = true;
		}
		// Swing when the crosshair is already on the target. That tick: shield down, snap the aim onto the hitbox centre (the
		// point attack_entity aims at, so its 0.5-deg check passes at once) and start attack_entity, which clicks in the same tick.
		// The shield is down ~1 tick per swing. CP-28 attempts 3 and 5: waiting for a 0.5-deg aim while zombies jostle her either
		// kept the shield down ~23 ticks per swing (death) or never swung at all (1 swing in 3 trials).
		boolean onTarget = b.minecraft().hitResult instanceof net.minecraft.world.phys.EntityHitResult oh && oh.getEntity() == target;
		boolean swingNow = ready && inReach && holdTicks == 0 && (EveCombat.api >= 3 ? onTarget : yawError(p, target) < 30);
		if (!swingNow) shield(b, true);
		if (swingNow) {
			shield(b, false);
			if (EveCombat.api >= 3) b.aim(target.getBoundingBox().getCenter(), 180f);
			pending = new JsonObject();
			pending.addProperty("tick", b.ticks());
			pending.addProperty("reach", r2(reach(p, target)));
			pending.addProperty("strength", r2(p.getAttackStrengthScale(0.5f)));
			pending.addProperty("yaw_error", r2(yawError(p, target)));
			maxReach = Math.max(maxReach, pending.get("reach").getAsDouble());
			targetHpBefore = target instanceof LivingEntity le ? le.getHealth() : 0;
			JsonObject a = new JsonObject();
			a.addProperty("entity_id", target.getId());
			// O136: body's attack_entity re-checks protection every tick; pass the owner's lift for this one named mob only
			if (ownerTarget && entityId != null && entityId == target.getId() && !(target instanceof net.minecraft.world.entity.player.Player)) a.addProperty("owner_target", true);
			swing = b.run("attack_entity", a);
			swings++;
			Step st = swing.tick();
			if (st.done()) endSwing(st);
			return st.done() ? Step.running(move(b, false)) : st;
		}
		phase[ready ? 2 : 1]++;
		return Step.running(move(b, true));
	}

	/** O98 hold: walk (inside) to the hold spot on the target's side, then stand facing the door with the shield up. */
	private Step holdAtDoor(BodyServices b, Vec3 t) {
		var p = b.player();
		doorHoldTicks++;
		int side = door.sideFor(t.x, t.z, p.getX(), p.getZ());
		double[] h = door.holdSpot(side);
		Vec3 spot = new Vec3(h[0], door.y(), h[1]);
		if (!footing(p.level(), spot)) { // no floor or a wall there: the other side
			h = door.holdSpot(-side);
			spot = new Vec3(h[0], door.y(), h[1]);
		}
		if (horiz(p.position(), spot) > 0.6) {
			if (doorWalk != null) {
				Step st = doorWalk.tick();
				if (!st.done()) {
					shield(b, true);
					return st;
				}
				doorWalk = null;
			}
			doorWalk = b.run("walk_to", posParams(spot));
			shield(b, true);
			return Step.running();
		}
		if (doorWalk != null) {
			doorWalk.cancel();
			doorWalk = null;
		}
		if (EveCombat.api >= 3) b.aim(new Vec3(door.x() + 0.5, door.y() + 1.2, door.z() + 0.5), 40f);
		shield(b, true);
		return Step.running();
	}

	/**
	 * Own the off-hand shield this tick (body API v3 guard; the melee shield reflex yields while we claim it). Without a usable
	 * shield (none, or it broke mid-fight: 45L shield_melee 14/20) guard returns false and the fight goes on unguarded.
	 */
	private void shield(BodyServices b, boolean up) {
		if (EveCombat.api < 3) return;
		guardCalled = true;
		wantGuard = up; // O174: only recorded here; applyGuard sends ONE guard call per tick (last wish wins)
	}

	/**
	 * O174 (sense's hit logger, x_archer on F: 53 of 62 arrows hit 0-4 ticks after the shield use started, vanilla blocks from the
	 * 5th; 69/186 raises re-pressed in the same tick): a guard(false) then guard(true) in one tick re-presses the use and restarts
	 * that delay. The fight's wishes this tick collapse into one call.
	 */
	private void applyGuard(BodyServices b) {
		if (!guardCalled || EveCombat.api < 3) return;
		boolean ok = b.guard(wantGuard);
		guarded = ok && wantGuard;
		if (guarded) guardTicks++;
		if (wantGuard && !ok) guardRefused++;
		if (guarded) hadShield = true;
	}

	/** The swing's click went out: vanilla resets the attack strength to ~0 on every attack. */
	private static boolean clicked(BodyServices b) {
		return b.player().getAttackStrengthScale(0.5f) < 0.5f;
	}

	static double reach(net.minecraft.world.entity.player.Player p, Entity t) {
		return Math.sqrt(t.getBoundingBox().distanceToSqr(p.getEyePosition()));
	}

	private void endSwing(Step st) {
		JsonObject r = swing.result();
		swing = null;
		JsonObject ev = r.has("evidence") ? r.getAsJsonObject("evidence") : new JsonObject();
		boolean hit = ev.has("hit_confirmed") && !ev.get("hit_confirmed").isJsonNull() && ev.get("hit_confirmed").getAsBoolean();
		float before = num(ev, "target_health_before", targetHpBefore), after = num(ev, "target_health_after", before);
		if (hit) {
			hits++;
			if (target != null) struck.add(target.getId());
			dealt += Math.max(0, before - after);
		}
		if ("protected_entity".equals(st.code())) {
			protectedHolds++;
			holdTicks = 10; // a protected entity inside the sweep area: wait for it to move, then try again
		}
		if (pending != null && log.size() < LOG_MAX) {
			pending.addProperty("status", st.status());
			if (st.code() != null) pending.addProperty("code", st.code());
			pending.addProperty("hp_before", before);
			pending.addProperty("hp_after", after);
			log.add(pending);
		}
		pending = null;
	}

	/** Spacing band and strafe toward/around the target, never onto a drop; jump a 1-block step when walking into it. */
	private Input move(BodyServices b, boolean turned) {
		var p = b.player();
		double d = horiz(p.position(), target.position());
		boolean ready = p.getAttackStrengthScale(0.5f) >= Tactics.READY;
		Tactics.Keys k = Tactics.spacing(d, ready, reach(p, target) <= SWING_REACH, b.ticks());
		float yaw = p.getYRot() * Mth.DEG_TO_RAD;
		Vec3 fwd = new Vec3(-Mth.sin(yaw), 0, Mth.cos(yaw)), left = new Vec3(Mth.cos(yaw), 0, Mth.sin(yaw));
		boolean f = k.forward() && footing(p.level(), p.position().add(fwd)), bk = k.backward() && footing(p.level(), p.position().subtract(fwd));
		boolean l = k.left() && footing(p.level(), p.position().add(left)), r = k.right() && footing(p.level(), p.position().subtract(left));
		// CP-28: a hostile at her side (>75 deg off her facing, within 3.5) gets past the shield and the sword: back off so the
		// group stays in front. ponytail: per-hostile bearing only; add wall-aware circling if the arena corner traps her.
		for (Entity h : hostiles(b, 3.5, false)) {
			if (h == target) continue;
			Vec3 to = h.position().subtract(p.position()).multiply(1, 0, 1);
			if (to.lengthSqr() > 1e-4 && to.normalize().dot(fwd) < Math.cos(Math.toRadians(75))) {
				bk = footing(p.level(), p.position().subtract(fwd));
				f = false;
				if (!bk) { // wall behind: slide sideways away from the flanker instead
					boolean fromLeft = to.dot(left) > 0;
					r = fromLeft && footing(p.level(), p.position().subtract(left));
					l = !fromLeft && footing(p.level(), p.position().add(left));
				}
				break;
			}
		}
		boolean jump = f && p.horizontalCollision && p.onGround();
		return new Input(f, bk, l, r, jump, false, false);
	}

	/** Run from the nearest hostile until none is within 10 blocks (or 5 s), then end failed health_floor. */
	private Step flee(BodyServices b) {
		var p = b.player();
		Entity h = nearestHostile(b, 10, false);
		if (h == null || ++fleeTicks > 100) return Step.failed("health_floor", "health " + p.getHealth() + " below floor " + floor + "; disengaged");
		Vec3 away = p.position().subtract(h.position()).multiply(1, 0, 1);
		if (away.lengthSqr() < 1e-4) away = new Vec3(1, 0, 0);
		// S28-DEATH-2 (live death 15:14:33, a phantom): never run from a flyer (it is faster) or into a wall; hold the guard facing
		// it instead (a sprint has the shield down, and a back turned is a free hit)
		if (Tactics.floorHold(flyer(h), footing(p.level(), p.position().add(away.normalize())))) {
			if (EveCombat.api >= 3) b.aim(h.getBoundingBox().getCenter(), 40f);
			shield(b, true);
			floorHoldTicks++;
			return Step.running();
		}
		Input in = b.steerTo(p.position().add(away.normalize().scale(8)));
		return Step.running(new Input(in.forward(), false, false, false, in.jump(), false, in.forward()));
	}

	private Entity named(BodyServices b) {
		if (ended > 0) return null;
		Entity e = b.minecraft().level.getEntity(entityId);
		return e != null && e.isAlive() ? e : null;
	}

	private Entity choose(BodyServices b) {
		List<Entity> es = hostiles(b, radius, true);
		List<Tactics.Cand> cs = new ArrayList<>();
		var p = b.player();
		es.removeIf(e -> unreachable.contains(e.getId()) || flyer(e)); // live 17:16:03: a group fight never melee-chases a phantom
		for (Entity e : es)
			cs.add(new Tactics.Cand(e.distanceTo(p), e instanceof LivingEntity le ? le.getHealth() : 20, sweepBlocked(b, e), p.hasLineOfSight(e)));
		int i = Tactics.pick(cs);
		return i < 0 ? null : es.get(i);
	}

	private Entity nearestHostile(BodyServices b, double within, boolean melee) {
		Entity best = null;
		for (Entity e : hostiles(b, within, melee)) if (best == null || e.distanceTo(b.player()) < best.distanceTo(b.player())) best = e;
		return best;
	}

	/** An Enemy, unless it is a calm neutral mob (body 863271e's Telemetry.isHostile rule): an enderman only while it has a target
	 * (isCreepy) or attacks, a zombified piglin only while aggressive. CP-31 a9 attempt 11: hitting a calm enderman provoked it and
	 * it killed her. */
	/** An archer: its main hand shoots (bow, crossbow, trident), the same test as body's Reflexes.ranged. */
	static boolean ranged(Entity e) {
		if (!(e instanceof LivingEntity le)) return false;
		var a = le.getMainHandItem().getUseAnimation();
		return a == net.minecraft.world.item.ItemUseAnimation.BOW || a == net.minecraft.world.item.ItemUseAnimation.CROSSBOW
				|| a == net.minecraft.world.item.ItemUseAnimation.TRIDENT;
	}

	/** Its shot is coming: a bow or trident being drawn (the synced use flag) or a charged crossbow, as body's drawn_bow reads it. */
	static boolean drawing(Entity e) {
		return e instanceof LivingEntity le && (le.isUsingItem() || net.minecraft.world.item.CrossbowItem.isCharged(le.getMainHandItem()));
	}

	/** S28-DEATH-2: a flying hostile, faster than she runs (phantom, ghast, vex, blaze, breeze as body counts them, bee; 26.3 has no FlyingMob). */
	static boolean flyer(Entity e) {
		return e instanceof net.minecraft.world.entity.monster.Phantom || e instanceof net.minecraft.world.entity.monster.Ghast
				|| e instanceof net.minecraft.world.entity.monster.Vex || e instanceof net.minecraft.world.entity.monster.Blaze
				|| e instanceof net.minecraft.world.entity.animal.bee.Bee || e instanceof net.minecraft.world.entity.monster.breeze.Breeze;
	}

	/**
	 * O174: a ranged threat to hold the guard for. An archer drawing at her now, one that drew within POST_RELEASE_HOLD ticks (its
	 * arrow is in the air: 0-4 tick raises after the release were what failed), or an arrow flying at her.
	 */
	boolean rangedThreat(BodyServices b) {
		if (drawnAt(b) != null) lastDrawSeen = b.ticks();
		return Tactics.holdForArrow(b.ticks() - lastDrawSeen, arrowIncoming(b));
	}

	/** O174: a flying arrow within ARCHER_RADIUS whose path points at her (a 25-degree cone, as body's drawn_bow test). */
	static boolean arrowIncoming(BodyServices b) {
		var p = b.player();
		Vec3 eye = p.getEyePosition();
		for (Entity e : p.level().getEntities(p, p.getBoundingBox().inflate(Tactics.ARCHER_RADIUS))) {
			if (!(e instanceof net.minecraft.world.entity.projectile.arrow.AbstractArrow a) || a.onGround() || a.getOwner() == p) continue;
			Vec3 v = a.getDeltaMovement(), to = eye.subtract(a.position());
			if (v.lengthSqr() > 0.04 && to.lengthSqr() > 1e-4 && v.normalize().dot(to.normalize()) >= Tactics.AIM_COS) return true;
		}
		return false;
	}

	/** S28(a): an archer within ARCHER_RADIUS drawing with its head turned to her (body's drawn_bow test: the 25-degree cone). */
	static Entity drawnAt(BodyServices b) {
		var p = b.player();
		for (Entity e : p.level().getEntities(p, p.getBoundingBox().inflate(Tactics.ARCHER_RADIUS))) if (drawingAt(e, p)) return e;
		return null;
	}

	/** S28(a) / H-autofight: this archer, within ARCHER_RADIUS, is drawing with its head turned to her (body's 25-degree cone). */
	static boolean drawingAt(Entity e, net.minecraft.world.entity.player.Player p) {
		if (!(e instanceof LivingEntity le) || !le.isAlive() || !ranged(e) || !drawing(e)) return false;
		Vec3 to = p.getEyePosition().subtract(le.getEyePosition()), v = le.getViewVector(1f);
		return to.length() <= Tactics.ARCHER_RADIUS && to.lengthSqr() > 1e-6 && v.normalize().dot(to.normalize()) >= Tactics.AIM_COS;
	}

	static boolean hostile(Entity e) {
		if (!(e instanceof Enemy)) return false;
		if (!(e instanceof NeutralMob)) return true;
		return e instanceof Mob m && m.isAggressive() || e instanceof Enderman em && em.isCreepy();
	}

	/** Living hostiles (vanilla Enemy) within range, never protected ones; creepers are not melee targets (CP-29: run, not fight). */
	private List<Entity> hostiles(BodyServices b, double within, boolean melee) {
		List<Entity> out = new ArrayList<>();
		var p = b.player();
		for (Entity e : b.minecraft().level.entitiesForRendering()) {
			if (e == p || !hostile(e) || !(e instanceof LivingEntity) || dead(e) || b.isProtected(e)) continue;
			if (melee && e instanceof Creeper) continue;
			if (e.distanceTo(p) <= within) out.add(e);
		}
		return out;
	}

	/** The body's attack_entity refuses a swing with a protected entity inside the sword's sweep area; same box here. */
	private boolean sweepBlocked(BodyServices b, Entity target) {
		AABB sweep = target.getBoundingBox().inflate(1.0, 0.25, 1.0);
		for (Entity o : b.minecraft().level.entitiesForRendering())
			if (o != b.player() && o != target && b.isProtected(o) && o.getBoundingBox().intersects(sweep)) return true;
		return false;
	}

	/** Below the floor she disengages, except while the body has handed the fight back (O102d: running is what got her cornered). */
	static boolean disengage(float health, float floor, boolean handedBack) {
		return health < floor && !handedBack;
	}

	private void cancelChildren() {
		if (swing != null) swing.cancel();
		if (walk != null) walk.cancel();
		if (doorWalk != null) doorWalk.cancel();
		if (equip != null) equip.cancel();
		swing = walk = doorWalk = equip = null;
		pending = null;
	}

	@Override
	public void cancel(BodyServices b) {
		cancelChildren();
		fill(b);
	}

	private void fill(BodyServices b) {
		JsonObject ev = b.evidence();
		ev.addProperty("targets", targets);
		ev.addProperty("kills", kills);
		ev.addProperty("swings", swings);
		ev.addProperty("hits", hits);
		ev.addProperty("damage_dealt", r2(dealt));
		ev.addProperty("damage_taken", r2(taken));
		ev.addProperty("health_start", healthStart);
		ev.addProperty("health_min", healthMin == Float.MAX_VALUE ? null : healthMin);
		ev.addProperty("health_end", b.player().getHealth());
		ev.addProperty("max_swing_reach", r2(maxReach));
		ev.addProperty("protected_holds", protectedHolds);
		ev.addProperty("door_hold_ticks", doorHoldTicks); // extra key (O98)
		ev.addProperty("disengaged", fleeTicks >= 0);
		ev.add("swing_log", log);
		JsonObject ph = new JsonObject(); // extra key (not in the registry list): where the ticks went
		String[] names = {"walk", "band", "approach", "swing", "flee"};
		for (int i = 0; i < names.length; i++) ph.addProperty(names[i], phase[i]);
		ev.add("phase_ticks", ph);
		ev.addProperty("archer_hold_ticks", archerHoldTicks); // O102v A: guard up while its bow was drawn
		ev.addProperty("archer_rush_ticks", archerRushTicks); // O102v A: sprinting in between its shots
		ev.addProperty("floor_hold_ticks", floorHoldTicks); // S28-DEATH-2: below the floor, guard held instead of a flee
		ev.addProperty("draw_guard_ticks", drawGuardTicks); // S28(a): guard raised for an archer drawing at her
		ev.addProperty("guard_ticks", guardTicks); // extra keys: shield use (body API v3)
		ev.addProperty("guard_refused", guardRefused);
		ev.addProperty("weapon", weapon);
		ev.addProperty("body_api", EveCombat.api);
		ev.addProperty("retargets", retargets);
		ev.addProperty("unreachable_skipped", unreachable.size()); // O126: group-mode targets dropped as unreachable
		ev.addProperty("shield_lost", hadShield && !net.minecraft.world.item.ItemStack.isSameItem(b.player().getOffhandItem(),
				new net.minecraft.world.item.ItemStack(net.minecraft.world.item.Items.SHIELD)));
	}

	static boolean dead(Entity e) {
		return e instanceof LivingEntity le ? le.isDeadOrDying() : !e.isAlive();
	}

	/** Ground to stand on at {@code at}: solid within 2 below the feet, no lava, head room. Strafing never steps off an edge. */
	static boolean footing(Level level, Vec3 at) {
		BlockPos feet = BlockPos.containing(at.x, at.y + 0.01, at.z);
		if (solid(level, feet.above())) return false;
		for (int dy = 0; dy >= -2; dy--) {
			if (level.getFluidState(feet.above(dy)).is(net.minecraft.tags.FluidTags.LAVA)) return false;
			if (solid(level, feet.above(dy - 1)) || solid(level, feet)) return true;
		}
		return false;
	}

	private static boolean solid(Level level, BlockPos b) {
		return !level.getBlockState(b).getCollisionShape(level, b).isEmpty();
	}

	static double yawError(net.minecraft.world.entity.player.Player p, Entity t) {
		Vec3 d = t.position().subtract(p.position());
		float want = (float) (Mth.atan2(d.z, d.x) * Mth.RAD_TO_DEG) - 90f;
		return Math.abs(Mth.wrapDegrees(want - p.getYRot()));
	}

	static double horiz(Vec3 a, Vec3 b) {
		return Math.sqrt((a.x - b.x) * (a.x - b.x) + (a.z - b.z) * (a.z - b.z));
	}

	static JsonObject posParams(Vec3 v) {
		JsonObject pos = new JsonObject(), o = new JsonObject();
		pos.addProperty("x", Math.floor(v.x) + 0.5);
		pos.addProperty("y", Math.floor(v.y));
		pos.addProperty("z", Math.floor(v.z) + 0.5);
		o.add("pos", pos);
		o.addProperty("tolerance", 3.0);
		return o;
	}

	static float num(JsonObject o, String k, float dflt) {
		return o.has(k) && !o.get(k).isJsonNull() ? o.get(k).getAsFloat() : dflt;
	}

	static double r2(double v) {
		return Math.round(v * 100) / 100.0;
	}
}
