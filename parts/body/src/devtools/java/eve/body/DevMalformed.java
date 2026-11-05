package eve.body;

import com.google.gson.JsonObject;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.Minecraft;

import java.net.http.WebSocket;
import java.nio.ByteBuffer;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

/**
 * DEV ONLY (CP-11, mind's live suite). Creating {@code <gamedir>/config/eve-devtools-malformed.trigger} makes the body send each
 * malformed frame once on its live link, in order, then write {@code eve-devtools-malformed.done} with the frame ids. The mind's
 * metrics are the proof it refused each. The 300 KB frame goes last: the mind closes with 1009 and the body reconnects.
 */
public final class DevMalformed implements ClientModInitializer {
	private static final Path TRIGGER = FabricLoader.getInstance().getConfigDir().resolve("eve-devtools-malformed.trigger");
	private static final Path DONE = FabricLoader.getInstance().getConfigDir().resolve("eve-devtools-malformed.done");
	private int tick;

	@Override
	public void onInitializeClient() {
		EveBody.LOG.warn("eve-body-devtools loaded: DEV ONLY (CP-11 malformed frames on {}); never deploy outside a test run", TRIGGER);
		ClientTickEvents.END_CLIENT_TICK.register(mc -> {
			if (++tick % 20 != 0 || !Files.exists(TRIGGER)) return;
			WebSocket w = Link.ws;
			if (w == null || Link.session == null || mc.player == null) return; // wait for a live, bound link
			try {
				Files.delete(TRIGGER);
				Files.writeString(DONE, Registry.GSON.toJson(send(mc, w)));
			} catch (Exception e) {
				EveBody.LOG.error("eve-body-devtools: {}", e.toString());
			}
		});
	}

	private static JsonObject frame(String id, String type, String session, JsonObject payload) {
		JsonObject m = new JsonObject();
		m.addProperty("v", 1);
		m.addProperty("id", id);
		m.addProperty("ts", System.currentTimeMillis());
		m.addProperty("tick", EveBody.gameTime);
		m.addProperty("from", "body");
		m.addProperty("type", type);
		m.add("reply_to", null);
		m.addProperty("session", session);
		m.add("world", null);
		m.add("payload", payload);
		return m;
	}

	private static JsonObject send(Minecraft mc, WebSocket w) {
		String s = Link.session;
		List<String[]> sent = new ArrayList<>(); // {case, id}
		JsonObject metrics = new JsonObject();
		metrics.addProperty("tick_ms_avg", 0);
		metrics.addProperty("fps", 0);
		metrics.addProperty("actions_running", 0);
		metrics.addProperty("reflex_triggers", 0);
		metrics.addProperty("dropped_messages", 0);
		text(w, "{\"v\":1,\"type\":\"state\",\"payload\":{", sent, "bad_json", null);
		chain(w, () -> w.sendBinary(ByteBuffer.wrap(new byte[] {0x7b, 0x00, (byte) 0xff, 0x7d}), true));
		sent.add(new String[] {"binary", null});
		String dup = UUID.randomUUID().toString();
		text(w, Registry.GSON.toJson(frame(dup, "metrics", s, metrics)), sent, "duplicate_id_first(valid)", dup);
		text(w, Registry.GSON.toJson(frame(dup, "metrics", s, metrics)), sent, "duplicate_id_second", dup);
		String id = UUID.randomUUID().toString();
		text(w, Registry.GSON.toJson(frame(id, "metrics", "00000000-0000-0000-0000-000000000000", metrics)), sent, "wrong_session", id);
		id = UUID.randomUUID().toString();
		text(w, Registry.GSON.toJson(frame(id, "bogus.type", s, new JsonObject())), sent, "unknown_type", id);
		JsonObject req = new JsonObject();
		req.addProperty("action_id", UUID.randomUUID().toString());
		req.addProperty("skill", "stop");
		req.addProperty("version", 1);
		req.add("params", new JsonObject());
		id = UUID.randomUUID().toString();
		text(w, Registry.GSON.toJson(frame(id, "action.request", s, req)), sent, "wrong_direction", id);
		JsonObject st = snapshot(mc);
		st.addProperty("dim", "overworld"); // not a namespaced dimension id: the only fault in this frame
		id = UUID.randomUUID().toString();
		text(w, Registry.GSON.toJson(frame(id, "state", s, st)), sent, "bad_payload(state dim)", id);
		id = UUID.randomUUID().toString();
		JsonObject big = new JsonObject();
		big.addProperty("kind", "chat_received");
		big.addProperty("detail", "x".repeat(300 * 1024));
		text(w, Registry.GSON.toJson(frame(id, "event", s, big)), sent, "payload_too_large(300KB, last)", id);
		JsonObject out = new JsonObject();
		out.addProperty("session", s);
		out.addProperty("sent_at_ms", System.currentTimeMillis());
		var arr = new com.google.gson.JsonArray();
		for (String[] c : sent) {
			JsonObject o = new JsonObject();
			o.addProperty("case", c[0]);
			o.addProperty("id", c[1]);
			arr.add(o);
		}
		out.add("frames", arr);
		EveBody.LOG.warn("eve-body-devtools: sent {} malformed frames on session {}", sent.size(), s);
		return out;
	}

	// Reflection on purpose: the release jar keeps these private and byte-identical; only this dev jar reaches in.
	@SuppressWarnings("unchecked")
	private static void chain(WebSocket w, java.util.function.Supplier<java.util.concurrent.CompletableFuture<WebSocket>> op) {
		try {
			var m = Link.class.getDeclaredMethod("chain", WebSocket.class, java.util.function.Supplier.class);
			m.setAccessible(true);
			m.invoke(null, w, op); // Link's own send queue: one outstanding send at a time
		} catch (ReflectiveOperationException e) {
			throw new IllegalStateException(e);
		}
	}

	private static JsonObject snapshot(Minecraft mc) {
		try {
			var inv = Telemetry.class.getDeclaredMethod("inventory", net.minecraft.client.player.LocalPlayer.class);
			inv.setAccessible(true);
			var a = (com.google.gson.JsonArray) inv.invoke(null, mc.player);
			var st = Telemetry.class.getDeclaredMethod("state", Minecraft.class, net.minecraft.client.player.LocalPlayer.class, String.class,
					com.google.gson.JsonArray.class, String.class);
			st.setAccessible(true);
			return (JsonObject) st.invoke(null, mc, mc.player, mc.player.level().dimension().identifier().toString(), a, "devtools");
		} catch (ReflectiveOperationException e) {
			throw new IllegalStateException(e);
		}
	}

	private static void text(WebSocket w, String body, List<String[]> sent, String name, String id) {
		chain(w, () -> w.sendText(body, true));
		sent.add(new String[] {name, id});
	}
}
