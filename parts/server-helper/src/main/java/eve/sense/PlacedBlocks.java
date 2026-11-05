package eve.sense;

import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.tags.BlockTags;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.LeavesBlock;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.storage.LevelResource;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Properties;
import java.util.Set;

/**
 * H-trees (Hunter's rule): Eve gathers wood only from real trees, never from builds or decoration made of logs.
 * Before any Eve break of a block in #logs or #leaves, {@link #why} names the reason it is not a real tree, or null:
 * - persistent_leaves: vanilla sets persistent=true on leaves a player placed (natural leaves are false);
 * - own: sense's own record of logs any player placed (BlockItemMixin), which covers Ledger's write queue (10 ticks);
 * - snapshot: a snapshot:true zone's latest snapshot (an owner-approved baseline) recorded this same block at this cell
 *   (a tree that grew there since stays choppable);
 * - structure: inside a piece of a generated structure (village houses, huts): worldgen has no placement record;
 * - ledger: Ledger's history says a player placed (or changed into) the block standing there now;
 * - ledger_unreadable: Ledger is installed but its DB cannot be read (fail closed).
 * Natural generation has no placement record, so a log with none of these is a real tree. Two exceptions (kickoff):
 * a remove step of sense's own zone.repair (repairRemove), and, in Rails.decideBreak, a block Eve herself placed
 * during a live break grant's task (builder's FIX pass). Server thread only.
 */
public final class PlacedBlocks {
	/** The latest block action at a cell (rolled-back actions do not count); Ledger 1.3.25's index actions_by_location. */
	private static final String SQL = "select ai.action_identifier, o.identifier, a.player_id, p.player_name from actions a"
			+ " join worlds w on w.id = a.world_id join ActionIdentifiers ai on ai.id = a.action_id"
			+ " join ObjectIdentifiers o on o.id = a.object_id left join players p on p.id = a.player_id"
			+ " where a.x = ? and a.y = ? and a.z = ? and w.identifier = ? and a.rolled_back = 0"
			+ " and ai.action_identifier in ('block-place', 'block-break', 'block-change')"
			+ " order by a.id desc limit 1";
	private static Path ledgerDb; // Ledger's <world>/ledger.sqlite (its default location)
	private static boolean ledgerLoaded; // Ledger installed: its history is required, an unreadable DB denies
	private static Connection conn;
	private static final Set<String> own = new HashSet<>(); // "<world_id> <dim> x y z" of player-placed logs
	static final long REPAIR_TTL_MS = 30 * 60_000;
	private static final Map<String, Object[]> repairRemoves = new HashMap<>(); // key -> {block id, expires ms}
	private static final Map<String, Object[]> evePlaced = new HashMap<>(); // key -> {block id, seq}: Eve's latest log/leaves placement
	private static long seq; // orders Eve's placements against grants (several can fall in one tick)

	static long next() {
		return ++seq;
	}

	/** Eve's own placement is the latest at this cell, of the block standing there now, and came after `start` (a grant's next()). */
	static boolean evePlacedSince(ServerLevel level, BlockPos pos, BlockState s, long start) {
		Object[] e = evePlaced.get(key(Rails.dim(level), pos));
		return e != null && BuiltInRegistries.BLOCK.getKey(s.getBlock()).toString().equals(e[0]) && (long) e[1] > start;
	}

	private PlacedBlocks() {}

	static void start(MinecraftServer server) {
		ledgerLoaded = FabricLoader.getInstance().isModLoaded("ledger");
		ledgerDb = server.getWorldPath(LevelResource.ROOT).resolve("ledger.sqlite").toAbsolutePath().normalize();
		loadOwn();
		if (ledgerLoaded) EveSense.LOG.info("H-trees: Ledger installed, history from {} (exists: {})", ledgerDb, Files.exists(ledgerDb));
		else EveSense.LOG.info("H-trees: no Ledger on this server; placement history from sense's own record only");
	}

	static void stop() {
		close();
	}

	/** Test hook: point at a (fake) Ledger DB, or none; clear sense's own record. */
	public static void ledgerForTest(Path db, boolean loaded) {
		close();
		ledgerDb = db;
		ledgerLoaded = loaded;
		own.clear();
		repairRemoves.clear();
		evePlaced.clear();
		try {
			Files.deleteIfExists(ownFile());
		} catch (IOException e) {
			throw new IllegalStateException(e);
		}
	}

	/** Test hook: re-read the persisted record, as at a restart. */
	public static void reloadOwnForTest() {
		loadOwn();
	}

	/** Why this block is not a real tree's, or null (a real tree, or not a log or leaves). */
	public static String why(ServerLevel level, BlockPos pos, BlockState s) {
		boolean leaves = s.is(BlockTags.LEAVES);
		if (!leaves && !s.is(BlockTags.LOGS)) return null;
		String dim = Rails.dim(level), block = BuiltInRegistries.BLOCK.getKey(s.getBlock()).toString(), k = key(dim, pos);
		Object[] rr = repairRemoves.get(k);
		if (rr != null && block.equals(rr[0]) && System.currentTimeMillis() <= (long) rr[1]) return null; // a repair, not gathering
		if (leaves && s.hasProperty(LeavesBlock.PERSISTENT) && s.getValue(LeavesBlock.PERSISTENT)) return "persistent_leaves";
		if (own.contains(k)) return "own";
		try {
			for (Envelope.Zone z : EveSense.env().zonesAt(dim, pos.getX(), pos.getY(), pos.getZ()))
				if (z.snapshot() && block.equals(Snapshots.recordedBlock(z.id(), pos.getX(), pos.getY(), pos.getZ()))) return "snapshot";
		} catch (RuntimeException e) {
			EveSense.LOG.warn("H-trees: a snapshot is unreadable ({}): denied", e.getMessage());
			return "snapshot_unreadable";
		}
		if (level.structureManager().getStructureWithPieceAt(pos, h -> true).isValid()) return "structure";
		if (leaves || !ledgerLoaded) return null; // placed leaves are persistent: no history query for leaves
		try {
			return ledgerPlacer(dim, pos, block) != null ? "ledger" : null;
		} catch (SQLException | RuntimeException e) {
			close();
			EveSense.LOG.warn("H-trees: Ledger DB {} unreadable ({}): Eve's log breaks are denied until it reads", ledgerDb, e.getMessage());
			return "ledger_unreadable";
		}
	}

	/** Any player's successful placement (BlockItemMixin). */
	public static void placed(Level level, BlockPos pos, Player player) {
		if (level.isClientSide() || player == null) return;
		BlockState s = level.getBlockState(pos);
		String k = key(Rails.dim(level), pos);
		if (Rails.isEve(player) && (s.is(BlockTags.LOGS) || s.is(BlockTags.LEAVES))) evePlaced.put(k, new Object[]{BuiltInRegistries.BLOCK.getKey(s.getBlock()).toString(), next()});
		else evePlaced.remove(k); // someone else's placement is the latest now
		if (s.is(BlockTags.LOGS) && own.add(k)) append("+ " + k);
	}

	/** Any player's break: the cell no longer holds the placed log. ponytail: fire or explosions leave a stale entry,
	 * which only over-protects that one cell (a tree growing exactly there stays denied). */
	public static void broken(Level level, BlockPos pos) {
		if (level.isClientSide()) return;
		String k = key(Rails.dim(level), pos);
		repairRemoves.remove(k); // a repair step is used once
		evePlaced.remove(k);
		if (own.remove(k)) append("- " + k);
	}

	/** zone.repair issued a remove step here (sense's own diff against an owner-approved snapshot; never from chat or
	 * model text): Eve's break of exactly that block at that cell is a repair, so H-trees lets it through for 30 min. */
	static void repairRemove(String dim, BlockPos pos, String block) {
		repairRemoves.put(key(dim, pos), new Object[]{block, System.currentTimeMillis() + REPAIR_TTL_MS});
	}

	/** The player who placed (or changed into) the block standing here, per Ledger's latest block action; null: no such record. */
	private static String ledgerPlacer(String dim, BlockPos pos, String block) throws SQLException {
		if (conn == null) conn = open();
		try (PreparedStatement q = conn.prepareStatement(SQL)) {
			q.setInt(1, pos.getX());
			q.setInt(2, pos.getY());
			q.setInt(3, pos.getZ());
			q.setString(4, dim);
			try (ResultSet r = q.executeQuery()) {
				if (!r.next()) return null; // no record: natural generation
				boolean placed = r.getObject(3) != null && !"block-break".equals(r.getString(1)) && block.equals(r.getString(2));
				return !placed ? null : r.getString(4) == null ? "?" : r.getString(4);
			}
		}
	}

	/** O135 (storage.index placed_by): Ledger's placer of this block, or null when Ledger is absent, unreadable or silent.
	 * Information only, never a permission, so an unreadable DB answers null here instead of failing closed. */
	static String ledgerPlacerOrNull(String dim, BlockPos pos, String block) {
		if (!ledgerLoaded) return null;
		try {
			return ledgerPlacer(dim, pos, block);
		} catch (SQLException | RuntimeException e) {
			close();
			return null;
		}
	}

	/** Read-only (open_mode 1 = SQLITE_OPEN_READONLY, no create): a missing DB is an error, never a new empty file.
	 * The driver is the sqlite-jdbc Ledger bundles; Ledger keeps the DB in WAL mode, so this reader never waits on it. */
	private static Connection open() throws SQLException {
		if (ledgerDb == null || !Files.isRegularFile(ledgerDb)) throw new SQLException("no Ledger DB at " + ledgerDb);
		try {
			Class.forName("org.sqlite.JDBC");
		} catch (ClassNotFoundException e) {
			throw new SQLException("no sqlite-jdbc driver on the classpath", e);
		}
		Properties p = new Properties();
		p.setProperty("open_mode", "1");
		p.setProperty("busy_timeout", "200");
		return DriverManager.getConnection("jdbc:sqlite:" + ledgerDb, p);
	}

	private static void close() {
		if (conn == null) return;
		try {
			conn.close();
		} catch (SQLException ignored) {
			// closing a broken connection
		}
		conn = null;
	}

	private static String key(String dim, BlockPos pos) {
		return EveSense.worldId() + " " + dim + " " + pos.getX() + " " + pos.getY() + " " + pos.getZ();
	}

	private static Path ownFile() {
		return FabricLoader.getInstance().getGameDir().resolve("eve-sense").resolve("placed-logs.log");
	}

	/** ponytail: replays the whole append-only log at start; compact it if it ever grows large. */
	private static void loadOwn() {
		own.clear();
		Path f = ownFile();
		if (!Files.exists(f)) return;
		try {
			for (String l : Files.readAllLines(f, StandardCharsets.UTF_8)) {
				if (l.startsWith("+ ")) own.add(l.substring(2));
				else if (l.startsWith("- ")) own.remove(l.substring(2));
			}
		} catch (IOException e) {
			throw new IllegalStateException("eve-sense: placed-logs record unreadable: " + e.getMessage(), e);
		}
	}

	private static void append(String line) {
		try {
			Files.createDirectories(ownFile().getParent());
			Files.writeString(ownFile(), line + "\n", StandardCharsets.UTF_8, StandardOpenOption.CREATE, StandardOpenOption.APPEND);
		} catch (IOException e) {
			EveSense.LOG.warn("H-trees: placed-logs record not written: {}", e.getMessage());
		}
	}
}
