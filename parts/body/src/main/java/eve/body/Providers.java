package eve.body;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import eve.body.api.BodyServices;
import eve.body.api.EveSkillProvider;
import eve.body.api.Skill;
import eve.body.api.SkillFactory;
import eve.body.api.SkillRegistrar;
import eve.body.api.Step;
import eve.body.api.SubAction;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.player.Input;
import net.minecraft.world.phys.Vec3;

import java.util.HashMap;
import java.util.Map;

/** Skills from other mods (entrypoint "eve-body:skills", API in eve.body.api): loaded once, then dispatched like the body's own. */
final class Providers {
	static final Map<String, SkillFactory> FACTORIES = new HashMap<>();
	static final Map<String, String> OWNER = new HashMap<>();
	/** API v4 (H-autofight): the one provider auto-fight rule, or null. */
	static eve.body.api.EngageHook ENGAGE;
	static String ENGAGE_OWNER;
	/** sense's mob.targeting frames relayed by the mind: entity id -> latest frame (client tick it arrived). */
	static final Map<Integer, eve.body.api.ClientView.Targeting> TARGETING = new HashMap<>();
	static final long TARGETING_STALE_TICKS = 60; // 3 s, as sense's contract

	/** The frames of the last 3 s only (older ones dropped). */
	static Map<Integer, eve.body.api.ClientView.Targeting> freshTargeting(Map<Integer, eve.body.api.ClientView.Targeting> all, long now) {
		all.values().removeIf(t -> now - t.tick() > TARGETING_STALE_TICKS);
		return java.util.Collections.unmodifiableMap(all);
	}

	/** API v4: what an EngageHook reads. */
	static final eve.body.api.ClientView VIEW = new eve.body.api.ClientView() {
		@Override
		public Minecraft minecraft() {
			return Minecraft.getInstance();
		}

		@Override
		public LocalPlayer player() {
			return Minecraft.getInstance().player;
		}

		@Override
		public boolean isProtected(Entity e) {
			return Telemetry.isProtected(e);
		}

		@Override
		public int hotbarSlot(String itemId) {
			return Hands.hotbarSlot(player(), itemId);
		}

		@Override
		public Map<String, Integer> inventoryCounts() {
			return Hands.counts(player());
		}

		@Override
		public long ticks() {
			return EveBody.clientTick;
		}

		@Override
		public int lastAttackerId() {
			return Telemetry.lastAttackerId;
		}

		@Override
		public long lastHitTick() {
			return Telemetry.lastHitTick;
		}

		@Override
		public Map<Integer, Targeting> targeting() {
			return freshTargeting(TARGETING, EveBody.clientTick);
		}
	};

	/** The hook's answer, or null; a hook that throws is logged once a minute and treated as null (never kills the tick). */
	static eve.body.api.Engage engage(eve.body.api.EngageHook hook, eve.body.api.ClientView view) {
		if (hook == null) return null;
		try {
			return hook.pick(view);
		} catch (Throwable t) {
			if (System.nanoTime() - engageErrNs > 60_000_000_000L) {
				engageErrNs = System.nanoTime();
				org.slf4j.LoggerFactory.getLogger("eve-body").error("eve-body: engage hook threw: {}", t.toString()); // not EveBody.LOG: unit tests run without Fabric
			}
			return null;
		}
	}

	private static long engageErrNs;

	static void load() {
		for (var c : FabricLoader.getInstance().getEntrypointContainers("eve-body:skills", EveSkillProvider.class)) {
			String mod = c.getProvider().getMetadata().getId();
			try {
				c.getEntrypoint().registerSkills(new SkillRegistrar() {
					@Override
					public void register(String name, int version, SkillFactory factory) {
						JsonObject reg = Registry.SKILLS.get(name);
						String why = reg == null ? "not in the shipped registry"
								: reg.get("version").getAsInt() != version ? "registry has version " + reg.get("version").getAsInt()
								: Registry.IMPLEMENTED.contains(name) ? "already implemented by " + OWNER.getOrDefault(name, "eve-body") : null;
						if (why != null) {
							EveBody.LOG.error("eve-body: refused skill {} v{} from {}: {}", name, version, mod, why);
							return;
						}
						FACTORIES.put(name, factory);
						OWNER.put(name, mod);
						Registry.IMPLEMENTED.add(name);
						EveBody.LOG.info("eve-body: skill {} v{} provided by {}", name, version, mod);
					}

					@Override
					public void registerEngage(eve.body.api.EngageHook hook) {
						if (ENGAGE != null) {
							EveBody.LOG.error("eve-body: refused engage hook from {}: {} already has it", mod, ENGAGE_OWNER);
							return;
						}
						ENGAGE = hook;
						ENGAGE_OWNER = mod;
						EveBody.LOG.info("eve-body: engage hook (auto_fight) provided by {}", mod);
					}

					@Override
					public int apiVersion() {
						return BodyServices.API_VERSION;
					}
				});
			} catch (Throwable t) {
				EveBody.LOG.error("eve-body: skill provider {} failed to register: {}", mod, t.toString());
			}
		}
	}

	/** null when the body's own validation, preconditions and pre-checks pass for skill + params. */
	static String precheck(Minecraft mc, String skill, JsonObject params) {
		JsonObject reg = Registry.SKILLS.get(skill);
		if (reg == null || !Registry.IMPLEMENTED.contains(skill)) return "skill " + skill + " is not implemented by this body";
		String bad = Registry.check(params, reg.getAsJsonObject("params"), "params");
		if (bad != null) return bad;
		String pre = Arbiter.preconditions(mc, reg);
		return pre != null ? pre : Skills.precheck(skill, params);
	}

	/** A provider skill under the arbiter: the arbiter owns deadline, cancel and reporting; this adapts the API to Skills.Act. */
	static final class ProviderAct extends Skills.Act implements BodyServices {
		final Skill skill;
		Child child;
		boolean cancelling;
		int children;

		ProviderAct(String name, String id, JsonObject params, long s, long d, SkillFactory f) {
			super(name, id, params, s, d);
			skill = f.create(this);
		}

		@Override
		Input tick(Minecraft mc, LocalPlayer p) {
			Step st = skill.tick(this);
			ownsInput = child != null && child.res == null && child.a.ownsInput; // a Baritone child drives the input while it paths
			sneak = st.sneak() != null ? st.sneak() : child != null && child.res == null ? child.a.sneak : null;
			if (st.done()) {
				if (child != null) child.cancel();
				releaseHands();
				closeMenu(); // a provider never leaves a screen open behind it
				return done(st.status(), st.code(), st.note());
			}
			return st.input() == null ? Input.EMPTY : st.input();
		}

		@Override
		void cancel(Minecraft mc, LocalPlayer p) {
			cancelling = true;
			if (child != null) child.cancel();
			skill.cancel(this);
			releaseHands();
			closeMenu(); // stop, reflex pre-emption, deadline, link loss
		}

		@Override
		boolean resumable() {
			return skill.resumable();
		}

		@Override
		void resume() {
			skill.resume(this);
		}

		// ---- BodyServices ----
		@Override
		public void claimCreeper(int entityId) {
			Reflexes.claimCreeper(entityId);
		}

		@Override
		public Minecraft minecraft() {
			return Minecraft.getInstance();
		}

		@Override
		public LocalPlayer player() {
			return Minecraft.getInstance().player;
		}

		@Override
		public String actionId() {
			return id;
		}

		@Override
		public JsonObject params() {
			return params;
		}

		@Override
		public int ticks() {
			return ticks;
		}

		@Override
		public boolean cancelled() {
			return cancelling;
		}

		@Override
		public JsonObject evidence() {
			return evidence;
		}

		@Override
		public void progress(Integer p, String n) {
			pct = p;
			note = n;
		}

		@Override
		public void event(String kind, JsonObject detail) {
			Telemetry.event(kind, detail);
		}

		@Override
		public JsonObject state() {
			return Telemetry.snapshot(minecraft(), player());
		}

		@Override
		public SubAction run(String name, JsonObject p) {
			if (child != null && child.res == null) child.cancel(); // one child at a time
			JsonObject params = p == null ? new JsonObject() : p;
			String why = Providers.precheck(minecraft(), name, params);
			child = new Child(why == null ? Skills.create(name, id + "/" + (++children), params, System.nanoTime(), deadlineNs) : null, why);
			return child;
		}

		@Override
		public SubAction useBlock(net.minecraft.core.BlockPos pos) {
			if (child != null && child.res == null) child.cancel();
			child = new Child(new Interact.UseBlock(id + "/" + (++children), pos.immutable(), System.nanoTime(), deadlineNs), null);
			return child;
		}

		@Override
		public boolean clickSlot(int slot, int button, net.minecraft.world.inventory.ContainerInput input) {
			LocalPlayer p = player();
			Minecraft mc = minecraft();
			if (p == null || mc.gameMode == null || p.containerMenu == null) return false; // inventoryMenu (id 0, 2x2 grid) counts
			mc.gameMode.handleContainerInput(p.containerMenu.containerId, slot, button, input, p);
			return true;
		}

		long guardTick = -10;
		boolean guarding, holding;

		@Override
		public boolean guard(boolean up) {
			guardTick = EveBody.clientTick;
			LocalPlayer p = player();
			Minecraft mc = minecraft();
			if (!up) {
				releaseHands();
				return true;
			}
			if (!Reflexes.shieldUsable(p) && !(guarding && p.isUsingItem())) return false;
			if (!guarding && Hands.shieldInUse(p)) { // O174: already raised (a shield reflex handed it over): take it as is, no re-press
				mc.options.keyUse.setDown(true);
				guarding = true;
				Hands.shieldHandedBack = false;
			}
			if (!guarding && Hands.blockedByScreen(mc) == null) {
				mc.hitResult = net.minecraft.world.phys.BlockHitResult.miss(p.getEyePosition(), net.minecraft.core.Direction.UP, p.blockPosition());
				Hands.use(mc);
				mc.options.keyUse.setDown(true);
				guarding = true;
			}
			return guarding;
		}

		/**
		 * True while this provider action claimed the shield this tick or the last (the melee shield reflex yields). Paused, it is
		 * not ticked and cannot claim: judged as of its pause (O102d c_rush #6: the claim lapsed a tick into a projectile shield and
		 * the reflexes took the zombie in reach as theirs for 19.5 s while the fight that owns melee waited).
		 */
		boolean guardClaimed() {
			return claimed(paused, pausedTick, EveBody.clientTick, guardTick);
		}

		@Override
		public boolean handedBack() {
			return Reflexes.handBackActive();
		}

		/** The guard claim rule: claimed this tick or the last, as of the pause while paused. */
		static boolean claimed(boolean paused, long pausedTick, long now, long guardTick) {
			return (paused ? pausedTick : now) - guardTick <= 1;
		}

		@Override
		public boolean aim(Vec3 point, float maxDegPerTick) {
			LocalPlayer p = player();
			float[] yp = Skills.yawPitch(p.getEyePosition(), point);
			if (Skills.angleError(p, yp[0], yp[1]) <= 0.5f) return true;
			Skills.turn(p, yp[0], yp[1], maxDegPerTick);
			return false;
		}

		@Override
		public void holdUse(boolean down) {
			Minecraft mc = minecraft();
			LocalPlayer p = player();
			if (!down) {
				releaseHands();
				return;
			}
			if (!holding && Hands.blockedByScreen(mc) == null) {
				mc.hitResult = net.minecraft.world.phys.BlockHitResult.miss(p.getEyePosition(), net.minecraft.core.Direction.UP, p.blockPosition());
				Hands.use(mc);
				mc.options.keyUse.setDown(true);
				holding = true;
			}
		}

		void releaseHands() {
			Minecraft mc = minecraft();
			LocalPlayer p = player();
			if (!guarding && !holding) {
				if (Hands.shieldHandedBack) Hands.releaseHandedBack(mc, p); // O174: handed back up but not wanted
				return;
			}
			if (Hands.shieldHandover && guarding && !holding && p != null && Hands.shieldInUse(p)) { // O174: the reflex takes it raised
				guarding = false;
				return;
			}
			mc.options.keyUse.setDown(false);
			if (p != null && p.isUsingItem()) mc.gameMode.releaseUsingItem(p);
			guarding = holding = false;
		}

		@Override
		public void closeMenu() {
			LocalPlayer p = player();
			if (p != null && p.containerMenu != p.inventoryMenu) p.closeContainer();
		}

		@Override
		public String precheck(String name, JsonObject p) {
			return Providers.precheck(minecraft(), name, p == null ? new JsonObject() : p);
		}

		@Override
		public Input steerTo(Vec3 goal) {
			return Skills.steer(player(), goal);
		}

		@Override
		public int hotbarSlot(String itemId) {
			return Hands.hotbarSlot(player(), itemId);
		}

		@Override
		public void selectHotbar(int slot) {
			player().getInventory().setSelectedSlot(slot);
		}

		@Override
		public Map<String, Integer> inventoryCounts() {
			return Hands.counts(player());
		}

		@Override
		public boolean isProtected(Entity e) {
			return Telemetry.isProtected(e);
		}
	}

	/** A body skill run as a child: the same Skills.Act the arbiter would run, ticked by the parent. */
	static final class Child implements SubAction {
		final Skills.Act a;
		JsonObject res;

		Child(Skills.Act a, String refused) {
			this.a = a;
			if (a == null) res = result("failed", "precondition_failed", refused, new JsonObject());
		}

		@Override
		public Step tick() {
			if (res == null) {
				Minecraft mc = Minecraft.getInstance();
				Input in = System.nanoTime() > a.deadlineNs ? a.done("timeout", "timeout", "deadline passed") : a.tick(mc, mc.player);
				a.ticks++;
				if (a.status == null) return a.sneak != null ? Step.running(in, a.sneak) : Step.running(in);
				a.fill(mc, mc.player);
				res = result(a.status, a.code, a.note, a.evidence);
			}
			String st = res.get("status").getAsString();
			JsonElement c = res.get("code"), n = res.get("note");
			String code = c == null || c.isJsonNull() ? null : c.getAsString(), note = n == null || n.isJsonNull() ? null : n.getAsString();
			return switch (st) {
				case "ok" -> Step.ok();
				case "uncertain" -> Step.uncertain(note);
				default -> Step.failed(code != null ? code : st, note);
			};
		}

		@Override
		public JsonObject result() {
			return res;
		}

		@Override
		public void cancel() {
			if (res != null) return;
			Minecraft mc = Minecraft.getInstance();
			if (mc.player != null) {
				a.cancel(mc, mc.player);
				a.fill(mc, mc.player);
			}
			res = result("cancelled", "cancelled", "parent ended or started another sub-action", a.evidence);
		}

		private static JsonObject result(String status, String code, String note, JsonObject evidence) {
			JsonObject r = new JsonObject();
			r.addProperty("status", status);
			r.addProperty("code", code);
			r.addProperty("note", note);
			r.add("evidence", evidence);
			return r;
		}
	}
}
