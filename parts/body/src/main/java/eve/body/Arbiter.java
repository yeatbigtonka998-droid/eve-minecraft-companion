package eve.body;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayNetworking;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.KeyMapping;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.ClientInput;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.world.entity.player.Input;
import net.minecraft.world.phys.Vec2;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;

/**
 * The one control arbiter. Priority: STOPPED > reflex (Reflexes) > active action > idle. Every client tick it decides
 * the inputs; nothing else writes movement, look, sneak or hotbar. STOPPED ends only on an explicit resume and survives
 * reconnects and client restarts (persisted in config/eve-body-control.json).
 */
final class Arbiter {
	/** Replaces the player's KeyboardInput: the keyboard cannot move Eve, only the arbiter can. */
	static final class EveInput extends ClientInput {
		Input want = Input.EMPTY;

		@Override
		public void tick() {
			keyPresses = want;
			moveVector = new Vec2(imp(want.left(), want.right()), imp(want.forward(), want.backward())).normalized();
		}

		void zero() {
			want = Input.EMPTY;
			keyPresses = Input.EMPTY;
			moveVector = Vec2.ZERO;
		}

		private static float imp(boolean a, boolean b) {
			return a == b ? 0 : a ? 1 : -1;
		}
	}

	record Remembered(String type, JsonObject payload, long atNanos) {}

	static final EveInput INPUT = new EveInput();
	static boolean stopped;
	static String mode = "manual";
	static boolean holdSneak;
	static Skills.Act active;
	/** O144: the skill of the last action that ended (after look_at she keeps the view the mind asked for). */
	static String lastEnded;
	static long actionsDone;

	private static final long REMEMBER_NS = TimeUnit.MINUTES.toNanos(10);
	private static final Map<String, Remembered> DONE = new LinkedHashMap<>(1024, 0.75f, false) {
		@Override
		protected boolean removeEldestEntry(Map.Entry<String, Remembered> e) {
			return size() > 1000;
		}
	};
	private static final Map<String, Boolean> NONCES = new LinkedHashMap<>(512, 0.75f, false) {
		@Override
		protected boolean removeEldestEntry(Map.Entry<String, Boolean> e) {
			return size() > 256;
		}
	};
	private static int deadTicks;
	private static String verifyOp;
	private static int verifyTick;

	/** START_CLIENT_TICK, client thread. */
	static void tick(Minecraft mc) {
		drain(mc);
		Interact.ChatSay.pump(mc); // O114: waiting chat lines, one per 2 s
		LocalPlayer p = mc.player;
		if (p == null) return;
		if (verifyOp != null) verify(mc, p);
		if (active != null && Link.session == null) end(mc, "cancelled", "interrupted", "mind link lost; IDLE-SAFE");
		// CP-31 death recovery: after 2 s on the death screen (the death event, with where she died, is out) press vanilla's
		// respawn; never while STOPPED, so an owner can look first. What to do next (fetch the drops, go home) is the mind's.
		if (p.isDeadOrDying() && !stopped && mc.gui.screen() instanceof net.minecraft.client.gui.screens.DeathScreen) {
			if (++deadTicks == 40) {
				p.respawn();
				mc.gui.setScreen(null);
				EveBody.LOG.info("eve: respawn requested after death");
			}
			return;
		}
		deadTicks = 0;
		Input want = Input.EMPTY;
		{
			boolean shift = holdSneak && !stopped;
			// reflexes run with the mind down too (IDLE-SAFE); O218 (third live death, 18:21: STOPPED cancelled them and she took 11 HP
			// of zombie hits standing still): STOPPED too, its defensive ones only (Reflexes.allowedStopped)
			Input rx = Reflexes.tick(mc, p);
			if (rx != null) {
				want = rx;
				if (rx.shift()) shift = true; // a reflex that sneaks (the darkness torch is sneak-placed) keeps it
				if (active != null) pause(mc, p, active);
			} else if (!stopped) {
				if (active != null && active.paused) unpause(mc, p, active);
				Skills.Act a = active;
				if (a != null) {
					want = runActive(mc, p);
					if (a.status == null && a.sneak != null) shift = a.sneak;
				}
				if (active == null && !"look_at".equals(lastEnded)) level(p); // O144: idle = level, facing where she moves
			}
			if (Reflexes.edgeSneak(p)) shift = true; // safety wins over an action's own sneak preference
			if (shift != want.shift()) want = new Input(want.forward(), want.backward(), want.left(), want.right(), want.jump(), shift, want.sprint());
		}
		// new LocalPlayer after respawn/dimension change gets a fresh KeyboardInput; a Baritone walk owns it only while it paths
		// (H-autofight: the reflex fight's Baritone child walk owns it the same way)
		if (p.input != INPUT && !Reflexes.reflexOwnsInput() && (stopped || active == null || !active.ownsInput)) p.input = INPUT;
		INPUT.want = want;
	}

	/**
	 * O144 (Hunter live: after "clear the nearest trees" her head stayed pitched straight up through "come to me"): idle (no
	 * action, no reflex), the pitch goes back to level, at most 9 deg a tick (straight up or down: level in 10 ticks), and the
	 * yaw turns toward where she is moving if she still moves. Not after a look_at: holding that view is its purpose (the mind's
	 * look_at then use_item, e.g. a boat onto water).
	 */
	static void level(LocalPlayer p) {
		p.setXRot(Skills.levelStep(p.getXRot()));
		var v = p.getDeltaMovement();
		if (v.horizontalDistanceSqr() > 0.01) Skills.turn(p, (float) Math.toDegrees(Math.atan2(v.z, v.x)) - 90f, p.getXRot(), 20f);
	}

	/**
	 * A reflex took the controls: the action holds nothing while it waits (Baritone's process cancelled, use/attack released).
	 * Reflexes.tick calls it when a reflex starts, before the reflex's first tick (O102d c_rush #6: paused after it, a guarding
	 * provider's release lifted the shield the reflex had just pressed); Arbiter.tick's own call is then a no-op.
	 */
	static void pause(Minecraft mc, LocalPlayer p, Skills.Act a) {
		if (a instanceof Skills.Stop) { // S28 (live 15:13-15:14: stop {} timed out 6x under a reflex): the stop is done, the reflex keeps her safe
			a.status = "ok";
			a.note = "action cancelled; a reflex holds the controls (" + Reflexes.activeName() + ")";
			a.evidence.addProperty("inputs_cleared", false);
			a.evidence.addProperty("latency_ms", (System.nanoTime() - a.startNs) / 1e6);
			a.evidence.addProperty("reflex_active", Reflexes.activeName());
			finish(mc);
			return;
		}
		if (System.nanoTime() > a.deadlineNs) {
			end(mc, "timeout", "timeout", "deadline passed while a reflex held the controls");
			return;
		}
		if (a.paused) return;
		a.paused = true;
		a.pausedTick = EveBody.clientTick;
		a.cancel(mc, p);
		a.ownsInput = false;
	}

	/** The reflex ended: the action resumes only if it can and its preconditions still hold, else it ends interrupted. */
	private static void unpause(Minecraft mc, LocalPlayer p, Skills.Act a) {
		a.paused = false;
		JsonObject reg = Registry.SKILLS.get(a.skill);
		String why = !a.resumable() ? "it cannot resume mid-step" : preconditions(mc, reg);
		if (why == null) {
			a.resume();
			EveBody.LOG.info("eve action {} {} resumed after a reflex", a.skill, a.id);
		} else {
			end(mc, "failed", "interrupted", "pre-empted by a reflex; " + why);
		}
	}

	/** Client thread: handle every queued link message (also scheduled per frame by the link, so a stop never waits for a tick). */
	static void drain(Minecraft mc) {
		for (Link.In in; (in = Link.INBOX.poll()) != null; ) handle(mc, in);
	}

	private static Input runActive(Minecraft mc, LocalPlayer p) {
		Skills.Act a = active;
		long now = System.nanoTime();
		if (now > a.deadlineNs) {
			end(mc, "timeout", "timeout", "deadline passed");
			return Input.EMPTY;
		}
		Input in = a.tick(mc, p);
		a.ticks++;
		if (a.status != null) {
			finish(mc);
			return Input.EMPTY;
		}
		if (!a.internal && now - a.lastProgressNs > TimeUnit.SECONDS.toNanos(2)) { // the mind never asked for an internal one
			a.lastProgressNs = now;
			a.fill(mc, p);
			JsonObject pr = new JsonObject();
			pr.addProperty("action_id", a.id);
			pr.addProperty("pct", a.pct);
			pr.addProperty("note", a.note);
			pr.add("partial_evidence", a.evidence.deepCopy());
			Link.send("action.progress", pr, null);
		}
		return in;
	}

	/** Ends the active action with the given outcome, releasing whatever it held. */
	static void end(Minecraft mc, String status, String code, String note) {
		Skills.Act a = active;
		if (a == null) return;
		a.status = status;
		a.code = code;
		a.note = note;
		if (mc.player != null) a.cancel(mc, mc.player);
		finish(mc);
	}

	private static void finish(Minecraft mc) {
		Skills.Act a = active;
		active = null;
		lastEnded = a.skill;
		actionsDone++;
		if (mc.player != null) a.fill(mc, mc.player);
		JsonObject r = new JsonObject();
		r.addProperty("action_id", a.id);
		r.addProperty("status", a.status);
		r.addProperty("code", a.code);
		r.add("evidence", a.evidence);
		r.addProperty("elapsed_ms", (System.nanoTime() - a.startNs) / 1_000_000);
		r.addProperty("note", a.note);
		if (a.internal) { // owner home: the contract reports it as a control event, not as a result the mind never asked for
			JsonObject d = new JsonObject();
			d.addProperty("op", "home");
			d.addProperty("result", "ok".equals(a.status) ? "arrived" : a.code != null ? a.code : a.status);
			d.addProperty("reason", a.note);
			d.add("evidence", a.evidence);
			Telemetry.event("control", d);
		} else {
			remember(a.id, "action.result", r);
			Link.send("action.result", r, null);
		}
		EveBody.LOG.info("eve action {} {} -> {} {} {}", a.skill, a.id, a.status, a.code, a.evidence);
	}

	private static void handle(Minecraft mc, Link.In in) {
		JsonObject m = in.msg(), p = m.getAsJsonObject("payload");
		String type = Link.str(m, "type"), id = Link.str(m, "id");
		switch (type) {
			case "hello.ok" -> {
				Telemetry.force = true;
				if (!Link.mode.equals(mode) && setMode(Link.mode, "hello.ok") != null) Link.error("mode_not_qualified", "body stays in " + mode, id);
				if (Link.gameJoins > 1) Telemetry.event("reconnect", new JsonObject());
			}
			case "control.stop" -> {
				stop(mc, "mind:" + Link.str(p, "source") + ":" + Link.str(p, "reason"));
				ack("stop", in.recvNanos(), id);
			}
			case "control.resume" -> {
				resume("mind:" + Link.str(p, "source"));
				ack("resume", in.recvNanos(), id);
			}
			case "control.home" -> {
				home(mc, "mind:" + Link.str(p, "reason"));
				ack("home", in.recvNanos(), id);
			}
			case "control.mode" -> {
				String err = setMode(Link.str(p, "mode"), "mind");
				if (err != null) Link.error(err, "mode " + Link.str(p, "mode") + " refused", id);
				else ack("mode", in.recvNanos(), id);
			}
			case "action.request" -> request(mc, m, p, in.recvNanos());
			case "chat.send" -> {
				String text = Link.str(p, "text");
				boolean reply = p != null && p.has("reply") && p.get("reply").isJsonPrimitive() && p.get("reply").getAsBoolean(); // O114
				String err = text == null || text.isEmpty() || text.length() > 256 ? "bad_payload"
						: mc.player == null ? "not_in_game" : Interact.ChatSay.send(mc.player, text, reply, id);
				if (err != null) Link.error(err.equals("bad_text") ? "bad_payload" : err, "chat.send refused: " + err, id);
			}
			case "mob.targeting" -> { // API v4 / H-autofight trigger 4: sense's frame relayed by the mind (data, never a permission)
				if (p != null && p.has("entity_id") && p.get("entity_id").isJsonPrimitive())
					Providers.TARGETING.put(p.get("entity_id").getAsInt(),
							new eve.body.api.ClientView.Targeting(Link.str(p, "target"), Link.str(p, "target_name"), EveBody.clientTick));
			}
			case "query" -> Link.error("not_implemented", "query is not implemented by this body yet", id);
			case "error" -> EveBody.LOG.warn("eve link: mind error {} {}", Link.str(p, "code"), Link.str(p, "message"));
			default -> Link.error("unknown_type", "unknown type " + type, id);
		}
	}

	private static void ack(String op, long recvNanos, String replyTo) {
		JsonObject a = new JsonObject();
		a.addProperty("op", op);
		a.addProperty("latency_ms", (System.nanoTime() - recvNanos) / 1e6);
		a.add("state_after", Telemetry.control());
		Link.send("control.ack", a, replyTo);
	}

	// ---- control ops (shared by the mind link and the eve:control payload) ----

	static void stop(Minecraft mc, String source) {
		boolean was = stopped;
		stopped = true;
		holdSneak = false;
		Reflexes.cancelNotDefensive(mc, mc.player, "stopped"); // O218: a defence in progress keeps going (and keeps its keys)
		if (active != null) end(mc, "cancelled", "cancelled", "stopped by " + source);
		if (Reflexes.activeName() == null) clearNow(mc);
		if (!was) persist();
		verifyOp = "stop";
		verifyTick = 0;
		JsonObject d = new JsonObject();
		d.addProperty("op", "stop");
		d.addProperty("source", source);
		Telemetry.event("control", d);
		EveBody.LOG.info("eve STOPPED by {}", source);
	}

	static void resume(String source) {
		boolean was = stopped;
		stopped = false;
		if (was) persist();
		JsonObject d = new JsonObject();
		d.addProperty("op", "resume");
		d.addProperty("source", source);
		d.addProperty("was_stopped", was);
		Telemetry.event("control", d);
		EveBody.LOG.info("eve resumed by {} (was stopped: {})", source, was);
	}

	static void home(Minecraft mc, String source) {
		JsonObject d = new JsonObject();
		d.addProperty("op", "home");
		d.addProperty("source", source);
		if (stopped) {
			d.addProperty("result", "refused_stopped");
			Telemetry.event("control", d);
			return;
		}
		if (active != null) end(mc, "cancelled", "cancelled", "home requested by " + source);
		JsonObject reg = Registry.SKILLS.get("go_home");
		String why = Link.session == null ? "the mind link is down (no new skill without the mind)"
				: !Skills.baritone() ? Skills.precheck("go_home", new JsonObject()) : preconditions(mc, reg);
		if (why != null) {
			d.addProperty("result", "home_blocked");
			d.addProperty("reason", why);
			Telemetry.event("control", d);
			return;
		}
		long now = System.nanoTime();
		Skills.Act a = Skills.create("go_home", "home-" + java.util.UUID.randomUUID(), new JsonObject(), now,
				now + TimeUnit.MILLISECONDS.toNanos(reg.get("timeout_ms").getAsLong()));
		a.internal = true;
		active = a;
		d.addProperty("result", "started");
		Telemetry.event("control", d);
	}

	/** Returns null on success or the error code. Autonomous is refused until CP-73+ pass. */
	static String setMode(String m, String source) {
		if (m == null || !List.of("manual", "supervised", "autonomous").contains(m)) return "bad_mode";
		if (m.equals("autonomous")) return "mode_not_qualified";
		if (!m.equals(mode)) {
			mode = m;
			persist();
			JsonObject d = new JsonObject();
			d.addProperty("op", "mode");
			d.addProperty("mode", m);
			d.addProperty("source", source);
			Telemetry.event("control", d);
		}
		return null;
	}

	/** Releases every held input now; the next player tick runs on an empty input. */
	static void clearNow(Minecraft mc) {
		INPUT.zero();
		KeyMapping.releaseAll();
		LocalPlayer p = mc.player;
		if (p == null) return;
		p.input = INPUT; // take it back from Baritone at once, not next tick
		p.setSprinting(false);
		if (p.containerMenu != p.inventoryMenu) p.closeContainer();
		if (mc.gameMode != null) {
			if (mc.gameMode.isDestroying()) mc.gameMode.stopDestroyBlock();
			if (p.isUsingItem()) mc.gameMode.releaseUsingItem(p);
		}
	}

	/** What the client actually holds right now, read back (not what we asked for). */
	static JsonObject clearedReport(Minecraft mc, LocalPlayer p) {
		boolean keys = p.input.keyPresses.equals(Input.EMPTY) && p.input.getMoveVector().lengthSquared() == 0;
		boolean sent = p.getLastSentInput().equals(Input.EMPTY);
		boolean use = !p.isUsingItem(), dig = mc.gameMode == null || !mc.gameMode.isDestroying(), sprint = !p.isSprinting();
		boolean maps = !mc.options.keyAttack.isDown() && !mc.options.keyUse.isDown();
		JsonObject r = new JsonObject();
		r.addProperty("client_input_empty", keys);
		r.addProperty("last_sent_input_empty", sent);
		r.addProperty("not_using_item", use);
		r.addProperty("not_destroying", dig);
		r.addProperty("not_sprinting", sprint);
		r.addProperty("attack_use_released", maps);
		r.addProperty("all", keys && sent && use && dig && sprint && maps);
		return r;
	}

	private static void verify(Minecraft mc, LocalPlayer p) {
		verifyTick++;
		JsonObject r = clearedReport(mc, p);
		if (!r.get("all").getAsBoolean() && verifyTick < 5) return;
		r.addProperty("op", verifyOp);
		r.addProperty("ticks_after_clear", verifyTick);
		r.addProperty("inputs_cleared", r.get("all").getAsBoolean());
		r.addProperty("reflex_active", Reflexes.activeName()); // O218: a defensive reflex keeps its own inputs under STOPPED
		Telemetry.event("control", r);
		EveBody.LOG.info("eve inputs cleared check after {} tick(s): {}", verifyTick, r);
		verifyOp = null;
	}

	/** eve:control from the server helper; Fabric runs this on the client thread. Works with the mind down. */
	static void onServerControl(Minecraft mc, String json) {
		long t0 = System.nanoTime();
		JsonObject ack = new JsonObject();
		JsonObject c = null;
		try {
			JsonElement e = JsonParser.parseString(json);
			if (e.isJsonObject()) c = e.getAsJsonObject();
		} catch (Exception ignored) {
		}
		String op = Link.str(c, "op"), nonce = Link.str(c, "nonce"), src = "server:" + Link.str(c, "source");
		ack.addProperty("nonce", nonce);
		ack.addProperty("op", op);
		if (c == null || op == null || nonce == null) {
			ack.addProperty("error", "bad_payload");
		} else if (NONCES.containsKey(nonce)) {
			ack.addProperty("duplicate", true); // re-acked, never re-applied
		} else {
			NONCES.put(nonce, true);
			switch (op) {
				case "stop" -> stop(mc, src);
				case "resume" -> resume(src);
				case "home" -> home(mc, src);
				case "world" -> Link.setWorld(Link.str(c, "level_name"), Link.str(c, "world_id"));
				case "mode" -> {
					String err = setMode(Link.str(c, "mode"), src);
					if (err != null) ack.addProperty("error", err);
				}
				default -> ack.addProperty("error", "unknown_op");
			}
		}
		ack.addProperty("latency_ms", (System.nanoTime() - t0) / 1e6);
		ack.addProperty("tick", EveBody.clientTick);
		ack.addProperty("stopped", stopped);
		ClientPlayNetworking.send(new Payloads.Ack(Registry.GSON.toJson(ack)));
		EveBody.LOG.info("eve:control {} -> ack {}", json, ack);
	}

	// ---- action requests ----

	private static void request(Minecraft mc, JsonObject m, JsonObject p, long recvNs) {
		String reqId = Link.str(m, "id"), aid = Link.str(p, "action_id"), skill = Link.str(p, "skill");
		if (aid == null) {
			Link.error("bad_params", "action_id missing", reqId);
			return;
		}
		if (active != null && active.id.equals(aid)) { // duplicate of the running action: re-report, never re-execute
			JsonObject a = new JsonObject();
			a.addProperty("action_id", aid);
			Link.send("action.accepted", a, reqId);
			return;
		}
		Remembered r = DONE.get(aid);
		if (r != null && System.nanoTime() - r.atNanos() < REMEMBER_NS) {
			Link.send(r.type(), r.payload(), reqId);
			return;
		}
		JsonObject reg = skill == null ? null : Registry.SKILLS.get(skill);
		if (reg == null || !Registry.IMPLEMENTED.contains(skill)) {
			reject(aid, reqId, "unknown_skill", "skill " + skill + " is not implemented by this body");
			return;
		}
		int want = reg.get("version").getAsInt();
		if (!p.has("version") || !p.get("version").isJsonPrimitive() || p.get("version").getAsDouble() != want) {
			reject(aid, reqId, "unknown_skill", skill + " version " + p.get("version") + " not implemented (have " + want + ")");
			return;
		}
		JsonElement params = p.has("params") && !p.get("params").isJsonNull() ? p.get("params") : new JsonObject();
		String bad = Registry.check(params, reg.getAsJsonObject("params"), "params");
		if (bad != null) {
			reject(aid, reqId, "bad_params", bad);
			return;
		}
		boolean isStop = skill.equals("stop");
		if (!isStop && stopped) {
			reject(aid, reqId, "stopped", "body is STOPPED; only an explicit resume ends it");
			return;
		}
		if (rank(reg.get("min_mode").getAsString()) > rank(mode)) {
			reject(aid, reqId, "mode_denied", skill + " needs mode " + reg.get("min_mode").getAsString());
			return;
		}
		if (!isStop && active != null) {
			reject(aid, reqId, "busy", "running " + active.skill + " " + active.id);
			return;
		}
		String pre = isStop ? null : preconditions(mc, reg);
		if (pre == null && !isStop) pre = Skills.precheck(skill, params.getAsJsonObject());
		if (pre != null) {
			reject(aid, reqId, "precondition_failed", pre);
			return;
		}
		long budgetMs = reg.get("timeout_ms").getAsLong();
		if (p.has("deadline_ms") && p.get("deadline_ms").isJsonPrimitive() && p.get("deadline_ms").getAsJsonPrimitive().isNumber()) {
			// ponytail: contract does not say whether deadline_ms is absolute or relative; >1e12 is read as epoch ms, else a duration.
			long d = p.get("deadline_ms").getAsLong();
			long rel = d > 1_000_000_000_000L ? d - System.currentTimeMillis() : d;
			if (rel <= 0) {
				reject(aid, reqId, "precondition_failed", "deadline already passed");
				return;
			}
			budgetMs = Math.min(budgetMs, rel);
		}
		Skills.Act a = Skills.create(skill, aid, params.getAsJsonObject(), recvNs, recvNs + TimeUnit.MILLISECONDS.toNanos(budgetMs));
		// H-boat (mind's note 1): a stop never leaves her floating. A follow_boat afloat (or with its boat still out) winds down
		// itself: to the shore, out, the boat picked up, then it ends cancelled. The stop answers ok at once; the hard STOP latch
		// (control.stop) still ends everything as before.
		if (isStop && active instanceof FollowBoat fb && mc.player != null && fb.windDown(mc.player)) {
			JsonObject acc = new JsonObject();
			acc.addProperty("action_id", aid);
			Link.send("action.accepted", acc, reqId);
			JsonObject wr = new JsonObject(), ev = new JsonObject();
			ev.addProperty("inputs_cleared", false);
			ev.addProperty("latency_ms", (System.nanoTime() - recvNs) / 1e6);
			ev.addProperty("winding_down", fb.id);
			wr.addProperty("action_id", aid);
			wr.addProperty("status", "ok");
			wr.add("code", null);
			wr.add("evidence", ev);
			wr.addProperty("elapsed_ms", (System.nanoTime() - recvNs) / 1_000_000);
			wr.addProperty("note", "follow_boat " + fb.id + " lands and picks up the boat first, then ends cancelled");
			remember(aid, "action.result", wr);
			Link.send("action.result", wr, null);
			return;
		}
		if (isStop) {
			if (active != null) end(mc, "cancelled", "cancelled", "stop skill " + aid);
			holdSneak = false;
			// S28 (15:13-15:14: six stops in the fatal fight): the mind's stop ends the action, never a defence. Releasing every key
			// under a reflex dropped its raised shield (and its use); the reflex keeps the controls and lets go itself
			if (Reflexes.activeName() == null) clearNow(mc);
			((Skills.Stop) a).clearedNs = System.nanoTime();
		}
		active = a;
		JsonObject acc = new JsonObject();
		acc.addProperty("action_id", aid);
		Link.send("action.accepted", acc, reqId);
		EveBody.LOG.info("eve action {} {} accepted in {} ms", skill, aid, (System.nanoTime() - recvNs) / 1e6);
	}

	private static void reject(String aid, String reqId, String code, String reason) {
		JsonObject r = new JsonObject();
		r.addProperty("action_id", aid);
		r.addProperty("code", code);
		r.addProperty("reason", reason);
		remember(aid, "action.rejected", r);
		Link.send("action.rejected", r, reqId);
		EveBody.LOG.info("eve action {} rejected: {} {}", aid, code, reason);
	}

	private static void remember(String aid, String type, JsonObject payload) {
		DONE.put(aid, new Remembered(type, payload, System.nanoTime()));
	}

	private static int rank(String m) {
		return switch (m) {
			case "manual" -> 0;
			case "supervised" -> 1;
			default -> 2;
		};
	}

	/** Null if every precondition holds; fails closed on preconditions this body cannot evaluate. */
	static String preconditions(Minecraft mc, JsonObject reg) {
		LocalPlayer pl = mc.player;
		for (JsonElement e : reg.getAsJsonArray("preconditions")) {
			String c = e.getAsString();
			boolean ok = switch (c) {
				case "alive" -> pl != null && !pl.isDeadOrDying();
				case "not_stopped" -> !stopped;
				case "link_fresh" -> Link.session != null;
				case "home_known" -> {
					Envelope.Home h = Envelope.home();
					yield h != null && pl != null && pl.level().dimension().identifier().toString().equals(h.dim());
				}
				case "on_ground" -> pl != null && pl.onGround();
				// param-dependent: the skill checks them itself and fails with its own code (out_of_range, protected_entity, no_item)
				case "target_not_protected", "in_reach", "has_item" -> true;
				default -> false;
			};
			if (!ok) return "precondition " + c + " does not hold";
		}
		return null;
	}

	/** Game disconnect: cancel (the mind gets the result while the link is still up) and drop every held input. */
	static void onGameDisconnect(Minecraft mc) {
		end(mc, "cancelled", "interrupted", "game disconnected");
		Reflexes.cancel(mc, mc.player, "disconnected");
		holdSneak = false;
		INPUT.zero();
	}

	// ---- persistence of STOPPED and mode (no automatic resume across restarts) ----

	private static Path file() {
		return FabricLoader.getInstance().getConfigDir().resolve("eve-body-control.json");
	}

	static void loadPersisted() {
		try {
			if (!Files.exists(file())) return;
			JsonObject o = JsonParser.parseString(Files.readString(file())).getAsJsonObject();
			stopped = o.get("stopped").getAsBoolean();
			String m = o.get("mode").getAsString();
			if (m.equals("manual") || m.equals("supervised")) mode = m;
			EveBody.LOG.info("eve control restored: stopped={} mode={}", stopped, mode);
		} catch (Exception e) {
			stopped = true; // unreadable state file: fail safe, wait for an explicit resume
			EveBody.LOG.warn("eve control state unreadable, starting STOPPED: {}", e.toString());
		}
	}

	private static void persist() {
		JsonObject o = new JsonObject();
		o.addProperty("stopped", stopped);
		o.addProperty("mode", mode);
		try {
			Files.createDirectories(file().getParent());
			Files.writeString(file(), Registry.GSON.toJson(o));
		} catch (Exception e) {
			EveBody.LOG.error("eve control state not saved: {}", e.toString());
		}
	}
}
