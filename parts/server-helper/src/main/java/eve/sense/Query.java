package eve.sense;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.fabricmc.fabric.api.tag.convention.v2.ConventionalBlockTags;
import net.minecraft.commands.arguments.blocks.BlockStateParser;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.Identifier;
import net.minecraft.resources.ResourceKey;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.tags.BlockTags;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.Property;
import net.minecraft.world.level.chunk.LevelChunk;

import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Set;

/**
 * CP-14 query.blocks / query.resources: what Eve could know by looking, under the envelope's sensor policy.
 * - Loaded chunks only: a chunk that is not loaded is listed in `unloaded` and never loaded by a query.
 * - No x-ray: a block whose six neighbours are all loaded, opaque full blocks is reported as "hidden", whatever it is.
 *   "Exposed" = at least one face touches a non-opaque block (air, fluid, glass, leaves...), as a player would see it from
 *   a cave or the surface. ponytail: face exposure, not line of sight from Eve; a "seen_only" policy needs view tracking.
 * - query.resources: ore and logs must be exposed; ore honours sensors.ore (none = nothing); crops honour sensors.crops
 *   (zones_only = inside an envelope zone; none = nothing). Bounded: volume <= 32768, radius <= 32, <= 2048 items.
 */
public final class Query {
	static final int MAX_VOLUME = 32_768, MAX_RADIUS = 32, MAX_ITEMS = 2048;
	static final String HIDDEN = "hidden", UNLOADED = "unloaded";

	private Query() { }

	public static JsonObject blocks(MinecraftServer server, JsonObject p) {
		ServerLevel level = level(server, p);
		BlockPos a = pos(p, "min"), b = pos(p, "max");
		int x0 = Math.min(a.getX(), b.getX()), y0 = Math.min(a.getY(), b.getY()), z0 = Math.min(a.getZ(), b.getZ());
		int x1 = Math.max(a.getX(), b.getX()), y1 = Math.max(a.getY(), b.getY()), z1 = Math.max(a.getZ(), b.getZ());
		long volume = (long) (x1 - x0 + 1) * (y1 - y0 + 1) * (z1 - z0 + 1);
		if (volume > MAX_VOLUME) throw new IllegalArgumentException("volume " + volume + " exceeds " + MAX_VOLUME);
		if (y0 < level.getMinY() || y1 > level.getMaxY())
			throw new IllegalArgumentException("y outside the world (" + level.getMinY() + ".." + level.getMaxY() + ")");
		Map<String, Integer> palette = new LinkedHashMap<>();
		Set<Long> unloaded = new LinkedHashSet<>();
		JsonArray data = new JsonArray();
		for (int y = y0; y <= y1; y++)
			for (int z = z0; z <= z1; z++)
				for (int x = x0; x <= x1; x++) {
					BlockState s = peek(level, x, y, z);
					String key;
					if (s == null) {
						key = UNLOADED;
						unloaded.add(((long) (x >> 4) << 32) | ((z >> 4) & 0xffffffffL));
					} else key = exposed(level, x, y, z) ? BlockStateParser.serialize(s) : HIDDEN;
					data.add(palette.computeIfAbsent(key, k -> palette.size()));
				}
		JsonObject r = new JsonObject();
		r.addProperty("dim", level.dimension().identifier().toString());
		r.add("min", Link.pos(new BlockPos(x0, y0, z0)));
		r.add("max", Link.pos(new BlockPos(x1, y1, z1)));
		r.addProperty("order", "x,z,y"); // x fastest, then z, then y
		JsonArray pal = new JsonArray();
		palette.keySet().forEach(pal::add);
		r.add("palette", pal);
		r.add("data", data);
		JsonArray ul = new JsonArray();
		for (long c : unloaded) {
			JsonObject o = new JsonObject();
			o.addProperty("cx", (int) (c >> 32));
			o.addProperty("cz", (int) c);
			ul.add(o);
		}
		r.add("unloaded", ul);
		r.addProperty("policy", "loaded_chunks_only, unexposed=hidden");
		return r;
	}

	public static JsonObject resources(MinecraftServer server, JsonObject p) {
		String kind = p.has("kind") ? p.get("kind").getAsString() : "";
		if (!Set.of("ore", "crops", "logs", "void_edge").contains(kind)) throw new IllegalArgumentException("kind must be ore|crops|logs|void_edge");
		ServerLevel level = level(server, p);
		String dim = level.dimension().identifier().toString();
		BlockPos c = pos(p, "center");
		if (!p.has("radius")) throw new IllegalArgumentException("radius missing");
		int radius = p.get("radius").getAsInt();
		if (radius < 1 || radius > MAX_RADIUS) throw new IllegalArgumentException("radius must be 1.." + MAX_RADIUS);
		Envelope e = EveSense.env();
		String policy = switch (kind) {
			case "ore" -> e.oreSensor;
			case "crops" -> e.cropSensor;
			case "void_edge" -> "geometry"; // the shape of loaded terrain, like exposure: no block identity is revealed
			default -> "exposed_only";
		};
		JsonArray items = new JsonArray();
		int scanned = 0, unloadedChunks = 0;
		boolean truncated = false;
		int ymin = Math.max(level.getMinY(), c.getY() - radius), ymax = Math.min(level.getMaxY(), c.getY() + radius);
		if (!policy.equals("none")) {
			scan:
			for (int cx = (c.getX() - radius) >> 4; cx <= (c.getX() + radius) >> 4; cx++)
				for (int cz = (c.getZ() - radius) >> 4; cz <= (c.getZ() + radius) >> 4; cz++) {
					LevelChunk ch = level.getChunkSource().getChunkNow(cx, cz);
					if (ch == null) { unloadedChunks++; continue; }
					scanned++;
					int xa = Math.max(cx << 4, c.getX() - radius), xb = Math.min((cx << 4) + 15, c.getX() + radius);
					int za = Math.max(cz << 4, c.getZ() - radius), zb = Math.min((cz << 4) + 15, c.getZ() + radius);
					for (int y = ymin; y <= ymax; y++)
						for (int z = za; z <= zb; z++)
							for (int x = xa; x <= xb; x++) {
								BlockPos q = new BlockPos(x, y, z);
								BlockState s = ch.getBlockState(q);
								if (kind.equals("void_edge")) {
									JsonObject v = voidEdge(level, q, s);
									if (v == null) continue;
									if (items.size() >= MAX_ITEMS) { truncated = true; break scan; }
									items.add(v);
									continue;
								}
								boolean want = switch (kind) {
									case "ore" -> s.is(ConventionalBlockTags.ORES) && exposed(level, x, y, z);
									case "logs" -> s.is(BlockTags.LOGS) && exposed(level, x, y, z);
									default -> s.is(BlockTags.CROPS) && !e.zonesAt(dim, x, y, z).isEmpty();
								};
								if (!want) continue;
								if (items.size() >= MAX_ITEMS) { truncated = true; break scan; }
								items.add(item(q, s));
							}
				}
		}
		JsonObject r = new JsonObject();
		r.addProperty("kind", kind);
		r.addProperty("dim", dim);
		r.add("center", Link.pos(c));
		r.addProperty("radius", radius);
		r.addProperty("policy", policy);
		r.add("items", items);
		r.addProperty("scanned_chunks", scanned);
		r.addProperty("unloaded_chunks", unloadedChunks);
		r.addProperty("truncated", truncated);
		return r;
	}

	static final int VOID_DEPTH = 8, DEPTH_SCAN = 64;

	/**
	 * A void edge (CP-53, for body's edge reflex and builder's bridging): a standable block (solid top, two clear cells
	 * above) with a horizontal neighbour Eve could walk off into (both its cells clear) and no solid block within
	 * VOID_DEPTH below that neighbour. Unloaded cells never count as void. Item: {pos, block, state, toward:[dirs],
	 * depth: blocks down to the first solid below the neighbour, or null if none within DEPTH_SCAN}.
	 */
	static JsonObject voidEdge(ServerLevel level, BlockPos q, BlockState s) {
		if (!solid(level, q, s) || !clear(level, q.above()) || !clear(level, q.above(2))) return null;
		JsonArray toward = new JsonArray();
		Integer depth = null;
		for (Direction d : Direction.Plane.HORIZONTAL) {
			BlockPos n = q.relative(d);
			if (!clear(level, n.above()) || !clear(level, n)) continue;
			Integer first = null;
			boolean known = true;
			for (int k = 1; k <= DEPTH_SCAN; k++) {
				BlockPos b = n.below(k);
				BlockState bs = peek(level, b.getX(), b.getY(), b.getZ());
				if (bs == null) { known = false; break; }
				if (solid(level, b, bs)) { first = k; break; }
			}
			if (!known || first != null && first <= VOID_DEPTH) continue;
			toward.add(d.getSerializedName());
			if (first != null && (depth == null || first < depth)) depth = first;
		}
		if (toward.isEmpty()) return null;
		JsonObject o = item(q, s);
		o.add("toward", toward);
		if (depth == null) o.add("depth", com.google.gson.JsonNull.INSTANCE);
		else o.addProperty("depth", depth);
		return o;
	}

	private static boolean solid(ServerLevel level, BlockPos p, BlockState s) {
		return s != null && !s.getCollisionShape(level, p).isEmpty() && s.getFluidState().isEmpty();
	}

	private static boolean clear(ServerLevel level, BlockPos p) {
		BlockState s = peek(level, p.getX(), p.getY(), p.getZ());
		return s != null && s.getCollisionShape(level, p).isEmpty();
	}

	/** The block at x,y,z if its chunk is loaded, else null. Never loads a chunk. */
	static BlockState peek(ServerLevel level, int x, int y, int z) {
		LevelChunk ch = level.getChunkSource().getChunkNow(x >> 4, z >> 4);
		return ch == null ? null : ch.getBlockState(new BlockPos(x, y, z));
	}

	/** At least one face touches a loaded non-opaque block (or the world's top/bottom). Unloaded neighbours count as opaque. */
	static boolean exposed(ServerLevel level, int x, int y, int z) {
		for (Direction d : Direction.values()) {
			int nx = x + d.getStepX(), ny = y + d.getStepY(), nz = z + d.getStepZ();
			if (ny < level.getMinY() || ny > level.getMaxY()) return true;
			BlockState n = peek(level, nx, ny, nz);
			if (n != null && !n.isSolidRender()) return true;
		}
		return false;
	}

	private static JsonObject item(BlockPos q, BlockState s) {
		JsonObject o = new JsonObject();
		o.add("pos", Link.pos(q));
		o.addProperty("block", BuiltInRegistries.BLOCK.getKey(s.getBlock()).toString());
		JsonObject st = new JsonObject();
		for (Property<?> prop : s.getProperties()) st.addProperty(prop.getName(), value(s, prop));
		o.add("state", st);
		return o;
	}

	private static <T extends Comparable<T>> String value(BlockState s, Property<T> prop) {
		return prop.getName(s.getValue(prop));
	}

	private static ServerLevel level(MinecraftServer server, JsonObject p) {
		if (!p.has("dim")) throw new IllegalArgumentException("dim missing");
		ServerLevel level = server.getLevel(ResourceKey.create(Registries.DIMENSION, Identifier.parse(p.get("dim").getAsString())));
		if (level == null) throw new IllegalArgumentException("unknown dim");
		return level;
	}

	private static BlockPos pos(JsonObject p, String k) {
		if (!p.has(k) || !p.get(k).isJsonObject()) throw new IllegalArgumentException(k + " must be {x,y,z}");
		JsonObject o = p.getAsJsonObject(k);
		for (String c : new String[]{"x", "y", "z"})
			if (!o.has(c) || !o.get(c).isJsonPrimitive() || !o.get(c).getAsJsonPrimitive().isNumber())
				throw new IllegalArgumentException(k + "." + c + " must be a number");
		return BlockPos.containing(o.get("x").getAsDouble(), o.get("y").getAsDouble(), o.get("z").getAsDouble());
	}
}
