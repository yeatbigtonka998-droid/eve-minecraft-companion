package eve.sense;

import com.google.gson.JsonObject;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.Mob;
import net.minecraft.world.entity.monster.Enemy;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * H-autofight (O166, link v1.13): a mob's AI target is not synced to clients, so Eve cannot see a hostile going for
 * Hunter at range. Every 10 ticks, for each online trusted player and Eve inside home's explore radius, sense sends
 * mob.targeting for each hostile (Enemy) within 32 blocks whose attack target (Mob.getTarget(), brain mobs included)
 * is that player: at once on first sight, then at most once per mob per 2 s. Data only, never a permission.
 */
public final class MobTargeting {
	static final int SCAN_TICKS = 10, RANGE = 32;
	static final long THROTTLE_MS = 2000;
	private static final Map<UUID, Long> last = new HashMap<>(); // mob -> last sent ms

	private MobTargeting() {}

	static void register() {
		ServerTickEvents.END_SERVER_TICK.register(s -> {
			if (s.getTickCount() % SCAN_TICKS == 0) scan(s.getPlayerList().getPlayers(), s.getTickCount(), System.currentTimeMillis());
		});
	}

	/** One scan (the tick body; tests call it with their own players and clock). */
	public static void scan(List<ServerPlayer> players, int tick, long now) {
		Envelope env = EveSense.env();
		last.values().removeIf(t -> now - t > 60_000);
		for (ServerPlayer p : players) {
			String role = Rails.isEve(p) ? "eve" : Control.isTrusted(p) ? "owner" : null;
			if (role == null || !inPlayArea(p, env)) continue;
			ServerLevel level = (ServerLevel) p.level();
			for (Mob m : level.getEntitiesOfClass(Mob.class, p.getBoundingBox().inflate(RANGE), x -> x instanceof Enemy && x.getTarget() == p)) {
				Long t = last.get(m.getUUID());
				if (t != null && now - t < THROTTLE_MS) continue;
				last.put(m.getUUID(), now);
				JsonObject o = new JsonObject();
				o.addProperty("entity_uuid", m.getUUID().toString());
				o.addProperty("entity_id", m.getId());
				o.addProperty("entity_type", BuiltInRegistries.ENTITY_TYPE.getKey(m.getType()).toString());
				o.addProperty("target", role);
				o.addProperty("target_name", p.getGameProfile().name());
				o.addProperty("dim", Rails.dim(level));
				o.add("pos", Link.pos(m.blockPosition()));
				o.addProperty("tick", tick);
				Link.INSTANCE.send("mob.targeting", o, null);
			}
		}
	}

	/** The play area: home's dimension, within the envelope's explore radius (horizontal) of home's centre. */
	static boolean inPlayArea(ServerPlayer p, Envelope env) {
		if (!Rails.dim(p.level()).equals(env.homeDim)) return false;
		double dx = p.getX() - env.homeCenter[0], dz = p.getZ() - env.homeCenter[2];
		return dx * dx + dz * dz <= (double) env.exploreRadius * env.exploreRadius;
	}

	public static void resetForTest() {
		last.clear();
	}
}
