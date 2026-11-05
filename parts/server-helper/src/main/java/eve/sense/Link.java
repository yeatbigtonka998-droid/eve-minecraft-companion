package eve.sense;

import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import eve.sense.Envelope.Decision;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.SharedConstants;
import net.minecraft.core.BlockPos;
import net.minecraft.resources.Identifier;
import net.minecraft.resources.ResourceKey;
import net.minecraft.core.registries.Registries;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.Entity;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.WebSocket;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.UUID;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;

/**
 * Sense link v1 client (contracts/link-protocol.md): ws://127.0.0.1:8951/sense, hello with token, ping/pong every 5 s,
 * 15 s silence = dead, reconnect with 1 s -> 30 s backoff + jitter, 256 KiB frame cap both ways.
 * All socket work runs on one "eve-link" thread; world reads hop to the server thread.
 */
public final class Link implements WebSocket.Listener {
	public static final Link INSTANCE = new Link();
	static final int MAX_FRAME = 262_144;

	/** Gametest tap: sees every message this mod emits, connected or not. */
	public static volatile Consumer<JsonObject> tap;

	private final ScheduledExecutorService ex = Executors.newSingleThreadScheduledExecutor(r -> {
		Thread t = new Thread(r, "eve-link");
		t.setDaemon(true);
		return t;
	});
	private final StringBuilder rx = new StringBuilder();
	private volatile WebSocket ws;
	private volatile String session;
	private volatile long lastRx;
	private volatile boolean running;
	private MinecraftServer server;
	private String url, token;
	private long backoffMs = 1000;
	private long dropped, droppedSessionMismatch;

	void start(MinecraftServer s) {
		server = s;
		url = System.getProperty("eve.link.url", "ws://127.0.0.1:8951/sense");
		token = readToken();
		if (token == null) {
			EveSense.LOG.warn("no link token (env EVE_LINK_TOKEN or config/eve-link-token.txt): link to the mind is disabled; rails still enforce");
			return;
		}
		running = true;
		ex.execute(this::connect);
		ex.scheduleAtFixedRate(this::heartbeat, 5, 5, TimeUnit.SECONDS);
		// Not on server ticks: an empty server pauses ticking (pause-when-empty-seconds) but still runs queued tasks.
		ex.scheduleAtFixedRate(() -> s.execute(() -> Storage.flush(s)), 1, 1, TimeUnit.SECONDS);
		ex.scheduleAtFixedRate(() -> s.execute(() -> send("eve.status", EveSense.status(s), null)), 30, 30, TimeUnit.SECONDS);
	}

	void stop() {
		running = false;
		WebSocket w = ws;
		if (w != null) w.sendClose(WebSocket.NORMAL_CLOSURE, "server stopping");
		ex.shutdownNow();
	}

	public boolean ready() { return session != null; }

	private static String readToken() {
		String t = System.getenv("EVE_LINK_TOKEN");
		if (t != null && !t.isBlank()) return t.trim();
		try {
			Path f = FabricLoader.getInstance().getConfigDir().resolve("eve-link-token.txt");
			return Files.exists(f) ? Files.readString(f).trim() : null;
		} catch (Exception e) {
			return null;
		}
	}

	private void connect() {
		if (!running) return;
		HttpClient.newHttpClient().newWebSocketBuilder().connectTimeout(Duration.ofSeconds(5)).buildAsync(URI.create(url), this)
				.whenComplete((w, err) -> ex.execute(() -> {
					if (err != null) { reconnectLater("connect failed: " + err.getMessage()); return; }
					ws = w;
					lastRx = System.currentTimeMillis();
					JsonObject p = new JsonObject();
					p.addProperty("token", token);
					p.addProperty("mod_version", EveSense.MOD_VERSION);
					p.addProperty("mc_version", SharedConstants.getCurrentVersion().name());
					p.addProperty("world_id", EveSense.worldId());
					p.addProperty("level_name", EveSense.levelName());
					p.addProperty("seed_hash", EveSense.seedHash());
					p.addProperty("envelope_version", EveSense.env().version);
					p.addProperty("envelope_hash", EveSense.env().hash);
					write(envelope("hello", p, null, null));
					EveSense.LOG.info("link: hello sent to {} (world_id={}, envelope sha256={})", url, EveSense.worldId(), EveSense.env().hash);
				}));
	}

	private void reconnectLater(String why) {
		ws = null;
		if (session != null && server != null) server.execute(() -> Rails.clearGrants("link_lost")); // the mind's task is interrupted
		session = null;
		if (!running) return;
		long delay = backoffMs + ThreadLocalRandom.current().nextLong(backoffMs / 2 + 1);
		EveSense.LOG.info("link: {}; reconnect in {} ms", why, delay);
		backoffMs = Math.min(backoffMs * 2, 30_000);
		ex.schedule(this::connect, delay, TimeUnit.MILLISECONDS);
	}

	private void heartbeat() {
		WebSocket w = ws;
		if (w == null) return;
		if (System.currentTimeMillis() - lastRx > 15_000) {
			w.abort(); // onError/onClose may not fire after abort
			reconnectLater("15 s silence");
			return;
		}
		if (session != null) write(envelope("ping", new JsonObject(), null, session));
	}

	/** Thread-safe. Dropped (and counted) when the link is not up; the audit log keeps denials and commands regardless. */
	public void send(String type, JsonObject payload, String replyTo) {
		JsonObject msg = envelope(type, payload, replyTo, session);
		Consumer<JsonObject> t = tap;
		if (t != null) t.accept(msg);
		if (ex.isShutdown()) return;
		ex.execute(() -> {
			if (session == null) { dropped++; return; }
			msg.addProperty("session", session);
			write(msg);
		});
	}

	private JsonObject envelope(String type, JsonObject payload, String replyTo, String sess) {
		JsonObject m = new JsonObject();
		m.addProperty("v", 1);
		m.addProperty("id", UUID.randomUUID().toString());
		m.addProperty("ts", System.currentTimeMillis());
		m.addProperty("tick", server == null ? 0 : server.getTickCount());
		m.addProperty("from", "sense");
		m.addProperty("type", type);
		m.add("reply_to", replyTo == null ? JsonNull.INSTANCE : new com.google.gson.JsonPrimitive(replyTo));
		m.add("session", sess == null ? JsonNull.INSTANCE : new com.google.gson.JsonPrimitive(sess));
		m.add("world", EveSense.worldId() == null ? JsonNull.INSTANCE : new com.google.gson.JsonPrimitive(EveSense.worldId()));
		m.add("payload", payload);
		return m;
	}

	/** Link thread only. */
	private void write(JsonObject msg) {
		WebSocket w = ws;
		if (w == null) { dropped++; return; }
		String s = msg.toString();
		if (s.getBytes(StandardCharsets.UTF_8).length > Math.min(MAX_FRAME, EveSense.env().payloadMax)) {
			EveSense.LOG.error("link: refusing to send {} of {} bytes (cap {})", msg.get("type").getAsString(), s.length(), MAX_FRAME);
			return;
		}
		try {
			w.sendText(s, true).get(10, TimeUnit.SECONDS);
		} catch (Exception e) {
			w.abort();
			reconnectLater("send failed: " + e);
		}
	}

	// ---- WebSocket.Listener (called on the HttpClient's threads; hop to the link thread) ----

	@Override
	public CompletionStage<?> onText(WebSocket w, CharSequence data, boolean last) {
		lastRx = System.currentTimeMillis();
		rx.append(data);
		// chars <= UTF-8 bytes, so the first test aborts early without ever accepting an oversized frame
		if (rx.length() > MAX_FRAME || last && rx.toString().getBytes(StandardCharsets.UTF_8).length > MAX_FRAME) {
			rx.setLength(0);
			ex.execute(() -> {
				error("payload_too_large", "frame over " + MAX_FRAME + " bytes", null);
				w.sendClose(1009, "payload_too_large");
				reconnectLater("peer sent an oversized frame");
			});
			return null;
		}
		if (last) {
			String s = rx.toString();
			rx.setLength(0);
			ex.execute(() -> receive(s));
		}
		w.request(1);
		return null;
	}

	@Override
	public CompletionStage<?> onClose(WebSocket w, int code, String reason) {
		ex.execute(() -> { if (ws == w) reconnectLater("closed " + code + " " + reason); });
		return null;
	}

	@Override
	public void onError(WebSocket w, Throwable err) {
		ex.execute(() -> { if (ws == w) reconnectLater("error " + err); });
	}

	/** Link thread. */
	private void receive(String raw) {
		JsonObject m;
		try {
			m = JsonParser.parseString(raw).getAsJsonObject();
		} catch (RuntimeException e) {
			error("bad_json", "not a JSON object", null);
			return;
		}
		String type = str(m, "type"), id = str(m, "id");
		JsonObject payload = m.has("payload") && m.get("payload").isJsonObject() ? m.getAsJsonObject("payload") : new JsonObject();
		if (session == null) {
			if ("hello.ok".equals(type) && str(payload, "session") != null) {
				session = str(payload, "session");
				backoffMs = 1000;
				EveSense.LOG.info("link: hello.ok, session {}", session);
				server.execute(() -> Storage.sendFull(server, null, null));
			} else if ("hello.reject".equals(type)) {
				EveSense.LOG.error("link: hello rejected: {}", payload);
				ws.sendClose(WebSocket.NORMAL_CLOSURE, "rejected");
				reconnectLater("hello.reject " + str(payload, "code"));
			}
			return;
		}
		if (!session.equals(str(m, "session"))) { droppedSessionMismatch++; return; }
		switch (type == null ? "" : type) {
			case "ping" -> write(envelope("pong", new JsonObject(), id, session));
			case "pong" -> { }
			case "error" -> EveSense.LOG.warn("link: mind reported error {}", payload);
			case "storage.request" -> server.execute(() -> Storage.sendFull(server, payload, id));
			case "permission.check" -> server.execute(() -> send("permission.result", check(payload), id));
			case "zone.list" -> server.execute(() -> send("zone.list.result", zoneList(), id));
			case "task.grant", "grant_region" -> server.execute(() -> {
				String refused = type.equals("grant_region") ? Rails.grantRegion(payload) : Rails.grant(payload);
				if (refused == null) return;
				JsonObject e = new JsonObject();
				e.addProperty("code", "bad_payload");
				e.addProperty("message", type + " refused: " + refused);
				e.addProperty("about_id", id);
				send("error", e, id);
			});
			case "query.blocks" -> server.execute(() -> answer("query.blocks.result", () -> Query.blocks(server, payload), id));
			case "query.resources" -> server.execute(() -> answer("query.resources.result", () -> Query.resources(server, payload), id));
			case "zone.snapshot.save" -> server.execute(() -> answer("zone.snapshot.saved", () -> Snapshots.echo(payload), id));
			case "zone.diff" -> server.execute(() -> answer("zone.diff.result", () -> Snapshots.diff(server, payload), id));
			case "container.tag" -> server.execute(() -> answer("container.tag.result", () -> ContainerTags.tag(server, payload), id)); // O135
			case "attack.grant" -> server.execute(() -> answer("attack.grant.result", () -> Rails.attackGrant(server, payload), id)); // O136
			case "zone.repair" -> server.execute(() -> answer("zone.repair.result", () -> Snapshots.repair(server, payload),id));
			default -> error("unknown_type", "unknown type " + type, id);
		}
	}

	/** Server thread. A malformed request is answered with error{bad_payload}, never a partial result. */
	private void answer(String type, java.util.function.Supplier<JsonObject> query, String id) {
		JsonObject r;
		try {
			r = query.get();
		} catch (IllegalArgumentException | IllegalStateException | NullPointerException | ClassCastException | UnsupportedOperationException e) {
			error("bad_payload", type.replace(".result", "") + ": " + e.getMessage(), id);
			return;
		}
		send(type, r, id);
	}

	private void error(String code, String message, String aboutId) {
		JsonObject p = new JsonObject();
		p.addProperty("code", code);
		p.addProperty("message", message);
		p.addProperty("about_id", aboutId);
		write(envelope("error", p, aboutId, session));
	}

	/** Server thread. Read-only: a check never grants anything; enforcement re-decides at the effect. */
	public JsonObject check(JsonObject p) {
		Decision d;
		try {
			if (!"eve".equals(str(p, "actor"))) throw new IllegalArgumentException("actor must be eve");
			String effect = str(p, "effect"), dim = str(p, "dim");
			ServerLevel level = server.getLevel(ResourceKey.create(Registries.DIMENSION, Identifier.parse(dim)));
			if (level == null) throw new IllegalArgumentException("unknown dim");
			d = switch (effect == null ? "" : effect) {
				case "break" -> {
					BlockPos b = blockPos(p);
					yield Rails.decideBreak(level, b, Query.peek(level, b.getX(), b.getY(), b.getZ())); // H-trees too
				}
				case "place" -> Rails.decideBlock(effect, dim, blockPos(p));
				case "open_container" -> {
					Decision g = Rails.gate();
					if (!g.allowed()) yield g;
					BlockPos pos = blockPos(p);
					Decision r = Decision.allow("container");
					for (BlockPos part : level.isLoaded(pos) ? Rails.containerParts(level, pos) : java.util.List.of(pos)) {
						r = Rails.decideContainer(level, part);
						if (!r.allowed()) break;
					}
					yield r;
				}
				case "attack" -> {
					Entity e = level.getEntity(p.get("entity_id").getAsInt());
					yield e == null ? Decision.deny("entity_unknown", "no such entity loaded") : Rails.decideAttack(e);
				}
				case "shear", "milk" -> { // CP-39 (envelope v9): the same rule as enforcement (Rails.decideAnimal)
					Entity e = level.getEntity(p.get("entity_id").getAsInt());
					yield e == null ? Decision.deny("entity_unknown", "no such entity loaded") : Rails.decideAnimal(effect, e);
				}
				case "use_item" -> { // same rule as enforcement (Rails.allowUseOn); optional face, absent = all six neighbours
					Decision g = Rails.gate();
					if (!g.allowed() || !p.has("pos")) yield g;
					String face = str(p, "face");
					net.minecraft.core.Direction dir = face == null ? null : net.minecraft.core.Direction.byName(face);
					if (face != null && dir == null) throw new IllegalArgumentException("face must be up|down|north|south|east|west");
					Decision r = g;
					for (BlockPos q : Rails.useOnTargets(blockPos(p), dir)) {
						r = Rails.decideBlock("place", dim, q, "use");
						if (!r.allowed()) break;
					}
					yield r;
				}
				case "use_hazard" -> {
					Decision g = Rails.gate();
					String hz = str(p, "hazard");
					if (!g.allowed()) yield g;
					if (hz != null && Boolean.TRUE.equals(EveSense.env().hazards.get(hz))) yield Decision.allow("hazard:" + hz);
					// O193: with pos + face, the same portal-light exception as enforcement (flint and steel on a frame)
					if ("fire".equals(hz) && p.has("pos") && p.has("face")) {
						net.minecraft.core.Direction f = net.minecraft.core.Direction.byName(str(p, "face"));
						if (f != null && Rails.portalLight(level, blockPos(p), f, new net.minecraft.world.item.ItemStack(net.minecraft.world.item.Items.FLINT_AND_STEEL)))
							yield Decision.allow("portal_light");
					}
					yield Decision.deny("hazard:" + hz, "hazard needs a direct owner command");
				}
				default -> throw new IllegalArgumentException("unknown effect");
			};
		} catch (RuntimeException e) {
			d = Decision.deny("bad_request", String.valueOf(e.getMessage()));
		}
		JsonObject r = new JsonObject();
		r.addProperty("allowed", d.allowed());
		r.addProperty("rule_id", d.ruleId());
		r.addProperty("reason", d.reason());
		return r;
	}

	private JsonObject zoneList() {
		com.google.gson.JsonArray a = new com.google.gson.JsonArray();
		for (Envelope.Zone z : EveSense.env().zones) {
			JsonObject o = new JsonObject();
			o.addProperty("id", z.id());
			o.addProperty("dim", z.dim());
			o.add("min", xyz(z.min()));
			o.add("max", xyz(z.max()));
			o.addProperty("break", z.brk());
			o.addProperty("place", z.place());
			o.addProperty("containers", z.containers());
			o.addProperty("snapshot", z.snapshot());
			int v = Snapshots.latestVersion(z.id());
			if (v > 0) o.addProperty("snapshot_version", v);
			else o.add("snapshot_version", JsonNull.INSTANCE);
			a.add(o);
		}
		JsonObject r = new JsonObject();
		r.add("zones", a);
		return r;
	}

	private static BlockPos blockPos(JsonObject p) {
		JsonObject pos = p.getAsJsonObject("pos");
		return BlockPos.containing(pos.get("x").getAsDouble(), pos.get("y").getAsDouble(), pos.get("z").getAsDouble());
	}

	static JsonObject pos(BlockPos p) {
		return xyz(new int[]{p.getX(), p.getY(), p.getZ()});
	}

	private static JsonObject xyz(int[] v) {
		JsonObject o = new JsonObject();
		o.addProperty("x", v[0]);
		o.addProperty("y", v[1]);
		o.addProperty("z", v[2]);
		return o;
	}

	private static String str(JsonObject o, String k) {
		JsonElement e = o.get(k);
		return e != null && e.isJsonPrimitive() ? e.getAsString() : null;
	}
}
