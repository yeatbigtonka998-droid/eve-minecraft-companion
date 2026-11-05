package eve.sense;

import com.google.gson.JsonObject;
import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

public class EveSense implements ModInitializer {
	public static final Logger LOG = LoggerFactory.getLogger("eve-sense");
	public static final String MOD_VERSION = FabricLoader.getInstance().getModContainer("eve-sense")
			.map(m -> m.getMetadata().getVersion().getFriendlyString()).orElse("dev");

	private static volatile Envelope env;
	private static volatile boolean stopped;
	private static volatile boolean worldMismatch = true; // fail closed until the server has told us which world this is
	private static volatile String worldId, seedHash, levelName;
	static volatile MinecraftServer server;

	@Override
	public void onInitialize() {
		Path file = envelopePath();
		try {
			env = Envelope.load(file);
		} catch (IOException | IllegalArgumentException e) {
			// Refuse to run: no rails config means no rails.
			throw new IllegalStateException("eve-sense: refusing to start, invalid or missing " + file + ": " + e.getMessage(), e);
		}
		LOG.info("envelope loaded: {} version={} sha256={}", file, env.version, env.hash);
		Path guard = FabricLoader.getInstance().getConfigDir().resolve("eve-sense.json");
		try {
			RconGuard.load(guard); // I28a
		} catch (IOException | IllegalArgumentException e) {
			throw new IllegalStateException("eve-sense: refusing to start, invalid " + guard + ": " + e.getMessage(), e);
		}
		stopped = Files.exists(stoppedFlag());
		if (stopped) LOG.warn("STOPPED flag present at startup; Eve stays stopped until a trusted /eve resume");

		Rails.register();
		Control.register();
		MobTargeting.register(); // H-autofight (O166)
		OwnerActivity.register(); // S97 initiative mode
		Storage.register();
		Snapshots.register();

		ServerLifecycleEvents.SERVER_STARTED.register(s -> {
			server = s;
			levelName = s.getWorldData().getLevelName();
			seedHash = Envelope.sha256(Long.toString(s.overworld().getSeed()).getBytes(StandardCharsets.UTF_8));
			worldId = levelName + ":" + seedHash.substring(0, 12);
			checkWorld();
			PlacedBlocks.start(s); // H-trees (after worldId: its record is per world)
			ContainerTags.start(); // O135 owner tags, per world
			if (!s.usesAuthentication()) LOG.warn("online-mode is off: player names can be spoofed, so name-based rails and trusted commands are not trustworthy on this server");
			Link.INSTANCE.start(s);
		});
		ServerLifecycleEvents.SERVER_STOPPING.register(s -> {
			Link.INSTANCE.stop();
			PlacedBlocks.stop();
		});
	}

	static Path envelopePath() {
		String override = System.getProperty("eve.envelope");
		return override != null ? Path.of(override) : FabricLoader.getInstance().getConfigDir().resolve("envelope.json");
	}

	static Path stoppedFlag() {
		return FabricLoader.getInstance().getGameDir().resolve("eve-sense").resolve("STOPPED");
	}

	public static Envelope env() { return env; }

	public static String worldId() { return worldId; }

	static String seedHash() { return seedHash; }

	static String levelName() { return levelName; }

	public static boolean stopped() { return stopped; }

	static boolean worldMismatch() { return worldMismatch; }

	/** Swap in a validated envelope (reload, tests). */
	public static void apply(Envelope e) {
		env = e;
		Rails.clearGrants("envelope");
		if (worldId != null) checkWorld();
		LOG.info("envelope applied: version={} sha256={}", e.version, e.hash);
	}

	private static void checkWorld() {
		worldMismatch = !env.worldId.equals(worldId);
		if (worldMismatch) LOG.error("envelope world_id {} does not match this world {}: every Eve effect is denied until the envelope names this world", env.worldId, worldId);
		else LOG.info("world_id {} matches the envelope", worldId);
	}

	/** Persisted so a restart never resumes Eve; /eve resume is the only exit. */
	public static void setStopped(boolean s) {
		stopped = s;
		try {
			Path f = stoppedFlag();
			if (s) {
				Files.createDirectories(f.getParent());
				Files.writeString(f, Long.toString(System.currentTimeMillis()));
			} else Files.deleteIfExists(f);
		} catch (IOException e) {
			LOG.error("could not persist STOPPED={} (in-memory state still applies)", s, e);
		}
	}

	static ServerPlayer findEve(MinecraftServer s) {
		return s == null ? null : s.getPlayerList().getPlayerByName(env.eveAccount);
	}

	static JsonObject status(MinecraftServer s) {
		JsonObject p = new JsonObject();
		p.addProperty("tps", Math.min(20.0, 1e9 / Math.max(1, s.getAverageTickTimeNanos())));
		ServerPlayer eve = findEve(s);
		int chunks = 0;
		if (eve != null) {
			int r = s.getPlayerList().getViewDistance(), cx = eve.chunkPosition().x(), cz = eve.chunkPosition().z();
			for (int x = cx - r; x <= cx + r; x++) for (int z = cz - r; z <= cz + r; z++) if (eve.level().getChunkSource().getChunkNow(x, z) != null) chunks++;
		}
		p.addProperty("loaded_chunks_by_eve", chunks);
		p.addProperty("blocks_broken_last_hour", Rails.brokenLastHour());
		p.addProperty("blocks_placed_last_hour", Rails.placedLastHour());
		p.addProperty("stopped", stopped);
		p.addProperty("eve_online", eve != null);
		return p;
	}

	/** One grep-able audit line per security-relevant event. */
	static void audit(String type, JsonObject payload) {
		LOG.info("[eve-audit] {} {}", type, payload);
	}
}
