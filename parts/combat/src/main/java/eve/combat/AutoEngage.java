package eve.combat;

import com.google.gson.JsonObject;

import java.util.List;
import java.util.Map;

/**
 * H-autofight (ledger f3a9b26c, H-AUTOFIGHT.md): whether, whom and with which skill she fights unasked. Pure rules on plain
 * values (AutoEngageTest); the adapter to body's EngageHook / ClientView (API v4) maps the game onto these records. Body owns the
 * slot: it polls pick() only above its floor, outside STOPPED, with auto_fight on, and owns pause/resume, the 60-tick clear
 * counter (started when pick() returns null) and the 120 s cap.
 */
final class AutoEngage {
	private AutoEngage() {}

	static final double NEAR = 8, OWNER_RANGE = 24, RANGED_MIN = 6;
	static final int HIT_TICKS = 60, FRAME_TTL = 60, MIN_ARROWS = 4; // 3 s at 20 tps
	static final double CREEPER_BOW_MIN = 5, CREEPER_BOW_MAX = 10;
	static final int MELEE_FLOOR = 8; // the health_floor pick() gives fight_melee

	/** Past where body's flee ends (9-13 m): a35's 10 let her re-enter at 11 m after every flee (CP-28 a36, 5 starts in 60 s). */
	static final double BREAK_OFF = 16;

	/** O325: a creeper in sight within 16 and no bow she can use: break the whole fight off (body's flee runs; re-entering every 3 s starved it, lane D 13:57). */
	static boolean breakOffForCreeper(double creeperDist, boolean bow, int arrows) {
		return creeperDist <= BREAK_OFF && !(bow && arrows >= MIN_ARROWS);
	}

	static final String BREAK_OFF_SAY = "a creeper and no bow: breaking off";

	/** O284: a creeper she can shoot before it reaches her goes ahead of every other target. Nearer than 5 is body's flee. */
	static boolean creeperFirst(double dist, boolean bow, int arrows) {
		return bow && arrows >= MIN_ARROWS && dist >= CREEPER_BOW_MIN && dist <= CREEPER_BOW_MAX;
	}

	/**
	 * The EngageHook (body API v4, registered by EveCombat only on v4+): maps body's ClientView onto the records and back. Read
	 * only. A bow anywhere counts (fight_ranged swaps one from the main inventory into the hotbar); a melee weapon anywhere (fight_melee equips it).
	 */
	static eve.body.api.Engage hook(eve.body.api.ClientView v) {
		var p = v.player();
		if (p == null) return null;
		// at or under the floor the fight_melee it names ends health_floor on its first tick (lane D 13:58:36-40: 55 starts in 4 s at HP 6-8)
		if (p.getHealth() <= MELEE_FLOOR) return null;
		List<Mob> mobs = new java.util.ArrayList<>();
		for (net.minecraft.world.entity.Entity e : p.level().getEntities(p, p.getBoundingBox().inflate(OWNER_RANGE))) {
			if (!(e instanceof net.minecraft.world.entity.LivingEntity le) || !le.isAlive()) continue;
			if (e instanceof net.minecraft.world.entity.monster.Creeper && !p.hasLineOfSight(e)) continue; // O284: fight_ranged would walk at an unseen one
			mobs.add(new Mob(e.getId(), net.minecraft.core.registries.BuiltInRegistries.ENTITY_TYPE.getKey(e.getType()).toString(), e.distanceTo(p),
					FightMelee.hostile(e), v.isProtected(e), e instanceof net.minecraft.world.entity.monster.Creeper, FightMelee.ranged(e),
					FightMelee.drawingAt(e, p), FightMelee.hostile(e) && p.hasLineOfSight(e), FightMelee.flyer(e)));
		}
		var inv = v.inventoryCounts();
		boolean melee = false;
		for (String w : FightMelee.WEAPONS) melee |= inv.getOrDefault("minecraft:" + w, 0) > 0;
		Kit kit = new Kit(melee, inv.getOrDefault("minecraft:bow", 0) > 0, inv.getOrDefault("minecraft:arrow", 0));
		Map<Integer, Targeting> frames = new java.util.HashMap<>();
		v.targeting().forEach((id, t) -> frames.put(id, new Targeting(t.target(), t.tick())));
		Engage e = pick(mobs, kit, v.ticks(), v.lastAttackerId() >= 0 ? v.lastAttackerId() : null, v.lastHitTick(), frames);
		return e == null ? null : new eve.body.api.Engage(e.skill(), e.params(), e.targetId(), e.reason());
	}

	/** One entity she can see. hostile = FightMelee.hostile (calm neutrals excluded); protected as body's isProtected. */
	record Mob(int id, String type, double dist, boolean hostile, boolean protectedEntity, boolean creeper, boolean ranged,
			boolean drawingAtHer, boolean seen, boolean flyer) {
		Mob(int id, String type, double dist, boolean hostile, boolean protectedEntity, boolean creeper, boolean ranged, boolean drawingAtHer) {
			this(id, type, dist, hostile, protectedEntity, creeper, ranged, drawingAtHer, true, false);
		}
		Mob(int id, String type, double dist, boolean hostile, boolean protectedEntity, boolean creeper, boolean ranged, boolean drawingAtHer, boolean seen) {
			this(id, type, dist, hostile, protectedEntity, creeper, ranged, drawingAtHer, seen, false);
		}
	}

	/** sense's mob.targeting frame via body: target "owner" | "eve", the tick it was received. */
	record Targeting(String target, long tick) {}

	/** Her weapons: a melee weapon anywhere in her inventory (fight_melee equips it), a bow and arrows. */
	record Kit(boolean melee, boolean bow, int arrows) {}

	/** The decision: the skill and params to run, the triggering target and why. */
	record Engage(String skill, JsonObject params, int targetId, String reason) {}

	/**
	 * Null = no fight. Otherwise the trigger with the highest priority, nearest first: hit her in the last 3 s, drawing at her,
	 * within 8 and in sight, targeting her (in sight) or the owner (a fresh frame) within 24. Never a protected mob, a calm neutral, or a creeper she cannot
	 * shoot (body's CreeperFlee owns those; O284: one in bow range goes first); never without a weapon.
	 */
	static Engage pick(List<Mob> mobs, Kit kit, long now, Integer lastHitBy, long lastHitTick, Map<Integer, Targeting> targeting) {
		if (!kit.melee() && !(kit.bow() && kit.arrows() >= MIN_ARROWS)) return null;
		Mob best = null, creeper = null;
		String why = null;
		int rank = Integer.MAX_VALUE;
		for (Mob m : mobs) {
			if (!m.hostile() || m.protectedEntity()) continue;
			if (m.creeper()) {
				if (breakOffForCreeper(m.dist(), kit.bow(), kit.arrows())) return null;
				if (creeperFirst(m.dist(), kit.bow(), kit.arrows()) && (creeper == null || m.dist() < creeper.dist())) creeper = m;
				continue;
			}
			// live 45L death 17:16:03: phantoms swoop inside RANGED_MIN, so they drew fight_melee (8 unreachable). A flyer is the bow's
			// or nobody's: in sight with a usable bow, else skipped (cover is body's).
			if (m.flyer() && !(m.seen() && kit.bow() && kit.arrows() >= MIN_ARROWS)) continue;
			Targeting t = targeting.get(m.id());
			boolean fresh = t != null && now - t.tick() <= FRAME_TTL;
			String r = lastHitBy != null && lastHitBy == m.id() && now - lastHitTick <= HIT_TICKS ? "hit"
					: m.drawingAtHer() ? "drawing"
					: m.dist() <= NEAR && m.seen() ? "near" // live 13:15 on the 45L: a skeleton 7 m off and 7 below, her doorway sealed, re-engaged all night
					: fresh && m.dist() <= OWNER_RANGE && "eve".equals(t.target()) ? (m.seen() ? "targeting_eve" : null)
					: fresh && m.dist() <= OWNER_RANGE ? "owner_targeted"
					: null;
			if (r == null) continue;
			int k = switch (r) { case "hit" -> 0; case "drawing" -> 1; case "near" -> 2; default -> 3; };
			if (k < rank || (k == rank && m.dist() < best.dist())) {
				best = m;
				why = r;
				rank = k;
			}
		}
		if (creeper != null) {
			JsonObject cp = new JsonObject();
			cp.addProperty("entity_id", creeper.id());
			cp.addProperty("radius", 16);
			cp.addProperty("max_arrows", 16);
			return new Engage("fight_ranged", cp, creeper.id(), "near");
		}
		if (best == null) return null;
		JsonObject p = new JsonObject();
		if (kit.bow() && kit.arrows() >= MIN_ARROWS && (best.ranged() || best.flyer() || best.dist() >= RANGED_MIN || !kit.melee())) {
			p.addProperty("entity_id", best.id());
			p.addProperty("radius", 16);
			p.addProperty("max_arrows", 16);
			return new Engage("fight_ranged", p, best.id(), why);
		}
		if (best.dist() > NEAR) { // beyond the group radius (an owner-targeting mob): name it
			p.addProperty("entity_id", best.id());
			p.addProperty("radius", OWNER_RANGE);
		} else {
			p.addProperty("radius", NEAR);
			p.addProperty("max_targets", 8);
		}
		p.addProperty("health_floor", MELEE_FLOOR);
		return new Engage("fight_melee", p, best.id(), why);
	}
}
