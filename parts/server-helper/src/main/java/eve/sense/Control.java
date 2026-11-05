package eve.sense;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.mojang.brigadier.arguments.StringArgumentType;
import com.mojang.brigadier.builder.LiteralArgumentBuilder;
import com.mojang.brigadier.context.CommandContext;
import net.fabricmc.fabric.api.command.v2.CommandRegistrationCallback;
import net.fabricmc.fabric.api.message.v1.ServerMessageEvents;
import net.fabricmc.fabric.api.networking.v1.PayloadTypeRegistry;
import net.fabricmc.fabric.api.networking.v1.ServerPlayConnectionEvents;
import net.fabricmc.fabric.api.networking.v1.ServerPlayNetworking;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.network.chat.Component;
import net.minecraft.network.codec.ByteBufCodecs;
import net.minecraft.network.codec.StreamCodec;
import net.minecraft.network.protocol.common.custom.CustomPacketPayload;
import net.minecraft.resources.Identifier;
import net.minecraft.server.level.ServerPlayer;

import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.regex.Pattern;

import static net.minecraft.commands.Commands.argument;
import static net.minecraft.commands.Commands.literal;

/**
 * The owner's /eve commands (trusted players only), the eve:control stop payload to Eve's client and its
 * eve:control_ack. Wire format of both payloads: one UTF-8 string (VarInt length prefix, ByteBufCodecs.STRING_UTF8)
 * holding a JSON object (link v1.1, pinned): control {"op":"stop"|"resume"|"world","nonce","source", level_name/world_id for world},
 * ack {"nonce","op","latency_ms","tick","stopped","duplicate"?,"error"?}.
 */
public final class Control {
	public record ControlPayload(String json) implements CustomPacketPayload {
		public static final Type<ControlPayload> TYPE = new Type<>(Identifier.fromNamespaceAndPath("eve", "control"));
		public static final StreamCodec<io.netty.buffer.ByteBuf, ControlPayload> CODEC = ByteBufCodecs.STRING_UTF8.map(ControlPayload::new, ControlPayload::json);

		@Override
		public Type<ControlPayload> type() { return TYPE; }
	}

	public record AckPayload(String json) implements CustomPacketPayload {
		public static final Type<AckPayload> TYPE = new Type<>(Identifier.fromNamespaceAndPath("eve", "control_ack"));
		public static final StreamCodec<io.netty.buffer.ByteBuf, AckPayload> CODEC = ByteBufCodecs.STRING_UTF8.map(AckPayload::new, AckPayload::json);

		@Override
		public Type<AckPayload> type() { return TYPE; }
	}

	/** O20: the body's reflex layer tags its fall rescue (op rescue_place|rescue_scoop|rescue_end). Data only: logged next to
	 *  the server's own verdict (Rails.rescueFall), never a permission; a missing or forged tag changes nothing. */
	public record RescuePayload(String json) implements CustomPacketPayload {
		public static final Type<RescuePayload> TYPE = new Type<>(Identifier.fromNamespaceAndPath("eve", "rescue"));
		public static final StreamCodec<io.netty.buffer.ByteBuf, RescuePayload> CODEC = ByteBufCodecs.STRING_UTF8.map(RescuePayload::new, RescuePayload::json);

		@Override
		public Type<RescuePayload> type() { return TYPE; }
	}

	private static final Map<String, Long> pending = new ConcurrentHashMap<>(); // nonce -> nanoTime sent
	private static final Pattern VERSION = Pattern.compile("v\\d{1,6}");

	static void register() {
		PayloadTypeRegistry.clientboundPlay().register(ControlPayload.TYPE, ControlPayload.CODEC);
		PayloadTypeRegistry.serverboundPlay().register(AckPayload.TYPE, AckPayload.CODEC);
		ServerPlayNetworking.registerGlobalReceiver(AckPayload.TYPE, (payload, ctx) -> onAck(ctx.player(), payload));
		PayloadTypeRegistry.serverboundPlay().register(RescuePayload.TYPE, RescuePayload.CODEC);
		ServerPlayNetworking.registerGlobalReceiver(RescuePayload.TYPE, (payload, ctx) -> {
			JsonObject a = new JsonObject();
			a.addProperty("from", ctx.player().getGameProfile().name());
			a.addProperty("eve", Rails.isEve(ctx.player()));
			String j = payload.json();
			a.addProperty("client", j.length() > 512 ? j.substring(0, 512) : j);
			EveSense.audit("rescue.signal", a);
			try { // O20: a rescue_place tag is one (bounded) recognition path, see Rails.rescueTag
				JsonObject c = JsonParser.parseString(j).getAsJsonObject();
				if ("rescue_place".equals(c.get("op").getAsString())) {
					JsonObject q = c.getAsJsonObject("pos");
					Rails.rescueTag(ctx.player(), new net.minecraft.core.BlockPos(q.get("x").getAsBigDecimal().intValueExact(),
							q.get("y").getAsBigDecimal().intValueExact(), q.get("z").getAsBigDecimal().intValueExact()));
				}
			} catch (RuntimeException ignored) {
				// a malformed tag is logged above and recognises nothing
			}
		});
		// Every join: tell Eve's client which world this is (it cannot see the level name). While STOPPED, tell it again;
		// a reconnect never resumes her.
		ServerPlayConnectionEvents.JOIN.register((handler, sender, server) -> {
			if (!Rails.isEve(handler.player)) return;
			JsonObject w = new JsonObject();
			w.addProperty("level_name", EveSense.levelName());
			w.addProperty("world_id", EveSense.worldId());
			sendControl(handler.player, "world", "join", w);
			if (EveSense.stopped()) sendControl(handler.player, "stop", "rejoin", null);
		});
		// O121: the trusted owner's position every 20 ticks and on join/leave, so the mind's follow keeps going out of sight
		ServerPlayConnectionEvents.JOIN.register((handler, sender, server) -> joined(handler.player));
		ServerPlayConnectionEvents.DISCONNECT.register((handler, server) -> left(handler.player));
		net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents.END_SERVER_TICK.register(s -> ownerPositions(s.getPlayerList().getPlayers(), s.getTickCount()));
		ServerMessageEvents.CHAT_MESSAGE.register((msg, player, type) -> {
			JsonObject p = new JsonObject();
			p.addProperty("sender", player.getGameProfile().name());
			p.addProperty("trusted", isTrusted(player));
			p.addProperty("text", msg.signedContent()); // data only: chat never triggers a command here (CP-45)
			p.add("sender_pos", senderPos(player)); // O111: where the speaker stood, so "come to me" needs no coordinates
			if (isTrusted(player)) ContainerTags.said(player, msg.signedContent()); // O135: a container.tag may echo it
			Link.INSTANCE.send("owner.chat", p, null);
		});

		CommandRegistrationCallback.EVENT.register((dispatcher, ctx, selection) -> {
			LiteralArgumentBuilder<CommandSourceStack> eve = literal("eve");
			for (String c : List.of("stop", "resume", "home", "reload")) eve.then(literal(c).executes(x -> run(x, c)));
			eve.then(literal("mode").then(argument("mode", StringArgumentType.word()).executes(x -> run(x, "mode", StringArgumentType.getString(x, "mode")))));
			for (String c : List.of("approve", "reject"))
				eve.then(literal(c).then(argument("project", StringArgumentType.word()).then(argument("version", StringArgumentType.word())
						.executes(x -> run(x, c, StringArgumentType.getString(x, "project"), StringArgumentType.getString(x, "version"))))));
			eve.then(literal("save_zone").then(argument("zone", StringArgumentType.word()).executes(x -> run(x, "save_zone", StringArgumentType.getString(x, "zone")))));
			eve.then(literal("say").then(argument("text", StringArgumentType.greedyString()).executes(x -> run(x, "say", StringArgumentType.getString(x, "text")))));
			dispatcher.register(eve);
		});
	}

	// ---- rule O17: a Ledger rollback is for block changes only; container transactions are never rolled back with it ----

	private static final Pattern LEDGER = Pattern.compile("(?:^|\\brun\\s+)/?(?:ledger|lg)\\s+(rollback|rb|restore)\\b(.*)", Pattern.CASE_INSENSITIVE);
	/** Ledger action types a rollback may touch: block changes and mob kills (restored correctly in CP-18 R6). */
	static final java.util.Set<String> LEDGER_SAFE = java.util.Set.of("block-break", "block-place", "block-change", "entity-kill");

	/**
	 * A refusal message when a /ledger rollback or restore could include Eve's container (or other unsafe) actions, else
	 * null. Safe = every action: filter is in LEDGER_SAFE, or the source: filter names only others than Eve. A rollback
	 * with no action: filter, or with item-insert/item-remove/item-pick-up/item-drop/entity-change/... over Eve, is refused
	 * (CP-18: container rollbacks duplicate items; inventories, entities and fluids go through backups). Audited.
	 */
	public static String ledgerRefusal(String command, String by) {
		var m = LEDGER.matcher(command.trim());
		if (!m.find()) return null;
		java.util.List<String> actions = new java.util.ArrayList<>(), sources = new java.util.ArrayList<>();
		for (String t : m.group(2).trim().split("\\s+")) {
			int c = t.indexOf(':');
			if (c < 0) continue;
			String k = t.substring(0, c).toLowerCase(java.util.Locale.ROOT), v = t.substring(c + 1);
			for (String one : v.split(",")) {
				if (k.equals("action") || k.equals("a")) actions.add(one.toLowerCase(java.util.Locale.ROOT));
				if (k.equals("source") || k.equals("s")) sources.add(one);
			}
		}
		String eve = EveSense.env().eveAccount;
		boolean coversEve = sources.isEmpty() || sources.stream().anyMatch(x -> x.equalsIgnoreCase(eve) || x.startsWith("@"));
		boolean safe = !actions.isEmpty() && LEDGER_SAFE.containsAll(actions);
		if (!coversEve || safe) return null;
		JsonObject a = new JsonObject();
		a.addProperty("by", by);
		a.addProperty("command", command.length() > 300 ? command.substring(0, 300) : command);
		a.addProperty("rule", "O17");
		EveSense.audit("ledger.refused", a);
		return "eve-sense refused this Ledger " + m.group(1).toLowerCase(java.util.Locale.ROOT) + " (rule O17): it would include " + eve
				+ "'s container or entity actions, which a rollback duplicates or cannot restore. Limit it to block changes, e.g. "
				+ "action:block-break,block-place,block-change,entity-kill, and recover containers/inventories/fluids from a backup.";
	}

	static boolean isTrusted(ServerPlayer p) {
		return p != null && EveSense.env().trusted.contains(p.getGameProfile().name());
	}

	/** Open to every player so untrusted attempts are refused politely and logged; authority comes only from owner.trusted_players. */
	private static int run(CommandContext<CommandSourceStack> ctx, String command, String... args) {
		CommandSourceStack src = ctx.getSource();
		ServerPlayer p = src.getPlayer();
		String sender = p != null ? p.getGameProfile().name() : src.getTextName();
		boolean trusted = isTrusted(p);
		if (!trusted) {
			relay(p, sender, false, command, args, ctx.getInput());
			src.sendFailure(Component.literal("Sorry, only Eve's owner can use /eve."));
			return 0;
		}
		JsonObject[] saved = {null};
		String error = switch (command) {
			case "stop" -> {
				EveSense.setStopped(true);
				Rails.clearGrants("stop"); // the active task is cancelled; its grants end with it
				ServerPlayer eve = EveSense.findEve(src.getServer());
				if (eve != null) sendControl(eve, "stop", sender, null);
				else EveSense.LOG.info("Eve is offline: STOPPED is enforced server-side; no payload sent");
				yield null;
			}
			case "resume" -> {
				EveSense.setStopped(false);
				ServerPlayer eve = EveSense.findEve(src.getServer());
				if (eve != null) sendControl(eve, "resume", sender, null);
				yield null;
			}
			case "mode" -> EveSense.env().modeAllowed.contains(args[0]) ? null : "mode " + args[0] + " is not allowed by the envelope (allowed: " + EveSense.env().modeAllowed + ")";
			case "approve", "reject" -> VERSION.matcher(args[1]).matches() ? null : "version must look like v3";
			case "save_zone" -> { // CP-17: the snapshot is taken here, on the owner's authenticated command, then announced
				try {
					saved[0] = Snapshots.save(src.getServer(), args[0], sender);
					Link.INSTANCE.send("zone.snapshot.saved", saved[0], null);
					yield null;
				} catch (IllegalArgumentException | IllegalStateException e) {
					yield e.getMessage();
				}
			}
			case "reload" -> reload();
			default -> null;
		};
		if (error != null) {
			src.sendFailure(Component.literal(error));
			return 0;
		}
		if (command.equals("reload")) { // before the relay, so the mind re-reads its copy already knowing sense's new hash
			JsonObject c = new JsonObject();
			c.addProperty("version", EveSense.env().version);
			c.addProperty("hash", EveSense.env().hash);
			EveSense.audit("envelope.changed", c);
			Link.INSTANCE.send("envelope.changed", c, null);
		}
		relay(p, sender, true, command, args, ctx.getInput());
		src.sendSuccess(() -> Component.literal(switch (command) {
			case "stop" -> "Eve stopped. Only /eve resume releases her.";
			case "resume" -> "Eve resumed.";
			case "reload" -> "Envelope reloaded: version " + EveSense.env().version + ", sha256 " + EveSense.env().hash.substring(0, 12);
			case "save_zone" -> "Snapshot " + saved[0].get("snapshot_id").getAsString() + " saved (" + saved[0].get("block_count").getAsInt() + " blocks"
					+ (saved[0].get("unloaded_positions").getAsInt() > 0 ? ", " + saved[0].get("unloaded_positions").getAsInt() + " positions not loaded" : "") + ").";
			default -> "Sent to Eve's mind: " + command;
		}), true);
		return 1;
	}

	private static String reload() {
		try {
			EveSense.apply(Envelope.load(EveSense.envelopePath()));
			return null;
		} catch (Exception e) {
			EveSense.LOG.error("envelope reload refused; previous envelope stays in force", e);
			return "Envelope refused, previous one stays in force: " + e.getMessage();
		}
	}

	/** O121 (link v1.11): owner.pos for each online trusted player when tick % 20 == 0. */
	public static void ownerPositions(List<ServerPlayer> players, int tick) {
		if (tick % 20 != 0) return;
		for (ServerPlayer p : players) if (isTrusted(p)) ownerPos(p, true);
	}

	public static void joined(ServerPlayer p) {
		if (isTrusted(p)) ownerPos(p, true);
	}

	/** On leave: online false with the last position. */
	public static void left(ServerPlayer p) {
		if (isTrusted(p)) ownerPos(p, false);
	}

	private static void ownerPos(ServerPlayer p, boolean online) {
		JsonObject o = senderPos(p);
		o.addProperty("sender", p.getGameProfile().name());
		o.addProperty("uuid", p.getUUID().toString());
		o.addProperty("ts", System.currentTimeMillis());
		o.addProperty("online", online);
		Link.INSTANCE.send("owner.pos", o, null);
	}

	/** O111: the sender's block position {dim, x, y, z} when the line was sent. */
	static JsonObject senderPos(ServerPlayer player) {
		JsonObject o = new JsonObject();
		o.addProperty("dim", Rails.dim(player.level()));
		var b = player.blockPosition();
		o.addProperty("x", b.getX());
		o.addProperty("y", b.getY());
		o.addProperty("z", b.getZ());
		return o;
	}

	/** player: the /eve sender, or null for the console or RCON (then no sender_pos). */
	private static void relay(ServerPlayer player, String sender, boolean trusted, String command, String[] args, String raw) {
		JsonObject p = new JsonObject();
		p.addProperty("sender", sender);
		p.addProperty("trusted", trusted);
		p.addProperty("command", command);
		JsonArray a = new JsonArray();
		for (String s : args) a.add(s);
		p.add("args", a);
		p.addProperty("raw", raw);
		p.addProperty("origin", "slash"); // O94q: owner.command comes only from /eve; chat is owner.chat, never a command
		if (player != null) p.add("sender_pos", senderPos(player)); // O111
		if (trusted && player != null) ContainerTags.said(player, raw); // O135
		EveSense.audit("owner.command", p);
		Link.INSTANCE.send("owner.command", p, null);
	}

	/** op stop|resume|world (link v1.1). Sent only when Eve's client declares the receiver; otherwise a loud error. */
	static void sendControl(ServerPlayer eve, String op, String source, JsonObject extra) {
		String nonce = UUID.randomUUID().toString();
		JsonObject j = extra == null ? new JsonObject() : extra.deepCopy();
		j.addProperty("op", op);
		j.addProperty("nonce", nonce);
		j.addProperty("source", source);
		JsonObject a = j.deepCopy();
		if (!ServerPlayNetworking.canSend(eve, ControlPayload.TYPE)) {
			EveSense.LOG.error("Eve's client has no eve:control receiver: op {} NOT delivered{}", op, op.equals("stop") ? " (STOPPED is still enforced server-side)" : "");
			EveSense.audit("eve:control not_sent", a);
			return;
		}
		if (pending.size() > 256) pending.clear(); // ponytail: unacked nonces only matter for latency logging
		pending.put(nonce, System.nanoTime());
		ServerPlayNetworking.send(eve, new ControlPayload(j.toString()));
		EveSense.audit("eve:control sent", a);
	}

	private static void onAck(ServerPlayer from, AckPayload payload) {
		JsonObject a = new JsonObject();
		a.addProperty("from", from.getGameProfile().name());
		try {
			JsonObject j = JsonParser.parseString(payload.json()).getAsJsonObject();
			String nonce = j.get("nonce").getAsString();
			Long sent = Rails.isEve(from) ? pending.remove(nonce) : null;
			a.addProperty("nonce", nonce);
			a.addProperty("op", j.has("op") ? j.get("op").getAsString() : null);
			a.addProperty("client_latency_ms", j.has("latency_ms") ? j.get("latency_ms").getAsDouble() : -1);
			a.addProperty("client_stopped", j.has("stopped") ? j.get("stopped").getAsBoolean() : null);
			if (j.has("duplicate")) a.add("duplicate", j.get("duplicate"));
			if (j.has("error")) a.add("error", j.get("error"));
			a.addProperty("server_rtt_ms", sent == null ? -1 : (System.nanoTime() - sent) / 1_000_000);
			a.addProperty("matched", sent != null);
		} catch (RuntimeException e) {
			a.addProperty("malformed", payload.json().length() > 200 ? payload.json().substring(0, 200) : payload.json());
		}
		EveSense.audit("eve:control_ack", a);
	}
}
