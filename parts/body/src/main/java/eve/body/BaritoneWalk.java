package eve.body;

import baritone.api.BaritoneAPI;
import baritone.api.IBaritone;
import baritone.api.Settings;
import baritone.api.pathing.goals.GoalBlock;
import baritone.api.pathing.goals.GoalNear;
import com.google.gson.JsonObject;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.world.entity.player.Input;
import net.minecraft.world.phys.Vec3;

/**
 * walk_to through Baritone v1.20.0 (API jar, a separate mod; only loaded when Skills.baritone() found it). While it runs, Baritone
 * owns player.input; the arbiter takes it back on every terminal path (stop, link loss, deadline, done) and cancels the process.
 */
class BaritoneWalk extends Skills.Act {
	/** Once at client start: Baritone must never take orders from chat, and nothing it does beyond walking is qualified yet. */
	static void configure() {
		Settings s = BaritoneAPI.getSettings();
		s.chatControl.value = false; // "#..." lines Eve sends (chat_say from the mind) would otherwise drive Baritone
		s.prefixControl.value = false;
		s.echoCommands.value = false;
		s.chatDebug.value = false;
		s.allowBreak.value = false; // ponytail: break/place/parkour/bucket falls stay off until their own checkpoints (CP-33+)
		s.allowPlace.value = false;
		s.allowParkour.value = false;
		s.allowParkourPlace.value = false;
		s.allowWaterBucketFall.value = false;
		s.allowInventory.value = false;
		s.autoTool.value = false;
		s.maxFallHeightNoWater.value = 3; // pinned (Baritone's default): a 3-block drop is the most that costs no health
		s.assumeWalkOnLava.value = false;
		// O142: what a build walk may place - throwaway blocks only, never her kit (Baritone places only from the hotbar)
		java.util.List<net.minecraft.world.item.Item> throwaway = new java.util.ArrayList<>(java.util.List.of(
				net.minecraft.world.item.Items.DIRT, net.minecraft.world.item.Items.COBBLESTONE, net.minecraft.world.item.Items.NETHERRACK));
		// by id, not the planks tag: item tags bind only on joining a world, and this runs at client start
		for (var it : net.minecraft.core.registries.BuiltInRegistries.ITEM)
			if (net.minecraft.core.registries.BuiltInRegistries.ITEM.getKey(it).getPath().endsWith("_planks")) throwaway.add(it);
		s.acceptableThrowawayItems.value = throwaway;
		s.logger.value = c -> EveBody.LOG.info("baritone: {}", c.getString());
		EveBody.LOG.info("eve-body: Baritone API found; walk_to uses it (chat control off; break/place/parkour off)");
	}

	final Vec3 goal;
	final double tol;
	final String failCode;
	IBaritone b;
	Vec3 last;
	long broken0 = -1, placed0;
	int breakBudget, placeBudget;
	boolean mayBreak, mayPlace, budgetHit, placeHit;
	JsonObject throwawayMoved;
	double pathLen, start = -1, best = Double.MAX_VALUE;
	int idleTicks, sinceBetter;

	BaritoneWalk(String id, JsonObject params, long s, long d) {
		this("walk_to", id, params, Skills.vec(params.getAsJsonObject("pos")), params.has("tolerance") ? params.get("tolerance").getAsDouble() : 1.5,
				"unreachable", s, d);
	}

	BaritoneWalk(String skill, String id, JsonObject params, Vec3 goal, double tol, String failCode, long s, long d) {
		super(skill, id, params, s, d);
		this.goal = goal;
		this.tol = tol;
		this.failCode = failCode;
		ownsInput = true;
	}

	/** go_home: the envelope home by the same qualified route (no breaking, placing or parkour). Done within 3 blocks. */
	static final class GoHome extends BaritoneWalk {
		GoHome(String id, JsonObject params, Envelope.Home home, long s, long d) {
			super("go_home", id, params, home.center(), 3.0, "home_blocked", s, d);
		}

		@Override
		void fill(Minecraft mc, LocalPlayer p) {
			super.fill(mc, p);
			evidence.addProperty("distance_to_home", Skills.r3(p.position().distanceTo(goal)));
			evidence.addProperty("route_kind", "baritone_walk_no_break_no_place_no_parkour");
		}
	}

	int dismountTicks, waitTicks;
	boolean replanned;

	@Override
	Input tick(Minecraft mc, LocalPlayer p) {
		// O145: in a vehicle she never walks. Someone else steering it toward the goal (riding with the owner): arrived. Else out first.
		if (p.isPassenger()) {
			var drv = p.getVehicle().getControllingPassenger();
			if (drv != null && drv != p && drv.position().distanceTo(goal) <= Math.max(tol, 3))
				return done("ok", null, "riding with " + drv.getName().getString());
			if (++dismountTicks > 20) return done("failed", "stuck", "could not leave the vehicle");
			sneak = true;
			ownsInput = false;
			return Input.EMPTY;
		}
		if (dismountTicks > 0) {
			dismountTicks = 0;
			sneak = null;
			ownsInput = true;
			evidence.addProperty("dismounted_first", true);
		}
		Vec3 pos = p.position();
		if (last != null) pathLen += pos.distanceTo(last);
		last = pos;
		double d = pos.distanceTo(goal);
		if (b == null) {
			b = BaritoneAPI.getProvider().getPrimaryBaritone();
			if (broken0 < 0) {
				// CP-33 budgets: break only with allow_break and at most max_blocks_broken; place only with allow_place (her own
				// hotbar blocks, Baritone's throwaway list). Settings are global in Baritone: set per walk, reset on every end.
				broken0 = Hands.blocksBroken;
				placed0 = Hands.blocksPlaced;
				int[] bud = Skills.walkBudgets(bool("build"), bool("allow_break"), bool("allow_place"), num("max_blocks_broken", -1), num("max_break", -1),
						num("max_place", -1));
				mayBreak = bud[0] == 1;
				breakBudget = bud[1];
				mayPlace = bud[2] == 1;
				placeBudget = bud[3];
				if (mayPlace && bool("build")) throwawayToHotbar(mc, p); // O142: Baritone places only from the hotbar
			}
			Settings s = BaritoneAPI.getSettings();
			boolean placeOn = mayPlace && !placeHit;
			s.allowPlace.value = placeOn; // Baritone's own placing (pillar up, bridge across), throwaway blocks only
			Hands.baritoneMayPlace = placeOn;
			BlockPos g = BlockPos.containing(goal);
			// GoalNear counts whole blocks; she can stand up to 0.71 off her block's centre, so ask for that much less than tol
			b.getCustomGoalProcess().setGoalAndPath(tol < 2 ? new GoalBlock(g) : new GoalNear(g, (int) Math.floor(tol - 0.71)));
			start = Math.max(d, 1e-6);
			return Input.EMPTY;
		}
		pct = (int) Math.max(0, Math.min(100, 100 * (1 - d / start)));
		if (approach != null) {
			Vec3 to = approach.subtract(p.position()).multiply(1, 0, 1);
			BlockPos c = BlockPos.containing(approach);
			if (p.isWithinBlockInteractionRange(c, 0) || ++approachTicks > 100 || to.lengthSqr() < 1) {
				approach = null;
				approachTicks = 0;
				if (startDig(mc, p)) return Input.EMPTY;
				return done("failed", failCode, "could not reach the block in the way");
			}
			Vec3 dir = to.normalize();
			if (!Reflexes.safeStep(p, p.getX() + dir.x * 0.8, p.getZ() + dir.z * 0.8)) return done("failed", failCode, "no safe footing toward the block in the way");
			Skills.turn(p, (float) Math.toDegrees(net.minecraft.util.Mth.atan2(dir.z, dir.x)) - 90f, 0f, 30f);
			return Reflexes.keysFor(p, dir, false, false);
		}
		if (dig != null) return digStep(mc, p);
		if (mayPlace && !placeHit && Hands.blocksPlaced - placed0 >= placeBudget) { // O142: the place budget is spent: re-plan without
			placeHit = true;
			evidence.addProperty("place_budget_hit", true);
			cancel(mc, p);
			b = null;
			return Input.EMPTY;
		}
		boolean busy = b.getCustomGoalProcess().isActive() || b.getPathingBehavior().isPathing();
		ownsInput = busy; // once Baritone lets go, the arbiter's input is back next tick (not vanilla's keyboard)
		if (busy) {
			idleTicks = 0;
			// O144: a plain walk keeps her head level (pitch steers nothing); a build walk leaves it to Baritone's own aiming
			if (!mayPlace && !mayBreak) p.setXRot(Skills.levelStep(p.getXRot()));
			// Baritone can keep re-planning toward a goal it cannot reach (walled in, a lava moat): no progress for 15 s ends it
			if (d < best - 0.5) {
				best = d;
				sinceBetter = 0;
			} else if (++sinceBetter > 300) {
				cancel(mc, p);
				ownsInput = false;
				if (startDig(mc, p)) return Input.EMPTY;
				return done("failed", failCode, "no progress for 15 s; Baritone found no qualified route (" + Skills.r2(d) + " from the goal)"
						+ (budgetHit ? "; break budget spent" : ""));
			}
			return Input.EMPTY;
		}
		// Baritone let go: wait for momentum to die so final_pos is where she rests, then judge against our own tolerance
		if (++idleTicks < 10 && p.getDeltaMovement().horizontalDistanceSqr() > 1e-4) return Input.EMPTY;
		if (d <= tol) return done("ok", null, null);
		if (startDig(mc, p)) return Input.EMPTY;
		// CP-86 run 4 (20:02:47: go_home failed at once, path 0, Baritone "open set 0, map size 1" just after a dig left her
		// "standing on air" until the server re-sent the blocks): no first step at all is one re-plan after 1 s, not a verdict
		if (pathLen < 0.5 && !replanned) {
			replanned = true;
			if (++waitTicks < 20) {
				replanned = false;
				return Input.EMPTY;
			}
			evidence.addProperty("replanned_after_no_first_step", true);
			b = null;
			best = Double.MAX_VALUE;
			sinceBetter = 0;
			idleTicks = 0;
			ownsInput = true;
			return Input.EMPTY;
		}
		return done("failed", failCode, "Baritone stopped " + Skills.r2(d) + " from the goal (no qualified route)" + (budgetHit ? "; break budget spent" : ""));
	}

	boolean bool(String k) {
		return params.has(k) && params.get(k).getAsBoolean();
	}

	int num(String k, int dflt) {
		return params.has(k) ? params.get(k).getAsInt() : dflt;
	}

	/** O142: no throwaway block in the hotbar: pull one stack of the first she has from the main inventory (O130's swap). */
	void throwawayToHotbar(Minecraft mc, LocalPlayer p) {
		var items = BaritoneAPI.getSettings().acceptableThrowawayItems.value;
		for (int i = 0; i < 9; i++) if (items.contains(p.getInventory().getItem(i).getItem())) return;
		for (var it : items) {
			String id = net.minecraft.core.registries.BuiltInRegistries.ITEM.getKey(it).toString();
			JsonObject m = new JsonObject();
			if (Hands.hotbarSlotPulling(mc, p, id, m) >= 0) {
				if (!m.isEmpty()) throwawayMoved = m;
				return;
			}
		}
		evidence.addProperty("no_throwaway", true); // nothing to build with: Baritone plans walking (and digging) only
	}

	/**
	 * CP-33 break budget, body-side: with allow_break and budget left, when Baritone finds no route, mine the first solid feet/head
	 * cell on the straight line toward the goal (within 3 blocks) with the body's own mine_block (best hotbar tool, zone rules,
	 * server-confirmed), one block at a time, then let Baritone re-plan. Never more than max_blocks_broken in all.
	 */
	Interact.MineBlock dig;
	Vec3 approach;
	int approachTicks;

	boolean startDig(Minecraft mc, LocalPlayer p) {
		if (!mayBreak || budgetHit) return false;
		if (Hands.blocksBroken - broken0 >= breakBudget) {
			budgetHit = true;
			evidence.addProperty("break_budget_hit", true);
			return false;
		}
		Vec3 dir = goal.subtract(p.position()).multiply(1, 0, 1);
		if (dir.lengthSqr() < 1e-4) return false;
		dir = dir.normalize();
		for (int k = 1; k <= 8; k++) {
			BlockPos feet = BlockPos.containing(p.getX() + dir.x * k, p.getY() + 0.01, p.getZ() + dir.z * k);
			for (BlockPos c : new BlockPos[] {feet.above(), feet}) {
				if (p.level().getBlockState(c).getCollisionShape(p.level(), c).isEmpty()) continue;
				if (!p.isWithinBlockInteractionRange(c, 0)) { // walk up to it first (straight, safe footing only)
					approach = Vec3.atCenterOf(c);
					ownsInput = false;
					return true;
				}
				JsonObject prm = new JsonObject(), pos = new JsonObject();
				pos.addProperty("x", c.getX());
				pos.addProperty("y", c.getY());
				pos.addProperty("z", c.getZ());
				prm.add("pos", pos);
				prm.addProperty("collect", false);
				dig = new Interact.MineBlock(id + "-dig", prm, System.nanoTime(), System.nanoTime() + 15_000_000_000L);
				ownsInput = false;
				return true;
			}
		}
		return false;
	}

	Input digStep(Minecraft mc, LocalPlayer p) {
		Input in = dig.tick(mc, p);
		dig.ticks++;
		if (dig.status == null) return in;
		String st = dig.status, code = dig.code;
		dig = null;
		if (!"ok".equals(st)) {
			evidence.addProperty("dig_failed", code);
			return done("failed", "zone_denied".equals(code) ? "zone_denied" : failCode, "digging through failed: " + code);
		}
		if (startDig(mc, p)) return Input.EMPTY; // another block in the way
		b = null; // the way is open (or the budget is spent): Baritone re-plans
		best = Double.MAX_VALUE;
		sinceBetter = 0;
		idleTicks = 0;
		ownsInput = true;
		return Input.EMPTY;
	}

	@Override
	boolean resumable() {
		return true;
	}

	@Override
	void resume() {
		b = null; // re-path from where the reflex left her
		best = Double.MAX_VALUE;
		sinceBetter = 0;
		idleTicks = 0;
		ownsInput = true;
	}

	@Override
	void cancel(Minecraft mc, LocalPlayer p) {
		if (dig != null) dig.cancel(mc, p);
		restore();
		if (b == null) return;
		b.getPathingBehavior().cancelEverything();
		b.getInputOverrideHandler().clearAllKeys();
	}

	/** Back to the qualified defaults (no break, no place) on every end of a walk. */
	static void restore() {
		Settings s = BaritoneAPI.getSettings();
		s.allowBreak.value = false;
		s.allowPlace.value = false;
		s.autoTool.value = false;
		Hands.baritoneMayBreak = Hands.baritoneMayPlace = false;
	}

	@Override
	void fill(Minecraft mc, LocalPlayer p) {
		Vec3 pos = p.position();
		evidence.add("final_pos", Telemetry.pos(pos));
		evidence.addProperty("distance_to_goal", Skills.r3(pos.distanceTo(goal)));
		restore();
		evidence.addProperty("blocks_broken", broken0 < 0 ? 0 : Hands.blocksBroken - broken0);
		evidence.addProperty("blocks_placed", broken0 < 0 ? 0 : Hands.blocksPlaced - placed0);
		evidence.addProperty("break_budget", breakBudget);
		if (bool("build")) {
			evidence.addProperty("place_budget", placeBudget);
			if (throwawayMoved != null) evidence.add("throwaway_from_inventory", throwawayMoved);
		}
		evidence.addProperty("path_length", Skills.r3(pathLen));
		evidence.addProperty("walker", "baritone");
	}
}
