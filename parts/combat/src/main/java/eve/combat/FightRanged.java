package eve.combat;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import eve.body.api.BodyServices;
import eve.body.api.Skill;
import eve.body.api.Step;
import eve.body.api.SubAction;
import net.minecraft.util.Mth;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.monster.Enemy;
import net.minecraft.world.entity.player.Input;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.Vec3;

/**
 * fight_ranged (CP-27, bow half): shoot one target with Eve's own bow until it dies. Full draw (20 ticks) through body API v3
 * holdUse, aim with aim(), elevation solved for the arrow's drag and gravity ({@link Tactics#solve}), lead = the target's
 * measured velocity times the flight time. Never releases while a protected entity is within a block of the predicted flight
 * path; it sidesteps instead. Out of sight or beyond radius it walks (walk_to); closer than 3 it ends too_close (melee range).
 * Hits are counted from the target's synced health after a release, never from the release itself.
 */
final class FightRanged implements Skill {
	static final int LOG_MAX = 32, HIT_WINDOW = 50;

	final Integer entityId;
	final boolean ownerTarget; // O136: the owner named this mob; the protected-type rule is lifted for it alone (never a player)
	final double radius;
	final int maxArrows;

	Entity target;
	Vec3 lastPos, vel = Vec3.ZERO;
	SubAction walk;
	int walkTicks, draw = -1, shots, hits, kills, protectedHolds, arrows0 = -1, sinceShot = -1;
	boolean holding, claimLogged, swapTried;
	// ponytail: no unit test drives decide() (needs a client); the swap itself is lane evidence (log "eve-combat bow swap")

	/** The hotbar slot a main-inventory bow swaps into: the first empty one, never her sword's (keep); else 8 (7 if the sword is in 8). The displaced item goes where the bow was. */
	static int swapTarget(boolean[] hotbarEmpty, int keep) {
		for (int i = 0; i < 9; i++) if (hotbarEmpty[i] && i != keep) return i;
		return keep == 8 ? 7 : 8;
	}
	float healthStart = -1, healthMin = Float.MAX_VALUE, hpAtShot;
	double maxRange;
	final JsonArray log = new JsonArray();
	JsonObject lastShot;
	FightMelee melee; // the sword finish when a target gets inside 3
	int meleeSwitches, creeperHolds, blockHolds, blockTicks, drawShieldTicks;
	boolean shieldUp; // S28(a): the fight holds its shield against a drawing archer
	int lastDrawSeen = -1000; // O174: last tick an archer was drawing at her
	static final double HOLD_AT = 7.5;
	static final int NO_CLEAR_TICKS = 60; // O116: 3 s without a clear arrow path ends no_clear_shot

	/** O116: a block collider at horizontal distance x along {@code flat} and height y above {@code from} (the release point). */
	static java.util.function.BiPredicate<Double, Double> solidAlong(net.minecraft.world.entity.player.Player p, Vec3 from, Vec3 flat) {
		var lv = p.level();
		return (x, y) -> {
			Vec3 q = from.add(flat.x * x, y, flat.z * x);
			var pos = net.minecraft.core.BlockPos.containing(q);
			for (AABB box : lv.getBlockState(pos).getCollisionShape(lv, pos).toAabbs()) {
				if (box.move(pos).contains(q)) return true;
			}
			return false;
		};
	}

	FightRanged(JsonObject params) {
		entityId = params.has("entity_id") ? params.get("entity_id").getAsInt() : null;
		ownerTarget = params.has("owner_target") && params.get("owner_target").getAsBoolean();
		radius = params.has("radius") ? params.get("radius").getAsDouble() : 24;
		maxArrows = params.has("max_arrows") ? params.get("max_arrows").getAsInt() : 16;
	}

	@Override
	public boolean resumable() {
		return true;
	}

	@Override
	public void resume(BodyServices b) {
		walk = null;
		draw = -1; // the body released the bow when the reflex took over
		blockTicks = 0; // O116: a fresh 3 s for a clear path after a reflex
		// O102d: a sword finish paused by a reflex is re-decided, not resumed (a target inside 3 makes a fresh one at once); a stale
		// one would run FightMelee's health_floor on resume and end the fight the body just handed back
		melee = null;
	}

	@Override
	public Step tick(BodyServices b) {
		var p = b.player();
		if (ownerTarget && entityId == null) return Step.failed("bad_params", "owner_target needs entity_id: the owner names one mob"); // O136
		if (healthStart < 0) healthStart = p.getHealth();
		healthMin = Math.min(healthMin, p.getHealth());
		Step s = decide(b);
		fill(b);
		return s;
	}

	private Step decide(BodyServices b) {
		var p = b.player();
		if (EveCombat.api < 3) return Step.failed("no_weapon", "fight_ranged needs body provider API v3 (aim, holdUse)");
		// too close for the bow: finish with the sword (fight_melee in-process, same target) until it dies or is 6+ away again.
		// CP-27 attempt 2: three trials ended too_close when a skeleton rushed her.
		// b6fc94c3 lane C: a swooping phantom within 3 m started this sword finish and chased it (2 unreachable); a flyer stays the bow's
		if (target != null && !FightMelee.dead(target) && !FightMelee.flyer(target) && (melee != null || FightMelee.horiz(p.position(), target.position()) < 3)) {
			if (melee == null) {
				if (FightMelee.bestWeapon(b) == null) {
					b.holdUse(false);
					return Step.failed("too_close", "target within 3 blocks and no melee weapon");
				}
				b.holdUse(false);
				draw = -1;
				JsonObject mp = new JsonObject();
				mp.addProperty("entity_id", target.getId());
				mp.addProperty("health_floor", 6);
				melee = new FightMelee(mp);
				meleeSwitches++;
			}
			Step ms = melee.tick(b);
			if (ms.done()) {
				int swordKills = melee.kills; // O280: only a target she struck counts
				melee = null;
				if ("ok".equals(ms.status())) {
					kills += swordKills;
					return Step.ok();
				}
				return ms;
			}
			if (FightMelee.horiz(p.position(), target.position()) > 6) melee = null; // room again: back to the bow
			return ms;
		}
		// A creeper within HOLD_AT (7.5): bow down, sword in hand. Body's creeper_flee pre-empts at 7, and if she is cornered its shield
		// can only rise with a main hand that has no use (a bow has one): CP-28 a13 trial 4, cornered with the bow, blown up.
		// CP-29 a18: with the hold at 8.5 every failed follow trial had 0 shots (the creeper closes ~0.5 blocks/s, so standing and
		// shooting from ~9.4 down to 7.5 leaves time for 3-4 full draws); 7.5 still hands body's flee (7) a sword-and-shield hand.
		// O284: the creeper she is shooting is hers down to 5 (API 5 claim, renewed every tick): body's flee leaves it alone until it primes
		Entity named = target != null ? target : entityId != null ? b.minecraft().level.getEntity(entityId) : null;
		boolean claim = EveCombat.api >= 5 && named instanceof net.minecraft.world.entity.monster.Creeper && !FightMelee.dead(named)
				&& named.distanceTo(p) >= AutoEngage.CREEPER_BOW_MIN;
		if (claim) {
			b.claimCreeper(named.getId());
			if (!claimLogged) EveCombat.LOG.info("eve-combat claim creeper={} dist={}", named.getId(), String.format(java.util.Locale.ROOT, "%.2f", named.distanceTo(p))); // O301: direct proof the claim was made
			claimLogged = true;
		}
		Entity near = creeperWithin(b, claim ? AutoEngage.CREEPER_BOW_MIN : HOLD_AT);
		if (near != null) {
			b.holdUse(false);
			draw = -1;
			String w = FightMelee.bestWeapon(b);
			if (w != null && p.getInventory().getSelectedSlot() != b.hotbarSlot(w)) b.selectHotbar(b.hotbarSlot(w));
			creeperHolds++;
			// flee-then-shoot: back away facing it (sword out, so a cornered flee can shield) until it is beyond HOLD_AT, then draw
			// again. CP-28 a16: standing still here let every close creeper walk in and blow (5/11 losses, all with holds).
			b.aim(near.getEyePosition(), 40f);
			float yaw = p.getYRot() * Mth.DEG_TO_RAD;
			Vec3 fwd = new Vec3(-Mth.sin(yaw), 0, Mth.cos(yaw));
			boolean back = FightMelee.footing(p.level(), p.position().subtract(fwd));
			return Step.running(new Input(false, back, false, false, false, false, false));
		}
		int bow = b.hotbarSlot("minecraft:bow");
		if (bow < 0) { // 547236a4 lane C: her live bow sits in the main inventory (slot 13); one number-key swap brings it to the hotbar
			var inv = p.getInventory();
			int from = -1;
			for (int i = 9; i < 36 && from < 0; i++) if (inv.getItem(i).is(net.minecraft.world.item.Items.BOW)) from = i;
			if (from < 0 || swapTried) return Step.failed("no_weapon", "no bow in her inventory");
			String w = FightMelee.bestWeapon(b);
			boolean[] empty = new boolean[9];
			for (int i = 0; i < 9; i++) empty[i] = inv.getItem(i).isEmpty();
			int to = swapTarget(empty, w == null ? -1 : b.hotbarSlot(w));
			b.clickSlot(from, to, net.minecraft.world.inventory.ContainerInput.SWAP); // inventory index 9-35 = menu slot 9-35
			swapTried = true;
			EveCombat.LOG.info("eve-combat bow swap from={} to={}", from, to);
			return Step.running();
		}
		if (p.getInventory().getSelectedSlot() != bow) b.selectHotbar(bow);
		int arrows = b.inventoryCounts().getOrDefault("minecraft:arrow", 0);
		if (arrows0 < 0) arrows0 = arrows;

		if (target == null) {
			target = entityId != null ? b.minecraft().level.getEntity(entityId) : nearest(b);
			if (target == null || FightMelee.dead(target)) return Step.failed("no_target", entityId != null ? "entity not in the client world" : "no hostile in sight within " + radius);
			String[] no = Tactics.ownerTarget(ownerTarget, entityId, target.getId(), target instanceof net.minecraft.world.entity.player.Player, b.isProtected(target));
			if (no != null) return Step.failed(no[0], no[1]);
			lastPos = target.position();
		}
		if (FightMelee.dead(target)) {
			if (sinceShot >= 0 && lastShot != null) { // the arrow in flight killed it
				hits++;
				lastShot.addProperty("hit", true);
			}
			if (hits > 0) kills++; // O280: a target that died of something else is not her kill
			b.holdUse(false);
			return Step.ok();
		}
		if (target.isRemoved()) return Step.failed("lost_target", "target left the client's view without dying");

		// velocity from the synced position, smoothed (the client's deltaMovement for mobs is not reliable)
		Vec3 now = target.position();
		vel = vel.scale(0.6).add(now.subtract(lastPos).scale(0.4));
		lastPos = now;

		// a hit = the target's health dropped within HIT_WINDOW ticks of a release
		if (sinceShot >= 0) {
			if (target instanceof LivingEntity le && le.getHealth() < hpAtShot) {
				hits++;
				if (lastShot != null) lastShot.addProperty("hit", true);
				sinceShot = -1;
			} else if (++sinceShot > HIT_WINDOW) {
				sinceShot = -1;
			}
		}
		if (arrows == 0 && draw < 0) return sinceShot >= 0 ? Step.running() : Step.failed("no_ammo", "no arrows left");
		if (shots >= maxArrows && draw < 0) return sinceShot >= 0 ? Step.running() : Step.failed("out_of_arrows", shots + " arrows shot");

		double d = FightMelee.horiz(p.position(), target.position());
		if (d < 3) { // inside 3 (even on the first tick of a call): the sword branch above takes it next tick (CP-27 attempt 4 c_rush #2)
			b.holdUse(false);
			return Step.running();
		}
		if (walk != null) {
			Step st = walk.tick();
			if (st.done() || (p.hasLineOfSight(target) && d <= radius) || ++walkTicks > 60) {
				walk.cancel();
				walk = null;
			} else {
				return st;
			}
		}
		if (!p.hasLineOfSight(target) || d > radius) {
			b.holdUse(false);
			draw = -1;
			walk = b.run("walk_to", FightMelee.posParams(target.position()));
			walkTicks = 0;
			return Step.running();
		}

		// the shot: aim point = where the target will be when the arrow arrives, elevation for that distance and height
		// O116: the arrow's own path must be clear, not just eye-to-eye sight (body's floor_pen: 14 arrows into a 1.5-high fence when
		// the hitbox centre needed a falling shot). Aim points in order: centre, 0.75 of its height, its eye; the first clear wins.
		Vec3 eye = p.getEyePosition();
		AABB bb = target.getBoundingBox();
		double[] heights = {bb.minY + bb.getYsize() * 0.5, bb.minY + bb.getYsize() * 0.75, target.getEyeY()};
		Vec3 aimAt = null;
		Tactics.Shot shot = null;
		boolean solved = false;
		for (double h : heights) {
			Vec3 base = new Vec3(bb.getCenter().x, h, bb.getCenter().z), at = base;
			Tactics.Shot s = null;
			for (int i = 0; i < 3; i++) { // lead depends on the flight time, which depends on the lead: iterate
				Vec3 to = at.subtract(eye);
				s = Tactics.solve(Math.sqrt(to.x * to.x + to.z * to.z), to.y - 0.1);
				if (s == null) break;
				at = base.add(vel.multiply(1, 0, 1).scale(s.ticks()));
			}
			if (s == null) continue;
			solved = true;
			Vec3 to = at.subtract(eye);
			if (Tactics.clearPath(s.elevation(), Math.sqrt(to.x * to.x + to.z * to.z), solidAlong(p, eye.subtract(0, 0.1, 0), to.multiply(1, 0, 1).normalize()))) {
				aimAt = at;
				shot = s;
				break;
			}
		}
		if (!solved) {
			walk = b.run("walk_to", FightMelee.posParams(target.position()));
			walkTicks = 0;
			return Step.running();
		}
		if (shot == null) { // no clear path from here: bow down, sidestep for a new line; after 3 s give up rather than waste arrows
			if (blockTicks++ == 0) blockHolds++;
			b.holdUse(false);
			draw = -1;
			if (blockTicks > NO_CLEAR_TICKS) return Step.failed("no_clear_shot", "no aim point on the target with a clear arrow path for 3 s");
			return Step.running(strafe(p, true, d));
		}
		blockTicks = 0;
		Vec3 flat = aimAt.subtract(eye).multiply(1, 0, 1).normalize();
		double el = Math.toRadians(shot.elevation());
		Vec3 dir = new Vec3(flat.x * Math.cos(el), Math.sin(el), flat.z * Math.cos(el));
		boolean aimed = b.aim(eye.add(dir.scale(10)), 30f);

		// line of fire: a protected entity within a block of the predicted flight path -> hold, sidestep
		boolean blocked = protectedOnPath(b, eye, dir, aimAt.subtract(eye).length());
		if (blocked && !holding) protectedHolds++;
		holding = blocked;

		if (blocked) { // bow down while sidestepping: a drawn bow walks at a fifth of the speed (CP-27 attempt 1: the skeleton closed in)
			b.holdUse(false);
			draw = -1;
			return Step.running(strafe(p, true, d));
		}
		// S28(a), the duel: an archer drawing at her gets the shield unless her own arrow leaves first with SHIELD_RAISE ticks to spare.
		// guard and holdUse never share a tick (API): release the draw, then raise; lower the shield, then draw again.
		Entity drawer = FightMelee.drawnAt(b);
		if (drawer != null) lastDrawSeen = b.ticks();
		if (EveCombat.api >= 3 && Tactics.shieldInsteadOfDraw(drawer != null, p.getHealth(), draw, drawer instanceof LivingEntity dl ? dl.getTicksUsingItem() : 0)) {
			if (draw >= 0) { // this tick: let the bow go (a partial draw fires short); the shield goes up next tick
				b.holdUse(false);
				draw = -1;
				return Step.running();
			}
			b.aim(drawer.getEyePosition(), 40f); // a shield blocks from the front only
			if (b.guard(true)) {
				shieldUp = true;
				drawShieldTicks++;
				return Step.running();
			}
		} else if (shieldUp && Tactics.holdForArrow(b.ticks() - lastDrawSeen, FightMelee.arrowIncoming(b))) {
			// O174: it has released and the arrow is in the air: keep the raised shield (lowering it now was the failed 0-4 ticks)
			b.guard(true);
			drawShieldTicks++;
			return Step.running();
		} else if (shieldUp) { // the arrow has passed: shield down this tick, draw from the next
			b.guard(false);
			shieldUp = false;
			return Step.running();
		}
		if (draw < 0) {
			b.holdUse(true);
			draw = 0;
			return Step.running(strafe(p, false, d));
		}
		b.holdUse(true);
		draw++;
		if (draw > Tactics.FULL_DRAW && aimed && !blocked) {
			hpAtShot = target instanceof LivingEntity le ? le.getHealth() : 0;
			b.holdUse(false); // a drawn bow fires on release
			shots++;
			EveCombat.LOG.info("eve-combat shot dist={} target={}", String.format(java.util.Locale.ROOT, "%.2f", d), target.getId()); // O301: auto_fight's event carries no skill evidence
			draw = -1;
			sinceShot = 0;
			maxRange = Math.max(maxRange, d);
			lastShot = new JsonObject();
			lastShot.addProperty("tick", b.ticks());
			lastShot.addProperty("distance", FightMelee.r2(d));
			lastShot.addProperty("elevation", FightMelee.r2(shot.elevation()));
			lastShot.addProperty("flight_ticks", shot.ticks());
			lastShot.addProperty("lead", FightMelee.r2(vel.multiply(1, 0, 1).length() * shot.ticks()));
			lastShot.addProperty("hit", false);
			if (log.size() < LOG_MAX) log.add(lastShot);
		}
		return Step.running(strafe(p, blocked, d));
	}

	/** Sidestep to clear a protected entity off the line of fire; back away from a target closer than 5; otherwise stand. */
	private static Input strafe(net.minecraft.world.entity.player.Player p, boolean blocked, double d) {
		float yaw = p.getYRot() * Mth.DEG_TO_RAD;
		Vec3 fwd = new Vec3(-Mth.sin(yaw), 0, Mth.cos(yaw)), left = new Vec3(Mth.cos(yaw), 0, Mth.sin(yaw));
		boolean back = d < 5 && FightMelee.footing(p.level(), p.position().subtract(fwd));
		if (!blocked) return back ? new Input(false, true, false, false, false, false, false) : Input.EMPTY;
		boolean l = FightMelee.footing(p.level(), p.position().add(left));
		return new Input(false, back, l, !l && FightMelee.footing(p.level(), p.position().subtract(left)), false, false, false);
	}

	private static Entity creeperWithin(BodyServices b, double d) {
		for (Entity e : b.minecraft().level.entitiesForRendering())
			if (e instanceof net.minecraft.world.entity.monster.Creeper c && c.isAlive() && c.distanceTo(b.player()) < d) return c;
		return null;
	}

	/** Any protected entity whose box (grown by 1) the simulated flight passes through before the target. */
	private boolean protectedOnPath(BodyServices b, Vec3 eye, Vec3 dir, double dist) {
		var level = b.minecraft().level;
		java.util.List<AABB> boxes = new java.util.ArrayList<>();
		for (Entity o : level.entitiesForRendering())
			if (o != b.player() && o != target && b.isProtected(o) && o.distanceTo(b.player()) < dist + 4) boxes.add(o.getBoundingBox().inflate(1.0));
		if (boxes.isEmpty()) return false;
		Vec3 pos = eye, v = dir.scale(Tactics.ARROW_SPEED);
		for (int t = 0; t < 100 && pos.distanceTo(eye) < dist; t++) {
			Vec3 next = pos.add(v);
			for (AABB bx : boxes) if (bx.contains(pos) || bx.clip(pos, next).isPresent()) return true;
			pos = next;
			v = v.scale(Tactics.ARROW_DRAG).subtract(0, Tactics.ARROW_GRAVITY, 0);
		}
		return false;
	}

	/**
	 * Nearest living hostile within radius, one in sight first (creepers included: the bow is how a creeper gets fought). With
	 * none in sight, the nearest hidden one: she walks until she sees it (CP-27 attempt 1: behind a wall it was no_target).
	 */
	private Entity nearest(BodyServices b) {
		Entity seen = null, hidden = null;
		var p = b.player();
		for (Entity e : b.minecraft().level.entitiesForRendering()) {
			if (e == p || !FightMelee.hostile(e) || !(e instanceof LivingEntity) || FightMelee.dead(e) || b.isProtected(e)) continue;
			if (e.distanceTo(p) > radius) continue;
			if (p.hasLineOfSight(e)) {
				// a creeper in sight goes first: it is the one threat the sword cannot answer (body's flee pre-empts inside 7)
				boolean eC = e instanceof net.minecraft.world.entity.monster.Creeper, sC = seen instanceof net.minecraft.world.entity.monster.Creeper;
				boolean better = seen == null || (eC && !sC) || (eC == sC && e.distanceTo(p) < seen.distanceTo(p));
				if (better) seen = e;
			} else if (hidden == null || e.distanceTo(p) < hidden.distanceTo(p)) {
				hidden = e;
			}
		}
		return seen != null ? seen : hidden;
	}

	@Override
	public void cancel(BodyServices b) {
		if (walk != null) walk.cancel();
		walk = null;
		fill(b);
	}

	private void fill(BodyServices b) {
		JsonObject ev = b.evidence();
		ev.addProperty("shots", shots);
		ev.addProperty("hits", hits);
		ev.addProperty("arrows_used", arrows0 < 0 ? 0 : arrows0 - b.inventoryCounts().getOrDefault("minecraft:arrow", 0));
		ev.addProperty("kills", kills);
		ev.addProperty("health_start", healthStart);
		ev.addProperty("health_min", healthMin == Float.MAX_VALUE ? null : healthMin);
		ev.addProperty("max_range", FightMelee.r2(maxRange));
		ev.addProperty("protected_holds", protectedHolds);
		ev.addProperty("draw_shield_ticks", drawShieldTicks); // S28(a): shield held while an archer drew at her
		ev.addProperty("block_holds", blockHolds); // O116: times no aim point had a clear arrow path (bow down, sidestep)
		ev.add("shot_log", log);
		ev.addProperty("melee_switches", meleeSwitches); // extra keys
		ev.addProperty("creeper_hold_ticks", creeperHolds);
	}
}
