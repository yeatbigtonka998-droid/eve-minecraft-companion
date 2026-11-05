package eve.sense;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.Identifier;
import net.minecraft.resources.ResourceKey;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.Container;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.ChestBlock;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.ChestType;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * O135: the owner tags a container shared or private in game, through the mind. container.tag {dim, pos, tag, by, line}
 * (mind→sense) is honoured only as the echo of a line a trusted player really said: one sense itself relayed
 * (owner.chat text or /eve raw) in the last 60 s, by that player, with the container within 16 blocks of where they
 * stood. No model text can invent such a line. The tag goes into a per-world overlay (eve-sense/container-tags.json),
 * not envelope.json, so the mind/sense envelope hash stays equal; it outranks the envelope's tag. Also records who
 * placed each container and when, for storage.index (placed_by, placed_ms). Server thread only.
 */
public final class ContainerTags {
	static final long LINE_MS = 60_000;
	static final int RADIUS = 16;
	record Line(String sender, String text, long ms, String dim, BlockPos pos) {}

	/** The line `text` that trusted player `by` said in the last 60 s (sense relayed it), or IllegalArgumentException.
	 * Shared by container.tag and attack.grant (O136): the owner's own words, never model text. */
	static Line echo(String by, String text) {
		if (!EveSense.env().trusted.contains(by)) throw new IllegalArgumentException(by + " is not a trusted player");
		long now = System.currentTimeMillis();
		Line said = null;
		for (Line l : lines) if (l.sender().equals(by) && l.text().equals(text) && now - l.ms() <= LINE_MS) said = l;
		if (said == null) throw new IllegalArgumentException("no line \"" + text + "\" from " + by + " in the last 60 s: it needs the owner's own words");
		return said;
	}
	private static final Deque<Line> lines = new ArrayDeque<>(); // trusted players' recent lines, newest last
	private static final Map<String, String> overlay = new HashMap<>(); // Envelope.key -> shared|private, this world
	private static final Map<String, Object[]> placedBy = new HashMap<>(); // Envelope.key -> {player, ms}; ponytail: this server run only

	private ContainerTags() {}

	/** The owner's tag here (null: none, the envelope decides). Envelope.containerTag consults it first. */
	static String overlay(String dim, int x, int y, int z) {
		return overlay.get(Envelope.key(dim, x, y, z));
	}

	static void start() {
		overlay.clear();
		JsonObject all = readFile();
		if (all.has(EveSense.worldId())) all.getAsJsonObject(EveSense.worldId()).entrySet().forEach(e -> overlay.put(e.getKey(), e.getValue().getAsString()));
		if (!overlay.isEmpty()) EveSense.LOG.info("O135: {} owner container tag(s) for this world", overlay.size());
	}

	/** A trusted player's relayed line (Control: owner.chat text, /eve raw), with where they stood. */
	static void said(ServerPlayer p, String text) {
		lines.addLast(new Line(p.getGameProfile().name(), text, System.currentTimeMillis(), Rails.dim(p.level()), p.blockPosition()));
		while (lines.size() > 64 || !lines.isEmpty() && System.currentTimeMillis() - lines.peekFirst().ms() > LINE_MS) lines.removeFirst();
	}

	/** container.tag (link v1.12). Throws IllegalArgumentException (answered as error bad_payload) when refused. */
	public static JsonObject tag(MinecraftServer server, JsonObject p) {
		String dim = str(p, "dim"), tag = str(p, "tag"), by = str(p, "by"), text = str(p, "line");
		if (dim == null || tag == null || by == null || text == null || !p.has("pos")) throw new IllegalArgumentException("dim, pos, tag, by and line are required");
		if (!tag.equals("shared") && !tag.equals("private")) throw new IllegalArgumentException("tag must be shared or private");
		if (!EveSense.env().trusted.contains(by)) throw new IllegalArgumentException(by + " is not a trusted player");
		BlockPos pos;
		try {
			JsonObject q = p.getAsJsonObject("pos");
			pos = new BlockPos(q.get("x").getAsBigDecimal().intValueExact(), q.get("y").getAsBigDecimal().intValueExact(), q.get("z").getAsBigDecimal().intValueExact());
		} catch (RuntimeException e) {
			throw new IllegalArgumentException("pos must be {x,y,z} integers");
		}
		Line said = echo(by, text);
		if (!said.dim().equals(dim) || said.pos().distSqr(pos) > RADIUS * RADIUS)
			throw new IllegalArgumentException("the container is more than " + RADIUS + " blocks from where " + by + " said it");
		ServerLevel level = server.getLevel(ResourceKey.create(Registries.DIMENSION, Identifier.parse(dim)));
		if (level == null || !level.isLoaded(pos) || !(level.getBlockEntity(pos) instanceof Container))
			throw new IllegalArgumentException("no loaded container at " + pos.toShortString());
		List<BlockPos> parts = new ArrayList<>(List.of(pos));
		BlockState s = level.getBlockState(pos);
		if (s.getBlock() instanceof ChestBlock && s.getValue(ChestBlock.TYPE) != ChestType.SINGLE)
			parts.add(pos.relative(ChestBlock.getConnectedDirection(s))); // a double chest is one container
		JsonArray done = new JsonArray();
		for (BlockPos b : parts) {
			overlay.put(Envelope.key(dim, b.getX(), b.getY(), b.getZ()), tag);
			done.add(Link.pos(b));
			Storage.changed(level, b);
		}
		save();
		JsonObject r = new JsonObject();
		r.addProperty("dim", dim);
		r.add("positions", done);
		r.addProperty("tag", tag);
		r.addProperty("by", by);
		r.addProperty("line", text);
		EveSense.audit("container.tag", r);
		ServerPlayer owner = server.getPlayerList().getPlayerByName(by);
		if (owner != null) owner.sendSystemMessage(Component.literal("Eve: the container at " + pos.getX() + " " + pos.getY() + " " + pos.getZ() + " is now " + tag + " (you said: \"" + text + "\")"));
		return r;
	}

	/** Any player's placement: a new container here starts with no owner tag, and we remember who placed it. */
	public static void placed(Level level, BlockPos pos, Player player) {
		if (level.isClientSide() || player == null || !(level.getBlockEntity(pos) instanceof Container)) return;
		String k = Envelope.key(Rails.dim(level), pos.getX(), pos.getY(), pos.getZ());
		placedBy.put(k, new Object[]{player.getGameProfile().name(), System.currentTimeMillis(), blockId(level, pos)});
		ledgerPlacers.remove(k);
		if (overlay.remove(k) != null) save();
	}

	/** Any player's break: the cell no longer holds that container. */
	public static void broken(Level level, BlockPos pos) {
		if (level.isClientSide()) return;
		String k = Envelope.key(Rails.dim(level), pos.getX(), pos.getY(), pos.getZ());
		placedBy.remove(k);
		ledgerPlacers.remove(k);
		if (overlay.remove(k) != null) save();
	}

	/** storage.index entry fields: placed_by + placed_ms when sense saw the placement; else placed_by alone from Ledger
	 * (its time column is local text, so no placed_ms from it). Ledger answers are cached per cell: an old container's
	 * placer does not change, and a new placement or a break here clears the cache. */
	static void describe(JsonObject entry, ServerLevel level, BlockPos pos, String kind) {
		String key = entry.get("id").getAsString();
		Object[] pb = placedBy.get(key);
		if (pb != null) {
			entry.addProperty("placed_by", (String) pb[0]);
			entry.addProperty("placed_ms", (long) pb[1]);
			return;
		}
		if (kind == null || kind.equals("minecraft:unknown")) return;
		String who = ledgerPlacers.computeIfAbsent(key, k -> {
			String w = PlacedBlocks.ledgerPlacerOrNull(Rails.dim(level), pos, kind);
			return w == null ? "" : w;
		});
		if (!who.isEmpty()) entry.addProperty("placed_by", who);
	}

	private static final Map<String, String> ledgerPlacers = new HashMap<>(); // Envelope.key -> Ledger's placer, "" = none

	/** O350 (Rails.decideContainer): who placed the container at pos, or null = unknown. Sense's record of this server
	 * run while the same block still stands there, else Ledger's latest placement of that block (cached per cell, as
	 * for storage.index). Not loaded (permission.check only): the record alone; enforcement re-decides when loaded.
	 * ponytail: a container replaced without a player (blast, then /setblock of the same block) keeps the old record. */
	static String placer(ServerLevel level, BlockPos pos) {
		String dim = Rails.dim(level), key = Envelope.key(dim, pos.getX(), pos.getY(), pos.getZ());
		Object[] pb = placedBy.get(key);
		if (!level.isLoaded(pos)) return pb == null ? null : (String) pb[0];
		String block = blockId(level, pos);
		if (pb != null && block.equals(pb[2])) return (String) pb[0];
		String who = ledgerPlacers.computeIfAbsent(key, k -> {
			String w = PlacedBlocks.ledgerPlacerOrNull(dim, pos, block);
			return w == null ? "" : w;
		});
		return who.isEmpty() ? null : who;
	}

	private static String blockId(Level level, BlockPos pos) {
		return BuiltInRegistries.BLOCK.getKey(level.getBlockState(pos).getBlock()).toString();
	}

	/** Test hooks. */
	public static void resetForTest() {
		lines.clear();
		overlay.clear();
		placedBy.clear();
		ledgerPlacers.clear();
		save();
	}

	public static void ageLinesForTest(long ms) {
		List<Line> aged = new ArrayList<>();
		for (Line l : lines) aged.add(new Line(l.sender(), l.text(), l.ms() - ms, l.dim(), l.pos()));
		lines.clear();
		lines.addAll(aged);
	}

	private static Path file() {
		return FabricLoader.getInstance().getGameDir().resolve("eve-sense").resolve("container-tags.json");
	}

	private static JsonObject readFile() {
		try {
			return Files.exists(file()) ? JsonParser.parseString(Files.readString(file(), StandardCharsets.UTF_8)).getAsJsonObject() : new JsonObject();
		} catch (IOException | RuntimeException e) {
			throw new IllegalStateException("eve-sense: container-tags.json unreadable: " + e.getMessage(), e);
		}
	}

	private static void save() {
		JsonObject all = readFile(), mine = new JsonObject();
		overlay.forEach(mine::addProperty);
		all.add(EveSense.worldId(), mine);
		try {
			Files.createDirectories(file().getParent());
			Files.writeString(file(), all.toString(), StandardCharsets.UTF_8);
		} catch (IOException e) {
			EveSense.LOG.warn("O135: container-tags.json not written: {}", e.getMessage());
		}
	}

	private static String str(JsonObject p, String k) {
		return p.has(k) && p.get(k).isJsonPrimitive() ? p.get(k).getAsString() : null;
	}
}
