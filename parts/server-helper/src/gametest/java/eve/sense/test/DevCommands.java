package eve.sense.test;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.mojang.authlib.GameProfile;
import com.mojang.brigadier.arguments.StringArgumentType;
import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.command.v2.CommandRegistrationCallback;
import net.fabricmc.fabric.api.entity.FakePlayer;
import net.fabricmc.fabric.api.networking.v1.PayloadTypeRegistry;
import net.fabricmc.fabric.api.networking.v1.ServerPlayNetworking;
import net.minecraft.network.chat.Component;
import net.minecraft.network.codec.ByteBufCodecs;
import net.minecraft.network.codec.StreamCodec;
import net.minecraft.network.protocol.common.custom.CustomPacketPayload;
import net.minecraft.resources.Identifier;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import org.slf4j.LoggerFactory;

import java.util.UUID;

import static net.minecraft.commands.Commands.argument;
import static net.minecraft.commands.Commands.literal;

/**
 * Dev-only (gametest source set; loaded by runGameTest, or runServer/runClient with -Peve.devtools=true; never in the
 * shipped jar). Console/RCON only:
 *   /evetest as <name> <command>    run a command as a FakePlayer with that name (scripted owner)
 *   /evetest drive <player> <json>  tell that player's client test driver to send real packets (DevClientDriver)
 *   /evetest menu <player>          print the player's open menu (server truth for container tests)
 *   /evetest linkfuzz <case|all>    send malformed frames once on the live /sense link (CP-11; see LinkFuzz)
 *   /evetest query <blocks|resources|diff|repair> <json>   run eve-sense's query.blocks/query.resources/zone.diff/zone.repair here, print the JSON
 */
public class DevCommands implements ModInitializer {
	public record Drive(String json) implements CustomPacketPayload {
		public static final Type<Drive> TYPE = new Type<>(Identifier.fromNamespaceAndPath("evetest", "drive"));
		public static final StreamCodec<io.netty.buffer.ByteBuf, Drive> CODEC = ByteBufCodecs.STRING_UTF8.map(Drive::new, Drive::json);

		@Override
		public Type<Drive> type() { return TYPE; }
	}

	@Override
	public void onInitialize() {
		// Own logger: this class also ships in eve-sense-devtools, which runs on Eve's client where eve-sense is absent.
		LoggerFactory.getLogger("eve-devtools").warn("eve-sense DEV TOOLS loaded (/evetest, client driver): disposable test worlds only, remove after the run");
		PayloadTypeRegistry.clientboundPlay().register(Drive.TYPE, Drive.CODEC);
		CommandRegistrationCallback.EVENT.register((d, ctx, sel) -> d.register(literal("evetest")
				.requires(src -> src.getPlayer() == null)
				.then(literal("as").then(argument("name", StringArgumentType.word()).then(argument("command", StringArgumentType.greedyString())
						.executes(c -> {
							String name = StringArgumentType.getString(c, "name");
							ServerPlayer p = FakePlayer.get(c.getSource().getServer().overworld(), new GameProfile(UUID.nameUUIDFromBytes(("dev:" + name).getBytes()), name));
							c.getSource().getServer().getCommands().performPrefixedCommand(p.createCommandSourceStack(), StringArgumentType.getString(c, "command"));
							return 1;
						}))))
				.then(literal("drive").then(argument("player", StringArgumentType.word()).then(argument("json", StringArgumentType.greedyString())
						.executes(c -> {
							ServerPlayer p = c.getSource().getServer().getPlayerList().getPlayerByName(StringArgumentType.getString(c, "player"));
							if (p == null || !ServerPlayNetworking.canSend(p, Drive.TYPE)) {
								c.getSource().sendFailure(Component.literal("evetest drive: player offline or no driver"));
								return 0;
							}
							ServerPlayNetworking.send(p, new Drive(StringArgumentType.getString(c, "json")));
							c.getSource().sendSuccess(() -> Component.literal("evetest drive sent"), false);
							return 1;
						}))))
				.then(literal("query").then(argument("type", StringArgumentType.word()).then(argument("json", StringArgumentType.greedyString())
					.executes(c -> {
						String out;
						try { // reflection: this jar must not link against eve-sense (it also loads on Eve's client)
							JsonObject q = JsonParser.parseString(StringArgumentType.getString(c, "json")).getAsJsonObject();
							String type = StringArgumentType.getString(c, "type"); // blocks | resources (Query), diff | repair (Snapshots)
							out = Class.forName(type.equals("diff") || type.equals("repair") ? "eve.sense.Snapshots" : "eve.sense.Query").getMethod(type, MinecraftServer.class, JsonObject.class)
									.invoke(null, c.getSource().getServer(), q).toString();
						} catch (java.lang.reflect.InvocationTargetException e) { // what the link answers: error{bad_payload}
							JsonObject err = new JsonObject();
							err.addProperty("code", "bad_payload");
							err.addProperty("message", String.valueOf(e.getCause().getMessage()));
							out = err.toString();
						} catch (ReflectiveOperationException | RuntimeException e) {
							JsonObject err = new JsonObject();
							err.addProperty("code", "evetest_failed");
							err.addProperty("message", e.toString());
							out = err.toString();
						}
						String r = out;
						LoggerFactory.getLogger("eve-devtools").info("[evetest] query -> {}", r);
						c.getSource().sendSuccess(() -> Component.literal(r), false);
						return 1;
					}))))
				.then(literal("linkfuzz").then(argument("case", StringArgumentType.word()).executes(c -> {
					String r;
					try {
						r = LinkFuzz.run(StringArgumentType.getString(c, "case"));
					} catch (ReflectiveOperationException | RuntimeException e) {
						r = "linkfuzz failed: " + e;
					}
					String out = r;
					c.getSource().sendSuccess(() -> Component.literal(out), false);
					return 1;
				})))
				.then(literal("menu").then(argument("player", StringArgumentType.word()).executes(c -> {
					ServerPlayer p = c.getSource().getServer().getPlayerList().getPlayerByName(StringArgumentType.getString(c, "player"));
					String m = p == null ? "offline" : p.containerMenu == p.inventoryMenu ? "inventory" : p.containerMenu.getClass().getSimpleName();
					c.getSource().sendSuccess(() -> Component.literal("evetest menu " + m), false);
					return 1;
				})))));
	}
}
