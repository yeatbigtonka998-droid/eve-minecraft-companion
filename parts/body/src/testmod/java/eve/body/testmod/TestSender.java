package eve.body.testmod;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.mojang.brigadier.arguments.StringArgumentType;
import com.mojang.brigadier.context.CommandContext;
import io.netty.buffer.ByteBuf;
import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.command.v2.CommandRegistrationCallback;
import net.fabricmc.fabric.api.entity.event.v1.ServerLivingEntityEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.fabricmc.fabric.api.event.player.PlayerBlockBreakEvents;
import net.minecraft.world.level.block.Blocks;
import net.fabricmc.fabric.api.networking.v1.PayloadTypeRegistry;
import net.fabricmc.fabric.api.networking.v1.ServerPlayNetworking;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.commands.arguments.EntityArgument;
import net.minecraft.network.chat.Component;
import net.minecraft.network.codec.ByteBufCodecs;
import net.minecraft.network.codec.StreamCodec;
import net.minecraft.network.protocol.common.custom.CustomPacketPayload;
import net.minecraft.resources.Identifier;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.player.Input;
import net.minecraft.world.phys.Vec3;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/**
 * DEV ONLY, never shipped: stands in for eve-sense's /eve stop until it exists. /evetest control <op> <players> [mode] sends
 * eve:control, logs the ack round trip, and watches the player for 40 server ticks: when the server sees the client's input go
 * empty and when the player stops moving. Everything goes to evetest-log.jsonl in the server run dir.
 */
public final class TestSender implements ModInitializer {
	// Independent copy of the body's wire format (one UTF-8 JSON string), so this also tests the format, not the shared class.
	record Control(String json) implements CustomPacketPayload {
		static final Type<Control> TYPE = new Type<>(Identifier.fromNamespaceAndPath("eve", "control"));
		static final StreamCodec<ByteBuf, Control> CODEC = ByteBufCodecs.STRING_UTF8.map(Control::new, Control::json);

		@Override
		public Type<Control> type() {
			return TYPE;
		}
	}

	/** eve:rescue from the body's fall-rescue reflex (O20); the dev server only logs it with the server tick and the block there. */
	record Rescue(String json) implements CustomPacketPayload {
		static final Type<Rescue> TYPE = new Type<>(Identifier.fromNamespaceAndPath("eve", "rescue"));
		static final StreamCodec<ByteBuf, Rescue> CODEC = ByteBufCodecs.STRING_UTF8.map(Rescue::new, Rescue::json);

		@Override
		public Type<Rescue> type() {
			return TYPE;
		}
	}

	record Ack(String json) implements CustomPacketPayload {
		static final Type<Ack> TYPE = new Type<>(Identifier.fromNamespaceAndPath("eve", "control_ack"));
		static final StreamCodec<ByteBuf, Ack> CODEC = ByteBufCodecs.STRING_UTF8.map(Ack::new, Ack::json);

		@Override
		public Type<Ack> type() {
			return TYPE;
		}
	}

	record Sent(long nanos, int tick) {}

	static final class Watch {
		final ServerPlayer player;
		final String nonce;
		final int startTick;
		Vec3 last;
		int inputEmptyAt = -1, stillAt = -1, stillRun;
		double driftAfterStill;
		final com.google.gson.JsonArray steps = new com.google.gson.JsonArray();
		final Vec3 startPos;

		Watch(ServerPlayer p, String nonce, int tick) {
			player = p;
			this.nonce = nonce;
			startTick = tick;
			last = startPos = p.position();
		}
	}

	static final Map<String, Sent> SENT = new ConcurrentHashMap<>();
	static final List<Watch> WATCHES = new ArrayList<>();
	static final Path LOG = Path.of("evetest-log.jsonl");

	@Override
	public void onInitialize() {
		PayloadTypeRegistry.clientboundPlay().register(Control.TYPE, Control.CODEC);
		PayloadTypeRegistry.serverboundPlay().register(Ack.TYPE, Ack.CODEC);
		PayloadTypeRegistry.serverboundPlay().register(Rescue.TYPE, Rescue.CODEC);
		ServerPlayNetworking.registerGlobalReceiver(Rescue.TYPE, (r, ctx) -> {
			JsonObject o = new JsonObject();
			o.addProperty("type", "rescue");
			o.addProperty("player", ctx.player().getGameProfile().name());
			o.addProperty("server_tick", ctx.server().getTickCount());
			try {
				JsonObject c = JsonParser.parseString(r.json()).getAsJsonObject();
				o.add("client", c);
				JsonObject pos = c.getAsJsonObject("pos");
				var bp = new net.minecraft.core.BlockPos(pos.get("x").getAsInt(), pos.get("y").getAsInt(), pos.get("z").getAsInt());
				o.addProperty("block_now", ctx.player().level().getBlockState(bp).toString());
			} catch (Exception e) {
				o.addProperty("bad_rescue", r.json());
			}
			log(o);
		});
		ServerPlayNetworking.registerGlobalReceiver(Ack.TYPE, (ack, ctx) -> {
			long now = System.nanoTime();
			JsonObject o = new JsonObject();
			o.addProperty("type", "ack");
			o.addProperty("player", ctx.player().getGameProfile().name());
			o.addProperty("server_tick", ctx.server().getTickCount());
			try {
				JsonObject a = JsonParser.parseString(ack.json()).getAsJsonObject();
				o.add("client", a);
				Sent s = a.has("nonce") && !a.get("nonce").isJsonNull() ? SENT.get(a.get("nonce").getAsString()) : null;
				if (s != null) {
					o.addProperty("rtt_ms", (now - s.nanos()) / 1e6);
					o.addProperty("ticks_since_send", ctx.server().getTickCount() - s.tick());
				}
			} catch (Exception e) {
				o.addProperty("bad_ack", ack.json());
			}
			log(o);
		});
		CommandRegistrationCallback.EVENT.register((dispatcher, ctx, env) -> dispatcher.register(Commands.literal("evetest")
				.requires(Commands.hasPermission(Commands.LEVEL_GAMEMASTERS))
				.then(Commands.literal("control")
						.then(Commands.argument("op", StringArgumentType.word())
								.then(Commands.argument("targets", EntityArgument.players())
										.executes(c -> send(c, null))
										.then(Commands.argument("mode", StringArgumentType.word())
												.executes(c -> send(c, StringArgumentType.getString(c, "mode")))))))));
		// O98j (dev only, never in a lane jar): the server's own view of every player hurt, for the shield misses of dev a96-a98
		// (client: blocking=True, use_ticks ~25, source < 20 deg off her view, yet full damage). Before the damage: the server's
		// isBlocking, use ticks and item, its yaw / head yaw and the source's horizontal angle off its view; after: blocked, taken.
		ServerLivingEntityEvents.ALLOW_DAMAGE.register((e, src, amount) -> {
			if (!(e instanceof ServerPlayer p)) return true;
			JsonObject o = new JsonObject();
			o.addProperty("type", "player_hurt");
			o.addProperty("ms", System.currentTimeMillis());
			o.addProperty("player", p.getGameProfile().name());
			o.addProperty("amount", amount);
			o.addProperty("cause", src.type().msgId());
			o.addProperty("blocking", p.isBlocking());
			o.addProperty("using", p.isUsingItem());
			o.addProperty("use_ticks", p.getTicksUsingItem());
			o.addProperty("use_item", net.minecraft.core.registries.BuiltInRegistries.ITEM.getKey(p.getUseItem().getItem()).toString());
			o.addProperty("yaw", p.getYRot());
			o.addProperty("head_yaw", p.getYHeadRot());
			Vec3 sp = src.getSourcePosition();
			if (sp != null) {
				Vec3 to = sp.subtract(p.position()).multiply(1, 0, 1), view = p.getViewVector(1f).multiply(1, 0, 1);
				double ang = to.lengthSqr() < 1e-6 || view.lengthSqr() < 1e-6 ? 0
						: Math.toDegrees(Math.acos(Math.max(-1, Math.min(1, to.normalize().dot(view.normalize())))));
				o.addProperty("source_angle_deg", Math.round(ang * 10) / 10.0);
				o.addProperty("source", sp.x + "," + sp.y + "," + sp.z);
			}
			log(o);
			return true;
		});
		ServerLivingEntityEvents.AFTER_DAMAGE.register((e, src, base, taken, blocked) -> {
			if (!(e instanceof ServerPlayer p)) return;
			JsonObject o = new JsonObject();
			o.addProperty("type", "player_hurt_after");
			o.addProperty("ms", System.currentTimeMillis());
			o.addProperty("player", p.getGameProfile().name());
			o.addProperty("base", base);
			o.addProperty("taken", taken);
			o.addProperty("blocked", blocked);
			log(o);
		});
		// Stand-in for sense's break rail: sponge can never be broken; the server cancels and restores it, as sense does.
		PlayerBlockBreakEvents.BEFORE.register((world, player, pos, state, be) -> {
			if (!state.is(Blocks.SPONGE)) return true;
			JsonObject o = new JsonObject();
			o.addProperty("type", "break_denied");
			o.addProperty("player", player.getGameProfile().name());
			o.addProperty("pos", pos.toShortString());
			log(o);
			return false;
		});
		ServerTickEvents.END_SERVER_TICK.register(server -> {
			int now = server.getTickCount();
			WATCHES.removeIf(w -> {
				int dt = now - w.startTick;
				Vec3 pos = w.player.position();
				double step = Math.hypot(pos.x - w.last.x, pos.z - w.last.z);
				w.last = pos;
				w.steps.add(Math.round(step * 10000) / 10000.0);
				if (w.inputEmptyAt < 0 && w.player.getLastClientInput().equals(Input.EMPTY)) w.inputEmptyAt = dt;
				// still = 5 consecutive server ticks without movement (a single empty tick happens while walking when no packet lands)
				if (w.stillAt < 0) {
					w.stillRun = step < 1e-3 && dt > 0 ? w.stillRun + 1 : 0;
					if (w.stillRun == 5) w.stillAt = dt - 4;
				} else {
					w.driftAfterStill += step;
				}
				if (dt < 40 && !w.player.isRemoved()) return false;
				JsonObject o = new JsonObject();
				o.addProperty("type", "watch");
				o.addProperty("nonce", w.nonce);
				o.addProperty("player", w.player.getGameProfile().name());
				o.addProperty("server_input_empty_after_ticks", w.inputEmptyAt);
				o.addProperty("still_after_ticks", w.stillAt);
				o.addProperty("travel_after_send_blocks", Math.hypot(pos.x - w.startPos.x, pos.z - w.startPos.z));
				o.addProperty("drift_after_still_blocks", w.driftAfterStill);
				o.add("steps_per_tick", w.steps);
				log(o);
				return true;
			});
		});
	}

	private static int send(CommandContext<CommandSourceStack> c, String mode) throws com.mojang.brigadier.exceptions.CommandSyntaxException {
		String op = StringArgumentType.getString(c, "op");
		int n = 0;
		for (ServerPlayer p : EntityArgument.getPlayers(c, "targets")) {
			String nonce = UUID.randomUUID().toString();
			JsonObject j = new JsonObject();
			j.addProperty("op", op);
			j.addProperty("nonce", nonce);
			j.addProperty("source", "evetest");
			if (op.equals("world") && mode != null) { // dev stand-in for sense's world announcement: the extra word is the level name
				j.addProperty("level_name", mode);
				j.addProperty("world_id", mode + ":evetest");
			} else if (mode != null) {
				j.addProperty("mode", mode);
			}
			JsonObject o = new JsonObject();
			o.addProperty("type", "send");
			o.addProperty("player", p.getGameProfile().name());
			o.addProperty("server_tick", c.getSource().getServer().getTickCount());
			o.add("payload", j);
			if (!ServerPlayNetworking.canSend(p, Control.TYPE)) {
				o.addProperty("error", "client cannot receive eve:control (eve-body missing?)");
				log(o);
				c.getSource().sendFailure(Component.literal(p.getGameProfile().name() + " cannot receive eve:control"));
				continue;
			}
			int tick = c.getSource().getServer().getTickCount();
			SENT.put(nonce, new Sent(System.nanoTime(), tick));
			ServerPlayNetworking.send(p, new Control(j.toString()));
			if (op.equals("stop")) WATCHES.add(new Watch(p, nonce, tick));
			log(o);
			n++;
		}
		int sent = n;
		c.getSource().sendSuccess(() -> Component.literal("eve:control " + op + " sent to " + sent), false);
		return n;
	}

	static synchronized void log(JsonObject o) {
		o.addProperty("ts", System.currentTimeMillis());
		try {
			Files.writeString(LOG, o + "\n", StandardOpenOption.CREATE, StandardOpenOption.APPEND);
		} catch (Exception e) {
			System.err.println("evetest log failed: " + e);
		}
		System.out.println("[evetest] " + o);
	}
}
