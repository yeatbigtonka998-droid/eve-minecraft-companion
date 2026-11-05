package eve.sense;

import com.google.gson.JsonObject;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.damagesource.DamageSource;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.projectile.arrow.AbstractArrow;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.phys.Vec3;

/**
 * What the server sees when Eve is hit and when she uses items (O174 diagnostic, made a normal audit line in 0.1.28).
 * `[eve-audit] eve.hit {...}`: one line per damage event on Eve, filled across LivingEntity.hurtServer (HEAD: her state,
 * the source, the angles), applyItemBlocking (RETURN: what the shield took) and getDamageAfterArmorAbsorb (armour),
 * logged at hurtServer RETURN. Angles are as vanilla's applyItemBlocking measures them: the source vs her HEAD yaw.
 * `[eve-audit] eve.use {...}`: one line per finished use (item, hand, start tick, held ticks, how it ended, whether it
 * restarted in the tick the previous use ended), plus an anomaly line for a use packet vanilla dropped because her client
 * had not loaded. `-Deve.hitlog=off|on|verbose` (default on; false = off): on = compact hit lines + use summaries;
 * verbose = every field, every use packet and every server start/stop as well (the O174 run's view). Server thread only.
 */
public final class HitLog {
	enum Mode { OFF, ON, VERBOSE }
	static Mode mode = parse(System.getProperty("eve.hitlog"));
	/** The compact hit line (mode on): these keys; `client_loaded` only when false; attacker without pos; direct as a type. */
	static final java.util.Set<String> HIT_KEEP = java.util.Set.of("what", "tick", "source_type", "attacker", "direct", "amount_in", "blocked",
			"armor_out", "hurt", "health_after", "is_blocking", "is_using", "ticks_using", "use_item", "angle_head_deg", "mainhand", "offhand",
			"arrow_pierce", "client_loaded");
	private static JsonObject current; // the hit being filled; Eve's hurts do not nest
	private static LivingEntity currentEve;
	public static volatile JsonObject lastHit, lastUse; // test hooks
	// the use in progress (Eve only: one entity)
	private static long useStart = -1, lastStop = Long.MIN_VALUE;
	private static String useItemId, useHand, pendingEnd;
	private static boolean restarted;

	private HitLog() {}

	static Mode parse(String s) {
		if (s == null) return Mode.ON;
		return switch (s.toLowerCase(java.util.Locale.ROOT)) {
			case "off", "false" -> Mode.OFF;
			case "verbose" -> Mode.VERBOSE;
			default -> Mode.ON;
		};
	}

	/** Test hook: off | on | verbose. */
	public static void modeForTest(String s) {
		mode = parse(s);
		useStart = -1;
		pendingEnd = null;
		lastStop = Long.MIN_VALUE;
	}

	static boolean isEve(Object e) {
		return mode != Mode.OFF && e instanceof ServerPlayer p && Rails.isEve(p);
	}

	private static long tick() {
		return EveSense.server == null ? -1 : EveSense.server.getTickCount();
	}

	/** hurtServer HEAD. */
	public static void begin(LivingEntity eve, DamageSource src, float amount) {
		if (!isEve(eve)) return;
		JsonObject o = state(eve, "hit");
		o.addProperty("amount_in", amount);
		o.addProperty("source_type", src.typeHolder().unwrapKey().map(k -> k.identifier().toString()).orElse(src.getMsgId()));
		o.addProperty("source_msg", src.getMsgId());
		o.add("attacker", entity(src.getEntity()));
		o.add("direct", entity(src.getDirectEntity()));
		if (src.getDirectEntity() instanceof AbstractArrow a) o.addProperty("arrow_pierce", a.getPierceLevel());
		Vec3 sp = src.getSourcePosition();
		if (sp != null) {
			o.add("source_pos", vec(sp));
			o.addProperty("angle_head_deg", angle(eve, sp, eve.getYHeadRot()));
			o.addProperty("angle_body_deg", angle(eve, sp, eve.getYRot()));
		}
		o.addProperty("health_before", eve.getHealth());
		current = o;
		currentEve = eve;
	}

	/** applyItemBlocking RETURN: the damage the blocking item took off (0 = not blocked). */
	public static void blocked(LivingEntity e, float blocked) {
		if (current == null || e != currentEve) return;
		current.addProperty("blocked", blocked);
		ItemStack with = e.getItemBlockingWith();
		current.addProperty("blocking_with", with == null ? "none" : id(with));
	}

	/** getDamageAfterArmorAbsorb RETURN. */
	public static void armor(LivingEntity e, float in, float out) {
		if (current == null || e != currentEve) return;
		current.addProperty("armor_in", in);
		current.addProperty("armor_out", out);
	}

	/** hurtServer RETURN: log the hit (compact unless verbose). */
	public static void end(LivingEntity e, boolean hurt) {
		if (current == null || e != currentEve) return;
		current.addProperty("hurt", hurt);
		current.addProperty("health_after", e.getHealth());
		JsonObject o = mode == Mode.VERBOSE ? current : compact(current);
		EveSense.audit("eve.hit", o);
		lastHit = o;
		current = null;
		currentEve = null;
	}

	static JsonObject compact(JsonObject full) {
		JsonObject o = new JsonObject();
		for (var en : full.entrySet()) if (HIT_KEEP.contains(en.getKey())) o.add(en.getKey(), en.getValue());
		if (o.has("client_loaded") && o.get("client_loaded").getAsBoolean()) o.remove("client_loaded"); // only the anomaly
		if (o.has("attacker") && o.getAsJsonObject("attacker").has("pos")) {
			JsonObject a = o.getAsJsonObject("attacker").deepCopy();
			a.remove("pos");
			o.add("attacker", a);
		}
		if (o.has("direct")) {
			JsonObject d = o.getAsJsonObject("direct");
			o.addProperty("direct", d.has("type") ? d.get("type").getAsString() : "none");
		}
		return o;
	}

	/** releaseUsingItem HEAD (her release packet, a bow's loose): the use that stops next ended "released". */
	public static void released(LivingEntity eve) {
		if (isEve(eve) && eve.isUsingItem()) pendingEnd = "released";
	}

	/** completeUsingItem HEAD (eaten, drunk): the use that stops next ended "finished". */
	public static void finished(LivingEntity eve) {
		if (isEve(eve) && eve.isUsingItem()) pendingEnd = "finished";
	}

	/**
	 * The server's own start/stop of her item use, and her release/swap packets. Start and stop feed the one-line use
	 * summary (written at stop); every raw line is written in verbose only.
	 */
	public static void use(LivingEntity eve, String what, InteractionHand hand) {
		if (!isEve(eve)) return;
		long t = tick();
		if (what.equals("server_start_using") && hand != null) {
			useStart = t;
			useHand = hand.name().toLowerCase(java.util.Locale.ROOT);
			useItemId = id(eve.getItemInHand(hand));
			restarted = t == lastStop; // re-pressed in the tick the previous use ended (O174: restarts the block delay)
		}
		if (mode == Mode.VERBOSE) {
			JsonObject o = state(eve, what);
			if (hand != null) {
				o.addProperty("hand", hand.name().toLowerCase(java.util.Locale.ROOT));
				o.addProperty("hand_item", id(eve.getItemInHand(hand)));
			}
			EveSense.audit("eve.use", o);
			lastUse = o;
		}
		if (what.equals("server_stop_using") && useStart >= 0) {
			JsonObject s = new JsonObject();
			s.addProperty("what", "use");
			s.addProperty("item", useItemId);
			s.addProperty("hand", useHand);
			s.addProperty("start_tick", useStart);
			s.addProperty("held_ticks", t - useStart);
			s.addProperty("ended", pendingEnd == null ? "server" : pendingEnd);
			s.addProperty("restarted", restarted);
			EveSense.audit("eve.use", s);
			lastUse = s;
			lastStop = t;
			useStart = -1;
			pendingEnd = null;
		}
	}

	/** Her use-item packet as received. Verbose: every packet with the client's rotation; on: only a packet vanilla
	 * dropped because her client had not loaded (the anomaly line). */
	public static void usePacket(LivingEntity eve, InteractionHand hand, int sequence, float yRot, float xRot) {
		if (!isEve(eve)) return;
		boolean loaded = !(eve instanceof ServerPlayer sp) || sp.connection == null || sp.connection.hasClientLoaded();
		if (mode != Mode.VERBOSE && loaded) return;
		JsonObject o = mode == Mode.VERBOSE ? state(eve, "packet_use_item") : new JsonObject();
		if (mode != Mode.VERBOSE) {
			o.addProperty("what", "packet_dropped_not_loaded");
			o.addProperty("tick", tick());
		}
		o.addProperty("hand", hand.name().toLowerCase(java.util.Locale.ROOT));
		o.addProperty("hand_item", id(eve.getItemInHand(hand)));
		o.addProperty("sequence", sequence);
		o.addProperty("packet_yaw", yRot);
		o.addProperty("packet_pitch", xRot);
		EveSense.audit("eve.use", o);
		lastUse = o;
	}

	private static JsonObject state(LivingEntity eve, String what) {
		JsonObject o = new JsonObject();
		o.addProperty("what", what);
		o.addProperty("tick", EveSense.server == null ? -1 : EveSense.server.getTickCount());
		// vanilla drops her use-item packets (handleUseItem returns) until the client has sent "player loaded"
		if (eve instanceof ServerPlayer sp && sp.connection != null) o.addProperty("client_loaded", sp.connection.hasClientLoaded());
		o.addProperty("is_blocking", eve.isBlocking());
		o.addProperty("is_using", eve.isUsingItem());
		o.addProperty("ticks_using", eve.getTicksUsingItem());
		o.addProperty("use_item", eve.isUsingItem() ? id(eve.getUseItem()) : "none");
		o.addProperty("used_hand", eve.isUsingItem() ? eve.getUsedItemHand().name().toLowerCase(java.util.Locale.ROOT) : "none");
		o.addProperty("mainhand", id(eve.getMainHandItem()));
		o.addProperty("offhand", id(eve.getOffhandItem()));
		o.add("pos", vec(eve.position()));
		o.addProperty("yaw_body", eve.getYRot());
		o.addProperty("yaw_head", eve.getYHeadRot());
		o.addProperty("pitch", eve.getXRot());
		return o;
	}

	/** Horizontal angle, as vanilla's applyItemBlocking: view vector from `yaw` vs the direction to the source. */
	static double angle(LivingEntity eve, Vec3 src, float yaw) {
		Vec3 view = Vec3.directionFromRotation(0, yaw);
		Vec3 to = src.subtract(eve.position());
		Vec3 flat = new Vec3(to.x, 0, to.z);
		if (flat.lengthSqr() < 1e-9) return 0;
		return Math.toDegrees(Math.acos(Math.max(-1, Math.min(1, flat.normalize().dot(view)))));
	}

	private static JsonObject entity(Entity e) {
		JsonObject o = new JsonObject();
		if (e == null) return o;
		o.addProperty("type", BuiltInRegistries.ENTITY_TYPE.getKey(e.getType()).toString());
		o.addProperty("id", e.getId());
		o.add("pos", vec(e.position()));
		return o;
	}

	private static JsonObject vec(Vec3 v) {
		JsonObject o = new JsonObject();
		o.addProperty("x", Math.round(v.x * 100) / 100.0);
		o.addProperty("y", Math.round(v.y * 100) / 100.0);
		o.addProperty("z", Math.round(v.z * 100) / 100.0);
		return o;
	}

	private static String id(ItemStack s) {
		return s == null || s.isEmpty() ? "empty" : BuiltInRegistries.ITEM.getKey(s.getItem()).toString();
	}
}
