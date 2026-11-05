package eve.body;

import com.google.gson.JsonObject;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.util.Mth;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.player.Input;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.phys.Vec3;

/**
 * Phase 1 primitives (CP-09). Each is a bounded contract: the arbiter calls {@link Act#tick} once per client tick and owns
 * deadline, cancel and reporting; the skill returns the inputs it wants and ends with evidence read from the client world.
 */
final class Skills {
	abstract static class Act {
		final String id, skill;
		final JsonObject params;
		final long startNs, deadlineNs;
		long lastProgressNs;
		/** Ticks already run before this call (0 on the first call). */
		int ticks;
		String status, code, note;
		Integer pct;
		/** Non-null forces sneak on/off while this action runs (place_block on, open_container off); null = the held sneak. */
		Boolean sneak;
		/** True while a library (Baritone) drives player.input; the arbiter leaves it alone until this is false or the action ends. */
		boolean ownsInput;
		/** Started by the body itself for an owner control op (home): reported as event{control}, not as an action.result. */
		boolean internal;
		/** A reflex holds the controls; the action is not ticked until it ends. */
		boolean paused;
		/** The client tick it was paused at (a provider's guard claim is judged as of then while it waits, O102d). */
		long pausedTick;
		final JsonObject evidence = new JsonObject();

		Act(String skill, String id, JsonObject params, long startNs, long deadlineNs) {
			this.skill = skill;
			this.id = id;
			this.params = params;
			this.startNs = startNs;
			this.deadlineNs = deadlineNs;
			this.lastProgressNs = startNs;
		}

		abstract Input tick(Minecraft mc, LocalPlayer p);

		/** Fills the registry's evidence keys from what the client observes now; called on every terminal path and for progress. */
		void fill(Minecraft mc, LocalPlayer p) {}

		/** Releases anything this skill holds outside the arbiter's input (none of the primitives do). */
		void cancel(Minecraft mc, LocalPlayer p) {}

		/**
		 * Can carry on after a reflex pre-empted it. ponytail: only movement/look/state primitives say yes; a hand skill
		 * (attack, use, mine, place, open, chat) ends `interrupted` and the mind re-plans it, rather than resuming mid-click.
		 * place_block resumes once its click is sent (only the server's answer is left).
		 */
		boolean resumable() {
			return false;
		}

		/** Called once when the action continues after a reflex (only if resumable). */
		void resume() {}

		Input done(String st, String c, String n) {
			status = st;
			code = c;
			note = n;
			return Input.EMPTY;
		}
	}

	static Act create(String skill, String id, JsonObject params, long startNs, long deadlineNs) {
		return switch (skill) {
			case "stop" -> new Stop(id, params, startNs, deadlineNs);
			case "look_at" -> new LookAt(id, params, startNs, deadlineNs);
			case "walk_to" -> baritone() ? new BaritoneWalk(id, params, startNs, deadlineNs) : new WalkTo(id, params, startNs, deadlineNs);
			case "select_hotbar" -> new SelectHotbar(id, params, startNs, deadlineNs);
			case "sneak" -> new Sneak(id, params, startNs, deadlineNs);
			case "jump" -> new Jump(id, params, startNs, deadlineNs);
			case "attack_entity" -> new Interact.AttackEntity(id, params, startNs, deadlineNs);
			case "use_item" -> new Interact.UseItem(id, params, startNs, deadlineNs);
			case "mine_block" -> new Interact.MineBlock(id, params, startNs, deadlineNs);
			case "place_block" -> new Interact.PlaceBlock(id, params, startNs, deadlineNs);
			case "open_container" -> new Interact.OpenContainer(id, params, startNs, deadlineNs);
			case "chat_say" -> new Interact.ChatSay(id, params, startNs, deadlineNs);
			case "equip_best" -> new Upkeep.EquipBest(id, params, startNs, deadlineNs);
			case "drop_item" -> new Interact.DropItem(id, params, startNs, deadlineNs); // O134, registry 0.15.1
			case "select_item" -> new SelectItem(id, params, startNs, deadlineNs); // registry 0.15.2
			case "mount" -> new Interact.Mount(id, params, startNs, deadlineNs); // O145, 0.15.2
			case "dismount" -> new Interact.Dismount(id, params, startNs, deadlineNs);
			case "follow_boat" -> new FollowBoat(id, params, startNs, deadlineNs); // H-boat, 0.15.4
			case "go_home" -> new BaritoneWalk.GoHome(id, params, Envelope.home(), startNs, deadlineNs);
			default -> {
				var f = Providers.FACTORIES.get(skill);
				if (f == null) throw new IllegalArgumentException(skill);
				yield new Providers.ProviderAct(skill, id, params, startNs, deadlineNs, f);
			}
		};
	}

	private static Boolean baritone;

	/** Baritone's API classes are present (baritone-api-fabric; the standalone build hides its API and does not count). */
	static boolean baritone() {
		if (baritone == null) {
			try {
				Class.forName("baritone.api.BaritoneAPI");
				baritone = true;
			} catch (Throwable t) {
				baritone = false;
			}
		}
		return baritone;
	}

	/**
	 * Something to stand on 0.7 blocks ahead: a solid block at foot level (a step or wall) or within 1 block below the floor. A drop of
	 * 2+ blocks is refused. ponytail: the edge-sneak/fall reflexes (Phase 3) replace this one-step look-ahead.
	 */
	static boolean groundAhead(LocalPlayer p) {
		double yaw = Math.toRadians(p.getYRot());
		double x = p.getX() - Math.sin(yaw) * 0.7, z = p.getZ() + Math.cos(yaw) * 0.7;
		var level = p.level();
		for (int dy = 1; dy >= -1; dy--) { // foot level, the floor, one below the floor
			net.minecraft.core.BlockPos b = net.minecraft.core.BlockPos.containing(x, p.getY() - 0.01 + dy, z);
			if (!level.getBlockState(b).getCollisionShape(level, b).isEmpty()) return true;
		}
		return false;
	}

	/** Param-dependent refusals before any side effect (action.rejected precondition_failed); null = fine. */
	static String precheck(String skill, JsonObject params) {
		if (skill.equals("chat_say") && params.get("text").getAsString().startsWith("/")) return "chat text must not start with / (commands are never sent)";
		if (skill.equals("go_home") && !baritone()) return "no qualified route: the Baritone API is not loaded (the direct walker is not qualified for go_home)";
		return null;
	}

	/** Walk straight toward {@code goal}: turn up to 20 deg/tick, forward when facing it, hop a 1-block step. */
	static Input steer(LocalPlayer p, Vec3 goal) {
		float dy = Mth.wrapDegrees(yawPitch(p.position(), goal)[0] - p.getYRot());
		p.setYRot(p.getYRot() + Mth.clamp(dy, -20f, 20f));
		boolean fwd = Math.abs(dy) < 45f && groundAhead(p); // stalls at a drop; the caller's no-progress check ends it
		return new Input(fwd, false, false, false, fwd && p.horizontalCollision && p.onGround(), false, false);
	}

	static final class Stop extends Act {
		long clearedNs;

		Stop(String id, JsonObject params, long s, long d) {
			super("stop", id, params, s, d);
		}

		@Override
		boolean resumable() {
			return true;
		}

		@Override
		Input tick(Minecraft mc, LocalPlayer p) {
			if (ticks == 0) return Input.EMPTY; // read back on the next tick, after the player ticked with the cleared input
			JsonObject r = Arbiter.clearedReport(mc, p);
			boolean all = r.get("all").getAsBoolean();
			if (!all && ticks < 5) return Input.EMPTY;
			evidence.addProperty("inputs_cleared", all);
			evidence.addProperty("latency_ms", (clearedNs - startNs) / 1e6);
			evidence.addProperty("ticks_to_verify", ticks);
			evidence.add("read_back", r);
			return all ? done("ok", null, null) : done("uncertain", null, "inputs not all clear after 5 ticks");
		}
	}

	static final class LookAt extends Act {
		float yaw, pitch, err = Float.NaN;

		LookAt(String id, JsonObject params, long s, long d) {
			super("look_at", id, params, s, d);
		}

		@Override
		boolean resumable() {
			return true;
		}

		@Override
		Input tick(Minecraft mc, LocalPlayer p) {
			Vec3 t;
			if (params.has("pos")) {
				t = vec(params.getAsJsonObject("pos"));
			} else {
				Entity e = mc.level.getEntity(params.get("entity_id").getAsInt());
				if (e == null) return done("failed", "entity_gone", "entity not in the client world");
				t = e.getEyePosition();
			}
			float[] yp = yawPitch(p.getEyePosition(), t);
			yaw = yp[0];
			pitch = yp[1];
			err = angleError(p, yaw, pitch);
			// The rotation set on an earlier tick has already gone to the server in that tick's movement packet.
			if (err <= 1.0f) return done("ok", null, null);
			turn(p, yaw, pitch, 30f);
			return Input.EMPTY;
		}

		@Override
		void fill(Minecraft mc, LocalPlayer p) {
			evidence.addProperty("yaw", r2(Mth.wrapDegrees(p.getYRot())));
			evidence.addProperty("pitch", r2(p.getXRot()));
			evidence.addProperty("angle_error_deg", Float.isNaN(err) ? null : r2(angleError(p, yaw, pitch)));
		}
	}

	/** Direct-input walker: straight line, steps up 1 block, no pathfinding. Baritone replaces this under the same contract (CP-33). */
	static final class WalkTo extends Act {
		final Vec3 goal;
		final double tol;
		Vec3 last;
		double pathLen, start = -1, best = Double.MAX_VALUE;
		int sinceBetter, settle = -1;

		WalkTo(String id, JsonObject params, long s, long d) {
			super("walk_to", id, params, s, d);
			goal = vec(params.getAsJsonObject("pos"));
			tol = params.has("tolerance") ? params.get("tolerance").getAsDouble() : 1.5;
		}

		@Override
		boolean resumable() {
			return true;
		}

		@Override
		void resume() {
			best = Double.MAX_VALUE;
			sinceBetter = 0;
			settle = -1;
		}

		@Override
		Input tick(Minecraft mc, LocalPlayer p) {
			Vec3 pos = p.position();
			if (last != null) pathLen += pos.distanceTo(last);
			last = pos;
			double d = pos.distanceTo(goal);
			if (start < 0) start = Math.max(d, 1e-6);
			pct = (int) Mth.clamp(100 * (1 - d / start), 0, 100);
			if (settle >= 0) { // inputs released; wait for momentum to die so final_pos is where she actually rests
				if (p.getDeltaMovement().horizontalDistanceSqr() < 1e-4 || ++settle > 20) {
					if (d <= tol) return done("ok", null, null);
					settle = -1; // slid out of tolerance: walk back in
				} else {
					return Input.EMPTY;
				}
			}
			if (d <= tol) {
				settle = 0;
				return Input.EMPTY;
			}
			if (d < best - 0.25) {
				best = d;
				sinceBetter = 0;
			} else if (++sinceBetter > 60) {
				return done("failed", "stuck", "no progress for 3 s (direct walker cannot path around obstacles)");
			}
			return Reflexes.creeperRingBlocks(p, goal, true) ? Input.EMPTY : steer(p, goal); // O308
		}

		@Override
		void fill(Minecraft mc, LocalPlayer p) {
			Vec3 pos = p.position();
			JsonObject fp = new JsonObject();
			fp.addProperty("x", r3(pos.x));
			fp.addProperty("y", r3(pos.y));
			fp.addProperty("z", r3(pos.z));
			evidence.add("final_pos", fp);
			evidence.addProperty("distance_to_goal", r3(pos.distanceTo(goal)));
			evidence.addProperty("blocks_broken", 0); // this walker has no break or place path at all
			evidence.addProperty("blocks_placed", 0);
			evidence.addProperty("path_length", r3(pathLen));
			evidence.addProperty("walker", "direct");
		}
	}

	// O142: walk_to's break/place budgets (here, not in BaritoneWalk, so unit tests run without Baritone on the classpath)
	static final int BUILD_DEFAULT_BUDGET = 32;

	/**
	 * O142 (Hunter: "she can always reach me"): {mayBreak, breakBudget, mayPlace, placeBudget}. build places (Baritone, throwaway
	 * blocks, max_place, default 32) and also digs (the body-side dig, max_break, default 32); without build the CP-33 options as
	 * before: allow_break with max_blocks_broken (default 0), allow_place unbudgeted. -1 = not given.
	 */
	static int[] walkBudgets(boolean build, boolean allowBreak, boolean allowPlace, int maxBlocksBroken, int maxBreak, int maxPlace) {
		int brk = maxBreak >= 0 ? maxBreak : maxBlocksBroken >= 0 ? maxBlocksBroken : build ? BUILD_DEFAULT_BUDGET : 0;
		boolean mayBreak = (build || allowBreak) && brk > 0;
		boolean mayPlace = build ? maxPlace != 0 : allowPlace;
		int place = build ? (maxPlace >= 0 ? maxPlace : BUILD_DEFAULT_BUDGET) : Integer.MAX_VALUE;
		return new int[] {mayBreak ? 1 : 0, brk, mayPlace ? 1 : 0, place};
	}

	/** select_item (0.15.2, mind-c's boat flow): hold an item; its stack is pulled into the hotbar first if it is only in the main inventory (O130). */
	static final class SelectItem extends Act {
		final String item;
		int slot = -1;
		JsonObject moved = new JsonObject();

		SelectItem(String id, JsonObject params, long s, long d) {
			super("select_item", id, params, s, d);
			item = params.get("item").getAsString();
		}

		@Override
		boolean resumable() {
			return true;
		}

		@Override
		void resume() {
			ticks = 0; // select again: an eat reflex may have restored a different slot
		}

		@Override
		Input tick(Minecraft mc, LocalPlayer p) {
			if (ticks == 0) {
				if (slot < 0) slot = Hands.hotbarSlotPulling(mc, p, item, moved);
				if (slot < 0) return done("failed", "no_item", item + " is not in her inventory (slots 0-35)");
				p.getInventory().setSelectedSlot(slot); // gameMode.tick() sends the carried-item packet this tick
				return Input.EMPTY;
			}
			return p.getInventory().getSelectedSlot() == slot && item.equals(Hands.itemId(p.getMainHandItem())) ? done("ok", null, null)
					: done("failed", "timeout", "selection did not stick");
		}

		@Override
		void fill(Minecraft mc, LocalPlayer p) {
			evidence.addProperty("hotbar_slot", p.getInventory().getSelectedSlot());
			evidence.addProperty("mainhand_item", Hands.itemId(p.getMainHandItem()));
			evidence.add("item_from_inventory", moved.isEmpty() ? null : moved);
		}
	}

	static final class SelectHotbar extends Act {
		SelectHotbar(String id, JsonObject params, long s, long d) {
			super("select_hotbar", id, params, s, d);
		}

		@Override
		boolean resumable() {
			return true;
		}

		@Override
		void resume() {
			ticks = 0; // select again: an eat reflex may have restored a different slot
		}

		@Override
		Input tick(Minecraft mc, LocalPlayer p) {
			int slot = params.get("slot").getAsInt();
			if (ticks == 0) {
				p.getInventory().setSelectedSlot(slot); // gameMode.tick() sends the carried-item packet this tick
				return Input.EMPTY;
			}
			return p.getInventory().getSelectedSlot() == slot ? done("ok", null, null) : done("failed", "timeout", "selection did not stick");
		}

		@Override
		void fill(Minecraft mc, LocalPlayer p) {
			evidence.addProperty("hotbar_slot", p.getInventory().getSelectedSlot());
			ItemStack s = p.getMainHandItem();
			evidence.addProperty("mainhand_item", s.isEmpty() ? null : BuiltInRegistries.ITEM.getKey(s.getItem()).toString());
		}
	}

	static final class Sneak extends Act {
		Sneak(String id, JsonObject params, long s, long d) {
			super("sneak", id, params, s, d);
		}

		@Override
		boolean resumable() {
			return true;
		}

		@Override
		Input tick(Minecraft mc, LocalPlayer p) {
			boolean on = params.get("on").getAsBoolean();
			if (ticks == 0) {
				Arbiter.holdSneak = on; // a held input of the arbiter: persists after this action, cleared by stop
				return Input.EMPTY;
			}
			return p.isShiftKeyDown() == on ? done("ok", null, null) : Input.EMPTY;
		}

		@Override
		void fill(Minecraft mc, LocalPlayer p) {
			evidence.addProperty("sneaking", p.isShiftKeyDown());
			evidence.addProperty("crouching_pose", p.isCrouching());
		}
	}

	static final class Jump extends Act {
		boolean left;

		Jump(String id, JsonObject params, long s, long d) {
			super("jump", id, params, s, d);
		}

		@Override
		boolean resumable() {
			return true;
		}

		@Override
		void resume() {
			ticks = 0; // jump again from the ground
		}

		@Override
		Input tick(Minecraft mc, LocalPlayer p) {
			if (ticks == 0) return new Input(false, false, false, false, true, false, false);
			if (!p.onGround()) {
				left = true;
				return done("ok", null, null);
			}
			return Input.EMPTY; // still on the ground: the arbiter's deadline ends it as timeout
		}

		@Override
		void fill(Minecraft mc, LocalPlayer p) {
			evidence.addProperty("left_ground", left);
		}
	}

	static Vec3 vec(JsonObject o) {
		return new Vec3(o.get("x").getAsDouble(), o.get("y").getAsDouble(), o.get("z").getAsDouble());
	}

	/** Minecraft yaw/pitch (degrees) that faces {@code to} from {@code from}. */
	static float[] yawPitch(Vec3 from, Vec3 to) {
		double dx = to.x - from.x, dy = to.y - from.y, dz = to.z - from.z;
		float yaw = (float) Math.toDegrees(Mth.atan2(dz, dx)) - 90f;
		float pitch = (float) -Math.toDegrees(Mth.atan2(dy, Math.sqrt(dx * dx + dz * dz)));
		return new float[] {yaw, pitch};
	}

	static float angleError(LocalPlayer p, float yaw, float pitch) {
		return (float) Math.hypot(Mth.wrapDegrees(yaw - p.getYRot()), pitch - p.getXRot());
	}

	/** Turns at most {@code max} degrees per axis this tick, like a fast mouse flick. */
	/** O144: one tick of levelling her head (Arbiter.level): at most 9 deg toward pitch 0. */
	static float levelStep(float pitch) {
		return pitch - Math.max(-9f, Math.min(9f, pitch));
	}

	static void turn(LocalPlayer p, float yaw, float pitch, float max) {
		p.setYRot(p.getYRot() + Mth.clamp(Mth.wrapDegrees(yaw - p.getYRot()), -max, max));
		p.setXRot(Mth.clamp(p.getXRot() + Mth.clamp(pitch - p.getXRot(), -max, max), -90f, 90f));
	}

	static double r2(double v) {
		return Math.round(v * 100) / 100.0;
	}

	static double r3(double v) {
		return Math.round(v * 1000) / 1000.0;
	}
}
