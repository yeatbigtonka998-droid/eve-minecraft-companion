package eve.sense.test;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.net.http.WebSocket;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/**
 * Dev-only (CP-11, eve-mind's request): send each malformed frame ONCE on sense's live /sense link, so the mind's refusal
 * counters can be checked. By reflection on eve.sense.Link (this jar must not link against eve-sense), on the link's own
 * thread so the frames never overlap a real send. Order as asked; the 300 KB frame is last because the mind then closes
 * the link (1009) and sense reconnects. Console only (/evetest linkfuzz). Disposable dev worlds only.
 */
final class LinkFuzz {
	private static final Logger LOG = LoggerFactory.getLogger("eve-devtools");
	static final List<String> CASES = List.of("bad_json", "binary", "duplicate_id", "wrong_session", "unknown_type",
			"wrong_direction", "bad_payload", "untrusted_owner_command", "payload_too_large");

	private LinkFuzz() { }

	/** Queues the cases (all, or the one named) on the link thread; returns what was queued. */
	static String run(String only) throws ReflectiveOperationException {
		Class<?> c = Class.forName("eve.sense.Link");
		Object link = c.getField("INSTANCE").get(null);
		Field ws = field(c, "ws"), session = field(c, "session"), ex = field(c, "ex");
		Method env = c.getDeclaredMethod("envelope", String.class, JsonObject.class, String.class, String.class);
		env.setAccessible(true);
		List<String> todo = only == null || only.equals("all") ? CASES : List.of(only);
		if (!CASES.containsAll(todo)) return "unknown case; one of " + CASES + " or all";
		if (ws.get(link) == null || session.get(link) == null) return "link is not up";
		((ScheduledExecutorService) ex.get(link)).execute(() -> {
			List<String> done = new ArrayList<>();
			for (String k : todo) {
				try {
					WebSocket w = (WebSocket) ws.get(link);
					String sess = (String) session.get(link);
					if (w == null) { done.add(k + ":no_link"); break; }
					send(w, k, sess, (t, p, s) -> {
						try {
							return (JsonObject) env.invoke(link, t, p, null, s);
						} catch (ReflectiveOperationException e) {
							throw new IllegalStateException(e);
						}
					});
					done.add(k + ":sent");
					Thread.sleep(400);
				} catch (Exception e) {
					done.add(k + ":" + e);
				}
			}
			LOG.warn("[evetest] linkfuzz {}", done);
		});
		return "linkfuzz queued " + todo;
	}

	interface Env { JsonObject make(String type, JsonObject payload, String session); }

	private static void send(WebSocket w, String k, String sess, Env env) throws Exception {
		switch (k) {
			case "bad_json" -> text(w, "{\"v\":1,\"type\":\"ping\",");
			case "binary" -> w.sendBinary(ByteBuffer.wrap("{\"v\":1}".getBytes(StandardCharsets.UTF_8)), true).get(5, TimeUnit.SECONDS);
			case "duplicate_id" -> {
				String m = env.make("ping", new JsonObject(), sess).toString();
				text(w, m);
				text(w, m); // the same id twice
			}
			case "wrong_session" -> text(w, env.make("ping", new JsonObject(), "00000000-0000-0000-0000-000000000000").toString());
			case "unknown_type" -> text(w, env.make("sense.bogus", new JsonObject(), sess).toString());
			case "wrong_direction" -> text(w, env.make("state", new JsonObject(), sess).toString()); // body->mind type from sense
			case "bad_payload" -> {
				JsonObject c = new JsonObject(); // a storage.index container without dim
				c.addProperty("id", "minecraft:overworld:0,64,0");
				JsonObject pos = new JsonObject();
				pos.addProperty("x", 0);
				pos.addProperty("y", 64);
				pos.addProperty("z", 0);
				c.add("pos", pos);
				c.addProperty("kind", "minecraft:chest");
				c.addProperty("tag", "private");
				c.addProperty("loaded", true);
				c.add("items", new JsonArray());
				JsonArray cs = new JsonArray();
				cs.add(c);
				JsonObject p = new JsonObject();
				p.add("containers", cs);
				p.addProperty("partial", true);
				text(w, env.make("storage.index", p, sess).toString());
			}
			case "untrusted_owner_command" -> {
				JsonObject p = new JsonObject();
				p.addProperty("sender", "Mallory_cp11");
				p.addProperty("trusted", true); // claims trust; Mallory is not in owner.trusted_players
				p.addProperty("command", "stop");
				p.add("args", new JsonArray());
				p.addProperty("raw", "/eve stop");
				text(w, env.make("owner.command", p, sess).toString());
			}
			case "payload_too_large" -> {
				JsonObject p = new JsonObject();
				p.addProperty("pad", "x".repeat(300_000));
				text(w, env.make("eve.status", p, sess).toString());
			}
			default -> throw new IllegalArgumentException(k);
		}
	}

	private static void text(WebSocket w, String s) throws Exception {
		w.sendText(s, true).get(5, TimeUnit.SECONDS);
	}

	private static Field field(Class<?> c, String name) throws NoSuchFieldException {
		Field f = c.getDeclaredField(name);
		f.setAccessible(true);
		return f;
	}
}
