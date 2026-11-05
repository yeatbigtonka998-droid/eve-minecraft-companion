package eve.body;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.entity.Mob;
import net.minecraft.world.entity.NeutralMob;
import net.minecraft.world.entity.TamableAnimal;
import net.minecraft.world.entity.animal.equine.AbstractHorse;
import net.minecraft.world.entity.animal.golem.AbstractGolem;
import net.minecraft.world.entity.monster.Creeper;
import net.minecraft.world.entity.monster.Enderman;
import net.minecraft.world.entity.monster.Enemy;
import net.minecraft.world.entity.monster.RangedAttackMob;
import net.minecraft.world.entity.npc.villager.AbstractVillager;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.entity.projectile.Projectile;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.phys.Vec3;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

/** state at state_hz and immediately on health/dimension/inventory change; damage/death/respawn events; metrics every 30 s. */
final class Telemetry {
	static final double ENTITY_RADIUS = 32; // envelope sensors.entities_radius default
	static int hz = 4;
	static boolean force;
	static double tickMsAvg;
	private static long lastStateNs, lastMetricsNs;
	private static float lastHealth = -1;
	private static String lastDim, lastInv;
	static int lastAttackerId = -1;
	static long lastHitTick = -1;
	private static boolean wasDead;

	/** END_CLIENT_TICK. */
	static void tick(Minecraft mc, long tickStartNs) {
		long now = System.nanoTime();
		tickMsAvg = tickMsAvg * 0.95 + 0.05 * (now - tickStartNs) / 1e6;
		LocalPlayer p = mc.player;
		if (p == null || Link.session == null || EveBody.lastServerPacketNanos == 0) return;
		float h = p.getHealth();
		String dim = p.level().dimension().identifier().toString();
		JsonArray inv = inventory(p);
		String invHash = sha1(Registry.GSON.toJson(inv));
		if (lastHealth >= 0 && h < lastHealth) {
			JsonObject d = new JsonObject();
			d.addProperty("amount", lastHealth - h);
			d.addProperty("health", h);
			// O98d diagnostic (a96 shield #2/#3: an arrow landed ~20 ticks into a raised, facing shield): her block state and where
			// the hit came from, relative to her horizontal view (vanilla blocks a source within the shield's horizontal angle)
			var src = p.getLastDamageSource();
			d.addProperty("cause", src == null ? null : src.type().msgId());
			d.addProperty("blocking", p.isBlocking());
			d.addProperty("using", p.isUsingItem());
			d.addProperty("use_ticks", p.getTicksUsingItem());
			d.addProperty("offhand", Hands.itemId(p.getOffhandItem()));
			d.addProperty("fatal", p.isDeadOrDying()); // S28: a fatal hit's inventory is already dropped (offhand null, blocking false)
			var sp = src == null ? null : src.getSourcePosition();
			if (sp != null) {
				var to = sp.subtract(p.position()).multiply(1, 0, 1);
				var view = p.getViewVector(1f).multiply(1, 0, 1);
				double ang = to.lengthSqr() < 1e-6 || view.lengthSqr() < 1e-6 ? 0
						: Math.toDegrees(Math.acos(Math.max(-1, Math.min(1, to.normalize().dot(view.normalize())))));
				d.addProperty("source_angle_deg", Math.round(ang * 10) / 10.0);
				d.add("source_pos", pos(sp));
			}
			event("damage", d);
			var att = src == null ? null : src.getEntity(); // API v4 ClientView: who hit her, and when
			lastAttackerId = att == null ? -1 : att.getId();
			lastHitTick = EveBody.clientTick;
		}
		boolean dead = p.isDeadOrDying();
		if (dead != wasDead) {
			JsonObject d = new JsonObject();
			d.add("pos", pos(p.position()));
			d.addProperty("dim", dim);
			if (dead) { // CP-31: the mind's night run records every death with its cause (vanilla damage type id, e.g. "mob", "lava")
				var src = p.getLastDamageSource();
				d.addProperty("cause", src == null ? null : src.type().msgId());
				d.addProperty("killer_type", src == null || src.getEntity() == null ? null : BuiltInRegistries.ENTITY_TYPE.getKey(src.getEntity().getType()).toString());
			}
			event(dead ? "death" : "respawn", d);
		}
		wasDead = dead;
		boolean changed = force || h != lastHealth || !dim.equals(lastDim) || !invHash.equals(lastInv);
		lastHealth = h;
		lastDim = dim;
		lastInv = invHash;
		if (changed || now - lastStateNs >= 1_000_000_000L / hz) {
			if (Link.send("state", state(mc, p, dim, inv, invHash), null)) {
				lastStateNs = now;
				force = false;
			}
		}
		if (now - lastMetricsNs >= 30_000_000_000L) {
			lastMetricsNs = now;
			JsonObject m = new JsonObject();
			m.addProperty("tick_ms_avg", Skills.r3(tickMsAvg));
			m.addProperty("fps", mc.getFps());
			m.addProperty("actions_running", Arbiter.active == null ? 0 : 1);
			m.addProperty("actions_done", Arbiter.actionsDone);
			m.addProperty("reflex_triggers", Reflexes.triggers);
			m.addProperty("dropped_messages", Link.DROPPED.get());
			m.addProperty("dropped_session_mismatch", Link.DROPPED_SESSION.get());
			Link.send("metrics", m, null);
		}
	}

	static void event(String kind, JsonObject detail) {
		JsonObject e = new JsonObject();
		e.addProperty("kind", kind);
		e.add("detail", detail);
		Link.send("event", e, null);
	}

	static JsonObject control() {
		JsonObject c = new JsonObject();
		c.addProperty("mode", Arbiter.mode);
		c.addProperty("stopped", Arbiter.stopped);
		// an internal action (owner home) is not a mind action: null here, so the mind's reconciliation does not trip on it
		c.addProperty("active_action_id", Arbiter.active == null || Arbiter.active.internal ? null : Arbiter.active.id);
		if (Arbiter.active != null && Arbiter.active.internal) c.addProperty("internal_action", Arbiter.active.skill);
		c.addProperty("reflex_active", Reflexes.activeName());
		return c;
	}

	/** The state payload as it would be sent now (skill providers' BodyServices.state()). */
	static JsonObject snapshot(Minecraft mc, LocalPlayer p) {
		JsonArray inv = inventory(p);
		return state(mc, p, p.level().dimension().identifier().toString(), inv, sha1(Registry.GSON.toJson(inv)));
	}

	private static JsonObject state(Minecraft mc, LocalPlayer p, String dim, JsonArray inv, String invHash) {
		var level = mc.level;
		BlockPos bp = p.blockPosition();
		JsonObject s = new JsonObject();
		s.add("pos", pos(p.position()));
		s.addProperty("dim", dim);
		s.addProperty("yaw", Skills.r2(net.minecraft.util.Mth.wrapDegrees(p.getYRot())));
		s.addProperty("pitch", Skills.r2(p.getXRot()));
		s.addProperty("health", p.getHealth());
		s.addProperty("food", p.getFoodData().getFoodLevel());
		s.addProperty("saturation", p.getFoodData().getSaturationLevel());
		s.addProperty("air", p.getAirSupply());
		s.addProperty("xp_level", p.experienceLevel);
		s.addProperty("on_ground", p.onGround());
		s.addProperty("in_water", p.isInWater());
		s.addProperty("in_lava", p.isInLava());
		s.addProperty("fall_distance", Skills.r3(p.fallDistance));
		s.addProperty("sneaking", p.isShiftKeyDown());
		s.addProperty("gamemode", mc.gameMode == null ? null : mc.gameMode.getPlayerMode().getName());
		s.add("inventory", inv);
		s.addProperty("inventory_hash", invHash);
		JsonObject eq = new JsonObject();
		for (EquipmentSlot slot : List.of(EquipmentSlot.HEAD, EquipmentSlot.CHEST, EquipmentSlot.LEGS, EquipmentSlot.FEET, EquipmentSlot.MAINHAND, EquipmentSlot.OFFHAND)) {
			ItemStack st = p.getItemBySlot(slot);
			eq.addProperty(slot.getName(), st.isEmpty() ? null : itemId(st));
		}
		s.add("equipment", eq);
		s.addProperty("hotbar_slot", p.getInventory().getSelectedSlot());
		JsonArray near = new JsonArray(), threats = new JsonArray();
		List<Entity> es = new ArrayList<>();
		// dying entities (a killed mob's 20-tick death animation) are not listed: absent means absent
		for (Entity e : level.entitiesForRendering()) if (e != p && e.isAlive() && e.distanceTo(p) <= ENTITY_RADIUS) es.add(e);
		es.sort(Comparator.comparingDouble(e -> e.distanceTo(p)));
		for (Entity e : es.subList(0, Math.min(es.size(), 64))) {
			double dist = e.distanceTo(p);
			boolean hostile = isHostile(e);
			JsonObject o = new JsonObject();
			o.addProperty("id", e.getId());
			o.addProperty("uuid", e.getUUID().toString()); // stable identity across ticks and sessions; the network id is not
			o.addProperty("type", BuiltInRegistries.ENTITY_TYPE.getKey(e.getType()).toString());
			o.addProperty("name", e.hasCustomName() || e instanceof Player ? e.getName().getString() : null);
			o.add("pos", pos(e.position()));
			o.addProperty("distance", Skills.r2(dist));
			o.addProperty("hostile", hostile);
			o.addProperty("protected", isProtected(e));
			o.addProperty("line_of_sight", p.hasLineOfSight(e));
			// H-boat (additive): what it rides and whether it is in water, so the mind's follow sees the owner take to a boat
			o.addProperty("vehicle", e.getVehicle() == null ? null : BuiltInRegistries.ENTITY_TYPE.getKey(e.getVehicle().getType()).toString());
			o.addProperty("in_water", e.isInWater());
			near.add(o);
			// ponytail: distance-only danger heuristic; Phase 3 reflexes replace it with measured threat geometry
			String kind = e instanceof Projectile pr && pr.getOwner() != p && e.getDeltaMovement().lengthSqr() > 0.01 ? "projectile"
					: !hostile ? null : e instanceof Creeper ? "explosive" : e instanceof RangedAttackMob ? "ranged" : "melee";
			if (kind != null && dist <= 16) {
				JsonObject t = new JsonObject();
				t.addProperty("entity_id", e.getId());
				t.addProperty("kind", kind);
				t.addProperty("danger", Skills.r2(1 - dist / 16));
				threats.add(t);
			}
		}
		s.add("nearby_entities", near);
		s.add("threats", threats);
		s.addProperty("light_level", level.getMaxLocalRawBrightness(bp));
		s.addProperty("biome", level.getBiome(bp).unwrapKey().map(k -> k.identifier().toString()).orElse(null));
		s.addProperty("time_of_day", Math.floorMod(level.getOverworldClockTime(), 24000L));
		s.addProperty("weather", level.isThundering() ? "thunder" : level.isRaining() ? "rain" : "clear");
		s.add("control", control());
		// world identity on every observation (envelope `world` stays null by contract; the mind binds from sense's hello)
		s.addProperty("world_id", Link.worldId());
		s.addProperty("level_name", Link.levelName());
		JsonObject f = new JsonObject();
		f.addProperty("tick", EveBody.gameTime);
		long last = EveBody.lastServerPacketNanos;
		f.addProperty("age_ms", (System.nanoTime() - last) / 1_000_000); // since the last packet from the server
		s.add("freshness", f);
		return s;
	}

	/**
	 * An Enemy, unless it is a neutral mob that is calm: an enderman counts only while it has a target (isCreepy, synced) or attacks,
	 * a zombified piglin only while aggressive (synced mob flag). CP-31 a9 attempt 11: a calm enderman 2 blocks away counted as a
	 * melee threat, her shield stayed up at it for 30 s and night_survival's fight provoked it (death 1). Telemetry and the reflex
	 * scan share this test.
	 */
	static boolean isHostile(Entity e) {
		if (!(e instanceof Enemy)) return false;
		if (!(e instanceof NeutralMob)) return true;
		return e instanceof Mob m && m.isAggressive() || e instanceof Enderman em && em.isCreepy();
	}

	/** Body's local view of envelope protected_entities (all true by default); the server helper is the enforcing authority. */
	static boolean isProtected(Entity e) {
		if (Envelope.extraProtected().contains(BuiltInRegistries.ENTITY_TYPE.getKey(e.getType()).toString())) return true;
		return e instanceof Player || e instanceof AbstractVillager || e instanceof AbstractGolem || e.hasCustomName()
				|| (e instanceof TamableAnimal t && t.isTame()) || (e instanceof AbstractHorse h && h.isTamed());
	}

	private static JsonArray inventory(LocalPlayer p) {
		JsonArray a = new JsonArray();
		var inv = p.getInventory();
		for (int i = 0; i < inv.getContainerSize(); i++) {
			ItemStack st = inv.getItem(i);
			if (st.isEmpty()) continue;
			JsonObject o = new JsonObject();
			o.addProperty("slot", i);
			o.addProperty("item", itemId(st));
			o.addProperty("count", st.getCount());
			o.addProperty("damage", st.getDamageValue());
			o.addProperty("max_damage", st.getMaxDamage());
			a.add(o);
		}
		return a;
	}

	private static String itemId(ItemStack st) {
		return BuiltInRegistries.ITEM.getKey(st.getItem()).toString();
	}

	static JsonObject pos(Vec3 v) {
		JsonObject o = new JsonObject();
		o.addProperty("x", Skills.r3(v.x));
		o.addProperty("y", Skills.r3(v.y));
		o.addProperty("z", Skills.r3(v.z));
		return o;
	}

	private static String sha1(String s) {
		try {
			StringBuilder sb = new StringBuilder();
			for (byte b : MessageDigest.getInstance("SHA-1").digest(s.getBytes(StandardCharsets.UTF_8))) sb.append(String.format("%02x", b));
			return sb.toString();
		} catch (Exception e) {
			throw new IllegalStateException(e);
		}
	}
}
