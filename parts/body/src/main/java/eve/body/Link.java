package eve.body;

import com.google.gson.JsonElement;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.SharedConstants;
import net.minecraft.client.Minecraft;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.WebSocket;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Body link client (contracts/link-protocol.md). Lives while the game is connected to a server; reconnects to the mind with
 * backoff 1 s -> 30 s. Network callbacks never touch the game: everything except ping/pong goes to {@link #INBOX}, drained on
 * the client thread by the arbiter.
 */
final class Link {
	static final URI URL = url();
	static final int MAX_FRAME = 262_144;
	static final long SILENCE_NS = TimeUnit.SECONDS.toNanos(15);

	record In(JsonObject msg, long recvNanos) {}

	static final ConcurrentLinkedQueue<In> INBOX = new ConcurrentLinkedQueue<>();
	static final AtomicLong DROPPED = new AtomicLong(), DROPPED_SESSION = new AtomicLong();

	private static final HttpClient HTTP = HttpClient.newHttpClient();
	private static final ScheduledExecutorService EXEC = Executors.newSingleThreadScheduledExecutor(r -> {
		Thread t = new Thread(r, "eve-link");
		t.setDaemon(true);
		return t;
	});
	private static final Map<String, Long> SEEN_IDS = new LinkedHashMap<>(256, 0.75f, false) {
		@Override
		protected boolean removeEldestEntry(Map.Entry<String, Long> e) {
			return size() > 20_000;
		}
	};

	static volatile WebSocket ws;
	static volatile String session;
	static volatile String mode = "manual"; // from hello.ok, informational
	private static volatile boolean wanted, connecting;
	private static volatile long lastRecvNanos;
	private static volatile String account, serverAddress, levelName, worldId;
	private static int backoffMs = 1000;
	private static boolean warnedNoToken;
	private static CompletableFuture<?> sendChain = CompletableFuture.completedFuture(null);
	static int gameJoins;

	static {
		EXEC.scheduleAtFixedRate(Link::heartbeat, 5, 5, TimeUnit.SECONDS);
	}

	/** The real mind is on 8951; dev tools use 8952 (-Deve.link.url or EVE_LINK_URL). Loopback only: the hello carries the token. */
	private static URI url() {
		String u = System.getProperty("eve.link.url", System.getenv("EVE_LINK_URL"));
		if (u != null) {
			try {
				URI x = URI.create(u);
				if ("ws".equals(x.getScheme()) && ("127.0.0.1".equals(x.getHost()) || "localhost".equals(x.getHost()))) return x;
			} catch (IllegalArgumentException ignored) {
			}
			EveBody.LOG.warn("eve link: ignoring non-loopback or malformed link url override");
		}
		return URI.create("ws://127.0.0.1:8951/body");
	}

	/** Client thread, on game JOIN. */
	static void open(Minecraft mc) {
		account = mc.getUser().getName();
		serverAddress = mc.getCurrentServer() != null ? mc.getCurrentServer().ip : mc.isLocalServer() ? "singleplayer" : null;
		// A multiplayer client cannot see the server's level name: sense tells us via eve:control {op:"world"} (setWorld).
		if (mc.getSingleplayerServer() != null) levelName = mc.getSingleplayerServer().getWorldData().getLevelName();
		gameJoins++;
		wanted = true;
		EXEC.execute(Link::connect);
	}

	/** Client thread, on game DISCONNECT: say so, then close. */
	static void close(String reason) {
		wanted = false;
		levelName = null;
		worldId = null;
		WebSocket w = ws;
		if (w == null) return;
		JsonObject d = new JsonObject();
		d.addProperty("reason", reason);
		Telemetry.event("disconnect", d);
		chain(w, () -> w.sendClose(WebSocket.NORMAL_CLOSURE, reason));
		lost(w, "game disconnected");
	}

	private static void connect() {
		if (!wanted || ws != null || connecting) return;
		String token = token();
		if (token == null) {
			if (!warnedNoToken) EveBody.LOG.warn("eve link: no EVE_LINK_TOKEN and no config/eve-link-token.txt; not connecting");
			warnedNoToken = true;
			retry();
			return;
		}
		connecting = true;
		HTTP.newWebSocketBuilder().connectTimeout(Duration.ofSeconds(5)).buildAsync(URL, new Listener()).whenComplete((w, err) -> {
			connecting = false;
			if (err != null || !wanted) {
				if (w != null) w.abort();
				retry();
				return;
			}
			ws = w;
			lastRecvNanos = System.nanoTime();
			JsonObject p = new JsonObject();
			p.addProperty("token", token);
			p.addProperty("mod_version", EveBody.MOD_VERSION);
			p.addProperty("mc_version", SharedConstants.getCurrentVersion().name());
			p.addProperty("account_name", account);
			// "" = not known yet (contract types are strings); the mind refuses dispatch until it matches sense's level_name.
			p.addProperty("server_address", serverAddress == null ? "" : serverAddress);
			p.addProperty("level_name", levelName == null ? "" : levelName);
			p.add("caps", Registry.caps());
			JsonArray accepts = new JsonArray(); // H-autofight: the mind relays mob.targeting only to a body that says so
			accepts.add("mob.targeting");
			p.add("accepts", accepts);
			p.addProperty("registry_hash", Registry.hash);
			send("hello", p, null);
			EveBody.LOG.info("eve link: connected to {}, hello sent ({} caps, registry {})", URL, Registry.IMPLEMENTED.size(), Registry.hash.substring(0, 12));
		});
	}

	/** World identity as sense announced it (null / "" until then); carried inside every state payload. */
	static String worldId() {
		return worldId;
	}

	static String levelName() {
		return levelName == null ? "" : levelName;
	}

	/** From sense via eve:control {op:"world"}: level name and world_id of the server we are on. Re-hellos so the mind binds them. */
	static void setWorld(String level, String world) {
		if (java.util.Objects.equals(level, levelName) && java.util.Objects.equals(world, worldId)) return;
		levelName = level;
		worldId = world;
		EveBody.LOG.info("eve link: world is {} ({}), re-hello", world, level);
		WebSocket w = ws;
		if (w != null) {
			chain(w, () -> w.sendClose(WebSocket.NORMAL_CLOSURE, "rebind"));
			lost(w, "rebind to world " + world);
		}
	}

	private static void retry() {
		if (!wanted) return;
		int delay = backoffMs + ThreadLocalRandom.current().nextInt(backoffMs / 4 + 1);
		backoffMs = Math.min(backoffMs * 2, 30_000);
		EXEC.schedule(Link::connect, delay, TimeUnit.MILLISECONDS);
	}

	private static void heartbeat() {
		WebSocket w = ws;
		if (w == null) return;
		if (System.nanoTime() - lastRecvNanos > SILENCE_NS) {
			w.abort();
			lost(w, "15 s silence");
			return;
		}
		if (session != null) send("ping", new JsonObject(), null);
	}

	/** Any thread. Marks the link down (the arbiter sees session == null and goes IDLE-SAFE) and schedules a reconnect. */
	static synchronized void lost(WebSocket w, String why) {
		if (w == null || ws != w) return;
		ws = null;
		session = null;
		EveBody.LOG.warn("eve link: down ({})", why);
		retry();
	}

	static String token() {
		String t = System.getenv("EVE_LINK_TOKEN");
		if (t != null && !t.isBlank()) return t.trim();
		try {
			var f = FabricLoader.getInstance().getConfigDir().resolve("eve-link-token.txt");
			if (Files.exists(f)) {
				t = Files.readString(f).trim();
				return t.isEmpty() ? null : t;
			}
		} catch (Exception e) {
			EveBody.LOG.warn("eve link: token file unreadable: {}", e.getClass().getSimpleName());
		}
		return null;
	}

	/** Any thread. Returns false when the link is down or the frame would exceed the cap. */
	static boolean send(String type, JsonObject payload, String replyTo) {
		WebSocket w = ws;
		if (w == null || (session == null && !type.equals("hello"))) return false;
		JsonObject m = new JsonObject();
		m.addProperty("v", 1);
		m.addProperty("id", UUID.randomUUID().toString());
		m.addProperty("ts", System.currentTimeMillis());
		long gt = EveBody.gameTime;
		if (gt >= 0) m.addProperty("tick", gt);
		else m.add("tick", null);
		m.addProperty("from", "body");
		m.addProperty("type", type);
		m.addProperty("reply_to", replyTo);
		m.addProperty("session", session);
		// Always null: the mind tags body frames with the world bound from sense's hello, and drops any non-null world that is
		// not bound (with sense down, that would be every frame). level_name in hello is what the mind's binding check uses.
		m.add("world", null);
		m.add("payload", payload);
		String s = Registry.GSON.toJson(m);
		if (s.getBytes(StandardCharsets.UTF_8).length > MAX_FRAME) {
			EveBody.LOG.error("eve link: refusing to send {} frame over {} bytes", type, MAX_FRAME);
			return false;
		}
		chain(w, () -> w.sendText(s, true));
		return true;
	}

	private static synchronized void chain(WebSocket w, java.util.function.Supplier<CompletableFuture<WebSocket>> op) {
		sendChain = sendChain.thenCompose(x -> op.get()).exceptionally(e -> {
			lost(w, "send failed: " + e.getClass().getSimpleName());
			return null;
		});
	}

	static void error(String code, String message, String aboutId) {
		JsonObject p = new JsonObject();
		p.addProperty("code", code);
		p.addProperty("message", message);
		p.addProperty("about_id", aboutId);
		send("error", p, aboutId);
	}

	private static final class Listener implements WebSocket.Listener {
		private final StringBuilder buf = new StringBuilder();

		@Override
		public CompletionStage<?> onText(WebSocket w, CharSequence data, boolean last) {
			buf.append(data);
			if (buf.length() > MAX_FRAME) return tooLarge(w);
			if (last) {
				String s = buf.toString();
				buf.setLength(0);
				if (s.getBytes(StandardCharsets.UTF_8).length > MAX_FRAME) return tooLarge(w);
				onFrame(s);
			}
			w.request(1);
			return null;
		}

		@Override
		public CompletionStage<?> onBinary(WebSocket w, ByteBuffer data, boolean last) {
			DROPPED.incrementAndGet();
			error("bad_frame", "binary frames are not part of the protocol", null);
			w.request(1);
			return null;
		}

		@Override
		public CompletionStage<?> onClose(WebSocket w, int code, String reason) {
			lost(w, "closed by mind " + code + " " + reason);
			return null;
		}

		@Override
		public void onError(WebSocket w, Throwable e) {
			lost(w, "error " + e.getClass().getSimpleName());
		}

		private CompletionStage<?> tooLarge(WebSocket w) {
			buf.setLength(0);
			DROPPED.incrementAndGet();
			error("payload_too_large", "frame exceeds " + MAX_FRAME + " bytes", null);
			chain(w, () -> w.sendClose(1009, "payload_too_large"));
			lost(w, "oversized frame from mind");
			return null;
		}
	}

	private static void onFrame(String s) {
		long now = System.nanoTime();
		lastRecvNanos = now;
		JsonObject m;
		try {
			JsonElement e = JsonParser.parseString(s);
			m = e.getAsJsonObject();
		} catch (Exception e) {
			DROPPED.incrementAndGet();
			error("bad_json", "frame is not a JSON object", null);
			return;
		}
		String id = str(m, "id"), type = str(m, "type");
		boolean v1 = m.has("v") && m.get("v").isJsonPrimitive() && m.get("v").getAsJsonPrimitive().isNumber() && m.get("v").getAsDouble() == 1;
		if (id == null || type == null || !v1 || !m.has("payload") || !m.get("payload").isJsonObject()) {
			DROPPED.incrementAndGet();
			error("bad_envelope", "need v=1, id, type, payload object", id);
			return;
		}
		synchronized (SEEN_IDS) {
			Long seen = SEEN_IDS.get(id);
			if (seen != null && now - seen < TimeUnit.MINUTES.toNanos(10)) {
				DROPPED.incrementAndGet();
				error("duplicate_id", "id seen in the last 10 minutes", id);
				return;
			}
			SEEN_IDS.put(id, now);
		}
		JsonObject p = m.getAsJsonObject("payload");
		if (session == null) { // before hello.ok only the handshake answers count
			switch (type) {
				case "hello.ok" -> {
					session = str(p, "session");
					if (session == null) {
						lost(ws, "hello.ok without session");
						return;
					}
					if (p.has("mode") && p.get("mode").isJsonPrimitive()) mode = p.get("mode").getAsString();
					if (p.has("state_hz")) Telemetry.hz = Math.max(1, Math.min(20, p.get("state_hz").getAsInt()));
					String rh = str(p, "registry_hash");
					if (rh != null && !rh.equals(Registry.hash)) EveBody.LOG.warn("eve link: mind registry_hash {} != body {}", rh, Registry.hash);
					backoffMs = 1000;
					EveBody.LOG.info("eve link: hello.ok session {}", session);
					INBOX.add(new In(m, now));
					Minecraft.getInstance().execute(() -> Arbiter.drain(Minecraft.getInstance()));
				}
				case "hello.reject" -> {
					EveBody.LOG.warn("eve link: hello rejected: {}", str(p, "code"));
					WebSocket w = ws;
					if (w != null) {
						w.abort();
						lost(w, "hello.reject");
					}
				}
				default -> DROPPED.incrementAndGet();
			}
			return;
		}
		if (!session.equals(str(m, "session"))) {
			DROPPED_SESSION.incrementAndGet();
			DROPPED.incrementAndGet();
			return;
		}
		switch (type) {
			case "ping" -> send("pong", new JsonObject(), id);
			case "pong" -> {}
			default -> {
				INBOX.add(new In(m, now));
				Minecraft.getInstance().execute(() -> Arbiter.drain(Minecraft.getInstance())); // next frame, not next tick: a stop waits <1 frame
			}
		}
	}

	static String str(JsonObject o, String k) {
		return o != null && o.has(k) && o.get(k).isJsonPrimitive() ? o.get(k).getAsString() : null;
	}
}
