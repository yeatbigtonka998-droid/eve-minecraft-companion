package eve.skills;

import com.google.gson.JsonObject;
import eve.body.api.BodyServices;
import eve.body.api.Skill;
import eve.body.api.Step;
import eve.body.api.SubAction;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.tags.FluidTags;
import net.minecraft.world.entity.player.Input;
import net.minecraft.world.level.Level;
import net.minecraft.world.phys.Vec3;

/**
 * dig_up{toward, max_blocks?, torch_every?, min_health?, minutes?} (O86): the way out when go_home is home_blocked
 * underground. With a pickaxe in the hotbar it is prospect's climbing staircase (Prospect.digUp: one block up a step toward
 * `toward`, with its threat, hazard, seal, bridge and zone-edge rules, never walking back). Without one (O86a: a17 ended
 * with her pickaxe worn out) it pillars up an open column with filler: look down, jump, and at the top of the jump place a
 * block in the cell she left; it ends no_tool as soon as a solid block over her head would have to be broken.
 */
final class DigUp implements Skill {
	static final Input JUMP = new Input(false, false, false, false, true, false, false);
	final JsonObject params;
	final BlockPos toward;
	final int maxPlaced, minutes;
	final float minHealth;
	Prospect tunnel;
	boolean decided, rising;
	BlockPos start, base;
	SubAction child;
	int placed, tries, waitTicks;
	String stop;
	long t0;

	DigUp(JsonObject p) {
		params = p;
		toward = Menus.pos(p.getAsJsonObject("toward"));
		maxPlaced = p.has("max_blocks") ? p.get("max_blocks").getAsInt() : 128;
		minutes = p.has("minutes") ? p.get("minutes").getAsInt() : 8;
		minHealth = p.has("min_health") ? p.get("min_health").getAsFloat() : 12f;
	}

	@Override
	public Step tick(BodyServices b) {
		LocalPlayer p = b.player();
		if (!decided) {
			decided = true;
			boolean pick = java.util.stream.IntStream.range(0, 9).anyMatch(i -> {
				String id = Menus.id(p.getInventory().getItem(i));
				return id != null && id.endsWith("_pickaxe");
			});
			if (pick) tunnel = Prospect.digUp(params);
			start = Prospect.feetCell(p);
			t0 = System.nanoTime();
		}
		if (tunnel != null) return tunnel.tick(b);
		return pillar(b, p, p.level());
	}

	/** One pillar cycle per landing: aim down, jump, place in the cell left behind at the top of the jump. */
	Step pillar(BodyServices b, LocalPlayer p, Level lv) {
		if (child != null) {
			Step s = child.tick();
			if (!s.done()) return s;
			child = null;
			rising = false;
			if ("ok".equals(s.status())) { placed++; tries = 0; return Step.running(); }
			if ("zone_denied".equals(s.code())) return end(b, p, "zone_denied", Step.failed("zone_denied", "the server refused a pillar block"));
			if (++tries >= 3) return end(b, p, "pillar failed", Step.failed("unreachable", "pillar: place failed 3 times in a row ("
					+ s.status() + "/" + s.code() + (s.note() != null ? ": " + s.note() : "") + ") [" + Prospect.stand(p) + "; base "
					+ Prospect.cell(lv, base) + ", over it " + Prospect.cell(lv, base.above()) + ", " + Prospect.cell(lv, base.above(2))
					+ ", floor " + Prospect.cell(lv, base.below()) + "]")); // O86u: what was there
			return Step.running();
		}
		if (p.getHealth() < minHealth) return end(b, p, "hurt", Step.failed("hurt", "health " + p.getHealth() + " below " + minHealth));
		BlockPos c = rising ? base : Prospect.feetCell(p);
		if (!rising) {
			if (c.getY() >= toward.getY() || c.getY() >= toward.getY() - 2 && lv.canSeeSky(c.above())) return end(b, p, "reached", Step.ok());
			if (placed >= maxPlaced) return end(b, p, "max_blocks", Step.failed("not_reached", "pillar: " + placed + " blocks placed"));
			if ((System.nanoTime() - t0) / 60_000_000_000L >= minutes) return end(b, p, "timer", Step.failed("not_reached", "pillar: out of time"));
			BlockPos over = c.above(2);
			for (BlockPos q : new BlockPos[] {over, over.above()})
				if (lv.getFluidState(q).is(FluidTags.LAVA)) return end(b, p, "hazard:lava", Step.failed("hazard", "lava over her head at " + q.toShortString()));
			if (Prospect.solid(lv, over)) return end(b, p, "solid over her head",
					Step.failed("no_tool", "no pickaxe and " + MineResource.id(lv.getBlockState(over)) + " over her head at " + over.toShortString()));
		}
		String item = filler(b);
		if (item == null) return end(b, p, "no filler", Step.failed("not_reached", "pillar: no filler left after " + placed + " blocks"));
		int h = Gather.toHand(b, p, item);
		if (h < 0) return end(b, p, "no filler", Step.failed("not_reached", "pillar: no filler in reach of the hotbar"));
		if (h == 0) return Step.running();
		if (!rising) {
			// look straight down at the floor block before jumping, so the place at the top of the jump only clicks
			if (!b.aim(Vec3.atCenterOf(c.below()).add(0, 0.5, 0), 30f)) return Step.running();
			if (!p.onGround() && !p.isInWater()) return Step.running();
			base = c;
			rising = true;
			waitTicks = 0;
			return Step.running(JUMP);
		}
		if (p.getY() >= base.getY() + 1.0) { // her feet are clear of the cell she left: put a block in it, the floor below carries it
			JsonObject a = new JsonObject();
			a.add("pos", HarvestReplant.json(base));
			a.addProperty("item", item);
			a.addProperty("face", "up");
			child = b.run("place_block", a);
			return Step.running();
		}
		if (++waitTicks > 30) { rising = false; return Step.running(); } // never got up (a ceiling, a cobweb): try again
		return Step.running(JUMP); // keep rising (in water, holding jump swims up)
	}

	/** Mined stone or dirt first, then planks while more than 6 are left for sticks (O86). */
	static String filler(BodyServices b) {
		java.util.Map<String, Integer> inv = b.inventoryCounts();
		for (String i : Prospect.FILLERS) if (inv.getOrDefault(i, 0) > 0) return i;
		for (java.util.Map.Entry<String, Integer> e : inv.entrySet()) if (e.getKey().endsWith("_planks") && e.getValue() > 6) return e.getKey();
		return null;
	}

	Step end(BodyServices b, LocalPlayer p, String why, Step s) {
		stop = why;
		JsonObject e = b.evidence();
		BlockPos c = p != null ? Prospect.feetCell(p) : start;
		e.addProperty("climbed", start == null || c == null ? 0 : c.getY() - start.getY());
		e.addProperty("reached", "reached".equals(why));
		e.addProperty("blocks_broken", 0);
		e.addProperty("stop_reason", why + " (pillar " + placed + ")");
		if (c != null) e.add("stop_at", HarvestReplant.json(c));
		return s;
	}

	@Override
	public void cancel(BodyServices b) {
		if (tunnel != null) tunnel.cancel(b);
		else end(b, b.player(), stop != null ? stop : "cancelled", null);
	}
}
