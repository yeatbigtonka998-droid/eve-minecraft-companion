package eve.sense;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.fabricmc.fabric.api.event.player.PlayerBlockBreakEvents;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.commands.arguments.blocks.BlockStateParser;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.Identifier;
import net.minecraft.resources.ResourceKey;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.tags.BlockTags;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.state.BlockState;

import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Comparator;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.zip.GZIPInputStream;
import java.util.zip.GZIPOutputStream;

/**
 * CP-17 zone snapshots (repair baselines) and diffs.
 * - A snapshot is taken only on a trusted /eve save_zone <zone> (the owner's authenticated command path) for a zone with
 *   snapshot:true. It stores every block state of the zone (loaded chunks only; the rest is "unloaded"), versioned per
 *   zone as <zone>@v<n> in <gamedir>/eve-sense/snapshots/<zone>-v<n>.snap (gzip), with its owner, time and variation
 *   policy. The update procedure is the same command: it writes v<n+1>; older versions stay readable for diffs.
 *   The mind's zone.snapshot.save only echoes a save the owner made in the last 60 s: no model text moves a baseline.
 * - zone.diff compares the zone now with a snapshot. missing = was a block, now air; changed = another block or state;
 *   ignored = permitted variation: crop_cycle (a crop's age, harvest or replanting), operational_state (only properties
 *   such as open/powered/lit differ), owner_edit (a trusted player broke/placed there after the snapshot), unloaded.
 *   Entries carry the actor sense saw change the position since the snapshot ("eve", "player:<name>"), when known.
 * ponytail: player edits are seen through block break/place only; buckets, pistons and explosions stay "damage".
 */
public final class Snapshots {
	static final int MAX_LIST = 2000, REPAIR_MAX = 1000;
	static final long ECHO_MS = 60_000;
	static final Set<String> OPERATIONAL = Set.of("open", "powered", "lit", "triggered", "enabled", "moisture", "distance",
			"snowy", "in_wall", "occupied", "power", "extended", "age", "stage", "has_record", "signal_fire");
	private static final Pattern ID = Pattern.compile("([a-z0-9_.-]{1,64})@v(\\d{1,6})");
	private static final Pattern STATE = Pattern.compile("([^\\[]+)(?:\\[(.*)])?");
	private static final Map<String, Object[]> lastSave = new HashMap<>(); // zone -> {JsonObject info, player, ms}

	private Snapshots() { }

	static void register() {
		PlayerBlockBreakEvents.AFTER.register((level, player, pos, state, be) -> edited(level, pos, player));
	}

	static Path dir() {
		return FabricLoader.getInstance().getGameDir().resolve("eve-sense").resolve("snapshots");
	}

	static JsonObject policy() {
		JsonObject p = new JsonObject();
		JsonArray props = new JsonArray();
		OPERATIONAL.stream().sorted().forEach(props::add);
		p.add("operational_properties", props);
		p.addProperty("crop_cycle", "a crop's age, harvest (crop -> air) and replanting (air -> crop) are normal");
		p.addProperty("owner_edits", "positions a trusted player broke or placed after the snapshot");
		p.addProperty("update", "/eve save_zone <zone> by a trusted player writes the next version");
		return p;
	}

	// ---- save (trusted /eve save_zone only) ----

	/** Server thread. Throws IllegalArgumentException with a message for the owner. */
	public static JsonObject save(MinecraftServer server, String zoneId, String requestedBy) {
		Envelope e = EveSense.env();
		if (!e.trusted.contains(requestedBy)) throw new IllegalArgumentException(requestedBy + " is not a trusted player");
		Envelope.Zone z = e.zones.stream().filter(x -> x.id().equals(zoneId) && x.snapshot()).findFirst()
				.orElseThrow(() -> new IllegalArgumentException("no zone " + zoneId + " with snapshot:true in the envelope"));
		ServerLevel level = level(server, z.dim());
		int[] mn = {z.min()[0], Math.max(z.min()[1], level.getMinY()), z.min()[2]}, mx = {z.max()[0], Math.min(z.max()[1], level.getMaxY()), z.max()[2]};
		IdentityHashMap<BlockState, Integer> idx = new IdentityHashMap<>();
		List<String> palette = new ArrayList<>(List.of(Query.UNLOADED));
		int n = (mx[0] - mn[0] + 1) * (mx[1] - mn[1] + 1) * (mx[2] - mn[2] + 1);
		int[] data = new int[n];
		int i = 0, blocks = 0, unloaded = 0;
		for (int y = mn[1]; y <= mx[1]; y++)
			for (int zz = mn[2]; zz <= mx[2]; zz++)
				for (int x = mn[0]; x <= mx[0]; x++) {
					BlockState s = Query.peek(level, x, y, zz);
					if (s == null) { data[i++] = 0; unloaded++; continue; }
					if (!s.isAir()) blocks++;
					data[i++] = idx.computeIfAbsent(s, k -> { palette.add(BlockStateParser.serialize(k)); return palette.size() - 1; });
				}
		int version = latestVersion(zoneId) + 1;
		JsonObject h = new JsonObject();
		h.addProperty("snapshot_id", zoneId + "@v" + version);
		h.addProperty("zone_id", zoneId);
		h.addProperty("version", version);
		h.addProperty("dim", z.dim());
		h.add("min", Link.pos(new BlockPos(mn[0], mn[1], mn[2])));
		h.add("max", Link.pos(new BlockPos(mx[0], mx[1], mx[2])));
		h.addProperty("requested_by", requestedBy);
		h.addProperty("created_ms", System.currentTimeMillis());
		h.addProperty("block_count", blocks);
		h.addProperty("unloaded_positions", unloaded);
		h.addProperty("world_id", EveSense.worldId());
		h.add("policy", policy());
		try {
			Files.createDirectories(dir());
			try (var out = new DataOutputStream(new GZIPOutputStream(Files.newOutputStream(file(zoneId, version))))) {
				byte[] hb = h.toString().getBytes(StandardCharsets.UTF_8);
				out.writeInt(hb.length);
				out.write(hb);
				out.writeInt(palette.size());
				for (String p : palette) out.writeUTF(p);
				out.writeInt(n);
				for (int d : data) out.writeInt(d);
			}
		} catch (IOException ex) {
			throw new IllegalStateException("snapshot not written: " + ex.getMessage(), ex);
		}
		lastSave.put(zoneId, new Object[]{h, requestedBy, System.currentTimeMillis()});
		EveSense.audit("zone.snapshot.saved", h);
		return h;
	}

	/** zone.snapshot.save from the mind: only the echo of an owner's in-game save within the last 60 s. */
	public static JsonObject echo(JsonObject p) {
		String zoneId = str(p, "zone_id"), by = str(p, "requested_by");
		Object[] s = zoneId == null ? null : lastSave.get(zoneId);
		if (s == null || !s[1].equals(by) || System.currentTimeMillis() - (long) s[2] > ECHO_MS)
			throw new IllegalArgumentException("a snapshot is taken only by a trusted /eve save_zone in game (none for " + zoneId + " by " + by + " in the last 60 s)");
		return (JsonObject) s[0];
	}

	public static int latestVersion(String zoneId) {
		int v = 0;
		try (var s = Files.list(dir())) {
			for (Path f : (Iterable<Path>) s::iterator) {
				Matcher m = Pattern.compile(Pattern.quote(zoneId) + "-v(\\d+)\\.snap").matcher(f.getFileName().toString());
				if (m.matches()) v = Math.max(v, Integer.parseInt(m.group(1)));
			}
		} catch (IOException ignored) {
			// no directory yet: no snapshot
		}
		return v;
	}

	private static Path file(String zoneId, int version) {
		return dir().resolve(zoneId + "-v" + version + ".snap");
	}

	// ---- player edits since a snapshot (for owner_edit and actors) ----

	/** Any player's break (and successful place, from BlockItemMixin) inside a snapshot:true zone. Server thread. */
	public static void edited(Level level, BlockPos pos, Player player) {
		if (level.isClientSide() || player == null) return;
		String dim = Rails.dim(level);
		if (EveSense.env().zonesAt(dim, pos.getX(), pos.getY(), pos.getZ()).stream().noneMatch(Envelope.Zone::snapshot)) return;
		String actor = Rails.isEve(player) ? "eve" : "player:" + player.getGameProfile().name();
		String line = System.currentTimeMillis() + " " + dim + " " + pos.getX() + " " + pos.getY() + " " + pos.getZ() + " " + actor + "\n";
		try {
			Files.createDirectories(dir());
			Files.writeString(dir().resolve("edits.log"), line, StandardCharsets.UTF_8, StandardOpenOption.CREATE, StandardOpenOption.APPEND);
		} catch (IOException ex) {
			EveSense.LOG.warn("snapshot edits log not written: {}", ex.getMessage());
		}
	}

	/** Rule O20: a position a fall rescue put water into (any zone), for diffs: ms dim x y z, in rescues.log. */
	public static void rescued(String dim, BlockPos pos) {
		String line = System.currentTimeMillis() + " " + dim + " " + pos.getX() + " " + pos.getY() + " " + pos.getZ() + "\n";
		try {
			Files.createDirectories(dir());
			Files.writeString(dir().resolve("rescues.log"), line, StandardCharsets.UTF_8, StandardOpenOption.CREATE, StandardOpenOption.APPEND);
		} catch (IOException ex) {
			EveSense.LOG.warn("rescue log not written: {}", ex.getMessage());
		}
	}

	private static Set<String> rescuedSince(String dim, long sinceMs) {
		Set<String> out = new HashSet<>();
		Path f = dir().resolve("rescues.log");
		if (!Files.exists(f)) return out;
		try {
			for (String l : Files.readAllLines(f, StandardCharsets.UTF_8)) {
				String[] t = l.split(" ");
				if (t.length == 5 && t[1].equals(dim) && Long.parseLong(t[0]) >= sinceMs) out.add(t[2] + "," + t[3] + "," + t[4]);
			}
		} catch (IOException | NumberFormatException ex) {
			EveSense.LOG.warn("rescue log unreadable: {}", ex.getMessage());
		}
		return out;
	}

	/** Last actor per "x,y,z" since `sinceMs`. ponytail: reads the whole log per diff; rotate it if it ever grows large. */
	private static Map<String, String> actorsSince(String dim, long sinceMs) {
		Map<String, String> out = new HashMap<>();
		Path f = dir().resolve("edits.log");
		if (!Files.exists(f)) return out;
		try {
			for (String l : Files.readAllLines(f, StandardCharsets.UTF_8)) {
				String[] t = l.split(" ");
				if (t.length == 6 && t[1].equals(dim) && Long.parseLong(t[0]) >= sinceMs) out.put(t[2] + "," + t[3] + "," + t[4], t[5]);
			}
		} catch (IOException | NumberFormatException ex) {
			EveSense.LOG.warn("snapshot edits log unreadable: {}", ex.getMessage());
		}
		return out;
	}

	// ---- diff ----

	/** One differing (or unreadable) position of a zone against a snapshot; s == null = chunk unloaded. */
	private interface Cell {
		void at(int x, int y, int z, String expected, BlockState s, String actual, String actor, String reason);
	}

	private record Loaded(JsonObject h, List<String> palette, int[] data) {}

	private static Loaded read(Path f, String sid) {
		try (var in = new DataInputStream(new GZIPInputStream(Files.newInputStream(f)))) {
			byte[] hb = new byte[in.readInt()];
			in.readFully(hb);
			JsonObject h = JsonParser.parseString(new String(hb, StandardCharsets.UTF_8)).getAsJsonObject();
			List<String> palette = new ArrayList<>();
			int np = in.readInt();
			for (int i = 0; i < np; i++) palette.add(in.readUTF());
			int[] data = new int[in.readInt()];
			for (int i = 0; i < data.length; i++) data[i] = in.readInt();
			return new Loaded(h, palette, data);
		} catch (IOException ex) {
			throw new IllegalStateException("snapshot " + sid + " unreadable: " + ex.getMessage(), ex);
		}
	}

	private static final Map<String, Loaded> latest = new HashMap<>(); // zone -> its latest snapshot (H-trees lookups)

	/** H-trees: the block id the zone's latest snapshot recorded at x,y,z; null when it has no snapshot or the cell is
	 * outside it or was unloaded then. ponytail: keeps each zone's latest snapshot in memory (the 45L home: ~6 MB). */
	static String recordedBlock(String zoneId, int x, int y, int z) {
		int v = latestVersion(zoneId);
		if (v == 0) return null;
		Loaded l = latest.get(zoneId);
		if (l == null || l.h().get("version").getAsInt() != v) latest.put(zoneId, l = read(file(zoneId, v), zoneId + "@v" + v));
		JsonObject mn = l.h().getAsJsonObject("min"), mx = l.h().getAsJsonObject("max");
		int x0 = mn.get("x").getAsInt(), y0 = mn.get("y").getAsInt(), z0 = mn.get("z").getAsInt();
		int x1 = mx.get("x").getAsInt(), y1 = mx.get("y").getAsInt(), z1 = mx.get("z").getAsInt();
		if (x < x0 || x > x1 || y < y0 || y > y1 || z < z0 || z > z1) return null;
		String s = l.palette().get(l.data()[((y - y0) * (z1 - z0 + 1) + (z - z0)) * (x1 - x0 + 1) + (x - x0)]);
		return s.equals(Query.UNLOADED) ? null : split(s)[0];
	}

	/** Reads the snapshot named by {zone_id, snapshot_id} and visits every position that differs; returns its header. */
	private static JsonObject scan(MinecraftServer server, JsonObject p, Cell cell) {
		String zoneId = str(p, "zone_id"), sid = str(p, "snapshot_id");
		if (zoneId == null || sid == null) throw new IllegalArgumentException("zone_id and snapshot_id are required");
		Matcher m = ID.matcher(sid);
		if (!m.matches()) throw new IllegalArgumentException("snapshot_id must look like <zone>@v<n>");
		if (!m.group(1).equals(zoneId)) throw new IllegalArgumentException("snapshot " + sid + " is not of zone " + zoneId);
		Path f = file(zoneId, Integer.parseInt(m.group(2)));
		if (!Files.exists(f)) throw new IllegalArgumentException("no snapshot " + sid);
		Loaded l = read(f, sid);
		JsonObject h = l.h();
		List<String> palette = l.palette();
		int[] data = l.data();
		ServerLevel level = level(server, h.get("dim").getAsString());
		JsonObject mn = h.getAsJsonObject("min"), mx = h.getAsJsonObject("max");
		int x0 = mn.get("x").getAsInt(), y0 = mn.get("y").getAsInt(), z0 = mn.get("z").getAsInt();
		int x1 = mx.get("x").getAsInt(), y1 = mx.get("y").getAsInt(), z1 = mx.get("z").getAsInt();
		Map<String, String> actors = actorsSince(h.get("dim").getAsString(), h.get("created_ms").getAsLong());
		Set<String> rescues = rescuedSince(h.get("dim").getAsString(), h.get("created_ms").getAsLong());
		Set<String> trusted = Set.copyOf(EveSense.env().trusted);
		IdentityHashMap<BlockState, String> names = new IdentityHashMap<>();
		// optional box {min,max}: only that part of the zone (a repair site; other work in the zone is left alone)
		int bx0 = x0, by0 = y0, bz0 = z0, bx1 = x1, by1 = y1, bz1 = z1;
		if (p.has("box")) {
			try {
				JsonObject bm = p.getAsJsonObject("box").getAsJsonObject("min"), bM = p.getAsJsonObject("box").getAsJsonObject("max");
				bx0 = Math.max(x0, bm.get("x").getAsBigDecimal().intValueExact()); bx1 = Math.min(x1, bM.get("x").getAsBigDecimal().intValueExact());
				by0 = Math.max(y0, bm.get("y").getAsBigDecimal().intValueExact()); by1 = Math.min(y1, bM.get("y").getAsBigDecimal().intValueExact());
				bz0 = Math.max(z0, bm.get("z").getAsBigDecimal().intValueExact()); bz1 = Math.min(z1, bM.get("z").getAsBigDecimal().intValueExact());
			} catch (RuntimeException ex) {
				throw new IllegalArgumentException("box must be {min:{x,y,z},max:{x,y,z}} integers");
			}
			if (bx0 > bx1 || by0 > by1 || bz0 > bz1) throw new IllegalArgumentException("box does not overlap the snapshot");
			JsonObject eb = new JsonObject(); // echoed in the reply: a reader can refuse an answer that ignored its box
			eb.add("min", Link.pos(new BlockPos(bx0, by0, bz0)));
			eb.add("max", Link.pos(new BlockPos(bx1, by1, bz1)));
			h.add("box", eb);
		}
		int dx = x1 - x0 + 1, dz = z1 - z0 + 1;
		for (int y = by0; y <= by1; y++)
			for (int z = bz0; z <= bz1; z++)
				for (int x = bx0; x <= bx1; x++) {
					int i = ((y - y0) * dz + (z - z0)) * dx + (x - x0);
					String expected = palette.get(data[i]);
					BlockState s = Query.peek(level, x, y, z);
					if (s == null || expected.equals(Query.UNLOADED)) {
						cell.at(x, y, z, expected, s, null, null, s == null ? "unloaded" : "not_in_snapshot");
						continue;
					}
					String actual = names.computeIfAbsent(s, BlockStateParser::serialize);
					if (actual.equals(expected)) continue;
					String key = x + "," + y + "," + z, actor = actors.get(key);
					String reason = actor != null && actor.startsWith("player:") && trusted.contains(actor.substring(7)) ? "owner_edit" : variation(expected, actual);
					// O20: a rescue's water, placed and scooped back; water still standing there is damage and stays reported
					if (reason == null && rescues.contains(key) && s.getFluidState().isEmpty()) reason = "rescue";
					cell.at(x, y, z, expected, s, actual, actor, reason);
				}
		h.addProperty("zone_id", zoneId);
		h.addProperty("snapshot_id", sid);
		return h;
	}

	public static JsonObject diff(MinecraftServer server, JsonObject p) {
		JsonArray missing = new JsonArray(), changed = new JsonArray(), ignored = new JsonArray();
		Map<String, Integer> counts = new TreeMap<>();
		JsonObject h = scan(server, p, (x, y, z, expected, s, actual, actor, reason) -> {
			if (actual == null) {
				counts.merge("ignored_" + reason, 1, Integer::sum);
				return;
			}
			JsonObject o = new JsonObject();
			o.add("pos", Link.pos(new BlockPos(x, y, z)));
			o.addProperty("expected", expected);
			if (actor != null) o.addProperty("actor", actor);
			JsonArray list;
			if (reason != null) {
				o.addProperty("actual", actual);
				o.addProperty("reason", reason);
				counts.merge("ignored_" + reason, 1, Integer::sum);
				list = ignored;
			} else if (s.isAir()) {
				counts.merge("missing", 1, Integer::sum);
				list = missing;
			} else {
				o.addProperty("actual", actual);
				counts.merge("changed", 1, Integer::sum);
				list = changed;
			}
			if (list.size() < MAX_LIST) list.add(o);
		});
		JsonObject r = header(h);
		r.add("missing", missing);
		r.add("changed", changed);
		r.add("ignored", ignored);
		JsonObject c = new JsonObject();
		counts.forEach(c::addProperty);
		r.add("counts", c);
		r.addProperty("truncated", counts.getOrDefault("missing", 0) > MAX_LIST || counts.getOrDefault("changed", 0) > MAX_LIST
				|| ignored.size() >= MAX_LIST);
		r.add("policy", h.getAsJsonObject("policy"));
		return r;
	}

	private static JsonObject header(JsonObject h) {
		JsonObject r = new JsonObject();
		if (h.has("box")) r.add("box", h.get("box")); // the clamped box the answer covers (absent = the whole zone)
		r.addProperty("zone_id", h.get("zone_id").getAsString());
		r.addProperty("snapshot_id", h.get("snapshot_id").getAsString());
		r.addProperty("version", h.get("version").getAsInt());
		r.addProperty("snapshot_created_ms", h.get("created_ms").getAsLong());
		return r;
	}

	/**
	 * CP-52 repair: exactly the blocks to restore from a snapshot (every snapshot is an owner-approved baseline), as ordered
	 * steps (link v1.3). Damage only: permitted variation (crop_cycle, operational_state, owner_edit, rescue) is listed in
	 * `skipped` {pos, reason, block (snapshot), actual}; unloaded positions are counted. op = remove {block: the block there now} or place {block: the
	 * snapshot's}; a changed block is a remove then a place. Removals come first, top down, then placements bottom up so
	 * supports stand first. Containers (a block entity holding items, now or in the snapshot) are never steps (rule O17:
	 * their contents come from backups): `manual` {pos, kind, note}.
	 * At most max_steps (default and cap 1000) steps per reply; truncated + total say there is more after these are done.
	 */
	public static JsonObject repair(MinecraftServer server, JsonObject p) {
		int max = REPAIR_MAX;
		if (p.has("max_steps")) {
			try {
				max = p.get("max_steps").getAsBigDecimal().intValueExact();
			} catch (RuntimeException ex) {
				throw new IllegalArgumentException("max_steps must be an integer");
			}
			if (max < 1 || max > REPAIR_MAX) throw new IllegalArgumentException("max_steps must be 1.." + REPAIR_MAX);
		}
		List<JsonObject> removes = new ArrayList<>(), places = new ArrayList<>();
		JsonArray manual = new JsonArray(), skipped = new JsonArray();
		Map<String, Integer> counts = new TreeMap<>();
		JsonObject h = scan(server, p, (x, y, z, expected, s, actual, actor, reason) -> {
			JsonObject at = Link.pos(new BlockPos(x, y, z));
			if (reason != null) {
				counts.merge("skipped_" + reason, 1, Integer::sum);
				if (actual != null && skipped.size() < MAX_LIST) {
					JsonObject k = new JsonObject();
					k.add("pos", at);
					k.addProperty("reason", reason);
					k.addProperty("block", expected); // the snapshot's state (crop_cycle: which crop to replant)
					k.addProperty("actual", actual);
					skipped.add(k);
				}
				return;
			}
			if (container(s) || container(expected)) {
				JsonObject m = new JsonObject();
				m.add("pos", at);
				m.addProperty("kind", container(s) ? split(actual)[0] : split(expected)[0]);
				m.addProperty("note", "container: restore from the backup (rule O17); snapshot " + expected + ", now " + actual);
				counts.merge("manual_container", 1, Integer::sum);
				if (manual.size() < MAX_LIST) manual.add(m);
				return;
			}
			if (!s.isAir()) removes.add(step("remove", at, actual, actor));
			if (!air(expected)) places.add(step("place", at, expected, actor));
		});
		counts.put("remove", removes.size());
		counts.put("place", places.size());
		removes.sort(Comparator.comparingInt((JsonObject o) -> -o.getAsJsonObject("pos").get("y").getAsInt()));
		places.sort(Comparator.comparingInt((JsonObject o) -> o.getAsJsonObject("pos").get("y").getAsInt()));
		JsonArray steps = new JsonArray();
		String dim = h.get("dim").getAsString();
		for (JsonObject o : removes)
			if (steps.size() < max) {
				steps.add(o);
				JsonObject q = o.getAsJsonObject("pos"); // H-trees: this removal is a repair, even of a snapshot-recorded log
				PlacedBlocks.repairRemove(dim, new BlockPos(q.get("x").getAsInt(), q.get("y").getAsInt(), q.get("z").getAsInt()), split(o.get("block").getAsString())[0]);
			}
		for (JsonObject o : places) if (steps.size() < max) steps.add(o);
		JsonObject r = header(h);
		r.add("steps", steps);
		r.add("manual", manual);
		r.add("skipped", skipped);
		JsonObject c = new JsonObject();
		counts.forEach(c::addProperty);
		r.add("counts", c);
		r.addProperty("total", removes.size() + places.size());
		r.addProperty("truncated", removes.size() + places.size() > steps.size());
		return r;
	}

	private static JsonObject step(String op, JsonObject pos, String block, String actor) {
		JsonObject o = new JsonObject();
		o.addProperty("op", op);
		o.add("pos", pos);
		o.addProperty("block", block);
		if (actor != null) o.addProperty("actor", actor);
		return o;
	}

	private static boolean air(String state) {
		String id = split(state)[0];
		return id.equals("minecraft:air") || id.equals("minecraft:cave_air") || id.equals("minecraft:void_air");
	}

	private static boolean container(BlockState s) {
		return s.getBlock() instanceof net.minecraft.world.level.block.EntityBlock eb
				&& eb.newBlockEntity(BlockPos.ZERO, s) instanceof net.minecraft.world.Container;
	}

	private static boolean container(String state) {
		var b = BuiltInRegistries.BLOCK.getOptional(Identifier.tryParse(split(state)[0]));
		return b.isPresent() && container(b.get().defaultBlockState());
	}

	/** The permitted-variation reason for expected -> actual, or null when it is a real change. */
	static String variation(String expected, String actual) {
		String[] e = split(expected), a = split(actual);
		if (crop(e[0]) || crop(a[0])) {
			boolean cropOrAir = (crop(e[0]) || e[0].equals("minecraft:air")) && (crop(a[0]) || a[0].equals("minecraft:air"));
			if (cropOrAir && (e[0].equals(a[0]) || e[0].equals("minecraft:air") || a[0].equals("minecraft:air"))) return "crop_cycle";
		}
		if (!e[0].equals(a[0])) return null;
		Map<String, String> pe = props(e[1]), pa = props(a[1]);
		if (!pe.keySet().equals(pa.keySet())) return null;
		for (String k : pe.keySet()) if (!pe.get(k).equals(pa.get(k)) && !OPERATIONAL.contains(k)) return null;
		return "operational_state";
	}

	private static boolean crop(String blockId) {
		var b = BuiltInRegistries.BLOCK.getOptional(Identifier.tryParse(blockId));
		return b.isPresent() && b.get().defaultBlockState().is(BlockTags.CROPS);
	}

	private static String[] split(String state) {
		Matcher m = STATE.matcher(state);
		return m.matches() ? new String[]{m.group(1), m.group(2) == null ? "" : m.group(2)} : new String[]{state, ""};
	}

	private static Map<String, String> props(String s) {
		Map<String, String> out = new LinkedHashMap<>();
		if (!s.isEmpty()) for (String kv : s.split(",")) {
			int eq = kv.indexOf('=');
			if (eq > 0) out.put(kv.substring(0, eq), kv.substring(eq + 1));
		}
		return out;
	}

	private static ServerLevel level(MinecraftServer server, String dim) {
		ServerLevel level = server.getLevel(ResourceKey.create(Registries.DIMENSION, Identifier.parse(dim)));
		if (level == null) throw new IllegalArgumentException("unknown dim " + dim);
		return level;
	}

	private static String str(JsonObject o, String k) {
		return o.has(k) && o.get(k).isJsonPrimitive() ? o.get(k).getAsString() : null;
	}
}
