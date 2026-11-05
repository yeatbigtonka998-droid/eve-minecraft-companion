package eve.combat;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import eve.body.api.BodyServices;
import eve.body.api.Skill;
import eve.body.api.Step;
import eve.body.api.SubAction;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.Mob;
import net.minecraft.world.entity.TamableAnimal;
import net.minecraft.world.entity.animal.Animal;
import net.minecraft.world.phys.Vec3;

import java.util.ArrayDeque;
import java.util.Deque;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;

/**
 * harvest_animal (O94 CP-39): kill exactly the animals sense granted, and collect their drops. Params:
 * {entity_ids:[int] (the grant), zone:{min:{x,y,z}, max:{x,y,z}} (block_pos), exclude_ids?:[int] (sense's refusals, e.g. breeding stock),
 * species?:[entity type ids], keep_min?:int (leave at least this many adults of the species in the zone), max_kills?, collect_drops?:true}.
 * Never hits an id outside entity_ids, never a baby / named / tamed / leashed / protected one or one of another type, never takes a
 * species below keep (counted from what the client sees: early, never late), and never walks or chases outside
 * the zone box: every walk_to target is clamped into it, and a target that runs out of the zone is dropped (left_zone).
 * Hits go through the body's attack_entity, as in fight_melee. Evidence: kills, drops, refused [{id, rule_id}] (sense's rule ids
 * where sense has the rule, client:* otherwise; the same as shear_sheep / milk_cow), swings.
 */
final class HarvestAnimal implements Skill {
	static final String[] EVIDENCE = {"kills", "drops", "refused", "swings", "targets"};
	private final Deque<Integer> queue = new ArrayDeque<>();
	private final Set<Integer> exclude = new HashSet<>();
	private final HarvestRules.Zone zone;
	private final Set<String> types = new HashSet<>();
	private final int maxKills, keep;
	private final boolean collect;
	private final JsonArray kills = new JsonArray(), refused = new JsonArray();
	private Map<String, Integer> inv0;
	private Entity target;
	private SubAction walk, swing;
	private int walkTicks, swings, targets, collectTicks = -1, targetSwings;
	private float targetHp0;
	private Vec3 dropAt;

	HarvestAnimal(JsonObject p) {
		for (JsonElement e : p.getAsJsonArray("entity_ids")) queue.add(e.getAsInt());
		if (p.has("exclude_ids")) for (JsonElement e : p.getAsJsonArray("exclude_ids")) exclude.add(e.getAsInt());
		JsonObject z = p.getAsJsonObject("zone");
		zone = HarvestRules.Zone.of(ints(z.getAsJsonObject("min")), ints(z.getAsJsonObject("max")));
		maxKills = p.has("max_kills") ? p.get("max_kills").getAsInt() : queue.size();
		if (p.has("species")) for (JsonElement e : p.getAsJsonArray("species")) types.add(e.getAsString());
		keep = p.has("keep_min") ? p.get("keep_min").getAsInt() : -1;
		collect = !p.has("collect_drops") || p.get("collect_drops").getAsBoolean();
	}

	private static int[] ints(JsonObject p) { // a block_pos {x, y, z}, as mine_resource and harvest_crop take it (O94e)
		return new int[] {p.get("x").getAsInt(), p.get("y").getAsInt(), p.get("z").getAsInt()};
	}

	@Override
	public boolean resumable() {
		return true;
	}

	@Override
	public void resume(BodyServices b) {
		walk = null; // a reflex took the controls: re-plan the approach
	}

	@Override
	public void cancel(BodyServices b) {
		if (walk != null) walk.cancel();
		if (swing != null) swing.cancel();
		fill(b);
	}

	@Override
	public Step tick(BodyServices b) {
		Step s = decide(b);
		if (s.done()) fill(b);
		return s;
	}

	private Step decide(BodyServices b) {
		var p = b.player();
		if (inv0 == null) {
			inv0 = new HashMap<>(b.inventoryCounts());
			if (!zone.contains(p.getX(), p.getY(), p.getZ())) return Step.failed("out_of_zone", "Eve is outside the harvest zone");
		}
		if (swing != null) { // the hit in flight
			Step st = swing.tick();
			if (!st.done()) return st;
			swing = null;
		}
		if (collectTicks >= 0) return collectDrops(b);
		if (target != null && FightMelee.dead(target)) { // killed: walk onto its drops (inside the zone) before the next
			JsonObject k = new JsonObject();
			k.addProperty("id", target.getId());
			k.addProperty("species", typeOf(target));
			k.addProperty("x", FightMelee.r2(target.getX()));
			k.addProperty("z", FightMelee.r2(target.getZ()));
			kills.add(k);
			dropAt = target.position();
			target = null;
			if (collect) {
				collectTicks = 0;
				return Step.running();
			}
		}
		if (target == null && !nextTarget(b)) return finish();
		if (!zone.contains(target.getX(), target.getY(), target.getZ())) { // it ran out of the zone: never chase it
			refuse(target.getId(), "client:left_zone");
			target = null;
			if (walk != null) walk.cancel();
			walk = null;
			return Step.running();
		}
		String w = FightMelee.bestWeapon(b); // a sword or axe when she has one; bare hands still kill an animal
		if (w != null && p.getInventory().getSelectedSlot() != b.hotbarSlot(w)) b.selectHotbar(b.hotbarSlot(w));
		if (FightMelee.reach(p, target) > FightMelee.SWING_REACH) return approach(b, target.position());
		if (walk != null) {
			walk.cancel();
			walk = null;
		}
		if (EveCombat.api >= 3) b.aim(target.getBoundingBox().getCenter(), 40f);
		else b.steerTo(target.position());
		boolean ready = p.getAttackStrengthScale(0.5f) >= Tactics.READY;
		boolean onTarget = EveCombat.api >= 3 ? b.minecraft().hitResult instanceof net.minecraft.world.phys.EntityHitResult eh && eh.getEntity() == target
				: FightMelee.yawError(p, target) < 30;
		if (ready && onTarget) {
			// The server (sense) refuses a non-eligible hit as permission.denied, which goes to the mind (appended to this action's
			// note with its rule_id), not to the client: here it only shows as swings that do no damage. 3 of those = denied.
			if (targetSwings >= 3 && target instanceof net.minecraft.world.entity.LivingEntity le && le.getHealth() >= targetHp0) {
				refuse(target.getId(), "client:denied");
				target = null;
				return Step.running();
			}
			if (!HarvestRules.keepAllows(adultsInZone(b, typeOf(target)), keep)) { // the herd changed since the pick: re-check per hit
				refuse(target.getId(), "client:keep_floor");
				target = null;
				return Step.running();
			}
			JsonObject a = new JsonObject();
			a.addProperty("entity_id", target.getId());
			swing = b.run("attack_entity", a);
			swings++;
			targetSwings++;
			Step st = swing.tick();
			if (st.done()) swing = null;
			return st.done() ? Step.running() : st;
		}
		return Step.running();
	}

	/** The next id that passes every rule; refused ones are recorded with their reason and never touched. */
	private boolean nextTarget(BodyServices b) {
		while (kills.size() < maxKills && !queue.isEmpty()) {
			int id = queue.poll();
			Entity e = b.minecraft().level.getEntity(id);
			boolean alive = e != null && !FightMelee.dead(e);
			var a = new HarvestRules.Animal(alive, e instanceof Animal, alive && (types.isEmpty() || types.contains(typeOf(e))),
					e instanceof Animal an && an.isBaby(), alive && e.hasCustomName(), e instanceof TamableAnimal t && t.isTame(),
					e instanceof Mob m && m.isLeashed(), alive && b.isProtected(e), exclude.contains(id),
					alive && zone.contains(e.getX(), e.getY(), e.getZ()));
			String why = HarvestRules.refusal(a);
			if (why == null && !HarvestRules.keepAllows(adultsInZone(b, typeOf(e)), keep)) why = "client:keep_floor";
			if (why != null) {
				refuse(id, why);
				continue;
			}
			target = e;
			targets++;
			targetSwings = 0;
			targetHp0 = e instanceof net.minecraft.world.entity.LivingEntity le ? le.getHealth() : 0;
			return true;
		}
		return false;
	}

	private static String typeOf(Entity e) {
		return net.minecraft.core.registries.BuiltInRegistries.ENTITY_TYPE.getKey(e.getType()).toString();
	}

	/** Living adults of this species the client sees inside the zone (keep floor: the client can only see fewer, never more). */
	private int adultsInZone(BodyServices b, String type) {
		int n = 0;
		for (Entity o : b.minecraft().level.entitiesForRendering())
			if (o instanceof Animal an && !an.isBaby() && !FightMelee.dead(o) && typeOf(o).equals(type) && zone.contains(o.getX(), o.getY(), o.getZ())) n++;
		return n;
	}

	private Step approach(BodyServices b, Vec3 to) {
		if (walk != null) {
			Step st = walk.tick();
			if (!st.done() && ++walkTicks < 60) return st; // re-plan every 3 s: animals wander
			if (!st.done()) walk.cancel();
			walk = null;
		}
		double[] c = zone.clamp(to.x, to.y, to.z);
		walk = b.run("walk_to", FightMelee.posParams(new Vec3(c[0], c[1], c[2])));
		walkTicks = 0;
		return Step.running();
	}

	private Step collectDrops(BodyServices b) {
		var p = b.player();
		if (dropAt == null || ++collectTicks > 60 || FightMelee.horiz(p.position(), dropAt) < 0.8) { // picked up, or 3 s spent
			collectTicks = -1;
			dropAt = null;
			if (walk != null) walk.cancel();
			walk = null;
			return Step.running();
		}
		return approach(b, dropAt);
	}

	private Step finish() {
		if (kills.size() > 0) return Step.ok();
		return refused.size() > 0 ? Step.failed("not_granted", "no granted animal could be harvested (see refused)")
				: Step.failed("no_target", "no animal ids to harvest");
	}

	private void refuse(int id, String why) {
		JsonObject r = new JsonObject();
		r.addProperty("id", id);
		r.addProperty("rule_id", why);
		refused.add(r);
	}

	private void fill(BodyServices b) {
		JsonObject ev = b.evidence();
		ev.add("kills", kills);
		ev.add("refused", refused);
		ev.addProperty("swings", swings);
		ev.addProperty("targets", targets);
		JsonObject drops = new JsonObject();
		if (inv0 != null) b.inventoryCounts().forEach((item, n) -> {
			int d = n - inv0.getOrDefault(item, 0);
			if (d > 0) drops.addProperty(item, d);
		});
		ev.add("drops", drops);
	}
}
