package eve.body;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.world.phys.Vec3;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashSet;
import java.util.Set;

/**
 * The body's local copy of the operating envelope (`<gamedir>/config/envelope.json`, same file as sense/mind). Used only for
 * `home` (go_home) and to ADD protected entity types; the server helper is the authority. Re-read when the file changes.
 */
final class Envelope {
	record Home(String dim, Vec3 center, double radius) {}

	private static long mtime = -1, checkedMs;
	private static JsonObject root;

	private static Path file() {
		return FabricLoader.getInstance().getConfigDir().resolve("envelope.json");
	}

	private static synchronized JsonObject read() {
		long now = System.currentTimeMillis();
		if (now - checkedMs < 1000) return root; // called per listed entity; one stat a second is plenty
		checkedMs = now;
		try {
			Path f = file();
			if (!Files.exists(f)) {
				root = null;
				mtime = -1;
				return null;
			}
			long m = Files.getLastModifiedTime(f).toMillis();
			if (m != mtime) {
				root = JsonParser.parseString(Files.readString(f)).getAsJsonObject();
				mtime = m;
			}
			return root;
		} catch (Exception e) {
			EveBody.LOG.warn("eve-body: config/envelope.json unreadable: {}", e.toString());
			return null;
		}
	}

	/**
	 * Zones whose `break` is not `free` (home, owner builds): the volumes a creeper blast must be kept away from (CP-29). Empty
	 * when there is no envelope copy. Boxes in block coordinates, inclusive, for the given dimension id.
	 */
	static java.util.List<net.minecraft.world.phys.AABB> keepClear(String dim) {
		java.util.List<net.minecraft.world.phys.AABB> out = new java.util.ArrayList<>();
		try {
			JsonObject r = read();
			if (r == null || !r.has("zones")) return out;
			for (JsonElement z : r.getAsJsonArray("zones")) {
				JsonObject o = z.getAsJsonObject();
				if (o.has("dim") && !o.get("dim").getAsString().equals(dim)) continue;
				if (o.has("break") && o.get("break").getAsString().equals("free")) continue;
				JsonObject mn = o.getAsJsonObject("min"), mx = o.getAsJsonObject("max");
				out.add(new net.minecraft.world.phys.AABB(mn.get("x").getAsDouble(), mn.get("y").getAsDouble(), mn.get("z").getAsDouble(),
						mx.get("x").getAsDouble() + 1, mx.get("y").getAsDouble() + 1, mx.get("z").getAsDouble() + 1));
			}
		} catch (Exception ex) {
			EveBody.LOG.warn("eve-body: envelope zones unreadable: {}", ex.toString());
		}
		return out;
	}

	/** The first envelope zone containing block b in dimension dim, or null (outside every zone, or no envelope copy). */
	static JsonObject zoneAt(String dim, net.minecraft.core.BlockPos b) {
		try {
			JsonObject r = read();
			if (r == null || !r.has("zones")) return null;
			for (JsonElement z : r.getAsJsonArray("zones")) {
				JsonObject o = z.getAsJsonObject(), mn = o.getAsJsonObject("min"), mx = o.getAsJsonObject("max");
				if (o.has("dim") && !o.get("dim").getAsString().equals(dim)) continue;
				if (b.getX() >= mn.get("x").getAsInt() && b.getX() <= mx.get("x").getAsInt() && b.getY() >= mn.get("y").getAsInt()
						&& b.getY() <= mx.get("y").getAsInt() && b.getZ() >= mn.get("z").getAsInt() && b.getZ() <= mx.get("z").getAsInt()) return o;
			}
		} catch (Exception ex) {
			return null;
		}
		return null;
	}

	/** Null when there is no envelope copy or it has no usable home. */
	static Home home() {
		try {
			JsonObject h = read().getAsJsonObject("home");
			JsonObject c = h.getAsJsonObject("center");
			return new Home(h.get("dim").getAsString(), new Vec3(c.get("x").getAsDouble(), c.get("y").getAsDouble(), c.get("z").getAsDouble()),
					h.has("radius") ? h.get("radius").getAsDouble() : 0);
		} catch (Exception e) {
			return null;
		}
	}

	/** protected_entities.extra_types: entity ids protected on top of the built-in rules (players, villagers, golems, pets, named). */
	static Set<String> extraProtected() {
		Set<String> out = new HashSet<>();
		try {
			for (JsonElement t : read().getAsJsonObject("protected_entities").getAsJsonArray("extra_types")) out.add(t.getAsString());
		} catch (Exception ignored) {
		}
		return out;
	}
}
