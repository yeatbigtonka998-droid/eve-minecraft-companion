package eve.example;

import com.google.gson.JsonObject;
import eve.body.api.BodyServices;
import eve.body.api.EveSkillProvider;
import eve.body.api.Skill;
import eve.body.api.SkillRegistrar;
import eve.body.api.Step;
import eve.body.api.SubAction;

/** DEV ONLY example provider: `example_walk_look` = walk_to(pos) through the body (Baritone), then look_at(look). */
public final class ExampleProvider implements EveSkillProvider {
	@Override
	public void registerSkills(SkillRegistrar r) {
		r.register("example_use_block", 1, body -> useBlock());
		// API v3 checks: guard (shield owned by the action) and a bow shot (aim + held use)
		r.register("example_guard", 1, body -> new Skill() {
			@Override
			public Step tick(BodyServices b) {
				// a shield blocks from the front only: face the nearest hostile while guarding (attempt 3 lost trials 3-4 from behind)
				net.minecraft.world.entity.Entity near = null;
				for (net.minecraft.world.entity.Entity e : b.minecraft().level.entitiesForRendering())
					if (e instanceof net.minecraft.world.entity.monster.Enemy && e.isAlive() && (near == null || e.distanceTo(b.player()) < near.distanceTo(b.player()))) near = e;
				if (near != null) b.aim(near.getEyePosition(), 60f);
				boolean up = b.guard(true);
				if (b.ticks() == 0) b.evidence().addProperty("guard_up_first_tick", up);
				return b.ticks() >= b.params().get("ticks").getAsInt() ? Step.ok() : Step.running();
			}
		});
		r.register("example_bow", 1, body -> new Skill() {
			int drawn = -1;

			@Override
			public Step tick(BodyServices b) {
				JsonObject t = b.params().getAsJsonObject("pos");
				net.minecraft.world.phys.Vec3 at = new net.minecraft.world.phys.Vec3(t.get("x").getAsDouble(), t.get("y").getAsDouble(), t.get("z").getAsDouble());
				boolean aimed = b.aim(at, 30f);
				if (drawn < 0) {
					if (!aimed) return Step.running();
					b.holdUse(true);
					drawn = 0;
					return Step.running();
				}
				if (++drawn == 22) {
					b.evidence().addProperty("ticks_using", b.player().getTicksUsingItem());
					b.holdUse(false); // a drawn bow fires on release
				}
				return drawn >= 30 ? Step.ok() : Step.running();
			}
		});
		r.register("example_walk_look", 1, body -> new Skill() {
			SubAction step;
			boolean looking;

			@Override
			public Step tick(BodyServices b) {
				if (step == null) step = b.run("walk_to", obj("pos", b.params().get("pos")));
				Step s = step.tick();
				if (!s.done()) return s; // the child's input (Baritone drives while it paths)
				b.evidence().add(looking ? "look" : "walk", step.result());
				if (looking || !"ok".equals(s.status())) return s;
				looking = true;
				step = b.run("look_at", obj("pos", b.params().get("look")));
				return Step.running();
			}
		});
	}

	/** API v2 check: `example_use_block` opens the block's menu (left open), clicks one slot, waits `hold` ticks, ends ok. */
	static Skill useBlock() {
		return new Skill() {
			SubAction open;
			int held;

			@Override
			public Step tick(BodyServices b) {
				if (open == null) {
					JsonObject pos = b.params().getAsJsonObject("pos");
					open = b.useBlock(new net.minecraft.core.BlockPos(pos.get("x").getAsInt(), pos.get("y").getAsInt(), pos.get("z").getAsInt()));
				}
				Step s = open.tick();
				if (!s.done()) return s;
				if (!"ok".equals(s.status())) return s;
				if (held == 0) {
					b.evidence().add("open", open.result());
					b.evidence().addProperty("click_sent", b.clickSlot(1, 0, net.minecraft.world.inventory.ContainerInput.PICKUP));
				}
				b.evidence().addProperty("menu_open_ticks", ++held);
				return held >= b.params().get("hold").getAsInt() ? Step.ok() : Step.running();
			}
		};
	}

	static JsonObject obj(String k, com.google.gson.JsonElement v) {
		JsonObject o = new JsonObject();
		o.add(k, v);
		return o;
	}
}
