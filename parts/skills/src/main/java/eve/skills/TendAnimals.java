package eve.skills;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import eve.body.api.BodyServices;
import eve.body.api.Skill;
import eve.body.api.Step;
import eve.body.api.SubAction;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.Mob;
import net.minecraft.world.entity.TamableAnimal;
import net.minecraft.world.entity.animal.cow.AbstractCow;
import net.minecraft.world.entity.animal.sheep.Sheep;
import net.minecraft.world.entity.item.ItemEntity;
import net.minecraft.world.phys.AABB;

import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Set;

/**
 * O94 (CP-39) shear_sheep / milk_cow {entity_ids, zone{min, max}, count?}: shears or milks only the animals sense granted
 * (entity_ids from the mind's entity grant, as combat's harvest_animal takes them), each at most once a call, never outside
 * zone and never chased out of it. The skill re-checks each id on the client and refuses it in evidence.refused
 * [{id, rule_id}] with sense's rule ids verbatim where sense has the rule (animal:baby, animal:leashed, protected:named,
 * protected:pets) and "client:" ones where only the client can tell (client:gone, client:wrong_type, client:out_of_zone,
 * client:left_zone, client:sheared, client:unreachable). The server's first refusal (zone_denied, its rule id in the note)
 * ends the skill. Every end says why in stop_reason.
 */
final class TendAnimals implements Skill {
	final boolean wool;
	final Set<Integer> ids = new LinkedHashSet<>(), tried = new LinkedHashSet<>();
	final AABB zone;
	final int want;
	final boolean countGiven; // no count = every eligible granted animal: refusals alone are not a shortfall
	final JsonArray used = new JsonArray(), refused = new JsonArray();
	final Map<Integer, Integer> walks = new HashMap<>();
	Map<String, Integer> before;
	Entity target;
	SubAction child;
	String stage = "pick";
	int done, settle, pickupTries;

	TendAnimals(boolean wool, JsonObject p) {
		this.wool = wool;
		for (JsonElement e : p.getAsJsonArray("entity_ids")) ids.add(e.getAsInt());
		JsonObject z = p.getAsJsonObject("zone");
		zone = Gather.box(Menus.pos(z.getAsJsonObject("min")), Menus.pos(z.getAsJsonObject("max")));
		countGiven = p.has("count");
		want = countGiven ? Math.min(p.get("count").getAsInt(), ids.size()) : ids.size();
	}

	@Override
	public Step tick(BodyServices b) {
		LocalPlayer p = b.player();
		if (before == null) {
			if (settle++ == 0) Menus.resync(b);
			if (settle <= Menus.SETTLE_TICKS) return Step.running();
			before = b.inventoryCounts();
		}
		if (child != null) {
			Step s = child.tick();
			if (!s.done()) return s;
			child = null;
			Step r = after(b, s);
			if (r != null) return r;
		}
		if (stage.equals("pickup")) { // the sheared wool near the sheep, inside the zone only
			ItemEntity drop = p.level().getEntitiesOfClass(ItemEntity.class, target.getBoundingBox().inflate(4),
					e -> Menus.id(e.getItem()).endsWith("_wool") && zone.contains(e.position())).stream().findFirst().orElse(null);
			if (drop == null || ++pickupTries > 6) { stage = "pick"; return Step.running(); }
			child = b.run("walk_to", Gather.at(drop.position(), 0.5));
			return Step.running();
		}
		if (done >= want) return end(b, Step.ok());
		if (stage.equals("pick")) {
			target = pick(b, p);
			if (target == null && done > 0 && !countGiven && !deniedSeen) return end(b, Step.ok()); // every eligible one tended, the rest refused with why
			if (target == null)
				return end(b, Step.failed(done > 0 || deniedSeen ? "partial" : "not_found", "no granted " + (wool ? "sheep" : "cow") + " left to "
						+ (wool ? "shear" : "milk") + " (" + refused.size() + " refused)"));
			tried.add(target.getId());
			stage = "tool";
		}
		if (stage.equals("tool")) {
			int t = Gather.toHand(b, p, tool(wool));
			if (t < 0) return end(b, Step.failed(done > 0 ? "partial" : "no_tool", "no " + (wool ? "shears" : "empty bucket") + " left in the inventory"));
			if (t == 0) return Step.running();
			stage = "walk";
		}
		if (stage.equals("walk")) {
			if (target.isRemoved()) { refuse(target.getId(), "client:gone"); stage = "pick"; return Step.running(); }
			if (!zone.contains(target.position())) { refuse(target.getId(), "client:left_zone"); stage = "pick"; return Step.running(); } // never chased
			if (p.isWithinEntityInteractionRange(target, -0.5)) {
				JsonObject a = new JsonObject();
				a.addProperty("entity_id", target.getId());
				stage = "use";
				child = b.run("use_item", a);
				return Step.running();
			}
			if (walks.merge(target.getId(), 1, Integer::sum) > 4) { refuse(target.getId(), "client:unreachable"); stage = "pick"; return Step.running(); }
			child = b.run("walk_to", Gather.at(target.position(), 1.5));
			return Step.running();
		}
		return Step.running();
	}

	Step after(BodyServices b, Step s) {
		if ("zone_denied".equals(s.code())) { // sense's permission.denied rule id rides in the action note
			java.util.regex.Matcher m = java.util.regex.Pattern.compile("(animal|protected|crop):[A-Za-z0-9_:.-]+").matcher(s.note() != null ? s.note() : "");
			refuse(target.getId(), m.find() ? m.group() : "server:zone_denied");
			return end(b, Step.failed("zone_denied", "the server refused " + stage + " on " + target.getId() + (s.note() != null ? ": " + s.note() : "")));
		}
		if (stage.equals("use")) {
			JsonObject o = new JsonObject();
			o.addProperty("id", target.getId());
			o.addProperty("type", target.getType().toShortString());
			o.addProperty("result", s.status() + (s.code() != null ? "/" + s.code() : ""));
			used.add(o);
			if ("ok".equals(s.status())) {
				done++;
				b.progress(Math.min(99, 100 * done / Math.max(1, want)), done + "/" + want);
				pickupTries = 0;
				stage = wool ? "pickup" : "pick";
			} else if (noEffect(s.code())) { // O282: never retried; only the mind hears the server's refusal, and it says failed/zone_denied
				deniedSeen = true;
				refuse(target.getId(), SERVER_DENIED);
				stage = "pick";
			} else if (walks.merge(target.getId(), 1, Integer::sum) <= 4) {
				tried.remove(target.getId()); // it moved (out_of_range, obstructed): walk to it again
				stage = "pick";
			} else {
				refuse(target.getId(), "client:unreachable");
				stage = "pick";
			}
		}
		return null;
	}

	/** The nearest granted id this call may still use; every other granted id met on the way is refused once, with why. */
	Entity pick(BodyServices b, LocalPlayer p) {
		Map<Integer, Animal> seen = new HashMap<>();
		for (int id : ids) if (!tried.contains(id)) seen.put(id, animal(b, p, p.level().getEntity(id)));
		Integer id = pick(wool, ids, tried, seen, (i, why) -> { refuse(i, why); tried.add(i); });
		return id == null ? null : p.level().getEntity(id);
	}

	/**
	 * What the client sees of one granted id (S93: the facts the rules need, so tests can give a synthetic herd). guarded =
	 * the body's protected view (sense's grants: villagers' animals, envelope extra types).
	 */
	record Animal(boolean present, boolean sheep, boolean cow, boolean inZone, boolean baby, boolean shearable, boolean named,
			boolean tame, boolean leashed, boolean guarded, double distSq) {}

	Animal animal(BodyServices b, LocalPlayer p, Entity e) {
		if (e == null || e.isRemoved()) return new Animal(false, false, false, false, false, false, false, false, false, false, 0);
		return new Animal(true, e instanceof Sheep, e instanceof AbstractCow, zone.contains(e.position()),
				e instanceof net.minecraft.world.entity.AgeableMob am && am.isBaby(), e instanceof Sheep sh && sh.readyForShearing(),
				e.hasCustomName(), e instanceof TamableAnimal t && t.isTame(), e instanceof Mob m && m.isLeashed(), b.isProtected(e), e.distanceToSqr(p));
	}

	/** Pure: sense's rule id (or a client: one), or null when the animal may be tended. Order as sense checks them. */
	static String why(boolean wool, Animal a) {
		if (a == null || !a.present()) return "client:gone";
		if (wool ? !a.sheep() : !a.cow()) return "client:wrong_type";
		if (!a.inZone()) return "client:out_of_zone";
		if (a.baby()) return "animal:baby";
		if (wool && !a.shearable()) return "client:sheared";
		if (a.named()) return "protected:named";
		if (a.tame()) return "protected:pets";
		if (a.leashed()) return "animal:leashed";
		if (a.guarded()) return "protected:pets";
		return null;
	}

	/** Pure: the nearest eligible granted id not yet tried; each ineligible one is handed to refuse with why, once. */
	static Integer pick(boolean wool, Set<Integer> ids, Set<Integer> tried, Map<Integer, Animal> seen, java.util.function.BiConsumer<Integer, String> refuse) {
		Integer best = null;
		double bd = Double.MAX_VALUE;
		for (int id : ids) {
			if (tried.contains(id)) continue;
			Animal a = seen.get(id);
			String why = why(wool, a);
			if (why != null) { refuse.accept(id, why); continue; }
			if (a.distSq() < bd) { bd = a.distSq(); best = id; }
		}
		return best;
	}

	static final String SERVER_DENIED = "server_denied?"; // a guess by design: body's use_item saw no effect, the rule id goes to the mind
	boolean deniedSeen;

	/** Pure (O282): the use had no effect on this target: a server refusal looks like this to the client, so one try only. */
	static boolean noEffect(String code) {
		return "nothing_to_use".equals(code);
	}

	/** Pure: the item the hand needs. */
	static String tool(boolean wool) {
		return wool ? "minecraft:shears" : "minecraft:bucket";
	}

	void refuse(int id, String reason) {
		JsonObject o = new JsonObject();
		o.addProperty("id", id);
		o.addProperty("rule_id", reason);
		refused.add(o);
	}

	Step end(BodyServices b, Step s) {
		JsonObject e = b.evidence();
		if (before != null) {
			Map<String, Integer> d = Plans.diff(before, b.inventoryCounts());
			d.values().removeIf(v -> v <= 0);
			e.add("gathered", Menus.json(d));
		}
		e.add("animals", used);
		e.add("refused", refused);
		e.addProperty("stop_reason", Gather.stopReason(s, done + (countGiven ? "/" + want : "") + (wool ? " sheared" : " milked") + ", "
				+ refused.size() + " refused"));
		return s;
	}

	@Override
	public void cancel(BodyServices b) {
		if (child != null) child.cancel();
		end(b, null);
	}
}
