package eve.skills;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import eve.body.api.BodyServices;
import eve.body.api.Skill;
import eve.body.api.Step;
import eve.body.api.SubAction;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.animal.cow.AbstractCow;
import net.minecraft.world.entity.animal.sheep.Sheep;
import net.minecraft.world.entity.item.ItemEntity;
import net.minecraft.world.inventory.ContainerInput;

import java.util.HashSet;
import java.util.Map;
import java.util.Set;

/**
 * gather{product: wood|wool|milk, count, radius}: renewable resources by qualified means only. wood = mine_resource over
 * #minecraft:logs. wool = shears on an adult unsheared sheep, then the dropped wool picked up. milk = a bucket on an adult
 * cow. Never kills; every animal the body's protected view covers (pets, named, villagers' animals, envelope extra types)
 * and every baby is skipped and counted. No eligible animal or no tool ends as a bounded shortage report.
 */
final class Gather implements Skill {
	final String product;
	final int want, radius;
	final Set<Integer> tried = new HashSet<>();
	final java.util.Map<Integer, Integer> attempts = new java.util.HashMap<>();
	final JsonArray used = new JsonArray();
	MineResource wood;
	Map<String, Integer> before;
	Entity target;
	SubAction child;
	String stage = "pick";
	int protectedSkipped, babies, misses, settle, pickupTries;

	Gather(JsonObject p) {
		product = p.get("product").getAsString();
		want = p.has("count") ? p.get("count").getAsInt() : 0;
		radius = p.has("radius") ? p.get("radius").getAsInt() : 16;
		if (product.equals("wood")) { // O133: count 0 or absent = every tree in radius, whole trees, nearest first
			JsonObject m = new JsonObject();
			m.addProperty("tag", "minecraft:logs");
			m.addProperty("count", want);
			m.addProperty("radius", radius);
			wood = new MineResource(m);
		}
	}

	@Override
	public Step tick(BodyServices b) {
		if (wood != null) { // logs from trees: the qualified mine_resource path, reported as gather evidence
			Step s = wood.tick(b);
			if (s.done()) b.evidence().add("gathered", b.evidence().get("drops"));
			return s;
		}
		LocalPlayer p = b.player();
		if (before == null) { // fresh inventory view before the baseline (Menus.resync)
			if (settle++ == 0) Menus.resync(b);
			if (settle <= Menus.SETTLE_TICKS) return Step.running();
			before = b.inventoryCounts();
		}
		if (got(b) >= want) return end(b, Step.ok());
		if (child != null) {
			Step s = child.tick();
			if (!s.done()) return s;
			child = null;
			return after(b, p, s);
		}
		if (stage.equals("pick")) {
			target = pick(b, p);
			if (target == null)
				return end(b, Step.failed(got(b) > 0 ? "partial" : "not_found", "no eligible " + animal() + " within " + radius
						+ " (protected skipped " + protectedSkipped + ", babies " + babies + ")"));
			tried.add(target.getId());
			stage = "tool";
		}
		if (stage.equals("tool")) {
			int t = toHand(b, p, tool());
			if (t < 0) return end(b, Step.failed("no_tool", "no " + tool() + " in the inventory"));
			if (t == 0) return Step.running();
			stage = "walk";
		}
		if (stage.equals("walk")) {
			if (target.isRemoved()) { stage = "pick"; return Step.running(); }
			if (p.isWithinEntityInteractionRange(target, -0.5)) {
				JsonObject a = new JsonObject();
				a.addProperty("entity_id", target.getId());
				stage = "use";
				child = b.run("use_item", a);
				return Step.running();
			}
			child = b.run("walk_to", at(target.position(), 1.5));
			return Step.running();
		}
		if (stage.equals("pickup")) { // wool on the ground near the sheep: walk over each piece
			ItemEntity drop = p.level().getEntitiesOfClass(ItemEntity.class, target.getBoundingBox().inflate(6), e -> Menus.id(e.getItem()).endsWith("_wool"))
					.stream().findFirst().orElse(null);
			if (drop == null || ++pickupTries > 6) { stage = "pick"; return Step.running(); }
			child = b.run("walk_to", at(drop.position(), 0.5));
			return Step.running();
		}
		return Step.running();
	}

	Step after(BodyServices b, LocalPlayer p, Step s) {
		if ("zone_denied".equals(s.code())) return end(b, Step.failed("zone_denied", "the server refused " + stage));
		switch (stage) {
			case "walk" -> {
				if (!"ok".equals(s.status()) && !p.isWithinEntityInteractionRange(target, 0) && ++misses >= 3) {
					misses = 0;
					stage = "pick"; // it walked off: another animal
				}
			}
			case "use" -> {
				JsonObject o = new JsonObject();
				o.addProperty("entity_id", target.getId());
				o.addProperty("type", target.getType().toShortString());
				o.addProperty("result", s.status() + (s.code() != null ? "/" + s.code() : ""));
				used.add(o);
				pickupTries = 0;
				// a cow can be milked again and again: after a success she stays eligible (CP-39 a1: one failed try on one of
				// two cows left nothing to milk). A sheared sheep is no longer readyForShearing, so wool needs no such rule.
				if (product.equals("milk") && "ok".equals(s.status())) tried.remove(target.getId());
				// animals move: an out_of_range / obstructed use is retried (walk again), up to 3 tries per animal (a2)
				// O282: no effect at all is not a moved animal: no retry (a server refusal looks like this to the client)
				if (!"ok".equals(s.status()) && !TendAnimals.noEffect(s.code()) && attempts.merge(target.getId(), 1, Integer::sum) < 3) tried.remove(target.getId());
				stage = product.equals("wool") && "ok".equals(s.status()) ? "pickup" : "pick";
			}
			case "pickup" -> {}
			default -> {}
		}
		return Step.running();
	}

	Entity pick(BodyServices b, LocalPlayer p) {
		Entity best = null;
		double bd = Double.MAX_VALUE;
		for (Entity e : p.level().getEntities(p, p.getBoundingBox().inflate(radius))) {
			boolean kind = product.equals("wool") ? e instanceof Sheep : e instanceof AbstractCow;
			if (!kind || tried.contains(e.getId())) continue;
			if (b.isProtected(e)) { protectedSkipped++; tried.add(e.getId()); continue; }
			if (e instanceof Sheep sh ? !sh.readyForShearing() : ((AbstractCow) e).isBaby()) { babies++; tried.add(e.getId()); continue; }
			double d = e.distanceToSqr(p);
			if (d < bd) { bd = d; best = e; }
		}
		return best;
	}

	/** 1 = the item is in the selected hotbar slot; 0 = a step was taken (swap or select), call again; -1 = none at all. */
	static int toHand(BodyServices b, LocalPlayer p, String item) {
		int hb = b.hotbarSlot(item);
		if (hb >= 0) {
			if (p.getInventory().getSelectedSlot() == hb) return 1;
			b.selectHotbar(hb);
			return 0;
		}
		for (int i = 9; i < 36; i++)
			if (item.equals(Menus.id(p.getInventory().getItem(i)))) {
				if (p.containerMenu != p.inventoryMenu) b.closeMenu();
				b.clickSlot(i, p.getInventory().getSelectedSlot(), ContainerInput.SWAP); // inventory menu slot index == inventory index here
				return 0;
			}
		return -1;
	}

	String animal() { return product.equals("wool") ? "sheep" : "cow"; }

	String tool() { return product.equals("wool") ? "minecraft:shears" : "minecraft:bucket"; }

	int got(BodyServices b) {
		Map<String, Integer> d = Plans.diff(before, b.inventoryCounts());
		return d.entrySet().stream().filter(e -> product.equals("wool") ? e.getKey().endsWith("_wool") : e.getKey().equals("minecraft:milk_bucket"))
				.mapToInt(e -> Math.max(0, e.getValue())).sum();
	}

	static JsonObject at(net.minecraft.world.phys.Vec3 v, double tol) {
		JsonObject a = new JsonObject(), c = new JsonObject();
		c.addProperty("x", v.x);
		c.addProperty("y", v.y);
		c.addProperty("z", v.z);
		a.add("pos", c);
		a.addProperty("tolerance", tol);
		return a;
	}

	Step end(BodyServices b, Step s) {
		JsonObject e = b.evidence();
		if (before != null) {
			Map<String, Integer> d = Plans.diff(before, b.inventoryCounts());
			d.values().removeIf(v -> v <= 0);
			e.add("gathered", Menus.json(d));
		}
		e.add("animals", used);
		e.addProperty("protected_skipped", protectedSkipped);
		e.addProperty("babies_skipped", babies);
		return s;
	}

	/** O94: why a skill ended, never null: "count" when ok, else code and note; a cancel says so. */
	static String stopReason(Step s, String progress) {
		if (s == null) return "cancelled (" + progress + ")";
		if ("ok".equals(s.status())) return "count (" + progress + ")";
		return s.code() + (s.note() != null ? ": " + s.note() : "") + " (" + progress + ")";
	}

	/** O94: a grant's block box min..max as an entity-space box (inclusive of both corner blocks, either order). */
	static net.minecraft.world.phys.AABB box(net.minecraft.core.BlockPos a, net.minecraft.core.BlockPos c) {
		return new net.minecraft.world.phys.AABB(Math.min(a.getX(), c.getX()), Math.min(a.getY(), c.getY()), Math.min(a.getZ(), c.getZ()),
				Math.max(a.getX(), c.getX()) + 1, Math.max(a.getY(), c.getY()) + 1, Math.max(a.getZ(), c.getZ()) + 1);
	}

	@Override
	public void cancel(BodyServices b) {
		if (wood != null) wood.cancel(b);
		else end(b, null);
	}
}
