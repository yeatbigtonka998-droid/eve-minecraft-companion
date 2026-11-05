package eve.sense;

import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.phys.Vec3;

import java.util.List;

/**
 * O295 (link v1.17): ground truth for watch and the creeper work, which otherwise infer a blast from a creeper leaving her
 * list. One world.explosion frame and one audit line per explosion in the play area: home's dimension, within the envelope's
 * explore radius (horizontal) of home's centre, as for mob.targeting. Data only, never a permission. Server thread only.
 * ponytail: no rate limit, "every explosion" as ordered; a TNT chain sends one frame per blast.
 */
public final class Explosions {
	private static List<ServerPlayer> testPlayers;

	private Explosions() {}

	/** ServerExplosionMixin, after the blast; broken = the blocks it really removed. */
	public static void report(ServerLevel level, Vec3 c, Entity source, float radius, int broken) {
		Envelope env = EveSense.env();
		double home = Math.hypot(c.x - env.homeCenter[0], c.z - env.homeCenter[2]);
		if (!Rails.dim(level).equals(env.homeDim) || home > env.exploreRadius) return;
		JsonObject near = new JsonObject();
		near.addProperty("home_m", tenth(home));
		double owner = -1;
		for (ServerPlayer p : testPlayers != null ? testPlayers : level.players()) {
			double d = p.position().distanceTo(c);
			if (Rails.isEve(p)) near.addProperty("eve_m", tenth(d));
			else if (Control.isTrusted(p) && (owner < 0 || d < owner)) owner = d;
		}
		if (owner >= 0) near.addProperty("owner_m", tenth(owner));
		JsonObject o = new JsonObject();
		o.addProperty("dim", Rails.dim(level));
		o.add("pos", Link.pos(BlockPos.containing(c)));
		o.addProperty("ts", System.currentTimeMillis());
		o.addProperty("tick", level.getServer().getTickCount());
		o.addProperty("source_type", source == null ? "unknown" : BuiltInRegistries.ENTITY_TYPE.getKey(source.getType()).toString());
		if (source != null) o.addProperty("source_uuid", source.getUUID().toString());
		o.addProperty("radius", radius);
		o.addProperty("blocks_broken", broken);
		o.add("near", near);
		EveSense.audit("world.explosion", o);
		Link.INSTANCE.send("world.explosion", o, null);
	}

	private static double tenth(double d) {
		return Math.round(d * 10) / 10.0;
	}

	/** Test hook: FakePlayers are not in the level's player list, so a test names who is near (null = the level's list). */
	public static void playersForTest(List<ServerPlayer> players) {
		testPlayers = players;
	}
}
