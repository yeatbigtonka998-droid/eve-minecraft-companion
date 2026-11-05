package eve.sense;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerBlockEntityEvents;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.Identifier;
import net.minecraft.resources.ResourceKey;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.Container;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.ChunkPos;
import net.minecraft.world.level.block.entity.BlockEntity;
import net.minecraft.world.level.chunk.LevelChunk;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

/**
 * storage.index (CP-13): containers in loaded chunks within limits.chunk_scan_radius of Eve and of the home centre.
 * Loaded chunks only; never reads or loads an unloaded chunk. Content changes arrive through BlockEntity.setChanged
 * (BlockEntityMixin) and block-entity load/unload; they are flushed once a second as partial updates.
 * A container once indexed stays tracked wherever Eve goes: its changes, its chunk unloading (loaded:false, no items)
 * and its removal are still sent, so the mind never keeps a stale "loaded" entry. ponytail: tracked set capped at
 * KNOWN_CAP; past it new containers are still indexed but leave the radius untracked.
 * Furnaces (furnace, smoker, blast furnace) also carry `furnace:{input, fuel, output, lit, lit_ticks_left, lit_total,
 * cook_progress, cook_total}` (skills CP-36), re-sent once a second while lit so progress stays current. A crafting
 * table has no server-side state: its grid lives in the player's own menu, which the body sees.
 * Workstations without inventory (link v1.5: crafting table, stonecutter, smithing table, loom, cartography table,
 * anvils, grindstone, fletching and enchanting tables) are listed too, with their kind and items [], from a scan of only
 * the chunk sections whose palette holds one; a player placing or breaking one sends a partial.
 */
public final class Storage {
	private static final int DIRTY_CAP = 4096, KNOWN_CAP = 16_384;
	private static final Map<String, String> known = new java.util.HashMap<>(); // id -> kind, sent as entries this server run
	private static final Map<String, Object[]> lit = new LinkedHashMap<>(); // furnaces burning at their last entry
	private static final Map<String, Object[]> dirty = new LinkedHashMap<>(); // id -> {ServerLevel, BlockPos, kind}; server thread only
	private static boolean overflow;

	static final java.util.Set<net.minecraft.world.level.block.Block> WORKSTATIONS = java.util.Set.of(
			net.minecraft.world.level.block.Blocks.CRAFTING_TABLE, net.minecraft.world.level.block.Blocks.STONECUTTER,
			net.minecraft.world.level.block.Blocks.SMITHING_TABLE, net.minecraft.world.level.block.Blocks.LOOM,
			net.minecraft.world.level.block.Blocks.CARTOGRAPHY_TABLE, net.minecraft.world.level.block.Blocks.ANVIL,
			net.minecraft.world.level.block.Blocks.CHIPPED_ANVIL, net.minecraft.world.level.block.Blocks.DAMAGED_ANVIL,
			net.minecraft.world.level.block.Blocks.GRINDSTONE, net.minecraft.world.level.block.Blocks.FLETCHING_TABLE,
			net.minecraft.world.level.block.Blocks.ENCHANTING_TABLE);

	static boolean workstation(net.minecraft.world.level.block.state.BlockState s) {
		return WORKSTATIONS.contains(s.getBlock());
	}

	/** A player placed or broke a block (BlockItemMixin / break event): a workstation there, or one gone, is a partial. */
	public static void blockChanged(net.minecraft.world.level.Level level, BlockPos pos, net.minecraft.world.level.block.state.BlockState before) {
		if (!(level instanceof ServerLevel sl)) return;
		var now = sl.getBlockState(pos);
		if (workstation(now) || before != null && workstation(before))
			mark(sl, pos, BuiltInRegistries.BLOCK.getKey((workstation(now) ? now : before).getBlock()).toString());
	}

	private static JsonObject workEntry(ServerLevel level, BlockPos pos, int tick) {
		JsonObject o = stub(level, pos, true, false, BuiltInRegistries.BLOCK.getKey(level.getBlockState(pos).getBlock()).toString());
		o.add("items", new JsonArray());
		o.addProperty("observed_tick", tick);
		if (known.size() < KNOWN_CAP || known.containsKey(o.get("id").getAsString())) known.put(o.get("id").getAsString(), o.get("kind").getAsString());
		return o;
	}

	static void register() {
		net.fabricmc.fabric.api.event.player.PlayerBlockBreakEvents.AFTER.register((level, player, pos, state, be) -> blockChanged(level, pos, state));
		ServerBlockEntityEvents.BLOCK_ENTITY_LOAD.register((be, level) -> { if (be instanceof Container) mark(level, be.getBlockPos(), kind(be)); });
		ServerBlockEntityEvents.BLOCK_ENTITY_UNLOAD.register((be, level) -> { if (be instanceof Container) mark(level, be.getBlockPos(), kind(be)); });
	}

	/** BlockEntity.setChanged (BlockEntityMixin), server thread. */
	public static void changed(ServerLevel level, BlockPos pos) {
		if (level.getBlockEntity(pos) instanceof Container && level.getBlockEntity(pos) instanceof BlockEntity be) mark(level, pos, kind(be));
	}

	/** The container's block id, taken while its block entity is in hand: an unloaded entry still says what it was. */
	private static String kind(BlockEntity be) {
		return BuiltInRegistries.BLOCK.getKey(be.getBlockState().getBlock()).toString();
	}

	/** Only containers are marked, so a marked position without a container at flush time was removed. Capped. */
	private static void mark(ServerLevel level, BlockPos pos, String kind) {
		if (!Link.INSTANCE.ready() && Link.tap == null) return; // a full index follows hello.ok anyway
		if (dirty.size() >= DIRTY_CAP) { overflow = true; return; }
		dirty.put(Envelope.key(Rails.dim(level), pos.getX(), pos.getY(), pos.getZ()), new Object[]{level, pos.immutable(), kind});
	}

	public static void flush(MinecraftServer server) {
		if (overflow) {
			dirty.clear();
			overflow = false;
			sendFull(server, null, null); // ponytail: overflow falls back to one full rescan, bounded by the scan radius
			return;
		}
		for (Map.Entry<String, Object[]> e : lit.entrySet()) if (!dirty.containsKey(e.getKey())) dirty.put(e.getKey(), e.getValue());
		if (dirty.isEmpty()) return;
		List<Center> centers = centers(server);
		JsonArray out = new JsonArray();
		for (Object[] d : dirty.values()) {
			ServerLevel level = (ServerLevel) d[0];
			BlockPos pos = (BlockPos) d[1];
			String id = Envelope.key(Rails.dim(level), pos.getX(), pos.getY(), pos.getZ());
			if (!known.containsKey(id) && centers.stream().noneMatch(c -> c.covers(level, pos))) continue;
			String kind = (String) d[2];
			if (!level.isLoaded(pos) || !(level.getBlockEntity(pos) instanceof Container)) lit.remove(id);
			if (!level.isLoaded(pos)) out.add(stub(level, pos, false, false, kind));
			else if (level.getBlockEntity(pos) instanceof Container c) out.add(entry(level, pos, c, server.getTickCount()));
			else if (workstation(level.getBlockState(pos))) out.add(workEntry(level, pos, server.getTickCount()));
			else {
				out.add(stub(level, pos, true, true, kind));
				known.remove(id);
			}
		}
		dirty.clear();
		if (!out.isEmpty()) send(out, true, null);
	}

	/** Full index (or the requested ids) for storage.request and after hello.ok. Server thread. */
	public static void sendFull(MinecraftServer server, JsonObject request, String replyTo) {
		java.util.Set<String> ids = null;
		if (request != null && request.has("ids") && request.get("ids").isJsonArray()) {
			ids = new java.util.HashSet<>();
			for (var e : request.getAsJsonArray("ids")) ids.add(e.getAsString());
		}
		Map<String, JsonObject> found = new TreeMap<>();
		for (Center c : centers(server)) {
			for (int cx = c.chunk.x() - c.radius; cx <= c.chunk.x() + c.radius; cx++)
				for (int cz = c.chunk.z() - c.radius; cz <= c.chunk.z() + c.radius; cz++) {
					LevelChunk chunk = c.level.getChunkSource().getChunkNow(cx, cz);
					if (chunk == null) continue; // unloaded: never loaded on our behalf
					for (BlockEntity be : chunk.getBlockEntities().values())
						if (be instanceof Container cont) {
							JsonObject e = entry(c.level, be.getBlockPos(), cont, server.getTickCount());
							found.putIfAbsent(e.get("id").getAsString(), e);
						}
					var sections = chunk.getSections();
					for (int si = 0; si < sections.length; si++) { // ponytail: palette check first; only sections holding one are scanned
						var sec = sections[si];
						if (sec.hasOnlyAir() || !sec.maybeHas(Storage::workstation)) continue;
						int by = c.level.getSectionYFromSectionIndex(si) << 4;
						for (int y = 0; y < 16; y++)
							for (int z = 0; z < 16; z++)
								for (int x = 0; x < 16; x++)
									if (workstation(sec.getBlockState(x, y, z))) {
										BlockPos wp = new BlockPos((cx << 4) + x, by + y, (cz << 4) + z);
										JsonObject e = workEntry(c.level, wp, server.getTickCount());
										found.putIfAbsent(e.get("id").getAsString(), e);
									}
					}
				}
		}
		for (String id : new ArrayList<>(known.keySet())) if (!found.containsKey(id)) { // indexed earlier, now outside the radius: still tell the truth
			var m = Envelope.TAG_KEY.matcher(id);
			ServerLevel level = m.matches() ? server.getLevel(ResourceKey.create(Registries.DIMENSION, Identifier.parse(m.group(1)))) : null;
			if (level == null) continue;
			BlockPos pos = new BlockPos(Integer.parseInt(m.group(2)), Integer.parseInt(m.group(3)), Integer.parseInt(m.group(4)));
			if (!level.isLoaded(pos)) found.put(id, stub(level, pos, false, false, known.get(id)));
			else if (level.getBlockEntity(pos) instanceof Container cont) found.put(id, entry(level, pos, cont, server.getTickCount()));
			else if (workstation(level.getBlockState(pos))) found.put(id, workEntry(level, pos, server.getTickCount()));
			else found.put(id, stub(level, pos, true, true, null));
		}
		known.keySet().removeIf(id -> found.get(id) != null && found.get(id).has("removed"));
		JsonArray out = new JsonArray();
		for (var e : found.entrySet()) if (ids == null || ids.contains(e.getKey())) out.add(e.getValue());
		if (ids != null) for (String id : ids) if (!found.containsKey(id)) { // never promise what we cannot see
			JsonObject u = unknown(id);
			if (u != null) out.add(u);
		}
		send(out, ids != null, replyTo);
	}

	/** Splits under the frame cap (link v1.1): a full index over several frames goes as partial frames, complete:true on the last. */
	private static void send(JsonArray containers, boolean partial, String replyTo) {
		int budget = Math.min(Link.MAX_FRAME, EveSense.env().payloadMax) - 4096;
		JsonArray batch = new JsonArray();
		int size = 0;
		List<JsonArray> batches = new ArrayList<>();
		for (var c : containers) {
			int n = c.toString().length();
			if (size + n > budget && !batch.isEmpty()) { batches.add(batch); batch = new JsonArray(); size = 0; }
			batch.add(c);
			size += n + 1;
		}
		batches.add(batch);
		for (int i = 0; i < batches.size(); i++) {
			JsonObject p = new JsonObject();
			p.add("containers", batches.get(i));
			p.addProperty("partial", partial || batches.size() > 1);
			if (!partial && batches.size() > 1 && i == batches.size() - 1) p.addProperty("complete", true);
			Link.INSTANCE.send("storage.index", p, replyTo);
		}
	}

	private static JsonObject entry(ServerLevel level, BlockPos pos, Container c, int tick) {
		JsonObject o = stub(level, pos, true, false, BuiltInRegistries.BLOCK.getKey(level.getBlockState(pos).getBlock()).toString());
		Map<String, Integer> counts = new TreeMap<>();
		for (int i = 0; i < c.getContainerSize(); i++) {
			ItemStack s = c.getItem(i);
			if (!s.isEmpty()) counts.merge(BuiltInRegistries.ITEM.getKey(s.getItem()).toString(), s.getCount(), Integer::sum);
		}
		JsonArray items = new JsonArray();
		counts.forEach((k, v) -> {
			JsonObject it = new JsonObject();
			it.addProperty("item", k);
			it.addProperty("count", v);
			items.add(it);
		});
		o.add("items", items);
		o.addProperty("observed_tick", tick);
		if (c instanceof net.minecraft.world.level.block.entity.AbstractFurnaceBlockEntity f) {
			net.minecraft.world.inventory.ContainerData d = ((eve.sense.mixin.FurnaceAccessor) f).eve$data();
			JsonObject fu = new JsonObject();
			String[] slots = {"input", "fuel", "output"};
			for (int i = 0; i < 3; i++) {
				ItemStack s = c.getItem(i);
				if (s.isEmpty()) {
					fu.add(slots[i], com.google.gson.JsonNull.INSTANCE);
					continue;
				}
				JsonObject it = new JsonObject();
				it.addProperty("item", BuiltInRegistries.ITEM.getKey(s.getItem()).toString());
				it.addProperty("count", s.getCount());
				fu.add(slots[i], it);
			}
			boolean burning = d.get(net.minecraft.world.level.block.entity.AbstractFurnaceBlockEntity.DATA_LIT_TIME) > 0;
			fu.addProperty("lit", burning);
			fu.addProperty("burn_ticks", d.get(net.minecraft.world.level.block.entity.AbstractFurnaceBlockEntity.DATA_LIT_TIME)); // v1.4
			fu.addProperty("lit_ticks_left", d.get(net.minecraft.world.level.block.entity.AbstractFurnaceBlockEntity.DATA_LIT_TIME));
			fu.addProperty("lit_total", d.get(net.minecraft.world.level.block.entity.AbstractFurnaceBlockEntity.DATA_LIT_DURATION));
			fu.addProperty("cook_progress", d.get(net.minecraft.world.level.block.entity.AbstractFurnaceBlockEntity.DATA_COOKING_PROGRESS));
			fu.addProperty("cook_total", d.get(net.minecraft.world.level.block.entity.AbstractFurnaceBlockEntity.DATA_COOKING_TOTAL_TIME));
			o.add("furnace", fu);
			String id = o.get("id").getAsString();
			if (burning) lit.put(id, new Object[]{level, pos.immutable(), o.get("kind").getAsString()});
			else lit.remove(id);
		}
		if (known.size() < KNOWN_CAP || known.containsKey(o.get("id").getAsString())) known.put(o.get("id").getAsString(), o.get("kind").getAsString());
		return o;
	}

	/** Every non-removed entry carries dim, pos, kind, tag, loaded and items (link v1.1 schema, enforced by the mind). */
	private static JsonObject stub(ServerLevel level, BlockPos pos, boolean loaded, boolean removed, String kind) {
		String dim = Rails.dim(level);
		JsonObject o = new JsonObject();
		o.addProperty("id", Envelope.key(dim, pos.getX(), pos.getY(), pos.getZ()));
		o.addProperty("dim", dim);
		o.add("pos", Link.pos(pos));
		o.addProperty("kind", kind == null ? "minecraft:unknown" : kind);
		o.addProperty("tag", EveSense.env().containerTag(dim, pos.getX(), pos.getY(), pos.getZ()));
		o.addProperty("loaded", loaded);
		if (removed) o.addProperty("removed", true); // link v1.1: removal marker for partial updates
		else ContainerTags.describe(o, level, pos, kind); // O135 (link v1.12): placed_by, placed_ms
		if (!loaded || removed) o.add("items", new JsonArray());
		return o;
	}

	/** A requested id we cannot see: position from the id, kind unknown, nothing promised. Null for an id that is not dim:x,y,z. */
	private static JsonObject unknown(String id) {
		var m = Envelope.TAG_KEY.matcher(id);
		if (!m.matches()) {
			EveSense.LOG.warn("storage.request: ignoring malformed container id {}", id.length() > 80 ? id.substring(0, 80) : id);
			return null;
		}
		JsonObject o = new JsonObject();
		o.addProperty("id", id);
		o.addProperty("dim", m.group(1));
		o.add("pos", Link.pos(new BlockPos(Integer.parseInt(m.group(2)), Integer.parseInt(m.group(3)), Integer.parseInt(m.group(4)))));
		o.addProperty("kind", "minecraft:unknown");
		o.addProperty("tag", "unknown");
		o.addProperty("loaded", false);
		o.add("items", new JsonArray());
		return o;
	}

	record Center(ServerLevel level, ChunkPos chunk, int radius) {
		boolean covers(ServerLevel l, BlockPos p) {
			return l == level && Math.abs((p.getX() >> 4) - chunk.x()) <= radius && Math.abs((p.getZ() >> 4) - chunk.z()) <= radius;
		}
	}

	/** Eve (when online) and the envelope's home centre, so the village chests stay indexed while she is away. */
	static List<Center> centers(MinecraftServer server) {
		Envelope e = EveSense.env();
		List<Center> out = new ArrayList<>();
		ServerPlayer eve = EveSense.findEve(server);
		if (eve != null) out.add(new Center(eve.level(), eve.chunkPosition(), e.chunkScanRadius));
		ServerLevel home = server.getLevel(ResourceKey.create(Registries.DIMENSION, Identifier.parse(e.homeDim)));
		if (home != null) out.add(new Center(home, ChunkPos.containing(new BlockPos(e.homeCenter[0], e.homeCenter[1], e.homeCenter[2])), e.chunkScanRadius));
		return out;
	}
}
