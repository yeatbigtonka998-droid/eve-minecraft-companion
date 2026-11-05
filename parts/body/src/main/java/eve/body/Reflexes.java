package eve.body;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.component.DataComponents;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.util.Mth;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.monster.Creeper;
import net.minecraft.world.entity.player.Input;
import net.minecraft.world.entity.projectile.Projectile;
import net.minecraft.world.food.FoodProperties;
import net.minecraft.world.item.CrossbowItem;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.ItemUseAnimation;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.attribute.EnvironmentAttributes;
import net.minecraft.world.phys.Vec3;

import java.util.Set;

/**
 * Phase 3 reflex layer: fixed Java rules, no model, evaluated every client tick under the arbiter (STOPPED > reflex > action >
 * idle). One pre-empting reflex at a time (shield/dodge > retreat > eat); edge-sneak is a modifier on top of whatever runs; fall
 * rescue empties a hotbar water bucket under her (else it only reports the fall). Every start and end is an event{kind:"reflex"}.
 */
final class Reflexes {
	static final float HEALTH_FLOOR = 6f; // retreat at or below 3 hearts with a hostile near
	static final int EAT_FOOD = 14, EAT_HURT_FOOD = 18;
	static final float EAT_HURT_HEALTH = 14f;
	static final double SAFE_RADIUS = 8, RETREAT_TRIGGER = 10, RETREAT_CLEAR = 16, MELEE_RADIUS = 3.5;
	static final int EAT_STARVING = 6; // food at which an unsafe meal is worth a retreat first (CP-25 bounded fallback)
	static final int AIR_LIMIT = 150; // of 300: surface at half the air left (CP-30 configured oxygen limit)
	static final int IMPACT_HORIZON = 20; // ticks: a projectile predicted to hit within this many ticks is incoming
	// O98 (CP-31 a16 night 1): a skeleton's arrow from 6 blocks lands 3-4 ticks after it is seen, and a shield blocks only from
	// its 5th raised tick, so the shield rises on the drawn bow (a 20-tick draw), not on the arrow
	static final double ARCHER_RADIUS = 24, AIM_COS = Math.cos(Math.toRadians(25));
	/** S28 (live death 13:26:56): a raised shield stays up while a shooter is this near in sight (not re-raised per arrow). */
	static final double ARCHER_HOLD = 16;
	/** Never eaten by a reflex: harmful, teleporting, or too valuable to spend on routine hunger. */
	static final Set<String> NO_EAT = Set.of("minecraft:rotten_flesh", "minecraft:spider_eye", "minecraft:poisonous_potato", "minecraft:pufferfish",
			"minecraft:suspicious_stew", "minecraft:chorus_fruit", "minecraft:golden_apple", "minecraft:enchanted_golden_apple");

	// O98o (2) (CP-31 a22: a drawn_bow shield held 1.87 s while she sealed the doorway it guarded): a place_block that seals the
	// opening the archer sees her through is not pre-empted by the drawn_bow shield for its first 1.5 s (docs/O98o-seal-over-shield.md)
	static final long SEAL_YIELD_NS = 1_500_000_000L;
	/** This tick's sealing place, or null: the drawn_bow shield stays down for it. */
	static Interact.PlaceBlock sealing;
	private static String yieldId;
	private static BlockPos yieldCell;
	private static int yieldArcher, yieldTicks;
	private static long yieldLastTick = -1;
	private static boolean yieldEndedShield;

	// O102d (3) (lane F cp27c c_rush #6: at 4.8 HP, cornered by a lone skeleton, Retreat/Shield/Eat cycled 50 s while the paused
	// fight_ranged never ran): the reflexes stand back from the fight for 8 s (docs/O102d-floor-handback.md)
	static final long HAND_BACK_NS = 8_000_000_000L, HAND_BACK_GRACE_NS = 1_000_000_000L;
	private static long handBackFromNs, handBackUntilNs, lastFightNs;
	private static String handBackAction;

	static R active;
	static long triggers;
	private static boolean edge;
	private static int edgeOffTicks;
	private static boolean fallReported;
	private static long eatRetryNs, eatRetreatNs, darkRetryNs, sleepRetryNs;
	private static int darkTicks;

	/** What the client world shows this tick. */
	record Threats(Entity hostile, double hostileDist, Projectile incoming, int impactTicks, Vec3 away, Creeper creeper, double creeperDist,
			Entity archer) {
		boolean safe() {
			return incoming == null && archer == null && (hostile == null || hostileDist > SAFE_RADIUS);
		}
	}

	abstract static class R {
		final String name;
		final int priority;
		final long startNs = System.nanoTime();
		final JsonObject trigger = new JsonObject(), detail = new JsonObject();
		int ticks;
		String result;

		R(String name, int priority) {
			this.name = name;
			this.priority = priority;
		}

		abstract Input tick(Minecraft mc, LocalPlayer p, Threats t);

		void release(Minecraft mc, LocalPlayer p) {}
	}

	/** Arbiter, client START tick (STOPPED too: its defensive reflexes only, O218): the reflex's input for this tick, or null when no reflex holds the controls. */
	static Input tick(Minecraft mc, LocalPlayer p) {
		if (p.isDeadOrDying() || mc.gameMode == null || mc.gameMode.getPlayerMode().isCreative() || p.isSpectator()) {
			cancel(mc, p, "not_applicable");
			return null;
		}
		Threats t = scan(mc, p);
		if (!(active instanceof CreeperFlee)) recordTrail(p.position(), EveBody.clientTick); // O324: not while she walks it back
		// O174: a shield left up for the resuming fight that its guard() has not taken within 3 ticks is let go
		if (Hands.shieldHandedBack && ++Hands.handedBackTicks > 3) Hands.releaseHandedBack(mc, p);
		fallWatch(p);
		handBackTick();
		sealYield(p, t);
		R want = pick(p, t);
		if (Arbiter.stopped && want != null && (!allowedStopped(want.name) || "unsafe_meal".equals(want.trigger.has("cause") ? want.trigger.get("cause").getAsString() : null)))
			want = null; // O218
		if (want != null && active instanceof Retreat && want instanceof Shield && !shieldMayPreemptRetreat(t.incoming() != null)) want = null; // 45L 17:16
		if (want instanceof AutoFight && active instanceof Eat && !autoFightMayInterruptMeal(p.getHealth())) want = null; // S105: 45L 17:12:26
		if (want != null && (active == null || want.priority > active.priority || engagesOverHeldShield(want instanceof AutoFight, active instanceof Shield, t.incoming() != null))) {
			if (active != null) {
				if (active instanceof Retreat && want instanceof Shield && !handBackActive()) floorRetreatPreempted(p, t); // O102o
				if (active instanceof Shield sh && want instanceof AutoFight) sh.handToAutoFight = true;
				end(mc, p, "preempted_by_" + want.name);
			}
			active = want;
			triggers++;
			JsonObject d = base(want, "start");
			d.add("trigger", want.trigger);
			// O174: a shield reflex taking over a shield the fight already raised keeps it up (no release, no re-press)
			Hands.shieldHandover = want instanceof Shield && Hands.shieldInUse(p);
			if (Hands.shieldHandover) d.addProperty("shield_taken_raised", true);
			if (Arbiter.active != null) {
				d.addProperty("action_paused", Arbiter.active.internal ? Arbiter.active.skill : Arbiter.active.id);
				// O102d: the action lets go before the reflex's first press (paused after it, a guarding provider's release lifted
				// the shield just raised: 391 ticks never blocking in c_rush #6)
				Arbiter.pause(mc, p, Arbiter.active);
			}
			JsonObject freed = Hands.shieldHandover || want instanceof AutoFight ? null : freeHands(mc, p, want); // a fight picks its own weapon
			Hands.shieldHandover = false;
			if (freed != null) d.add("hands_freed", freed);
			Telemetry.event("reflex", d);
			EveBody.LOG.info("eve reflex {} start {}", want.name, want.trigger);
		}
		if (active == null) {
			if (want == null && Arbiter.active == null && !Arbiter.stopped) Upkeep.tick(mc, p, t);
			return null;
		}
		Input in = active.tick(mc, p, t);
		active.ticks++;
		if (active.result != null) {
			end(mc, p, active.result);
			return Input.EMPTY; // the action (if any) gets the controls back next tick
		}
		return in;
	}

	/**
	 * O53i: a threat reflex is never blocked by what an action put in her hand. At its start, release any item use (a drawn bow:
	 * vanilla fires it) and, if the main hand holds something with a use (bow, crossbow, food, ...), select a hotbar sword, else any
	 * slot without a use, so a shield can rise (CP-28 a13: cornered with a drawn bow, no shield, blown up). Eat, fall rescue, sleep
	 * and darkness manage her hands themselves. Null when nothing needed doing.
	 */
	static JsonObject freeHands(Minecraft mc, LocalPlayer p, R r) {
		if (r instanceof Eat || r instanceof FallRescue || r instanceof Sleep || r instanceof Darkness) return null;
		JsonObject o = new JsonObject();
		if (p.isUsingItem()) {
			o.addProperty("released", Hands.itemId(p.getUseItem()));
			mc.options.keyUse.setDown(false);
			mc.gameMode.releaseUsingItem(p);
		}
		ItemStack main = p.getMainHandItem();
		if (!main.isEmpty() && (main.getUseAnimation() != ItemUseAnimation.NONE || main.get(DataComponents.FOOD) != null)) {
			int best = -1;
			for (int i = 0; i < 9; i++) {
				ItemStack s = p.getInventory().getItem(i);
				boolean free = s.isEmpty() || s.getUseAnimation() == ItemUseAnimation.NONE && s.get(DataComponents.FOOD) == null;
				if (!free) continue;
				if (s.is(net.minecraft.tags.ItemTags.SWORDS)) {
					best = i;
					break;
				}
				if (best < 0) best = i;
			}
			if (best >= 0) {
				o.addProperty("from", Hands.itemId(main));
				o.addProperty("to_slot", best);
				o.addProperty("to", Hands.itemId(p.getInventory().getItem(best)));
				p.getInventory().setSelectedSlot(best);
			}
		}
		return o.isEmpty() ? null : o;
	}

	/**
	 * O218: what may still run while STOPPED. STOPPED suppresses actions, skills and movement orders; staying alive is not one of
	 * them (the owner's stop is not "let her be killed"). No sleep, torch-placing, upkeep moves or meal-retreat.
	 */
	/** H-autofight config flag (default on): -Deve.autoFight=false or EVE_AUTO_FIGHT=0 switches it off. */
	static final boolean AUTO_FIGHT = !Set.of("0", "false", "off").contains(String.valueOf(System.getProperty("eve.autoFight",
			System.getenv("EVE_AUTO_FIGHT") == null ? "on" : System.getenv("EVE_AUTO_FIGHT"))).toLowerCase());
	private static long engageRefusedNs;

	/** H-autofight: the hook is asked only when on, not STOPPED, above the floor, no fight action running, and a hook exists. */
	static boolean autoFightMayPoll(boolean stopped, float hp, boolean flag, boolean fightAction, boolean hook) {
		return flag && hook && !stopped && hp > HEALTH_FLOOR && !fightAction;
	}

	/**
	 * O311/O313 (45L: auto_fight cycled on a skeleton behind her wall, 0 kills, each cycle chatted): a target that just ended a fight with no kill is
	 * left for 30 s, unless it hit her or the owner.
	 */
	static boolean engageCooling(int target, String reason, int noKillTarget, long noKillTick, long now) {
		return target == noKillTarget && now - noKillTick < ENGAGE_COOL_TICKS && !"hit".equals(reason) && !"owner_targeted".equals(reason);
	}

	static final long ENGAGE_COOL_TICKS = 600;
	private static int noKillTarget = -1;
	private static long noKillTick;

	/** S96 a119: a fight the hook asks for takes over a shield that only waits on a shooter or a mob in reach; never one against an arrow in flight. */
	static boolean engagesOverHeldShield(boolean wantFight, boolean activeShield, boolean arrowInFlight) {
		return wantFight && activeShield && !arrowInFlight;
	}

	/** H-autofight: why it ends this tick, or null: the floor, the 120 s cap, or 60 ticks with no eligible hostile. */
	static String autoFightEnd(float hp, long elapsedNs, int quietTicks) {
		if (hp <= HEALTH_FLOOR) return "floor";
		if (elapsedNs > AUTO_FIGHT_CAP_NS) return "timeout";
		return quietTicks >= AUTO_FIGHT_CLEAR_TICKS ? "clear" : null;
	}

	/** The auto_fight start event's reason values (kickoff ruling 6a77f4a1 added targeting_eve: a mob.targeting frame names her). */
	static final Set<String> ENGAGE_REASONS = Set.of("near", "drawing", "hit", "owner_targeted", "targeting_eve");

	static final long AUTO_FIGHT_CAP_NS = 120_000_000_000L;
	static final int AUTO_FIGHT_CLEAR_TICKS = 60;

	static final Set<String> DEFENSIVE = Set.of("shield", "dodge", "retreat", "creeper_flee", "eat", "fall_rescue", "hazard", "phantom_cover");

	static boolean allowedStopped(String reflex) {
		return DEFENSIVE.contains(reflex);
	}

	/** O218: STOPPED: end a non-defensive reflex (sleep, darkness); a defence in progress continues. */
	static void cancelNotDefensive(Minecraft mc, LocalPlayer p, String why) {
		if (active != null && !allowedStopped(active.name)) end(mc, p, why);
	}

	/** Stop, disconnect, death: release whatever the reflex holds. */
	static void cancel(Minecraft mc, LocalPlayer p, String why) {
		if (active != null) end(mc, p, why);
		edge = false;
		fallReported = false;
	}

	private static void end(Minecraft mc, LocalPlayer p, String result) {
		R r = active;
		active = null;
		if (p != null) r.release(mc, p);
		JsonObject d = base(r, "end");
		d.addProperty("result", result);
		d.addProperty("elapsed_ms", (System.nanoTime() - r.startNs) / 1_000_000);
		d.addProperty("ticks", r.ticks);
		d.add("detail", r.detail);
		Telemetry.event("reflex", d);
		EveBody.LOG.info("eve reflex {} end {} after {} ticks {}", r.name, result, r.ticks, r.detail);
	}

	private static JsonObject base(R r, String phase) {
		JsonObject d = new JsonObject();
		d.addProperty("reflex", r.name);
		d.addProperty("phase", phase);
		return d;
	}

	/** H-autofight: the running auto-fight's Baritone child walk owns player.input this tick. */
	static boolean reflexOwnsInput() {
		return active instanceof AutoFight af && af.ownsInput();
	}

	static String activeName() {
		return active == null ? null : active.name;
	}

	// ---- perception ----

	static Threats scan(Minecraft mc, LocalPlayer p) {
		Entity hostile = null;
		double hd = Double.MAX_VALUE;
		Projectile inc = null;
		int impact = Integer.MAX_VALUE;
		Vec3 away = Vec3.ZERO;
		AABB me = p.getBoundingBox().inflate(0.3);
		Creeper creeper = null;
		double cd = Double.MAX_VALUE, ad = Double.MAX_VALUE;
		Entity archer = null;
		for (Entity e : mc.level.entitiesForRendering()) {
			if (e == p || !e.isAlive() || e.distanceTo(p) > 32) continue;
			if (e instanceof Creeper c && e.distanceTo(p) < cd) {
				cd = e.distanceTo(p);
				creeper = c;
			}
			if (Telemetry.isHostile(e) && !Telemetry.isProtected(e)) { // a calm enderman is not a threat (CP-31 a9 attempt 11)
				double d = e.distanceTo(p);
				if (d < hd) {
					hd = d;
					hostile = e;
				}
				if (d <= RETREAT_CLEAR) away = away.add(p.position().subtract(e.position()).multiply(1, 0, 1).normalize().scale(1 / Math.max(d, 0.5)));
				if (d < ad && d <= ARCHER_RADIUS && drawingAt(e, p) && p.hasLineOfSight(e)) {
					ad = d;
					archer = e;
				}
			} else if (e instanceof Projectile pr && pr.getOwner() != p) {
				int k = impactTicks(e, me);
				if (k < impact) {
					impact = k;
					inc = pr;
				}
			}
		}
		return new Threats(hostile, hd, impact <= IMPACT_HORIZON ? inc : null, impact, away, creeper, cd, archer);
	}

	/** A bow, crossbow or trident in its main hand: a shooter, whose cover is a wall, not distance. */
	static boolean ranged(Entity e) {
		if (!(e instanceof LivingEntity le)) return false;
		ItemUseAnimation a = le.getMainHandItem().getUseAnimation();
		return a == ItemUseAnimation.BOW || a == ItemUseAnimation.CROSSBOW || a == ItemUseAnimation.TRIDENT;
	}

	/** O98: drawing a bow or trident, or holding a charged crossbow, with its head turned to her (the synced use flag and head rotation). */
	static boolean drawingAt(Entity e, LocalPlayer p) {
		if (!(e instanceof LivingEntity le) || !ranged(e)) return false;
		boolean drawn = le.isUsingItem() || CrossbowItem.isCharged(le.getMainHandItem());
		return drawn && aimedAt(le.getEyePosition(), le.getViewVector(1f), p.getEyePosition());
	}

	/** Where a projectile comes from, as seen from {@code eye}: 8 back along its path (its own position when it barely moves). */
	static Vec3 alongPath(Vec3 eye, Vec3 pos, Vec3 v) {
		return v.lengthSqr() < 1e-4 ? pos : eye.subtract(v.normalize().scale(8));
	}

	/** The shooter's view within 25 deg of the line from its eyes to {@code target}. */
	static boolean aimedAt(Vec3 eye, Vec3 view, Vec3 target) {
		Vec3 to = target.subtract(eye);
		return to.lengthSqr() > 1e-6 && view.lengthSqr() > 1e-6 && view.normalize().dot(to.normalize()) >= AIM_COS;
	}

	/** Ticks until {@code e}'s ballistic path (drag 0.99, its own gravity) enters {@code box}; MAX_VALUE if it does not within 30. */
	static int impactTicks(Entity e, AABB box) {
		Vec3 pos = e.position().add(0, e.getBbHeight() / 2, 0), v = e.getDeltaMovement();
		if (v.lengthSqr() < 0.04) return Integer.MAX_VALUE; // stuck in a block or nearly still
		double g = e.getGravity();
		for (int k = 1; k <= 30; k++) {
			Vec3 next = pos.add(v);
			if (box.contains(pos) || box.clip(pos, next).isPresent()) return k;
			pos = next;
			v = v.scale(0.99).subtract(0, g, 0);
		}
		return Integer.MAX_VALUE;
	}

	private static R pick(LocalPlayer p, Threats t) {
		if (!(active instanceof FallRescue)) {
			double landing = landingFall(p);
			int bucket = Hands.hotbarSlot(p, "minecraft:water_bucket");
			if (landing > 3.5 && bucket >= 0 && !p.level().environmentAttributes().getValue(EnvironmentAttributes.WATER_EVAPORATES, p.position()))
				return new FallRescue(p, bucket, landing);
		}
		if (!(active instanceof Hazard)) {
			String h = hazard(p);
			if (h != null) return new Hazard(p, h);
		}
		if (!(active instanceof CreeperFlee) && creeperThreat(p, t)) return new CreeperFlee(p, t);
		boolean shield = shieldUsable(p);
		// H-autofight: an arrow at her while the auto-fight's own guard is up is the fight's (its guard blocks it; a shield reflex
		// would end the fight and restart the block delay)
		if (t.incoming() != null && !(active instanceof AutoFight && Hands.shieldInUse(p))) return shield ? new Shield(p, t) : new Dodge(t);
		// a provider action that owns the shield (API v3 guard) handles melee itself; projectiles still raise it here
		boolean guarded = guardedFight();
		// at the floor, running beats blocking: a shield covers the front only and a surrounded Eve died behind one (combat CP-28
		// lane C attempt 6, 5 zombies, 417 ticks). The melee shield stays the fallback for 3 s after a retreat ends cornered.
		if (retreatFirst(p, t)) return new Retreat(p, t);
		// S28: at the floor with a shooter nearest, the shield owns the controls and walks her back (no retreat/shield cycle)
		if (shield && floorShield(p, t)) return new Shield(p, t);
		// S96 a119 (auto_archer: 0 engages, the drawn-bow shield held 120 s until her walk timed out): a fight comes before the
		// shield that only waits; the fight's own draw guard covers the bow. Below the floor rules and the arrow in flight.
		// S105 phantom_cover (45L death 17:16, phantoms under open sky): a phantom hit in the last 5 s, or hurt with one on her
		if (!(active instanceof PhantomCover) && System.nanoTime() > coverRetryNs) {
			var ph = phantomNear(p, 16);
			boolean hitByPhantom = p.level().getEntity(Telemetry.lastAttackerId) instanceof net.minecraft.world.entity.monster.Phantom
					&& EveBody.clientTick - Telemetry.lastHitTick <= 100;
			if (phantomCoverDue(p.level().canSeeSky(p.blockPosition().above()), hitByPhantom, p.getHealth(), ph != null)) {
				coverRetryNs = System.nanoTime() + 3_000_000_000L;
				return new PhantomCover(p, hitByPhantom);
			}
		}
		// H-autofight (API v4): the provider's EngageHook decides whom; priority 2, below the floor rules above, above eating
		if (autoFightMayPoll(Arbiter.stopped, p.getHealth(), AUTO_FIGHT, isFight(Arbiter.active), Providers.ENGAGE != null)
				&& !(active instanceof AutoFight)) {
			eve.body.api.Engage e = Providers.engage(Providers.ENGAGE, Providers.VIEW);
			if (e != null && !engageCooling(e.targetId(), e.reason(), noKillTarget, noKillTick, EveBody.clientTick)) {
				String why = Providers.precheck(Minecraft.getInstance(), e.skill(), e.params() == null ? new JsonObject() : e.params());
				if (why == null) return new AutoFight(p, e);
				if (System.nanoTime() - engageRefusedNs > 10_000_000_000L) {
					engageRefusedNs = System.nanoTime();
					EveBody.LOG.warn("eve reflex auto_fight: engage {} refused: {}", e.skill(), why);
				}
			}
		}
		// O98: raised on the drawn bow and held toward it; a provider fight that owns the shield (combat's fight_melee) handles it.
		// O98o (2): not while a place seals the opening that archer sees her through (the block is the defence)
		if (shield && shieldCause(false, t.archer() != null, melee(p, t), guarded, sealing != null) != null) return new Shield(p, t);
		if (p.getHealth() <= HEALTH_FLOOR && t.hostile() != null && t.hostileDist() <= RETREAT_TRIGGER && !handBackActive() && !floorShield(p, t))
			return new Retreat(p, t);
		// starving with food at hand but a hostile near: get clear first (bounded: once per 30 s), then the eat below can run
		if (active == null && p.getFoodData().getFoodLevel() <= EAT_STARVING && t.incoming() == null && t.hostile() != null
				&& t.hostileDist() <= SAFE_RADIUS && System.nanoTime() > eatRetreatNs && foodSlot(p) >= 0 && !handBackActive()
				&& !isFight(Arbiter.active)) { // O102ab: not over a fight
			eatRetreatNs = System.nanoTime() + 30_000_000_000L;
			Retreat r = new Retreat(p, t);
			r.trigger.addProperty("cause", "unsafe_meal");
			return r;
		}
		// CP-30 idle routines (45L, real nights): out of an enclosed dark spot inside a zone; into a bed at night when one is in reach
		if (active == null && Arbiter.active == null && t.safe() && p.onGround() && !p.isInWater()) {
			if (System.nanoTime() > sleepRetryNs && !p.isSleeping() && p.level().isDarkOutside()) {
				BlockPos bed = bedInReach(p);
				if (bed != null) return new Sleep(p, bed);
			}
			// debounced: the client's light lags a block change by a few ticks (dev a40: a fresh room read lit, then dark)
			darkTicks = enclosedDark(p) ? darkTicks + 1 : 0;
			// any enclosed dark spot (lane C a2: no local envelope copy on the lane clients, so a zone gate never fired); the
			// local zone only decides whether she may place a torch there
			if (System.nanoTime() > darkRetryNs && darkTicks >= 20) return new Darkness(p);
		}
		if (active == null && System.nanoTime() > eatRetryNs && eatAllowed(t.safe(), hungry(p), isFight(Arbiter.active)) && p.onGround()
				&& !p.isInWater() && !p.isInLava() && !p.isOnFire()) {
			int slot = foodSlot(p);
			if (slot >= 0) return new Eat(p, slot);
		}
		return null;
	}

	/** A provider fight owns the guard (its claim, judged as of its pause while paused), or the floor hand-back is open (O102d (3)). */
	static boolean guardedFight() {
		// O102ab fix A: any active provider fight (running, or paused by a reflex) owns melee and the drawn bow
		return isFight(Arbiter.active) || Arbiter.active instanceof Providers.ProviderAct pa && pa.guardClaimed() || handBackActive()
				|| active instanceof AutoFight; // H-autofight: the reflex's own fight owns melee and the drawn bow too
	}

	static boolean isFight(Skills.Act a) {
		return a instanceof Providers.ProviderAct && (a.skill.equals("fight_ranged") || a.skill.equals("fight_melee"));
	}

	/**
	 * O102d (3): the stand-back window opens when a retreat ends cornered at the health floor, the nearest hostile is out of melee
	 * reach and a ranged one is in sight, and a provider fight is the action (paused or not).
	 */
	static boolean handBackOpens(float hp, double hostileDist, boolean rangedInSight, boolean fight) {
		return hp <= HEALTH_FLOOR && hostileDist > MELEE_RADIUS && rangedInSight && fight;
	}

	/** Open for 8 s, and only while a fight is the action or one ended under 1 s ago (the next call, one target each, is coming). */
	static boolean handBackOpen(long now, long until, boolean fight, long lastFightNs) {
		return now < until && (fight || now - lastFightNs < HAND_BACK_GRACE_NS);
	}

	static boolean handBackActive() {
		return handBackOpen(System.nanoTime(), handBackUntilNs, isFight(Arbiter.active), lastFightNs);
	}

	/**
	 * O102o (floor_pen a101: in 4 of 10 trials the retreat never ended cornered, it ended preempted_by_shield over and over while the
	 * shield ended yield_retreat): a second floor retreat pre-empted by a shield within 10 s also opens the window, once per trigger.
	 */
	static final long PREEMPT_TWICE_NS = 10_000_000_000L;
	private static long floorPreemptNs; // the last such pre-emption since the last trigger, or 0

	static boolean secondPreempt(long now, long last) {
		return last != 0 && now - last <= PREEMPT_TWICE_NS;
	}

	private static void floorRetreatPreempted(LocalPlayer p, Threats t) {
		long now = System.nanoTime();
		if (secondPreempt(now, floorPreemptNs)) {
			floorPreemptNs = 0;
			handBack(p, t, "preempted_twice");
		} else {
			floorPreemptNs = now;
		}
	}

	/** Retreat ended cornered (or was pre-empted twice): open the window if the conditions hold (event reflex {retreat, phase hand_back}). */
	static void handBack(LocalPlayer p, Threats t, String trigger) {
		Entity h = t.hostile();
		Entity shooter = t.archer() != null ? t.archer() : h != null && ranged(h) && p.hasLineOfSight(h) ? h : null;
		if (h == null || !handBackOpens(p.getHealth(), t.hostileDist(), shooter != null, isFight(Arbiter.active))) return;
		long now = System.nanoTime();
		handBackFromNs = lastFightNs = now;
		handBackUntilNs = now + HAND_BACK_NS;
		handBackAction = Arbiter.active.id;
		JsonObject d = new JsonObject();
		d.addProperty("reflex", "retreat");
		d.addProperty("phase", "hand_back");
		d.addProperty("action_id", handBackAction);
		d.addProperty("until_ms", HAND_BACK_NS / 1_000_000);
		d.addProperty("hostile_id", shooter.getId());
		d.addProperty("trigger", trigger);
		Telemetry.event("reflex", d);
		EveBody.LOG.info("eve reflex retreat hand_back {}", d);
	}

	/** Every tick: track the fight and close the window (event {retreat, phase hand_back_end, reason timeout | no_fight}). */
	private static void handBackTick() {
		if (handBackUntilNs == 0) return;
		long now = System.nanoTime();
		if (isFight(Arbiter.active)) lastFightNs = now;
		if (handBackOpen(now, handBackUntilNs, isFight(Arbiter.active), lastFightNs)) return;
		JsonObject d = new JsonObject();
		d.addProperty("reflex", "retreat");
		d.addProperty("phase", "hand_back_end");
		d.addProperty("action_id", handBackAction);
		d.addProperty("reason", now >= handBackUntilNs ? "timeout" : "no_fight");
		d.addProperty("held_ms", (now - handBackFromNs) / 1_000_000);
		Telemetry.event("reflex", d);
		EveBody.LOG.info("eve reflex retreat hand_back_end {}", d);
		handBackUntilNs = 0;
	}

	/**
	 * O102ab: which shield may start. An arrow predicted to hit always raises it ("projectile"). Over a guarded fight (fix A: any
	 * provider fight, a guard claim, an open hand-back) nothing else does (lane F c_rush #18: a melee shield held fight_ranged
	 * paused 50 s). Else the drawn bow (unless a place seals against that archer, O98o (2)), else a mob in melee reach in sight.
	 */
	static String shieldCause(boolean incoming, boolean archerDrawing, boolean meleeInSight, boolean guarded, boolean sealing) {
		if (incoming) return "projectile";
		if (guarded) return null;
		if (archerDrawing && !sealing) return "drawn_bow";
		return meleeInSight ? "melee" : null;
	}

	/** O102ab: Eat starts when hungry and safe, never over a fight (c_rush #18: it paused the fight inside the hand-back). */
	static boolean eatAllowed(boolean safe, boolean hungry, boolean fight) {
		return safe && hungry && !fight;
	}

	/** A hostile in melee reach and in sight: the melee shield's own trigger (unchanged by O98o). */
	static boolean melee(LocalPlayer p, Threats t) {
		return t.hostile() != null && !(t.hostile() instanceof net.minecraft.world.entity.monster.Phantom) && t.hostileDist() <= MELEE_RADIUS
				&& p.hasLineOfSight(t.hostile());
	}

	/**
	 * 45L death 17:16:03 (watch 105b2135): at 6 HP the shield yielded to the retreat and the next phantom in reach started it again, 7 times in 3
	 * minutes; she neither got away nor blocked (4 of 14 hits), and no meal got in. A running retreat yields to the shield only for a projectile.
	 */
	static boolean shieldMayPreemptRetreat(boolean projectileIncoming) {
		return projectileIncoming;
	}

	/**
	 * 45L 17:16 (she drifted 30 m from the shelter while go_home waited): the retreat leans toward home or a near owner, not when that way is
	 * behind the mob; at night with a phantom on her it heads for home outright (cover is the only answer to a dive). Unit vectors in, unit out.
	 */
	static Vec3 retreatWay(Vec3 away, Vec3 toward, boolean phantomAtNight) {
		if (toward == null) return away;
		if (phantomAtNight) return toward;
		if (toward.dot(away) < -0.3) return away; // home lies past the mob
		return away.add(toward).normalize();
	}

	/**
	 * O98o (2): the active place_block if it seals an opening this archer sees her through: under 1.5 s old, not finished, and the
	 * line from the archer's eye to hers crosses its cell or the cell above or below it. Else null.
	 */
	static Interact.PlaceBlock sealingAgainst(LocalPlayer p, Entity archer) {
		if (!(Arbiter.active instanceof Interact.PlaceBlock pb) || pb.status != null || System.nanoTime() - pb.startNs > SEAL_YIELD_NS) return null;
		return sealsLine(pb.pos, archer.getEyePosition(), p.getEyePosition()) ? pb : null;
	}

	/**
	 * O98o (2): the line from the archer's eye to hers crosses the cell or its vertical neighbour (a22's site: the aim line crossed
	 * only the head cell, so the feet cell, sealed first, qualifies through the cell above it; a cell beside the opening does not).
	 */
	static boolean sealsLine(BlockPos cell, Vec3 archerEye, Vec3 eye) {
		for (BlockPos c : new BlockPos[] {cell.below(), cell, cell.above()}) {
			AABB box = new AABB(c);
			if (box.contains(archerEye) || box.contains(eye) || box.clip(archerEye, eye).isPresent()) return true;
		}
		return false;
	}

	/** O98o (2), every tick before pick: this tick's sealing place, and one yield_seal event per place once its yield is over. */
	private static void sealYield(LocalPlayer p, Threats t) {
		boolean guarded = guardedFight();
		sealing = shieldUsable(p) && !guarded && t.incoming() == null && t.archer() != null && !melee(p, t) && !retreatFirst(p, t)
				? sealingAgainst(p, t.archer()) : null;
		if (sealing != null) keepDown(sealing, t.archer(), false);
		else if (yieldId != null && !(Arbiter.active instanceof Interact.PlaceBlock pb && pb.id.equals(yieldId) && pb.status == null
				&& System.nanoTime() - pb.startNs <= SEAL_YIELD_NS)) flushYield();
	}

	/** The drawn_bow shield is kept down this tick (or a raised one just ended) for this sealing place. */
	static void keepDown(Interact.PlaceBlock pb, Entity archer, boolean endedShield) {
		if (!pb.id.equals(yieldId)) {
			flushYield();
			yieldId = pb.id;
			yieldCell = pb.pos;
			yieldArcher = archer.getId();
			yieldTicks = 0;
			yieldEndedShield = false;
		}
		if (yieldLastTick != EveBody.clientTick) yieldTicks++; // once a tick (sealYield and Shield.tick can both call it)
		yieldLastTick = EveBody.clientTick;
		yieldEndedShield |= endedShield;
	}

	/** event reflex {shield, phase yield_seal, action_id, cell, archer_id, held_ms, ended_shield} (combat's review, O98o). */
	private static void flushYield() {
		if (yieldId == null) return;
		JsonObject d = new JsonObject(), c = new JsonObject();
		d.addProperty("reflex", "shield");
		d.addProperty("phase", "yield_seal");
		d.addProperty("action_id", yieldId);
		c.addProperty("x", yieldCell.getX());
		c.addProperty("y", yieldCell.getY());
		c.addProperty("z", yieldCell.getZ());
		d.add("cell", c);
		d.addProperty("archer_id", yieldArcher);
		d.addProperty("held_ms", yieldTicks * 50L);
		d.addProperty("ended_shield", yieldEndedShield);
		Telemetry.event("reflex", d);
		EveBody.LOG.info("eve reflex shield yield_seal {}", d);
		yieldId = null;
	}

	static long corneredNs;

	/** At the health floor with a hostile near, unless a retreat just ended cornered (then shield instead, for 3 s). */
	static boolean retreatFirst(LocalPlayer p, Threats t) {
		return p.getHealth() <= HEALTH_FLOOR && t.hostile() != null && t.hostileDist() <= RETREAT_TRIGGER && System.nanoTime() - corneredNs > 3_000_000_000L
				&& !handBackActive() // O102d (3): the fight's turn
				&& !floorShield(p, t); // S28: a shooter nearest: the shield's
	}

	/**
	 * S28 (live death 13:26:56: at 4 HP a retreat from the skeleton at 1.3 m and an arrow's shield pre-empted each other, so no
	 * shield was up 5 ticks before the fatal arrow): at the health floor with a shooter the nearest hostile, in sight within
	 * ARCHER_HOLD, and a shield to hold, the shield owns the controls (and backs her away, Shield.tick); a melee mob nearest still
	 * means retreat. Not while the floor hand-back gives the fight its turn.
	 */
	static boolean floorShield(LocalPlayer p, Threats t) {
		Entity h = t.hostile();
		boolean inHand = shieldUsable(p) || p.isUsingItem() && p.getOffhandItem().get(DataComponents.BLOCKS_ATTACKS) != null;
		if (h == null || p.getHealth() > HEALTH_FLOOR || !inHand || handBackActive()) return false;
		// S28-2 (combat's split, 15:14:33: the fatal hit was a phantom she ran from): a flyer is never outrun; shield it like a shooter.
		// Not a phantom (watch R1 on fb07484c: the floor shield still rose on phantoms, no retreat): it dives, the shield faces forward, the
		// answer is cover and home (phantom_cover, the retreat toward home)
		boolean raw = floorShield(p.getHealth(), ranged(h) || floorShieldFlyer(h), t.hostileDist(), p.hasLineOfSight(h), true);
		long now = System.nanoTime();
		if (!latchOpen(now, floorLatchNs)) {
			floorLatchShield = raw;
			floorLatchNs = now + FLOOR_LATCH_NS;
		}
		return floorLatchShield;
	}

	/**
	 * S28 (second live death, 15:14:33: at the floor the nearest hostile flipped skeleton / phantom / zombie and the shield and the
	 * retreat took the controls from each other 9 times in 9 s): the floor choice, shield or retreat, holds for 2 s once made.
	 */
	static final long FLOOR_LATCH_NS = 2_000_000_000L;
	private static long floorLatchNs;
	private static boolean floorLatchShield;

	static boolean latchOpen(long now, long until) {
		return now < until;
	}

	static boolean floorShield(float hp, boolean nearestRanged, double dist, boolean seen, boolean shieldInHand) {
		return hp <= HEALTH_FLOOR && nearestRanged && dist <= ARCHER_HOLD && seen && shieldInHand;
	}

	/** The flyers the floor shield is for: every flyer but a phantom. */
	static boolean floorShieldFlyer(Entity e) {
		return flyer(e) && !(e instanceof net.minecraft.world.entity.monster.Phantom);
	}

	/** S28-2: hostiles she cannot outrun on foot. */
	static boolean flyer(Entity e) {
		return e instanceof net.minecraft.world.entity.monster.Phantom || e instanceof net.minecraft.world.entity.monster.Vex
				|| e instanceof net.minecraft.world.entity.monster.Blaze || e instanceof net.minecraft.world.entity.monster.Ghast
				|| e instanceof net.minecraft.world.entity.monster.breeze.Breeze;
	}

	/** S28: the shooter a raised shield stays up for: the drawing archer, else the nearest hostile if it is a shooter in sight within ARCHER_HOLD. */
	static Entity shooterNear(LocalPlayer p, Threats t) {
		if (t.archer() != null) return t.archer();
		Entity h = t.hostile();
		return h != null && ranged(h) && t.hostileDist() <= ARCHER_HOLD && p.hasLineOfSight(h) ? h : null;
	}

	/** A creeper primed within 12, or walking at her within 6: get out of the blast before it goes. */
	static boolean creeperThreat(LocalPlayer p, Threats t) {
		Creeper c = t.creeper();
		if (c == null) return false;
		boolean hissing = c.getSwelling(1f) > 0 || c.isIgnited();
		long now = EveBody.clientTick;
		if (c.getId() != creeperId || now - creeperTick > 2) { // a new creeper, or not watched for a while: a fresh history
			creeperId = c.getId();
			java.util.Arrays.fill(creeperHist, c.position());
		}
		creeperTick = now;
		creeperHist[(int) (now % creeperHist.length)] = c.position();
		Vec3 ago = creeperHist[(int) ((now + 1) % creeperHist.length)]; // 10 ticks back
		Vec3 lastTick = creeperHist[(int) ((now + creeperHist.length - 1) % creeperHist.length)];
		if (creeperClaimed(c.getId(), claimedCreeperId, claimedUntilTick, now)) return hissing && t.creeperDist() <= 12;
		Vec3 v = p.getDeltaMovement();
		double hx = c.getX() - p.getX(), hz = c.getZ() - p.getZ(), len = Math.hypot(hx, hz);
		boolean walkingAtIt = len > 1e-6 && (v.x * hx + v.z * hz) / len > 0.05;
		double advance = wakeAdvance(creeperAdvance(ago.x, ago.z, c.getX(), c.getZ(), p.getX(), p.getZ()),
				creeperAdvance(lastTick.x, lastTick.z, c.getX(), c.getZ(), p.getX(), p.getZ()), t.creeperDist());
		return creeperFlees(hissing, t.creeperDist(), advance,
				c.getId() == safeCreeperId ? now - safeCreeperTick : Long.MAX_VALUE, walkingAtIt);
	}

	/** O308: the ring non-fight walks keep around a creeper in sight (it primes at 3; a drop is not worth the 1.5 m margin). */
	static final double CREEPER_RING = 4.5;

	/** O308: a straight walk to (gx, gz) would end in or cross the ring. Already inside, walking out of it is allowed. */
	static boolean ringBlocks(double hx, double hz, double gx, double gz, double cx, double cz) {
		double goal = Math.hypot(gx - cx, gz - cz), her = Math.hypot(hx - cx, hz - cz);
		if (goal < CREEPER_RING) return true;
		if (her < CREEPER_RING) return false; // goal outside, she inside: out
		double dx = gx - hx, dz = gz - hz, len2 = dx * dx + dz * dz;
		double t = len2 < 1e-9 ? 0 : Mth.clamp(((cx - hx) * dx + (cz - hz) * dz) / len2, 0, 1);
		return Math.hypot(hx + t * dx - cx, hz + t * dz - cz) < CREEPER_RING;
	}

	/**
	 * O308: a creeper, not claimed by a fight, whose ring this walk would enter. O314 (a135: an unseen idle creeper woke with her 3.49 m off for a
	 * drop, flee at 3.15): a drop is not worth it even with a wall between them; seenOnly is for walks she was ordered on.
	 */
	static boolean creeperRingBlocks(LocalPlayer p, Vec3 goal, boolean seenOnly) {
		for (Creeper c : p.level().getEntitiesOfClass(Creeper.class, p.getBoundingBox().inflate(16), Creeper::isAlive))
			if (!creeperClaimed(c.getId(), claimedCreeperId, claimedUntilTick, EveBody.clientTick) && (!seenOnly || p.hasLineOfSight(c))
					&& ringBlocks(p.getX(), p.getZ(), goal.x, goal.z, c.getX(), c.getZ())) return true;
		return false;
	}

	/**
	 * S105 (kickoff, 45L death 17:16): cover from phantoms. Due under open sky after a phantom hit in the last 5 s (any health), or at <= 14 HP with
	 * a phantom within 16 m.
	 */
	static boolean phantomCoverDue(boolean openSky, boolean phantomHitLast5s, float hp, boolean phantomNear) {
		return openSky && (phantomHitLast5s || hp <= 14 && phantomNear);
	}

	/** S105 arbiter fix (45L 17:12:26: a meal pre-empted by auto_fight while hurt): at <= 14 HP a meal is not broken off for an auto_fight. */
	static boolean autoFightMayInterruptMeal(float hp) {
		return hp > 14;
	}

	/** kickoff (a): how enclosed a cover cell is: each of the 8 wall cells beside her feet and head, and a low ceiling (head+1 best). */
	static int coverScore(int walls, int ceiling) {
		return walls + (ceiling == 2 ? 4 : ceiling == 3 ? 2 : 0);
	}

	static net.minecraft.world.entity.monster.Phantom phantomNear(LocalPlayer p, double r) {
		var l = p.level().getEntitiesOfClass(net.minecraft.world.entity.monster.Phantom.class, p.getBoundingBox().inflate(r), e -> e.isAlive());
		return l.isEmpty() ? null : l.get(0);
	}

	private static long coverRetryNs;

	/** API 5: the fight's claim holds for 5 ticks after its last call. */
	static boolean creeperClaimed(int id, int claimedId, long untilTick, long now) {
		return id == claimedId && now <= untilTick;
	}

	static void claimCreeper(int id) {
		claimedCreeperId = id;
		claimedUntilTick = EveBody.clientTick + 5;
	}

	private static int claimedCreeperId = -1;
	private static long claimedUntilTick = -1;

	/** O314: within 5 a creeper's first step at her (it walks 0.14 a tick) counts at once, without the 10 ticks for 0.3 (a135: 0.45 s, flee at 3.15). */
	static double wakeAdvance(double advance, double lastTickStep, double dist) {
		return dist <= 5 && lastTickStep > 0.05 ? Math.max(advance, 0.31) : advance;
	}

	/** How far the creeper itself came toward her over the window: its own move, projected on the line to her (her own walk does not count). */
	static double creeperAdvance(double agoX, double agoZ, double nowX, double nowZ, double herX, double herZ) {
		double hx = herX - nowX, hz = herZ - nowZ, len = Math.hypot(hx, hz);
		return len < 1e-6 ? 0 : ((nowX - agoX) * hx + (nowZ - agoZ) * hz) / len;
	}

	/**
	 * O272/O280 (S96 lane C x_creeper: at 7 m, 315 flees and 0 swings; at 4 m, 10 of 10 exploded): run from a creeper that is primed, or
	 * advancing within 6, or within 5 even in the 3 s after a safe flee. It walks 2.73 m/s (a116), so a flat 3 s lockout from 9+ m would
	 * let it reach its 3 m fuse range before she moved; the 5 keeps a 2 m margin. Further off she keeps fighting. O294 (lane C: 26-28
	 * re-fires at 4.7-5 m, 0.85 s apart, her fight walked her back): the 5 does not fire inside the 3 s while she walks at it herself.
	 */
	static boolean creeperFlees(boolean hissing, double dist, double advance, long ticksSinceSafe, boolean walkingAtIt) {
		return hissing && dist <= 12 || advance > 0.3 && (dist <= 6 && ticksSinceSafe >= 60 || dist <= 5 && !walkingAtIt);
	}

	private static int creeperId = -1, safeCreeperId = -1;
	private static long creeperTick = -100, safeCreeperTick;
	private static final Vec3[] creeperHist = new Vec3[11];

	/** Blast-safe distance: vanilla damage reaches 2 x power (power 3, charged 6) blocks; a margin on top. */
	static double creeperSafe(Creeper c) {
		return c.isPowered() ? 14 : 9; // O280: far enough that a walking creeper leaves her a window to fight in
	}

	static boolean hungry(LocalPlayer p) {
		return hungry(p.getHealth(), p.getFoodData().getFoodLevel());
	}

	/**
	 * Hungry, or hurt below regeneration food. O227 (live 18:44-19:2x: HP sat at 15 for 40 min with bread in the pack; food 17 is
	 * below vanilla's regen threshold 18 and HP 15 above the hurt rule's 14): any missing health with food <= 17 is a top-up, still
	 * only when safe and not over a fight (eatAllowed).
	 */
	static boolean hungry(float hp, int food) {
		return food <= EAT_FOOD || (hp <= EAT_HURT_HEALTH && food < EAT_HURT_FOOD) || (hp < 20f && food < EAT_HURT_FOOD);
	}

	/** Best hotbar food a reflex may eat, or -1. ponytail: hotbar only, like every hand skill until CP-38. */
	static int foodSlot(LocalPlayer p) {
		int best = -1, bestN = 0;
		for (int i = 0; i < 9; i++) {
			ItemStack s = p.getInventory().getItem(i);
			FoodProperties f = s.get(DataComponents.FOOD);
			if (f == null || NO_EAT.contains(BuiltInRegistries.ITEM.getKey(s.getItem()).toString()) || !p.canEat(f.canAlwaysEat())) continue;
			if (f.nutrition() > bestN) {
				bestN = f.nutrition();
				best = i;
			}
		}
		return best;
	}

	/**
	 * A shield in the off hand that a use click will raise: no cooldown, and the main hand has no use of its own or freeHands can
	 * select a hotbar slot without one at the reflex's start. S28: a bow or food in the main hand made this false before the
	 * reflex could free the hand, so pick() chose Dodge (or nothing) and the shield never rose; freeHands was only reached by a
	 * Shield already picked.
	 */
	static boolean shieldUsable(LocalPlayer p) {
		ItemStack off = p.getOffhandItem();
		if (off.get(DataComponents.BLOCKS_ATTACKS) == null || p.getCooldowns().isOnCooldown(off)) return false;
		if (handFree(p.getMainHandItem())) return true;
		if (guardedFight()) return false; // a provider fight's bow stays in its hand, as before (fix A: the fight owns it)
		for (int i = 0; i < 9; i++) if (handFree(p.getInventory().getItem(i))) return true;
		return false;
	}

	/** Nothing a use click would use instead of the off-hand shield (no bow, crossbow, food, ...). */
	static boolean handFree(ItemStack s) {
		return s.isEmpty() || s.getUseAnimation() == ItemUseAnimation.NONE && s.get(DataComponents.FOOD) == null;
	}

	/** Footing at {@code x,z} she can walk onto without harm: a 1-block step up, level ground, or a drop of at most 2; never lava. */
	static boolean safeStep(LocalPlayer p, double x, double z) {
		return unsafeWhy(p, x, z) == null;
	}

	/** Why {@link #safeStep} refuses {@code x,z} (hot, obstacle, no_head_room, drop), or null when it is safe. */
	static String unsafeWhy(LocalPlayer p, double x, double z) {
		var level = p.level();
		BlockPos feet = BlockPos.containing(x, p.getY() + 0.01, z);
		for (int dy = 2; dy >= -3; dy--) {
			BlockPos b = feet.above(dy);
			if (level.getFluidState(b).is(net.minecraft.tags.FluidTags.LAVA) || level.getBlockState(b).is(Blocks.FIRE)) return "hot";
		}
		if (solid(level, feet)) return !solid(level, feet.above()) && !solid(level, feet.above(2)) ? null : "obstacle"; // a step: jump it
		if (solid(level, feet.above())) return "no_head_room";
		for (int dy = 1; dy <= 3; dy++) if (solid(level, feet.below(dy))) return null;
		return "drop";
	}

	private static boolean solid(net.minecraft.world.level.Level level, BlockPos b) {
		return !level.getBlockState(b).getCollisionShape(level, b).isEmpty();
	}

	/**
	 * Drop of 4+ blocks (a fall that costs health) within half a block of her feet, or the rim of lava or fire (CP-30: sneak keeps
	 * her out of a pool sunk into the floor; lava level with the floor is not stopped by sneaking, the hazard reflex covers that).
	 */
	static boolean atEdge(LocalPlayer p) {
		var level = p.level();
		for (int i = 0; i < 8; i++) {
			double a = i * Math.PI / 4, x = p.getX() + Math.cos(a) * 0.55, z = p.getZ() + Math.sin(a) * 0.55;
			BlockPos top = BlockPos.containing(x, p.getY() - 0.01, z);
			// lava/fire in the floor cell or at her feet level; under the floor cell only when that cell is not solid (lava below
			// solid ground is not a rim: lane B a2-a3, lava falling under the sky arena kept her sneaking on intact floor)
			boolean openFloor = level.getBlockState(top).getCollisionShape(level, top).isEmpty();
			for (BlockPos h : openFloor ? new BlockPos[] {top, top.above(), top.below()} : new BlockPos[] {top, top.above()})
				if (level.getFluidState(h).is(net.minecraft.tags.FluidTags.LAVA) || level.getBlockState(h).is(net.minecraft.tags.BlockTags.FIRE)) return true;
			boolean floor = false;
			for (int dy = 0; dy < 4 && !floor; dy++) {
				BlockPos b = top.below(dy);
				floor = !level.getBlockState(b).getCollisionShape(level, b).isEmpty() || !level.getFluidState(b).isEmpty();
			}
			if (!floor) return true;
		}
		return false;
	}

	/** Edge-sneak modifier for the arbiter: true holds sneak this tick. Vanilla's own sneak back-off then keeps her on the edge. */
	static boolean edgeSneak(LocalPlayer p) {
		// never inside a hazard: sneaking in lava or water slows the way out (CP-30 attempt 28: a step-in became a death)
		boolean now = p.onGround() && !p.isInWater() && !p.isInLava() && !(active instanceof Hazard) && atEdge(p);
		if (now) {
			edgeOffTicks = 0;
			if (!edge) {
				edge = true;
				triggers++;
				JsonObject d = new JsonObject();
				d.addProperty("reflex", "edge_sneak");
				d.addProperty("phase", "start");
				d.add("pos", Telemetry.pos(p.position()));
				Telemetry.event("reflex", d);
			}
		} else if (edge && ++edgeOffTicks > 10) {
			edge = false;
			JsonObject d = new JsonObject();
			d.addProperty("reflex", "edge_sneak");
			d.addProperty("phase", "end");
			d.addProperty("result", "clear");
			d.add("pos", Telemetry.pos(p.position()));
			Telemetry.event("reflex", d);
		}
		return edge;
	}

	/** Fall rescue without a water bucket in the hotbar: detection only, reported once per fall (`no_qualified_item`). */
	/** Ground under her feet while she falls (the first solid block), or null when there is none within 64 or fluid comes first. */
	static BlockPos ground(LocalPlayer p) {
		var level = p.level();
		BlockPos b = p.blockPosition();
		for (int depth = 0; depth < 64; depth++, b = b.below()) {
			if (!level.getFluidState(b).isEmpty()) return null; // water or lava breaks the fall (or is not a fall problem)
			if (!level.getBlockState(b).getCollisionShape(level, b).isEmpty()) return b;
		}
		return null;
	}

	/** Total fall distance at landing if she is falling now (0 when not falling, or landing in fluid). */
	static double landingFall(LocalPlayer p) {
		if (p.onGround() || p.isInWater() || p.getAbilities().flying || p.getDeltaMovement().y >= 0) return 0;
		BlockPos g = ground(p);
		return g == null ? 0 : p.fallDistance + (p.getY() - (g.getY() + 1));
	}

	private static void fallWatch(LocalPlayer p) {
		if (p.onGround() || p.isInWater() || p.getAbilities().flying) {
			fallReported = false;
			return;
		}
		if (fallReported || active instanceof FallRescue) return;
		double landing = landingFall(p);
		if (landing <= 3.5 || Hands.hotbarSlot(p, "minecraft:water_bucket") >= 0) return; // with a bucket, FallRescue acts instead
		BlockPos b = ground(p);
		fallReported = true;
		triggers++;
		JsonObject d = new JsonObject();
		d.addProperty("reflex", "fall_rescue");
		d.addProperty("phase", "detected");
		d.addProperty("result", "no_qualified_item");
		d.addProperty("predicted_fall", Skills.r2(landing));
		d.addProperty("predicted_damage", Math.max(0, (int) Math.ceil(landing - 3)));
		d.addProperty("height_above_ground", Skills.r2(p.getY() - (b.getY() + 1)));
		Telemetry.event("reflex", d);
		EveBody.LOG.info("eve reflex fall_rescue detected {}", d);
	}

	/** World direction {@code d} (horizontal) as movement keys for her current yaw. */
	static Input keysFor(LocalPlayer p, Vec3 d, boolean jump, boolean sprint) {
		double yaw = Math.toRadians(p.getYRot()), sin = Math.sin(yaw), cos = Math.cos(yaw);
		double fwd = -d.x * sin + d.z * cos, left = d.x * cos + d.z * sin;
		return new Input(fwd > 0.3, fwd < -0.3, left > 0.3, left < -0.3, jump, false, sprint && fwd > 0.3);
	}

	// ---- the reflexes ----

	/**
	 * A fall that will cost health, with a water bucket in the hotbar: look straight down, and once the landing block is in reach
	 * (vanilla's own pick from the eye, so the server's reach check holds) empty the bucket on its top face; after she lands in the
	 * water, scoop it back up and give the previous slot back. Not in a dimension where water evaporates.
	 */
	static final class FallRescue extends R {
		final int slot, prevSlot;
		final String nonce = java.util.UUID.randomUUID().toString();
		final double fall;
		boolean placed, scooped;
		BlockPos placedAt;
		int after;
		long placedNs;

		FallRescue(LocalPlayer p, int slot, double landing) {
			super("fall_rescue", 5);
			this.slot = slot;
			prevSlot = p.getInventory().getSelectedSlot();
			fall = landing;
			trigger.addProperty("predicted_fall", Skills.r2(landing));
			trigger.addProperty("predicted_damage", (int) Math.ceil(landing - 3));
			trigger.addProperty("height_above_ground", Skills.r2(p.getY() - (ground(p).getY() + 1)));
			trigger.addProperty("item", "minecraft:water_bucket");
		}

		@Override
		Input tick(Minecraft mc, LocalPlayer p, Threats t) {
			p.getInventory().setSelectedSlot(slot);
			p.setXRot(90f); // straight down: one flick, like a player's
			if (!placed) {
				if (p.onGround() || p.isInWater()) {
					result = p.isInWater() ? "landed_in_water" : "missed";
					return Input.EMPTY;
				}
				if (ticks == 0) return Input.EMPTY; // the down-look reaches the server with this tick's movement packet
				HitResult h = p.pick(p.blockInteractionRange(), 1f, false);
				if (h instanceof BlockHitResult bh && h.getType() == HitResult.Type.BLOCK && bh.getDirection() == Direction.UP
						&& Hands.blockedByScreen(mc) == null) {
					placedAt = bh.getBlockPos().above();
					signal("rescue_place", null); // before the use packet, on the same connection (O20)
					mc.hitResult = bh;
					Hands.use(mc);
					placed = true;
					placedNs = System.nanoTime();
					detail.addProperty("placed_height", Skills.r2(p.getY() - placedAt.getY()));
					detail.add("placed_at", Telemetry.pos(Vec3.atCenterOf(placedAt)));
				}
				return Input.EMPTY;
			}
			if (System.nanoTime() - placedNs > 9_000_000_000L) { // O20: the bucket comes back within 10 s or the reflex gives up
				result = "rescued_water_left";
				detail.addProperty("water_left", !p.level().getFluidState(placedAt).isEmpty());
				return Input.EMPTY;
			}
			if (!scooped) {
				if (!(p.onGround() || p.isInWater())) return Input.EMPTY;
				if (++after < 3) return Input.EMPTY; // let the landing tick reach the server first
				mc.hitResult = BlockHitResult.miss(p.getEyePosition(), Direction.UP, p.blockPosition()); // the bucket raycasts water itself
				signal("rescue_scoop", null);
				Hands.use(mc);
				scooped = true;
				after = 0;
				return Input.EMPTY;
			}
			if (Hands.hotbarSlot(p, "minecraft:water_bucket") == slot || ++after > 20) {
				result = Hands.hotbarSlot(p, "minecraft:water_bucket") == slot ? "rescued" : "rescued_water_left";
				detail.addProperty("water_left", !p.level().getFluidState(placedAt).isEmpty());
			}
			return Input.EMPTY;
		}

		@Override
		void release(Minecraft mc, LocalPlayer p) {
			if (prevSlot != slot) p.getInventory().setSelectedSlot(prevSlot);
			if (placed) signal("rescue_end", result != null ? result : "cancelled");
		}

		/** eve:rescue to sense, only when the server registered the channel (no sense, no signal; the rescue still runs). */
		private void signal(String op, String res) {
			if (!net.fabricmc.fabric.api.client.networking.v1.ClientPlayNetworking.canSend(Payloads.Rescue.TYPE)) return;
			JsonObject o = new JsonObject();
			o.addProperty("op", op);
			o.addProperty("nonce", nonce);
			JsonObject pos = new JsonObject(); // integer block coordinates: the block the water goes into
			pos.addProperty("x", placedAt.getX());
			pos.addProperty("y", placedAt.getY());
			pos.addProperty("z", placedAt.getZ());
			o.add("pos", pos);
			if (op.equals("rescue_place")) {
				o.addProperty("item", "minecraft:water_bucket");
				o.addProperty("fall", Skills.r2(fall));
				o.addProperty("tick", EveBody.clientTick);
			}
			if (res != null) o.addProperty("result", res);
			net.fabricmc.fabric.api.client.networking.v1.ClientPlayNetworking.send(new Payloads.Rescue(Registry.GSON.toJson(o)));
		}
	}

	/** Raise the off-hand shield toward an incoming projectile or a hostile in melee reach; lower it 10 ticks after the threat. */
	static final class Shield extends R {
		int quiet;
		boolean raised;
		int firstBlockingTick = -1;
		int pressTick = -100, presses;
		boolean handToAutoFight; // S96: an auto_fight takes over: its guard adopts the raised shield
		final Entity archer; // the drawing archer it was raised for, if any (O98o (2): a seal against it ends the shield)

		Shield(LocalPlayer p, Threats t) {
			super("shield", SHIELD_PRIORITY);
			if (Hands.shieldInUse(p)) { // O174: already raised (the fight's guard): keep it, no press (a re-press restarts the block delay)
				raised = true;
				presses = 1;
				pressTick = 0;
				Hands.shieldHandedBack = false; // taken over again, still up
			}
			archer = t.archer();
			trigger.addProperty("cause", t.incoming() != null ? "projectile" : t.archer() != null ? "drawn_bow"
					: floorShield(p, t) ? "floor_shooter" : "melee"); // watch R1: the floor shield was logged as "melee"
			Entity e = t.incoming() != null ? t.incoming() : t.archer() != null ? t.archer() : t.hostile();
			trigger.addProperty("entity", BuiltInRegistries.ENTITY_TYPE.getKey(e.getType()).toString());
			trigger.addProperty("entity_id", e.getId());
			if (t.incoming() != null) trigger.addProperty("impact_ticks", t.impactTicks());
			else trigger.addProperty("distance", Skills.r2(e.distanceTo(p)));
		}

		@Override
		Input tick(Minecraft mc, LocalPlayer p, Threats t) {
			// O98o (2): a place sealing the opening this archer sees her through ends the shield, whatever holds it up (a skeleton
			// parked inside the 4.5 hold below kept it raised for good, CP-10 a22); never against an arrow in flight or a mob in reach
			Entity a = t.archer() != null ? t.archer() : archer;
			Interact.PlaceBlock seal = t.incoming() == null && !melee(p, t) && a != null && a.isAlive() ? sealingAgainst(p, a) : null;
			if (seal != null) {
				keepDown(seal, a, true);
				result = "yield_seal";
				return Input.EMPTY;
			}
			// O102d: under a guarding provider (paused, its claim judged as of the pause) only the projectile is the shield's; the
			// drawn bow and melee in reach are the fight's (c_rush #6: an arrow's shield held 19.5 s on the zombie the fight was on)
			boolean guarded = guardedFight(), floor = floorShield(p, t);
			Entity shooter = shooterNear(p, t);
			// S28: held while a shooter is near in sight, between its draws too (the 13:26 death: three drawn_bow shields ended clear
			// between shots, and the next arrows found it down or raised under 5 ticks); at the floor the shooter is the shield's even
			// over a guarded fight (one owner, no cycle)
			Entity threat = t.incoming() != null ? t.incoming() : floor ? t.hostile() : guarded ? null : shooter != null ? shooter
					: t.hostile() != null && t.hostileDist() <= MELEE_RADIUS + 1 ? t.hostile() : null;
			if (threat == null) {
				if (++quiet > 10) result = "clear";
			} else {
				quiet = 0;
				// O98d (dev a97: 3 arrows landed with blocking=True, use_ticks 23-25): turning to the arrow's own position swings her
				// view off the shooter in its last ticks, when it is beside her; hold it toward the archer, or back along the arrow's path
				// S28 (15:13:18 / :37: two arrows hit at full damage while blocking, with several archers): an arrow in flight from
				// someone other than the drawing archer is met along its own path, not with the shield toward the drawing one
				Projectile inc = t.incoming();
				boolean otherShooter = inc != null && (t.archer() == null || inc.getOwner() != t.archer());
				Vec3 at = otherShooter ? alongPath(p.getEyePosition(), inc.position(), inc.getDeltaMovement())
						: t.archer() != null ? t.archer().getEyePosition() : floor || threat == shooter && shooter != null ? threat.getEyePosition()
						: threat instanceof Projectile pr ? alongPath(p.getEyePosition(), pr.position(), pr.getDeltaMovement()) : threat.getEyePosition();
				float[] yp = Skills.yawPitch(p.getEyePosition(), at);
				Skills.turn(p, yp[0], yp[1], 60f);
			}
			if (t.incoming() == null && retreatFirst(p, t)) { // melee shield at the health floor: hand over to retreat
				result = "yield_retreat";
				return Input.EMPTY;
			}
			if (!shieldUsable(p) && !p.isUsingItem()) {
				result = "no_shield";
				return Input.EMPTY;
			}
			String step = pressStep(raised, p.isUsingItem(), ticks - pressTick, presses);
			if ("no_block".equals(step)) { // the shield never came up: hand the controls back rather than stand there unshielded
				result = "no_block";
				return Input.EMPTY;
			}
			if (step != null && Hands.blockedByScreen(mc) == null) {
				mc.hitResult = BlockHitResult.miss(p.getEyePosition(), Direction.UP, p.blockPosition()); // never click a block with it
				Hands.use(mc);
				mc.options.keyUse.setDown(true);
				raised = true;
				pressTick = ticks;
				presses++;
			}
			if (firstBlockingTick < 0 && p.isBlocking()) firstBlockingTick = ticks;
			if (floor) { // S28: shield up toward the shooter, walk backwards away from it while the footing behind is safe
				if (!detail.has("floor_walk")) detail.addProperty("floor_walk", true);
				Vec3 back = p.getViewVector(1f).multiply(-1, 0, -1);
				if (back.lengthSqr() > 1e-6) {
					back = back.normalize();
					if (safeStep(p, p.getX() + back.x, p.getZ() + back.z) && safeStep(p, p.getX() + back.x * 2, p.getZ() + back.z * 2))
						return new Input(false, true, false, false, false, false, false);
				}
			}
			return Input.EMPTY;
		}

		/**
		 * O102d (c_rush #6: one press, then 391 ticks with blocking_from_tick -1 and every hit using:false): "press" to raise it, and
		 * again while she is not using it 4+ ticks after the last press, 3 presses at most; then "no_block". Null: nothing to do.
		 */
		static String pressStep(boolean raised, boolean using, int sincePress, int presses) {
			if (!raised) return "press";
			if (using || sincePress < 4) return null;
			return presses >= 3 ? "no_block" : "press";
		}

		@Override
		void release(Minecraft mc, LocalPlayer p) {
			// O174: the guarding fight resumes next: leave the raised shield up for it (its guard() takes it without a re-press)
			if (Hands.shieldInUse(p) && (isFight(Arbiter.active) && "clear".equals(result) || handToAutoFight)) {
				detail.addProperty("shield_handed_back_raised", true);
				Hands.shieldHandedBack = true;
				Hands.handedBackTicks = 0;
			}
			else {
				mc.options.keyUse.setDown(false);
				if (p.isUsingItem()) mc.gameMode.releaseUsingItem(p);
			}
			detail.addProperty("blocking_from_tick", firstBlockingTick);
			detail.addProperty("presses", presses);
		}
	}

	/** CP-30: which hazard holds her now, or null: in lava or fire, head in a solid block, or out of air under water. */
	static String hazard(LocalPlayer p) {
		if (p.isInLava()) return "lava";
		if (inFire(p)) return "fire";
		if (p.isInWall()) return "suffocation";
		if (p.isEyeInFluid(net.minecraft.tags.FluidTags.WATER) && p.getAirSupply() <= AIR_LIMIT) return "drowning";
		// still burning after lava/fire (vanilla: up to 15 s, 1 HP a second): put it out in water if there is some near
		if (p.isOnFire() && !p.isInWater() && !p.hasEffect(net.minecraft.world.effect.MobEffects.FIRE_RESISTANCE) && System.nanoTime() > burnGiveUpNs
				&& nearestWater(p) != null) return "burning";
		return null;
	}

	/** The nearest water block within 8 (feet level or one below) that she can walk into, or null. */
	static BlockPos nearestWater(LocalPlayer p) {
		BlockPos at = p.blockPosition(), best = null;
		double bestD = 99;
		for (int dx = -8; dx <= 8; dx++)
			for (int dz = -8; dz <= 8; dz++)
				for (int dy = -1; dy <= 0; dy++) {
					BlockPos b = at.offset(dx, dy, dz);
					double d = Math.hypot(dx, dz);
					if (d < bestD && p.level().getFluidState(b).is(net.minecraft.tags.FluidTags.WATER)) {
						bestD = d;
						best = b;
					}
				}
		return best;
	}

	/** After a burning escape gives up (no safe path to the water), not again for 5 s: no start/end flapping (lane B attempt 1). */
	static long burnGiveUpNs;

	static boolean inFire(LocalPlayer p) {
		for (BlockPos b : BlockPos.betweenClosed(BlockPos.containing(p.getBoundingBox().minX, p.getY(), p.getBoundingBox().minZ),
				BlockPos.containing(p.getBoundingBox().maxX, p.getY() + 1, p.getBoundingBox().maxZ)))
			if (p.level().getBlockState(b).is(net.minecraft.tags.BlockTags.FIRE)) return true;
		return false;
	}

	/**
	 * CP-30 hazard escape (priority 4, under the fall rescue): lava or fire -> the nearest direction with safe footing (never lava
	 * or fire, safeStep), sprint-jumping; suffocation -> an adjacent cell with room for her body; drowning -> swim up, toward the
	 * nearest open water surface if a ceiling is overhead. No emergency item is assumed (a bucket or potion is never relied on).
	 * Ends `escaped` after 10 clear ticks, `trapped` when no way out is found, `timeout` after 10 s.
	 */
	static final class Hazard extends R {
		String kind;
		Vec3 dir;
		int clear;

		Hazard(LocalPlayer p, String kind) {
			super("hazard", 4);
			this.kind = kind;
			trigger.addProperty("kind", kind);
			trigger.addProperty("health", p.getHealth());
			trigger.addProperty("air", p.getAirSupply());
			trigger.add("pos", Telemetry.pos(p.position()));
		}

		@Override
		Input tick(Minecraft mc, LocalPlayer p, Threats t) {
			String now = hazard(p);
			// out of the lava/fire is not clear of it: keep going until her box is a block from any (lane B a4: she stopped balanced
			// on the rim corner, slipped back in and burned to death)
			if (now == null && (kind.equals("lava") || kind.equals("fire")) && hotNear(p)) {
				clear = 0;
				if (ticks > 200) {
					result = "timeout";
					return Input.EMPTY;
				}
				return outOfFire(p);
			}
			if (now != null && !now.equals(kind) && now.equals("burning")) { // out of the lava/fire, still alight: on to water
				kind = "burning";
				detail.addProperty("then", "burning");
				dir = null;
			}
			if (now == null || (kind.equals("drowning") && !p.isEyeInFluid(net.minecraft.tags.FluidTags.WATER))) {
				if (++clear >= 10) {
					result = "escaped";
					detail.addProperty("health", p.getHealth());
					detail.addProperty("air", p.getAirSupply());
					detail.add("pos", Telemetry.pos(p.position()));
				}
				return kind.equals("drowning") ? new Input(false, false, false, false, true, false, false) : Input.EMPTY; // keep the head up
			}
			clear = 0;
			if (ticks > 200) {
				result = "timeout";
				return Input.EMPTY;
			}
			return switch (kind) {
				case "drowning" -> swimUp(p);
				case "suffocation" -> unstuck(p);
				case "burning" -> toWater(p);
				default -> outOfFire(p);
			};
		}

		Input outOfFire(LocalPlayer p) {
			if (dir == null || ticks % 10 == 0) dir = bestExit(p, false);
			if (dir == null) {
				result = "trapped";
				return Input.EMPTY;
			}
			float yaw = (float) Math.toDegrees(Mth.atan2(dir.z, dir.x)) - 90f;
			Skills.turn(p, yaw, 0f, 60f);
			return keysFor(p, dir, true, true);
		}

		Input toWater(LocalPlayer p) {
			BlockPos w = nearestWater(p);
			if (w == null) {
				result = "burning_no_water";
				burnGiveUpNs = System.nanoTime() + 5_000_000_000L;
				return Input.EMPTY;
			}
			Vec3 d = Vec3.atCenterOf(w).subtract(p.position()).multiply(1, 0, 1);
			if (d.lengthSqr() < 1e-4) return Input.EMPTY;
			d = d.normalize();
			if (!safeStep(p, p.getX() + d.x * 0.8, p.getZ() + d.z * 0.8)) { // never through lava, fire or off a drop for it
				result = "burning_no_safe_path";
				burnGiveUpNs = System.nanoTime() + 5_000_000_000L;
				return Input.EMPTY;
			}
			float yaw = (float) Math.toDegrees(Mth.atan2(d.z, d.x)) - 90f;
			Skills.turn(p, yaw, 0f, 60f);
			return keysFor(p, d, p.horizontalCollision, true);
		}

		Input unstuck(LocalPlayer p) {
			if (dir == null) dir = bestExit(p, true);
			if (dir == null) {
				result = "trapped";
				return Input.EMPTY;
			}
			return keysFor(p, dir, false, false);
		}

		/** The nearest of 8 directions (1..4 blocks out) whose footing is safe; for suffocation only the body's room matters. */
		Vec3 bestExit(LocalPlayer p, boolean roomOnly) {
			Vec3 best = null;
			double bestD = 9;
			for (int i : new int[] {0, 2, 4, 6, 1, 3, 5, 7}) { // cardinals first, so they win a tie: from a pool's middle the
				double a = i * Math.PI / 4;                     // diagonal reached the rim face ~40% later in lava (I14: SW, not W)
				Vec3 d = new Vec3(Math.cos(a), 0, Math.sin(a));
				for (int k = 1; k <= 4; k++) { // out of a pool up to 4 wide from its middle
					double x = p.getX() + d.x * k, z = p.getZ() + d.z * k;
					boolean ok = roomOnly ? room(p, BlockPos.containing(x, p.getY() + 0.01, z)) : safeStep(p, x, z) && !hot(p, x, z);
					if (ok && k < bestD) {
						bestD = k;
						best = d;
					}
				}
			}
			return best;
		}

		/** Lava or fire within a block of her bounding box, at feet level or the floor below it. */
		static boolean hotNear(LocalPlayer p) {
			AABB bb = p.getBoundingBox().inflate(1.0, 0, 1.0);
			for (BlockPos b : BlockPos.betweenClosed(BlockPos.containing(bb.minX, p.getY() - 1, bb.minZ), BlockPos.containing(bb.maxX, p.getY() + 0.5, bb.maxZ)))
				if (p.level().getFluidState(b).is(net.minecraft.tags.FluidTags.LAVA) || p.level().getBlockState(b).is(net.minecraft.tags.BlockTags.FIRE)) return true;
			return false;
		}

		static boolean room(LocalPlayer p, BlockPos feet) {
			var l = p.level();
			return l.getBlockState(feet).getCollisionShape(l, feet).isEmpty() && l.getBlockState(feet.above()).getCollisionShape(l, feet.above()).isEmpty();
		}

		static boolean hot(LocalPlayer p, double x, double z) {
			BlockPos b = BlockPos.containing(x, p.getY() + 0.01, z);
			for (BlockPos c : new BlockPos[] {b, b.below(), b.above()})
				if (p.level().getFluidState(c).is(net.minecraft.tags.FluidTags.LAVA) || p.level().getBlockState(c).is(net.minecraft.tags.BlockTags.FIRE)) return true;
			return false;
		}

		Input swimUp(LocalPlayer p) {
			var l = p.level();
			BlockPos head = BlockPos.containing(p.getEyePosition());
			if (dir == null || ticks % 10 == 0) {
				dir = null;
				if (!openAbove(l, head)) { // a ceiling: the nearest column within 6 that opens to air
					double bestD = 99;
					for (int dx = -6; dx <= 6; dx++)
						for (int dz = -6; dz <= 6; dz++) {
							BlockPos c = head.offset(dx, 0, dz);
							double d = Math.hypot(dx, dz);
							if (d < bestD && openAbove(l, c) && l.getBlockState(c).getCollisionShape(l, c).isEmpty()) {
								bestD = d;
								dir = new Vec3(dx, 0, dz).normalize();
							}
						}
					if (dir == null) {
						detail.addProperty("note", "no open water surface within 6");
						dir = Vec3.ZERO;
					}
				} else dir = Vec3.ZERO;
			}
			Skills.turn(p, p.getYRot(), -60f, 30f); // look up while swimming up
			return dir == Vec3.ZERO ? new Input(false, false, false, false, true, false, false) : keysFor(p, dir, true, false);
		}

		/** From {@code b} straight up: only water or air until air within 12 blocks. */
		static boolean openAbove(net.minecraft.world.level.Level l, BlockPos b) {
			for (int dy = 0; dy <= 12; dy++) {
				BlockPos c = b.above(dy);
				if (l.getBlockState(c).isAir()) return true;
				if (!l.getBlockState(c).getCollisionShape(l, c).isEmpty() || !l.getFluidState(c).is(net.minecraft.tags.FluidTags.WATER)) return false;
			}
			return false;
		}
	}

	static String dimId(LocalPlayer p) {
		return p.level().dimension().identifier().toString();
	}

	/** Enclosed and dark: no block light at feet or head and no sky light at feet (a room or cave: monsters spawn at block light 0). */
	static boolean enclosedDark(LocalPlayer p) {
		var l = p.level();
		BlockPos f = p.blockPosition();
		return l.getBrightness(net.minecraft.world.level.LightLayer.BLOCK, f) == 0 && l.getBrightness(net.minecraft.world.level.LightLayer.BLOCK, f.above()) == 0
				&& l.getBrightness(net.minecraft.world.level.LightLayer.SKY, f) == 0;
	}

	/** A lit standing spot: block light >= 8 at feet, or open sky by day (never the night sky: leaving a dark house into the night is worse). */
	static boolean lit(LocalPlayer p, BlockPos f) {
		var l = p.level();
		return l.getBrightness(net.minecraft.world.level.LightLayer.BLOCK, f) >= 8
				|| !l.isDarkOutside() && l.getBrightness(net.minecraft.world.level.LightLayer.SKY, f) > 0;
	}

	/** A bed within reach whose dimension rule lets her sleep and does not blow it up (CP-30 dimension policy). */
	static BlockPos bedInReach(LocalPlayer p) {
		var l = p.level();
		BlockPos best = null;
		double bd = 99;
		for (BlockPos b : BlockPos.betweenClosed(p.blockPosition().offset(-4, -1, -4), p.blockPosition().offset(4, 2, 4))) {
			if (!(l.getBlockState(b).getBlock() instanceof net.minecraft.world.level.block.BedBlock) || !p.isWithinBlockInteractionRange(b, 0)) continue;
			var rule = l.environmentAttributes().getValue(EnvironmentAttributes.BED_RULE, Vec3.atCenterOf(b));
			if (rule.destroyOnUse() || !rule.canSleep(l)) continue;
			double d = Vec3.atCenterOf(b).distanceTo(p.getEyePosition());
			if (d < bd) {
				bd = d;
				best = b.immutable();
			}
		}
		return best;
	}

	/**
	 * CP-30 darkness: idle in an enclosed dark spot. With a torch in the hotbar and a local envelope zone there whose
	 * `place` is free, place it on a free floor cell beside her (the body's own place_block, sneak-placed); otherwise walk to the nearest lit
	 * spot within 16. Ends torch_placed, moved_to_light, torch_failed, or no_way; not again for 10 s. ponytail: straight-line
	 * steering to the lit spot (a doorway in line); a maze needs walk_to.
	 */
	static final class Darkness extends R {
		Interact.PlaceBlock place;
		Vec3 target, start;
		int litTicks;
		final int prevSlot;

		Darkness(LocalPlayer p) {
			super("darkness", 1);
			start = p.position();
			prevSlot = p.getInventory().getSelectedSlot();
			trigger.add("pos", Telemetry.pos(p.position()));
			JsonObject z = Envelope.zoneAt(dimId(p), p.blockPosition());
			trigger.addProperty("zone", z == null ? null : z.get("id").getAsString());
			darkRetryNs = System.nanoTime() + 10_000_000_000L;
			boolean canPlace = z != null && z.has("place") && z.get("place").getAsString().equals("free") && Hands.hotbarSlot(p, "minecraft:torch") >= 0;
			if (canPlace) {
				for (Direction d : new Direction[] {Direction.NORTH, Direction.SOUTH, Direction.EAST, Direction.WEST}) {
					BlockPos c = p.blockPosition().relative(d);
					if (p.level().getBlockState(c).canBeReplaced() && !p.level().getBlockState(c.below()).getCollisionShape(p.level(), c.below()).isEmpty()) {
						JsonObject prm = new JsonObject(), pos = new JsonObject();
						pos.addProperty("x", c.getX());
						pos.addProperty("y", c.getY());
						pos.addProperty("z", c.getZ());
						prm.add("pos", pos);
						prm.addProperty("item", "minecraft:torch");
						place = new Interact.PlaceBlock("darkness-torch", prm, System.nanoTime(), System.nanoTime() + 5_000_000_000L);
						trigger.add("torch_at", Telemetry.pos(Vec3.atCenterOf(c)));
						break;
					}
				}
			}
			if (place == null) findLight(p);
		}

		void findLight(LocalPlayer p) {
			BlockPos me = p.blockPosition(), best = null;
			double bd = 99;
			for (BlockPos b : BlockPos.betweenClosed(me.offset(-16, -1, -16), me.offset(16, 1, 16))) {
				double d = Math.hypot(b.getX() - me.getX(), b.getZ() - me.getZ());
				if (d < bd && lit(p, b) && Hazard.room(p, b) && !p.level().getBlockState(b.below()).getCollisionShape(p.level(), b.below()).isEmpty()
						&& walkable(p, p.position(), Vec3.atBottomCenterOf(b))) {
					bd = d;
					best = b.immutable();
				}
			}
			target = best == null ? null : Vec3.atBottomCenterOf(best);
			detail.add("light_at", target == null ? null : Telemetry.pos(target));
		}

		@Override
		Input tick(Minecraft mc, LocalPlayer p, Threats t) {
			if (place != null) {
				Input in = place.tick(mc, p);
				place.ticks++;
				if (place.status == null) return withSubActKeys(in, place); // sneak-placed
				detail.addProperty("place_status", place.status);
				detail.addProperty("place_code", place.code);
				boolean ok = "ok".equals(place.status);
				place = null;
				if (ok) {
					result = "torch_placed";
					return Input.EMPTY;
				}
				findLight(p); // refused or obstructed: leave instead
			}
			if (target == null) {
				result = "no_way";
				return Input.EMPTY;
			}
			litTicks = lit(p, p.blockPosition()) && p.position().distanceTo(start) >= 1 ? litTicks + 1 : 0;
			if (litTicks >= 5) {
				result = "moved_to_light";
				detail.add("pos", Telemetry.pos(p.position()));
				return Input.EMPTY;
			}
			if (ticks > 200) {
				result = "no_way";
				return Input.EMPTY;
			}
			Vec3 d = target.subtract(p.position()).multiply(1, 0, 1);
			if (d.lengthSqr() < 0.04) return Input.EMPTY;
			d = d.normalize();
			if (!safeStep(p, p.getX() + d.x * 0.8, p.getZ() + d.z * 0.8)) {
				result = "no_way";
				return Input.EMPTY;
			}
			Skills.turn(p, (float) Math.toDegrees(Mth.atan2(d.z, d.x)) - 90f, 0f, 45f);
			return keysFor(p, d, p.horizontalCollision, false);
		}

		/** A straight walk from a to b on her own level: room for her body and a floor every half block (no lit spot behind a wall). */
		static boolean walkable(LocalPlayer p, Vec3 a, Vec3 b) {
			Vec3 d = b.subtract(a).multiply(1, 0, 1);
			int n = (int) Math.ceil(d.length() / 0.5);
			for (int i = 1; i <= n; i++) {
				Vec3 q = a.add(d.scale((double) i / n));
				BlockPos f = BlockPos.containing(q.x, a.y + 0.01, q.z);
				if (!Hazard.room(p, f) || p.level().getBlockState(f.below()).getCollisionShape(p.level(), f.below()).isEmpty()) return false;
			}
			return true;
		}

		@Override
		void release(Minecraft mc, LocalPlayer p) {
			if (place != null && place.status == null) place.cancel(mc, p);
			p.getInventory().setSelectedSlot(prevSlot); // the torch placement selected the torch: give her hand back (dev a41: eat checks)
		}
	}

	/**
	 * CP-30 beds: at night, idle, no hostile within 8 (vanilla's own rule), a bed in reach in a dimension where sleeping is safe:
	 * right-click it (not sneaking). Ends `sleeping` once the client is in bed, else `not_sleeping` after 2 s (not again for
	 * 30 s). Vanilla wakes her in the morning. Note: sleeping also sets her spawn point to that bed.
	 */
	static final class Sleep extends R {
		final BlockPos bed;
		boolean clicked;
		int waited;

		Sleep(LocalPlayer p, BlockPos bed) {
			super("sleep", 1);
			this.bed = bed;
			trigger.add("bed", Telemetry.pos(Vec3.atCenterOf(bed)));
			sleepRetryNs = System.nanoTime() + 30_000_000_000L;
		}

		@Override
		Input tick(Minecraft mc, LocalPlayer p, Threats t) {
			if (p.isSleeping()) {
				result = "sleeping";
				return Input.EMPTY;
			}
			if (!clicked) {
				if (p.isShiftKeyDown()) return Input.EMPTY; // a sneaking click would not use the bed
				if (Hands.blockedByScreen(mc) != null) return Input.EMPTY;
				if (!Hands.aim(p, Vec3.atCenterOf(bed))) return Input.EMPTY;
				if (!Hands.crosshairOn(mc, bed)) {
					result = "obstructed";
					return Input.EMPTY;
				}
				Hands.use(mc);
				clicked = true;
				return Input.EMPTY;
			}
			if (++waited > 40) result = "not_sleeping";
			return Input.EMPTY;
		}
	}

	/** No shield: step sideways off the projectile's line (the side she already leans to, if it has footing). */
	static final class Dodge extends R {
		Vec3 dir;
		int quiet;

		Dodge(Threats t) {
			super("dodge", 3);
			trigger.addProperty("entity", BuiltInRegistries.ENTITY_TYPE.getKey(t.incoming().getType()).toString());
			trigger.addProperty("entity_id", t.incoming().getId());
			trigger.addProperty("impact_ticks", t.impactTicks());
		}

		@Override
		Input tick(Minecraft mc, LocalPlayer p, Threats t) {
			if (t.incoming() == null) {
				if (++quiet > 3) result = "clear";
				return Input.EMPTY;
			}
			quiet = 0;
			if (ticks > 30) {
				result = "timeout";
				return Input.EMPTY;
			}
			if (dir == null) {
				Vec3 v = t.incoming().getDeltaMovement().multiply(1, 0, 1);
				Vec3 n = v.lengthSqr() < 1e-4 ? new Vec3(1, 0, 0) : new Vec3(-v.z, 0, v.x).normalize();
				Vec3 off = p.position().subtract(t.incoming().position()).multiply(1, 0, 1);
				if (off.dot(n) < 0) n = n.scale(-1); // keep moving the way she is already off the line
				if (!safeStep(p, p.getX() + n.x, p.getZ() + n.z)) n = n.scale(-1);
				if (!safeStep(p, p.getX() + n.x, p.getZ() + n.z)) {
					result = "no_footing";
					return Input.EMPTY;
				}
				dir = n;
				detail.add("dir", Telemetry.pos(n));
			}
			if (!safeStep(p, p.getX() + dir.x * 0.6, p.getZ() + dir.z * 0.6)) return Input.EMPTY; // never step off for a dodge
			return keysFor(p, dir, false, false);
		}
	}

	/**
	 * At or below the health floor with a hostile near: turn away from the hostiles and sprint until none is within 16 blocks. From a
	 * shooter (bow, crossbow, trident), first a wall: the nearest cell within 5 she can walk straight to where it cannot see her
	 * (coverCell), held while hidden (O98, a16: 6 retreats ran into the back wall straight behind the doorway, in the line of fire).
	 */
	static final class Retreat extends R {
		double bestDist;
		int sinceBetter;
		BlockPos cover;
		long coverTick = -100; // clientTick counts up from 0: the first tick picks a cover

		Retreat(LocalPlayer p, Threats t) {
			super("retreat", RETREAT_PRIORITY);
			trigger.addProperty("health", p.getHealth());
			trigger.addProperty("hostile", BuiltInRegistries.ENTITY_TYPE.getKey(t.hostile().getType()).toString());
			trigger.addProperty("distance", Skills.r2(t.hostileDist()));
			if (ranged(t.hostile())) trigger.addProperty("ranged", true);
			bestDist = t.hostileDist();
		}

		@Override
		Input tick(Minecraft mc, LocalPlayer p, Threats t) {
			if (t.hostile() == null || t.hostileDist() > RETREAT_CLEAR) {
				result = "safe";
				detail.addProperty("hostile_distance", t.hostile() == null ? null : Skills.r2(t.hostileDist()));
				return Input.EMPTY;
			}
			if (ticks > 200) {
				result = "timeout";
				return Input.EMPTY;
			}
			if (ranged(t.hostile())) {
				if (!exposed(p, t.hostile(), p.position())) { // hidden from it: hold here, not cornered
					sinceBetter = 0;
					if (!detail.has("covered_at")) detail.add("covered_at", Telemetry.pos(p.position()));
					return Input.EMPTY;
				}
				if (EveBody.clientTick - coverTick >= 10) { // the shooter moves: re-pick every 10 ticks
					coverTick = EveBody.clientTick;
					Entity s = t.hostile();
					cover = coverCell(p.blockPosition(), 5, c -> walkable(p, c), c -> exposed(p, s, Vec3.atBottomCenterOf(c)));
					if (cover != null && !detail.has("cover_cell")) detail.add("cover_cell", Telemetry.pos(Vec3.atBottomCenterOf(cover)));
				}
				if (cover != null) {
					sinceBetter = 0;
					Vec3 d = Vec3.atBottomCenterOf(cover).subtract(p.position()).multiply(1, 0, 1);
					if (d.lengthSqr() < 0.04) return Input.EMPTY;
					float yaw = (float) Math.toDegrees(Mth.atan2(d.z, d.x)) - 90f;
					Skills.turn(p, yaw, 0f, 45f);
					return new Input(Math.abs(Mth.wrapDegrees(yaw - p.getYRot())) < 30f, false, false, false, false, false, false);
				}
			}
			if (t.hostileDist() > bestDist + 0.5) {
				bestDist = t.hostileDist();
				sinceBetter = 0;
			} else if (++sinceBetter > 40) {
				result = "cornered";
				corneredNs = System.nanoTime();
				detail.addProperty("hostile_distance", Skills.r2(t.hostileDist()));
				handBack(p, t, "cornered"); // O102d (3)
				return Input.EMPTY;
			}
			Vec3 away = t.away().lengthSqr() < 1e-6 ? p.position().subtract(t.hostile().position()).multiply(1, 0, 1) : t.away();
			if (away.lengthSqr() > 1e-6) {
				boolean phantomNight = t.hostile() instanceof net.minecraft.world.entity.monster.Phantom && p.level().isDarkOutside();
				Vec3 way = retreatWay(away.multiply(1, 0, 1).normalize(), fleeToward(p), phantomNight);
				if (way != away) detail.addProperty("retreat_toward", phantomNight ? "home (phantom, night)" : "home or owner");
				away = way;
			}
			Input in = flee(p, away);
			return in != null ? in : Input.EMPTY; // no safe step in any direction away: stand rather than fall
		}
	}

	/** O98: the nearest cell within r of {@code feet} (same level) that {@code reach} allows and {@code seen} finds hidden, or null. */
	static BlockPos coverCell(BlockPos feet, int r, java.util.function.Predicate<BlockPos> reach, java.util.function.Predicate<BlockPos> seen) {
		BlockPos best = null;
		int bd = Integer.MAX_VALUE;
		for (int dx = -r; dx <= r; dx++)
			for (int dz = -r; dz <= r; dz++) {
				int d = dx * dx + dz * dz;
				if (d >= bd || d > r * r) continue;
				BlockPos c = feet.offset(dx, 0, dz);
				if (reach.test(c) && !seen.test(c)) {
					bd = d;
					best = c;
				}
			}
		return best;
	}

	static boolean exposed(LocalPlayer p, Entity shooter, Vec3 at) {
		return exposed(shooter.getEyePosition(), at,
				(from, q) -> p.level().clip(new ClipContext(from, q, ClipContext.Block.COLLIDER, ClipContext.Fluid.NONE, p)).getType() == HitResult.Type.MISS);
	}

	/**
	 * Any of her body points standing at {@code at} (chest and eyes, centre and 0.3 out) in a clear line from the shooter's eyes,
	 * or from a point 1 to either side of them (it steps sideways between shots).
	 */
	static boolean exposed(Vec3 eye, Vec3 at, java.util.function.BiPredicate<Vec3, Vec3> clear) {
		Vec3 side = at.subtract(eye).multiply(1, 0, 1);
		side = side.lengthSqr() < 1e-6 ? new Vec3(1, 0, 0) : new Vec3(-side.z, 0, side.x).normalize();
		for (Vec3 from : new Vec3[] {eye, eye.add(side), eye.subtract(side)})
			for (double h : new double[] {0.9, 1.5})
				for (double[] o : new double[][] {{0, 0}, {0.3, 0}, {-0.3, 0}, {0, 0.3}, {0, -0.3}})
					if (clear.test(from, new Vec3(at.x + o[0], at.y + h, at.z + o[1]))) return true;
		return false;
	}

	/** A straight walk from her feet to the centre of c with safe footing every half block (a wall, drop or lava refuses it). */
	static boolean walkable(LocalPlayer p, BlockPos c) {
		Vec3 to = Vec3.atBottomCenterOf(c).subtract(p.position()).multiply(1, 0, 1);
		double len = to.length();
		for (double k = 0.5; k < len + 0.5; k += 0.5) {
			Vec3 q = p.position().add(to.scale(Math.min(k, len) / Math.max(len, 1e-6)));
			if (unsafeWhy(p, q.x, q.z) != null) return false;
		}
		return true;
	}

	/**
	 * Sprint along {@code away} (horizontal), or the nearest of +-30/60/90 deg with safe footing 1 and 2 blocks out (no drop over 2,
	 * no lava, head room). Open ground only: it never steers toward cover. Null when every direction away is unsafe.
	 */
	static Input flee(LocalPlayer p, Vec3 away) {
		Vec3 a = away.multiply(1, 0, 1);
		if (a.lengthSqr() < 1e-6) a = new Vec3(-Math.sin(Math.toRadians(p.getYRot())), 0, Math.cos(Math.toRadians(p.getYRot()))).scale(-1);
		a = a.normalize();
		for (int deg : new int[] {0, 30, -30, 60, -60, 90, -90}) {
			Vec3 d = a.yRot((float) Math.toRadians(deg));
			int gait = safeStep(p, p.getX() + d.x * 2, p.getZ() + d.z * 2) ? fleeGait(freeRun(p, d, 3.0)) : 0;
			if (gait > 0) {
				float yaw = (float) Math.toDegrees(Mth.atan2(d.z, d.x)) - 90f;
				Skills.turn(p, yaw, 0f, 45f);
				boolean facing = Math.abs(Mth.wrapDegrees(yaw - p.getYRot())) < 30f;
				return new Input(facing, false, false, false, facing && p.horizontalCollision && p.onGround(), false, facing && gait == 2);
			}
		}
		return null;
	}

	/**
	 * CP-29: flee() for a creeper, scored so the blast stays clear of structures (combat CP-29 lane C a2: the straight-away run
	 * led a creeper into a 3x3x3 hut and the blast took 9/27 of its blocks, 4 of 4 times). Among the safe directions at 0/+-30/
	 * +-60/+-90 from straight away, take the lowest cost = turn penalty (1 per 30 deg) + 2 per built block and 60 per keep-clear
	 * zone (envelope zones whose break is not free) near the points she will be at 3, 5 and 7 blocks out (where a following
	 * creeper would blow). With structure on every side, the cheapest is the one that passes them rather than ends in them.
	 * Re-scored every 10 ticks. ponytail: "built" = not natural terrain at or above her feet within 3 of each point; sense's zone
	 * snapshots would be exact; this misses a structure made only of natural blocks (a dirt hut).
	 */
	/** Monsters (the one she flees from is behind her) within 1.5 of her next 6 blocks that way. */
	static int hostilesOnLine(LocalPlayer p, Vec3 d) {
		Vec3 a = p.position(), b = a.add(d.scale(6));
		return p.level().getEntitiesOfClass(net.minecraft.world.entity.monster.Monster.class, new AABB(a, b).inflate(1.5, 1, 1.5), m -> {
			double t = Mth.clamp(m.position().subtract(a).dot(d) / 6, 0, 1);
			return t > 0 && m.position().distanceToSqr(a.add(d.scale(6 * t))) < 2.25;
		}).size();
	}

	/** The run of the direction fleeClear chose last (8 = open). */
	static double fleeRun = 8;

	/** O324: her own positions, one every 5 ticks while she moves, the last ~10 s; cleared on a jump of more than 4 (a teleport). */
	static final java.util.ArrayDeque<Vec3> TRAIL = new java.util.ArrayDeque<>();

	static void recordTrail(Vec3 at, long tick) {
		Vec3 last = TRAIL.peekLast();
		if (last != null && last.distanceTo(at) > 4) TRAIL.clear();
		if (tick % 5 != 0 || last != null && last.distanceTo(at) < 0.5) return;
		TRAIL.addLast(at);
		while (TRAIL.size() > 40) TRAIL.removeFirst();
	}

	/**
	 * O324: the trail point to walk back to. Points she has reached (within 1.5) are used up; the newest left is the next. Null if none is left,
	 * or if it is no farther from the creeper than she is (it came up behind her: the trail is its way in, not hers out).
	 */
	static Vec3 trailTarget(java.util.Deque<Vec3> trail, Vec3 her, Vec3 creeper) {
		while (!trail.isEmpty() && Math.hypot(trail.peekLast().x - her.x, trail.peekLast().z - her.z) < 1.5) trail.removeLast();
		Vec3 q = trail.peekLast();
		if (q == null) return null;
		return Math.hypot(q.x - creeper.x, q.z - creeper.z) > Math.hypot(her.x - creeper.x, her.z - creeper.z) + 0.5 ? q : null;
	}

	/** O326 (no-bow branch): the way to the nearest other player within 48 (the owner, in co-play), else home in this dimension; unit, or null. */
	static Vec3 fleeToward(LocalPlayer p) {
		Vec3 at = null;
		double best = 48 * 48;
		for (var o : p.level().players())
			if (o != p && o.isAlive() && o.distanceToSqr(p) < best) {
				best = o.distanceToSqr(p);
				at = o.position();
			}
		if (at == null) {
			Envelope.Home h = Envelope.home();
			if (h != null && h.dim().equals(dimId(p))) at = h.center();
		}
		if (at == null) return null;
		Vec3 v = at.subtract(p.position()).multiply(1, 0, 1);
		return v.lengthSqr() < 4 ? null : v.normalize();
	}

	/** O335: a creeper outranks the shield and the floor retreat (an archer at the floor too: the creeper wins, she sprints). */
	static final int CREEPER_FLEE_PRIORITY = 4, SHIELD_PRIORITY = 3, RETREAT_PRIORITY = 2;

	/**
	 * O335: S28-2's shield-up flee at the floor (walking pace) is for melee and ranged mobs; from a creeper she sprints with the shield down at
	 * any health: only distance beats the fuse (> 7 m in 1.5 s), and a shield behind her blocks nothing (a148: rear = bare).
	 */
	static boolean fleeShieldUp(boolean fromCreeper, float hp) {
		return !fromCreeper && hp <= HEALTH_FLOOR;
	}

	/** O347: with no 7 m run open: "trail" if her way back leads away; else "shield" toward it, or with no shield "run" the best direction. */
	static String confinedPlan(boolean shield, boolean trail) {
		return trail ? "trail" : shield ? "shield" : "run";
	}

	/**
	 * O355: a sub-act a reflex drives gets every key it asked for (a place's crouch, a dismount's sneak). Lane C: the creeper wall dropped the
	 * place's crouch and 0 of 5 landed; follow_boat once dropped the dismount's sneak (b4726fcb).
	 */
	static Input withSubActKeys(Input in, Skills.Act sub) {
		boolean shift = sub.sneak != null ? sub.sneak : in.shift();
		return new Input(in.forward(), in.backward(), in.left(), in.right(), in.jump(), shift, in.sprint());
	}

	/** O347 revised: a full solid block in the hotbar to wall with, or null. */
	static String wallItem(LocalPlayer p) {
		for (int i = 0; i < 9; i++) {
			ItemStack s = p.getInventory().getItem(i);
			if (s.getItem() instanceof net.minecraft.world.item.BlockItem bi && bi.getBlock().defaultBlockState().isSolid()
					&& bi.getBlock().defaultBlockState().getLightEmission() == 0 && !(bi.getBlock() instanceof net.minecraft.world.level.block.FallingBlock))
				return Hands.itemId(s);
		}
		return null;
	}

	/** O347 revised: the horizontal side of her cell that faces it. */
	static Direction towardCreeper(double hx, double hz, double cx, double cz) {
		double dx = cx - hx, dz = cz - hz;
		return Math.abs(dx) >= Math.abs(dz) ? (dx > 0 ? Direction.EAST : Direction.WEST) : (dz > 0 ? Direction.SOUTH : Direction.NORTH);
	}

	/** O326 (a): walking keys toward (dx, dz) while she faces yaw (shield toward the creeper, so often backwards). */
	static Input facingMove(float yaw, double dx, double dz) {
		double r = Math.toRadians(yaw), fx = -Math.sin(r), fz = Math.cos(r), len = Math.hypot(dx, dz);
		if (len < 1e-6) return Input.EMPTY;
		double f = (dx * fx + dz * fz) / len, l = (dx * Math.cos(r) + dz * Math.sin(r)) / len; // left of facing (-sin, cos) is (cos, sin)
		return new Input(f > 0.35, f < -0.35, l > 0.35, l < -0.35, false, false, false);
	}

	/** a144: nothing solid on the straight line to the point, at her feet and at her head. */
	static boolean openTo(LocalPlayer p, Vec3 q) {
		for (double h : new double[] {0.3, 1.5}) {
			Vec3 a = p.position().add(0, h, 0), b = new Vec3(q.x, p.getY() + h, q.z);
			if (p.level().clip(new ClipContext(a, b, ClipContext.Block.COLLIDER, ClipContext.Fluid.NONE, p)).getType() != HitResult.Type.MISS) return false;
		}
		return true;
	}

	static Input fleeClear(LocalPlayer p, Vec3 away) {
		Vec3 a = away.multiply(1, 0, 1);
		if (a.lengthSqr() < 1e-6) return flee(p, away);
		a = a.normalize();
		if (fleeDir == null || EveBody.clientTick - fleeTick >= 10) {
			fleeTick = EveBody.clientTick;
			fleeDir = null;
			fleeRun = 0;
			double bestCost = Double.MAX_VALUE;
			var zones = Envelope.keepClear(p.level().dimension().identifier().toString());
			JsonObject scores = new JsonObject();
			// O57a: the way she already moves (or faces) costs no turn; 1 per 90 deg off it. 45L k_walk: +-90 tied at 21 and the
			// first in scan order was the one behind her, so she was still turning when the hissing creeper's 10 ticks ran out
			Vec3 v = p.getDeltaMovement().multiply(1, 0, 1);
			Vec3 heading = v.lengthSqr() > 0.003 ? v.normalize()
					: new Vec3(-Math.sin(Math.toRadians(p.getYRot())), 0, Math.cos(Math.toRadians(p.getYRot())));
			java.util.Map<String, Integer> ids = new java.util.TreeMap<>();
			JsonObject rejected = new JsonObject(); // combat O57c: which footing test refused a direction (45L k_walk: 0/+-30 unexplained)
			JsonObject runs = new JsonObject();
			for (int deg : new int[] {0, 30, -30, 60, -60, 90, -90}) {
				Vec3 d = a.yRot((float) Math.toRadians(deg));
				String why = unsafeWhy(p, p.getX() + d.x, p.getZ() + d.z), at = "1";
				if (why == null) {
					why = unsafeWhy(p, p.getX() + d.x * 2, p.getZ() + d.z * 2);
					at = "2";
				}
				if (why == null && freeRun(p, d, 2.0) < 2.0) { // O237: a drop between the 1- and 2-block checks
					why = "drop";
					at = "1.5";
				}
				if (why != null) {
					rejected.addProperty(String.valueOf(deg), why + "@" + at + " feet_y=" + Skills.r2(p.getY()) + (p.onGround() ? "" : " airborne"));
					continue;
				}
				double cost = Math.abs(deg) / 30.0 + Math.toDegrees(Math.acos(Mth.clamp(d.dot(heading), -1, 1))) / 90.0;
				// O72a: how far she can actually run that way (footing every half block, up to 8). A heading that ends at a wall
				// or in a corner within 8 loses to open ground: 8 per missing block (combat k_follow_shallow, 45L + lane E a28:
				// she fled into the SE corner and was cornered, blast at 2.6-2.8).
				double run = freeRun(p, d, 8.0);
				runs.addProperty(String.valueOf(deg), Skills.r2(run));
				cost += 8 * (8.0 - run);
				Vec3 toward = fleeToward(p);
				if (toward != null) cost += Math.toDegrees(Math.acos(Mth.clamp(d.dot(toward), -1, 1))) / 45.0; // O326: toward the owner or home
				cost += 16 * hostilesOnLine(p, d); // O294: 3 zombies ahead, the creeper behind: "away" ran her into them and she stalled
				for (int k : new int[] {3, 5, 7}) {
					// where she can actually get: points beyond a wall counted fewer wall blocks than points along it, so the built
					// term pushed her into a corner (combat lane E a33 #13/#19: +-30 run 2.5-3 beat 90 run 5.5-7)
					Vec3 q = p.position().add(d.scale(Math.min(k, run)));
					cost += 2 * builtNear(p, q, ids);
					AABB blast = new AABB(q.x - 4, q.y - 2, q.z - 4, q.x + 4, q.y + 4, q.z + 4);
					for (AABB z : zones) if (z.intersects(blast)) cost += 60;
				}
				scores.addProperty(String.valueOf(deg), Skills.r2(cost));
				if (cost < bestCost) {
					bestCost = cost;
					fleeDir = d;
					fleeRun = run;
				}
			}
			lastFleeScores = scores;
			if (active != null && !active.detail.has("flee_scores")) { // the first choice, for evidence
				active.detail.add("flee_scores", scores);
				active.detail.add("flee_rejected", rejected);
				active.detail.add("flee_runs", runs);
				JsonObject b = new JsonObject();
				ids.forEach(b::addProperty);
				active.detail.add("built_counted", b);
			}
			if (fleeDir == null) return null;
		}
		Vec3 d = fleeDir;
		float yaw = (float) Math.toDegrees(Mth.atan2(d.z, d.x)) - 90f;
		Skills.turn(p, yaw, 0f, 45f);
		boolean facing = Math.abs(Mth.wrapDegrees(yaw - p.getYRot())) < 30f;
		int gait = fleeGait(freeRun(p, d, 3.0));
		if (gait == 0) {
			fleeDir = null; // footing changed under the chosen line: re-score next tick
			return Input.EMPTY;
		}
		return new Input(facing, false, false, false, facing && p.horizontalCollision && p.onGround(), false, facing && gait == 2);
	}

	/**
	 * O237 (22:03:14: a creeper flee ran her off the doorway lip, 77 -> 70, 2 fall damage): how she may move along a heading with
	 * this much safe footing ahead (freeRun, up to 3). Under 1: not that way. Under 3: walk (a sprint carries her past the last safe
	 * step before she can stop). 3: sprint. The footing rule itself is unchanged: a drop of 3+ is never a step.
	 */
	static int fleeGait(double run) {
		return run < 1.0 ? 0 : run < 3.0 ? 1 : 2;
	}

	/** Free run along d: the farthest distance (half-block steps, up to max) with safe footing all the way. */
	static double freeRun(LocalPlayer p, Vec3 d, double max) {
		for (double t = 1.0; t <= max; t += 0.5)
			if (unsafeWhy(p, p.getX() + d.x * t, p.getZ() + d.z * t) != null) return t - 0.5;
		return max;
	}

	static Vec3 fleeDir;
	static long fleeTick;
	static JsonObject lastFleeScores;

	/** Blocks within 3 (horizontally) of q, from her feet level to 3 above, that are not natural terrain or plants. */
	static int builtNear(LocalPlayer p, Vec3 q, java.util.Map<String, Integer> ids) {
		var l = p.level();
		int n = 0, y0 = (int) Math.floor(p.getY() + 0.01);
		BlockPos.MutableBlockPos b = new BlockPos.MutableBlockPos();
		for (int dx = -3; dx <= 3; dx++)
			for (int dz = -3; dz <= 3; dz++)
				for (int dy = 0; dy <= 3; dy++) {
					b.set(Mth.floor(q.x) + dx, y0 + dy, Mth.floor(q.z) + dz);
					var st = l.getBlockState(b);
					if (st.isAir() || st.canBeReplaced() || !l.getFluidState(b).isEmpty()) continue;
					if (st.is(net.minecraft.tags.BlockTags.DIRT) || st.is(net.minecraft.tags.BlockTags.BASE_STONE_OVERWORLD)
							|| st.is(net.minecraft.tags.BlockTags.SAND) || st.is(net.minecraft.tags.BlockTags.LEAVES) || st.is(net.minecraft.tags.BlockTags.LOGS)
							|| st.is(net.minecraft.tags.BlockTags.ICE) || st.is(Blocks.GRAVEL) || st.is(Blocks.SNOW_BLOCK) || st.is(Blocks.BEDROCK)
							|| st.is(net.minecraft.tags.BlockTags.BASE_STONE_NETHER)) continue;
					n++;
					ids.merge(BuiltInRegistries.BLOCK.getKey(st.getBlock()).getPath(), 1, Integer::sum);
				}
		return n;
	}

	/**
	 * CP-29: a creeper close or hissing pre-empts everything but a fall rescue. Run from it to the blast-safe distance (8, charged
	 * 14) over open ground; done when it is that far and not hissing, or gone (exploded or killed). Trapped (no progress for 2 s,
	 * or no safe direction): raise a usable shield toward it, which blocks blast damage from the front, and report `cornered`.
	 */
	static final class CreeperFlee extends R {
		final Creeper c;
		double best;
		int sinceBetter, gone, calm;
		Vec3 from;
		double moved;
		final double[] recent = new double[20]; // her distance to it over the last second
		boolean shielding;

		CreeperFlee(LocalPlayer p, Threats t) {
			super("creeper_flee", CREEPER_FLEE_PRIORITY);
			c = t.creeper();
			if (creeperClaimed(c.getId(), claimedCreeperId, claimedUntilTick, EveBody.clientTick)) trigger.addProperty("claimed", true);
			fleeDir = null; // a fresh scoring for this creeper
			best = t.creeperDist();
			trigger.addProperty("distance", Skills.r2(t.creeperDist()));
			trigger.addProperty("hissing", c.getSwelling(1f) > 0 || c.isIgnited());
			trigger.addProperty("charged", c.isPowered());
			trigger.addProperty("entity_id", c.getId());
		}

		@Override
		Input tick(Minecraft mc, LocalPlayer p, Threats t) {
			if (!c.isAlive() || c.isRemoved()) {
				if (++gone > 5) result = shielding ? "cornered_shielded" : "safe_creeper_gone";
				detail.addProperty("distance_at_end", Skills.r2(best));
				return Input.EMPTY;
			}
			double d = c.distanceTo(p);
			recent[ticks % recent.length] = d;
			boolean hissing = c.getSwelling(1f) > 0 || c.isIgnited();
			calm = d >= creeperSafe(c) && !hissing ? calm + 1 : 0;
			if (calm > 10) {
				result = shielding ? "cornered_shielded" : "safe";
				detail.addProperty("distance_at_end", Skills.r2(d));
				if (!shielding) {
					safeCreeperId = c.getId();
					safeCreeperTick = EveBody.clientTick;
				}
				return Input.EMPTY;
			}
			if (ticks > 300) {
				result = "timeout";
				return Input.EMPTY;
			}
			if (from == null) from = p.position();
			double m = Math.hypot(p.getX() - from.x, p.getZ() - from.z);
			if (d > best + 0.3 || m > moved + 0.3) { // O57a: a sideways run gains distance slowly at first; her own progress counts
				best = Math.max(best, d);
				moved = Math.max(moved, m);
				sinceBetter = 0;
			} else if (d < creeperSafe(c)) {
				sinceBetter++;
			}
			// hissing: the fuse is 1.5 s, so half a second without gaining distance means running will not make it
			// O294 (lane C: 6 of 10 "cornered" with all ground open, she stood shielded 8 s until the blast): calm and still 4 off, try again
			if (shielding && sinceBetter > 60 && !hissing && d > 4) {
				sinceBetter = 0;
				best = d;
				from = p.position();
				moved = 0;
				fleeDir = null;
			}
			Input in = sinceBetter > (hissing ? 10 : 40) ? null : fleeClear(p, p.position().subtract(c.position()));
			// O324/O347 (lane D, 35fdb4b6: two cave deaths, flees "cornered" underground): no 7 m run open. Back out the way she came if it leads
			// away (shield toward it if she has one); else the shield toward it; with no shield, the best direction at once. No wall (O347: 0 of
			// 5 places landed inside a creeper's approach)
			if (wall != null) { // O347 revised: no shield; it lands or fails, never abandoned on distance
				Input w = wall.tick(mc, p);
				wall.ticks++;
				// the place waits for her crouch before it clicks: the reflex must pass its sneak on, as darkness does for its torch (lane C:
				// 0 of 5 landed, every one still waiting for a crouch that never came)
				if (wall.status == null) return withSubActKeys(w, wall);
				detail.addProperty("wall_" + walls, wall.status + (wall.code == null ? "" : ":" + wall.code) + (wall.note == null ? "" : " " + wall.note)
						+ " at " + Skills.r2(d) + " after " + wall.ticks + " ticks");
				detail.add("wall_" + walls + "_steps", wall.steps);
				boolean landed = "ok".equals(wall.status);
				wall = landed && walls < 2 ? nextWall(p) : null;
				if (wall != null) return Input.EMPTY;
			}
			if (in == null || fleeRun < 7) {
				// O347 revised: with no shield, blocks in the hotbar, the creeper calm and 3.5+ away: a block in the cell(s) between them first
				if (walls == 0 && !(shieldUsable(p) || p.isUsingItem()) && !hissing && d >= 3.5 && (wall = nextWall(p)) != null) return Input.EMPTY;
				Vec3 back = trailTarget(TRAIL, p.position(), c.position());
				if (back != null && !openTo(p, back)) { // a144: an old point outside the pen she was walled into: not a way out
					detail.addProperty("trail_blocked", true);
					back = null;
				}
				boolean shield = shieldUsable(p) || p.isUsingItem();
				String plan = confinedPlan(shield, back != null);
				if (plan.equals("trail")) {
					detail.addProperty("trail_retreat", true);
					sinceBetter = Math.min(sinceBetter, 20);
					if (!(shieldUsable(p) || p.isUsingItem())) in = Skills.steer(p, back);
					else {
						float[] yp = Skills.yawPitch(p.getEyePosition(), c.getEyePosition());
						Skills.turn(p, yp[0], yp[1], 60f);
						raiseShield(mc, p);
						detail.addProperty("trail_shielded", true);
						return facingMove(p.getYRot(), back.x - p.getX(), back.z - p.getZ());
					}
				} else if (plan.equals("run")) {
					detail.addProperty("confined_run", "no shield, no trail"); // O347: no shield: run the best direction, never stand
				} else {
					// O334: never the short run with the shield down: the shield toward it now
					detail.addProperty("confined_shield", "no trail");
					in = null;
				}
			}
			if (in != null) {
				if (fleeShieldUp(true, p.getHealth()) && (shieldUsable(p) || p.isUsingItem())) { // S28-2 floor rule: never from a creeper (O335)
					if (!shielding) {
						mc.hitResult = BlockHitResult.miss(p.getEyePosition(), Direction.UP, p.blockPosition());
						Hands.use(mc);
						mc.options.keyUse.setDown(true);
						shielding = true;
					}
					detail.addProperty("fled_shielded", true);
					return new Input(in.forward(), in.backward(), in.left(), in.right(), in.jump(), false, false);
				}
				if (shielding) release(mc, p);
				shielding = false;
				return in;
			}
			// trapped: the least-damage response this body has is a raised shield facing the blast
			detail.addProperty("cornered", true);
			if (shieldUsable(p) || p.isUsingItem()) {
				float[] yp = Skills.yawPitch(p.getEyePosition(), c.getEyePosition());
				Skills.turn(p, yp[0], yp[1], 60f);
				if (!shielding) {
					mc.hitResult = BlockHitResult.miss(p.getEyePosition(), Direction.UP, p.blockPosition());
					Hands.use(mc);
					mc.options.keyUse.setDown(true);
					shielding = true;
				}
			} else if (++sinceBetter > 80) {
				result = "cornered";
			}
			return Input.EMPTY;
		}

		Interact.PlaceBlock wall;
		int walls;

		/** O347 revised: the next free cell between her and it (floor level, then head height), with a hotbar block, where the envelope allows. */
		private Interact.PlaceBlock nextWall(LocalPlayer p) {
			String item = wallItem(p);
			if (item == null) return null;
			BlockPos cell = p.blockPosition().relative(towardCreeper(p.getX(), p.getZ(), c.getX(), c.getZ()));
			for (BlockPos q : new BlockPos[] {cell, cell.above()}) {
				// O355 (lane C: 4 retries refused "Eve herself stands in the target space"): a cell her own box reaches into is no wall cell
				if (!p.level().getBlockState(q).canBeReplaced() || c.getBoundingBox().intersects(new AABB(q)) || p.getBoundingBox().intersects(new AABB(q))) continue;
				JsonObject z = Envelope.zoneAt(dimId(p), q);
				if (z != null && !(z.has("place") && "free".equals(z.get("place").getAsString()))) return null;
				JsonObject prm = new JsonObject(), pos = new JsonObject();
				pos.addProperty("x", q.getX());
				pos.addProperty("y", q.getY());
				pos.addProperty("z", q.getZ());
				prm.add("pos", pos);
				prm.addProperty("item", item);
				walls++;
				detail.addProperty("wall_" + walls + "_started", item + " at " + q.toShortString() + ", creeper " + Skills.r2(c.distanceTo(p)));
				return new Interact.PlaceBlock("creeper-wall", prm, System.nanoTime(), System.nanoTime() + 2_000_000_000L);
			}
			return null;
		}

		private void raiseShield(Minecraft mc, LocalPlayer p) {
			if (shielding) return;
			mc.hitResult = BlockHitResult.miss(p.getEyePosition(), Direction.UP, p.blockPosition());
			Hands.use(mc);
			mc.options.keyUse.setDown(true);
			shielding = true;
		}

		@Override
		void release(Minecraft mc, LocalPlayer p) {
			if (wall != null && wall.status == null) {
				detail.add("wall_" + walls + "_steps", wall.steps); // the flee ended mid-place (the creeper went): where it had got to
				wall.cancel(mc, p);
			}
			// O53a: the mind decides what follows a flee (fight_ranged, hold the task): give it the creeper and whether it still closes
			detail.addProperty("creeper_id", c.getId());
			detail.addProperty("creeper_distance", c.isAlive() ? Skills.r2(c.distanceTo(p)) : null);
			double then = recent[(ticks + 1) % recent.length];
			detail.addProperty("creeper_closing", c.isAlive() && ticks >= recent.length && c.distanceTo(p) < then - 0.3);
			if (!shielding) return;
			mc.options.keyUse.setDown(false);
			if (p.isUsingItem()) mc.gameMode.releaseUsingItem(p);
		}
	}

	/**
	 * S105 phantom_cover (kickoff ruling, 45L death 17:16:03): phantoms dive from above, a forward shield blocked 4 of 14, melee cannot reach
	 * them. (a) Existing cover: the most enclosed standable cell within 4 (a low ceiling, walls beside her) with something solid or leaves
	 * 2-4 above it, on an open line; walk there. (b) Else a roof over her head if something beside the head+1 cell holds it (placed on the
	 * ground only); not interruptible. (c) Else home, if within 32 and she closes on it. Pillar+roof is gone (kickoff, a163). (c) Hold under it, eating if she can, until no phantom has been
	 * within 16 for 10 s, it is day, or the owner gives an order (a new action). Gives up (3 failed places, no block, no cover, 5 s walking)
	 * to the retreat. Logged: cover type, ms from the trigger to covered, hits taken while holding, blocks left behind.
	 */
	static final class PhantomCover extends R {
		BlockPos coverCell;
		Vec3 home;
		double homeBest = Double.MAX_VALUE;
		int homeStall;
		final java.util.ArrayDeque<BlockPos> plan = new java.util.ArrayDeque<>();
		Interact.PlaceBlock place;
		int placed, failed, quiet, walkTicks, hitsHeld = 0;
		boolean holding, eating;
		long lastSeenHit;
		final String actionAtStart;
		String type;

		PhantomCover(LocalPlayer p, boolean byHit) {
			super("phantom_cover", 3);
			actionAtStart = Arbiter.active == null ? null : Arbiter.active.id;
			trigger.addProperty("cause", byHit ? "phantom_hit" : "hurt_phantom_near");
			trigger.addProperty("health", Skills.r2(p.getHealth()));
			coverCell = findCover(p, p.blockPosition());
			if (coverCell != null) type = "existing";
			else {
				// kickoff (a): no built cover. Pillar+roof never placed (a163); the neighbour roof failed "occupied" 4 of 4 even on the ground
				// (a163, a166). Home if near: the most enclosed covered cell within 4 of its centre, else none
				Envelope.Home h = Envelope.home();
				if (h != null && h.dim().equals(dimId(p)) && h.center().distanceTo(p.position()) <= 32) {
					BlockPos at = findCover(p, BlockPos.containing(h.center()));
					if (at != null) {
						home = Vec3.atBottomCenterOf(at);
						type = "home";
					}
				}
			}
			trigger.addProperty("type", type == null ? "none" : type);
			lastSeenHit = Telemetry.lastHitTick;
		}

		static boolean mayPlace(LocalPlayer p, BlockPos q) {
			JsonObject z = Envelope.zoneAt(dimId(p), q);
			return z == null || z.has("place") && "free".equals(z.get("place").getAsString());
		}

		static BlockPos findCover(LocalPlayer p, BlockPos f) {
			boolean near = f.equals(p.blockPosition()); // around her: an open line to it; around home: the walk finds the way
			var l = p.level();
			BlockPos best = null;
			double bd = Double.MAX_VALUE;
			for (int dx = -4; dx <= 4; dx++)
				for (int dz = -4; dz <= 4; dz++)
					for (int dy = -1; dy <= 1; dy++) {
						BlockPos c = f.offset(dx, dy, dz);
						if (l.getBlockState(c.below()).getCollisionShape(l, c.below()).isEmpty()) continue;
						if (!l.getBlockState(c).getCollisionShape(l, c).isEmpty() || !l.getBlockState(c.above()).getCollisionShape(l, c.above()).isEmpty()) continue;
						int ceiling = 0;
						for (int h = 2; h <= 4 && ceiling == 0; h++) if (!l.getBlockState(c.above(h)).isAir()) ceiling = h;
						if (ceiling == 0) continue;
						int walls = 0;
						for (Direction d : new Direction[] {Direction.NORTH, Direction.SOUTH, Direction.EAST, Direction.WEST})
							for (int h = 0; h <= 1; h++)
								if (!l.getBlockState(c.relative(d).above(h)).getCollisionShape(l, c.relative(d).above(h)).isEmpty()) walls++;
						// kickoff (a): the most enclosed first (a ceiling at head+1 over one 3 up; each wall beside her), then the nearest
						double d = Vec3.atBottomCenterOf(c).distanceTo(p.position()) - 10 * coverScore(walls, ceiling);
						if (d < bd && (!near || openTo(p, Vec3.atBottomCenterOf(c)))) {
							bd = d;
							best = c;
						}
					}
			return best;
		}

		@Override
		Input tick(Minecraft mc, LocalPlayer p, Threats t) {
			if (place != null) { // the build: not interruptible
				Input in = place.tick(mc, p);
				place.ticks++;
				if (place.status == null) return withSubActKeys(in, place);
				if ("ok".equals(place.status)) placed++;
				else failed++;
				detail.addProperty("place_" + (placed + failed), place.status + (place.code == null ? "" : ":" + place.code));
				place = null;
				if (failed >= 3) {
					result = "no_cover_build";
					return Input.EMPTY;
				}
			}
			if (!holding) {
				if (coverCell != null) {
					Vec3 c = Vec3.atBottomCenterOf(coverCell);
					if (Math.hypot(c.x - p.getX(), c.z - p.getZ()) < 0.4) cover(p);
					else if (++walkTicks > 100) result = "no_cover_reached";
					else return Skills.steer(p, c);
					return Input.EMPTY;
				}
				if (home != null) {
					if (!p.level().canSeeSky(p.blockPosition().above())) cover(p); // under the shelter's roof
					else {
						double dh = Math.hypot(home.x - p.getX(), home.z - p.getZ());
						if (dh < homeBest - 0.3) {
							homeBest = dh;
							homeStall = 0;
						} else if (++homeStall > 60) {
							result = "home_unreachable"; // the retreat toward home takes it from here
							return Input.EMPTY;
						}
						if (dh < 0.4) cover(p); // on the covered cell
						else return Skills.steer(p, home);
					}
					if (!holding) return Input.EMPTY;
				}
				if (!plan.isEmpty() && !p.onGround()) return Input.EMPTY; // a163: knocked up, her box reached the roof cell (occupied)
				if (!plan.isEmpty()) {
					String item = wallItem(p);
					if (item == null) {
						result = "no_blocks";
						return Input.EMPTY;
					}
					BlockPos q = plan.poll();
					JsonObject prm = new JsonObject(), pos = new JsonObject();
					pos.addProperty("x", q.getX());
					pos.addProperty("y", q.getY());
					pos.addProperty("z", q.getZ());
					prm.add("pos", pos);
					prm.addProperty("item", item);
					place = new Interact.PlaceBlock("phantom-cover", prm, System.nanoTime(), System.nanoTime() + 3_000_000_000L);
					return Input.EMPTY;
				}
				if (!holding && (type == null || placed == 0 && coverCell == null && home == null)) {
					result = "no_cover";
					return Input.EMPTY;
				}
				if (!holding) cover(p);
			}
			// the hold: still, eating if she can
			if (Telemetry.lastHitTick != lastSeenHit) {
				lastSeenHit = Telemetry.lastHitTick;
				hitsHeld++;
				detail.addProperty("hits_while_covered", hitsHeld);
			}
			if (Arbiter.active != null && !Arbiter.active.internal && !Arbiter.active.id.equals(actionAtStart)) {
				result = "owner_order"; // S-ruling: the owner's word ends the hold
				return Input.EMPTY;
			}
			quiet = phantomNear(p, 16) == null ? quiet + 1 : 0;
			if (quiet > 200 || !p.level().isDarkOutside()) {
				result = "clear";
				return Input.EMPTY;
			}
			int food = foodSlot(p);
			boolean wantEat = food >= 0 && (p.getFoodData().getFoodLevel() < 20 || p.getHealth() < 20) && p.canEat(false);
			if (wantEat && !eating) {
				p.getInventory().setSelectedSlot(food);
				mc.hitResult = BlockHitResult.miss(p.getEyePosition(), Direction.UP, p.blockPosition());
				Hands.use(mc);
				eating = true;
			}
			mc.options.keyUse.setDown(wantEat);
			if (!wantEat) eating = false;
			return Input.EMPTY;
		}

		private void cover(LocalPlayer p) {
			holding = true;
			detail.addProperty("covered_ms", (System.nanoTime() - startNs) / 1_000_000);
			detail.addProperty("covered_sky", p.level().canSeeSky(p.blockPosition().above()));
		}

		@Override
		void release(Minecraft mc, LocalPlayer p) {
			if (place != null && place.status == null) place.cancel(mc, p);
			if (eating) mc.options.keyUse.setDown(false);
			detail.addProperty("cover_type", type == null ? "none" : type);
			detail.addProperty("blocks_left", placed); // mind may tidy by day; the reflex never breaks them
			detail.addProperty("hits_while_covered", hitsHeld);
		}
	}

	/**
	 * H-autofight (parts/combat/H-AUTOFIGHT.md): fight what the provider's EngageHook names, unasked, then let her task resume.
	 * The provider skill (fight_melee / fight_ranged) runs in-process as this reflex's own act with a dispatched fight's guard
	 * rights. Ends: the fight's own end (ok / its failure code), clear (hook null 60 ticks), floor, timeout (120 s), stopped, or
	 * a higher reflex pre-empting it. Events: start {trigger: entity_id, type, distance, reason, skill}, end {result, elapsed_ms,
	 * kills, health_min, resumes}.
	 */
	static final class AutoFight extends R {
		final Skills.Act fight;
		final eve.body.api.Engage engage;
		final String resumes;
		int quiet;
		float healthMin;

		AutoFight(LocalPlayer p, eve.body.api.Engage e) {
			super("auto_fight", 2);
			engage = e;
			long now = System.nanoTime();
			fight = Skills.create(e.skill(), "auto-" + Long.toHexString(now), e.params() == null ? new JsonObject() : e.params(), now, now + AUTO_FIGHT_CAP_NS);
			resumes = Arbiter.active != null && !Arbiter.active.internal ? Arbiter.active.id : null;
			healthMin = p.getHealth();
			Entity t = p.level().getEntity(e.targetId());
			trigger.addProperty("entity_id", e.targetId());
			trigger.addProperty("type", t == null ? null : BuiltInRegistries.ENTITY_TYPE.getKey(t.getType()).toString());
			trigger.addProperty("distance", t == null ? null : Skills.r2(t.distanceTo(p)));
			trigger.addProperty("reason", e.reason());
			if (!ENGAGE_REASONS.contains(e.reason())) trigger.addProperty("reason_unlisted", true); // still fights; flagged for the mind
			trigger.addProperty("skill", e.skill());
		}

		/** Its Baritone child walk owns player.input while it paths (the arbiter leaves it then). */
		boolean ownsInput() {
			return fight.ownsInput;
		}

		@Override
		Input tick(Minecraft mc, LocalPlayer p, Threats t) {
			healthMin = Math.min(healthMin, p.getHealth());
			quiet = Providers.engage(Providers.ENGAGE, Providers.VIEW) == null ? quiet + 1 : 0;
			String why = autoFightEnd(p.getHealth(), System.nanoTime() - startNs, quiet);
			if (why != null) {
				result = why;
				return Input.EMPTY;
			}
			Input in = fight.tick(mc, p);
			fight.ticks++;
			if (fight.status != null) {
				result = "ok".equals(fight.status) ? "ok" : fight.code != null ? fight.code : fight.status;
				return Input.EMPTY;
			}
			return in;
		}

		@Override
		void release(Minecraft mc, LocalPlayer p) {
			if (fight.status == null) fight.cancel(mc, p);
			fight.fill(mc, p);
			JsonElement kills = fight.evidence.get("kills");
			detail.add("kills", kills == null ? new com.google.gson.JsonPrimitive(0) : kills);
			if ((kills == null || kills.getAsInt() == 0) && Set.of("clear", "unreachable", "no_target").contains(String.valueOf(result))) {
				noKillTarget = engage.targetId();
				noKillTick = EveBody.clientTick;
			}
			detail.addProperty("health_min", Skills.r2(healthMin));
			detail.addProperty("resumes", resumes);
			detail.addProperty("fight_status", fight.status);
			detail.addProperty("fight_code", fight.code);
		}
	}

	/** Hungry (or hurt and below regeneration food) and safe: eat the best hotbar food, then give the previous slot back. */
	static final class Eat extends R {
		final int slot, prevSlot, food0, count0;
		final String item;
		boolean clicked, using;
		int after;

		Eat(LocalPlayer p, int slot) {
			super("eat", 1);
			this.slot = slot;
			prevSlot = p.getInventory().getSelectedSlot();
			food0 = p.getFoodData().getFoodLevel();
			ItemStack s = p.getInventory().getItem(slot);
			item = Hands.itemId(s);
			count0 = s.getCount();
			trigger.addProperty("food", food0);
			trigger.addProperty("health", p.getHealth());
			trigger.addProperty("item", item);
			trigger.addProperty("slot", slot);
		}

		@Override
		Input tick(Minecraft mc, LocalPlayer p, Threats t) {
			if (!t.safe()) {
				result = "aborted_unsafe";
				return Input.EMPTY;
			}
			if (ticks == 0) {
				p.getInventory().setSelectedSlot(slot);
				return Input.EMPTY;
			}
			if (!clicked) {
				if (Hands.blockedByScreen(mc) != null) return Input.EMPTY;
				mc.hitResult = BlockHitResult.miss(p.getEyePosition(), Direction.UP, p.blockPosition()); // eat, never click the block in view
				Hands.use(mc);
				mc.options.keyUse.setDown(true);
				clicked = true;
				return Input.EMPTY;
			}
			using |= p.isUsingItem();
			if (p.isUsingItem() && ticks < 80) return Input.EMPTY;
			mc.options.keyUse.setDown(false);
			ItemStack s = p.getInventory().getItem(slot);
			boolean ate = p.getFoodData().getFoodLevel() > food0 || !item.equals(Hands.itemId(s)) || s.getCount() < count0;
			if (!ate && ++after < 10) return Input.EMPTY; // let the server's food update arrive
			detail.addProperty("food_after", p.getFoodData().getFoodLevel());
			detail.addProperty("count_after", item.equals(Hands.itemId(s)) ? s.getCount() : 0);
			if (!ate) eatRetryNs = System.nanoTime() + 5_000_000_000L; // never loop on a meal that does not work
			else eatRetreatNs = 0; // the hungry spell is over: a later one may retreat to eat again
			result = ate ? "ate" : using ? "not_eaten" : "would_not_start";
			return Input.EMPTY;
		}

		@Override
		void release(Minecraft mc, LocalPlayer p) {
			mc.options.keyUse.setDown(false);
			if (p.isUsingItem()) mc.gameMode.releaseUsingItem(p);
			if (prevSlot != slot) p.getInventory().setSelectedSlot(prevSlot);
		}
	}
}
