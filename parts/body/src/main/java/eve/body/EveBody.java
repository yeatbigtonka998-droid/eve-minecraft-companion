package eve.body;

import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientLifecycleEvents;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.message.v1.ClientReceiveMessageEvents;
import net.fabricmc.fabric.api.client.screen.v1.ScreenEvents;
import net.fabricmc.fabric.api.event.client.player.ClientPlayerBlockBreakEvents;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayConnectionEvents;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayNetworking;
import net.fabricmc.fabric.api.networking.v1.PayloadTypeRegistry;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.InactivityFpsLimit;
import net.minecraft.client.gui.screens.ConnectScreen;
import net.minecraft.client.gui.screens.TitleScreen;
import net.minecraft.client.multiplayer.ServerData;
import net.minecraft.client.multiplayer.resolver.ServerAddress;

import java.nio.file.Files;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public final class EveBody implements ClientModInitializer {
	static final Logger LOG = LoggerFactory.getLogger("eve-body");
	static final String MOD_VERSION = FabricLoader.getInstance().getModContainer("eve-body").orElseThrow().getMetadata().getVersion().getFriendlyString();
	/** Client tick counter (increments every client tick, in or out of a world). */
	static volatile long clientTick;
	/** World game time as the client sees it, -1 outside a world; used for envelope tick and freshness.tick. */
	static volatile long gameTime = -1;
	/** Set by ConnectionMixin on every packet from the server; state.freshness.age_ms is measured from it. */
	public static volatile long lastServerPacketNanos;
	private static long tickStartNs;
	private static boolean autojoinTried;

	@Override
	public void onInitializeClient() {
		Registry.load();
		Providers.load(); // skills from other mods (entrypoint "eve-body:skills"); must follow the registry
		Arbiter.loadPersisted();
		PayloadTypeRegistry.clientboundPlay().register(Payloads.Control.TYPE, Payloads.Control.CODEC);
		PayloadTypeRegistry.serverboundPlay().register(Payloads.Ack.TYPE, Payloads.Ack.CODEC);
		PayloadTypeRegistry.serverboundPlay().register(Payloads.Rescue.TYPE, Payloads.Rescue.CODEC);
		ClientPlayNetworking.registerGlobalReceiver(Payloads.Control.TYPE, (payload, ctx) -> Arbiter.onServerControl(ctx.client(), payload.json()));
		ClientLifecycleEvents.CLIENT_STARTED.register(mc -> {
			// Ticks run inside frames: with the default AFK frame limit (Eve's client is always AFK) frames drop to ~10/s, ticks come
			// in bursts and a stop waits up to ~100 ms (CP-12 attempt 5). Eve's window must also never be minimized.
			mc.options.inactivityFpsLimit().set(InactivityFpsLimit.MINIMIZED);
			mc.options.pauseOnLostFocus = false;
			if (Skills.baritone()) BaritoneWalk.configure();
			StallWatch.start(Thread.currentThread()); // O102af: the render thread's stack and GC on a client stall of 1.5 s+ (logging only)
		});
		// Baritone leaves a non-daemon worker alive, so after the game's main thread ends the JVM cannot exit and vanilla's
		// post-main watchdog crashes it ~18 s later (45L, every quit). Once the game is fully done, exit the JVM normally.
		ClientLifecycleEvents.CLIENT_STOPPING.register(mc -> {
			Thread main = Thread.currentThread();
			Thread exit = new Thread(() -> {
				try {
					main.join();
				} catch (InterruptedException e) {
					return;
				}
				System.exit(0);
			}, "eve-exit-after-main");
			exit.setDaemon(true);
			exit.start();
		});
		// Unattended restarts on the 45L: join the server named in config/eve-autojoin.txt once per game start, from the title
		// screen, no retries (a failed join stays on vanilla's disconnect screen for a person or a restart to handle).
		ScreenEvents.AFTER_INIT.register((mc, screen, w, h) -> {
			if (autojoinTried || !(screen instanceof TitleScreen)) return;
			autojoinTried = true;
			var f = FabricLoader.getInstance().getConfigDir().resolve("eve-autojoin.txt");
			try {
				if (!Files.exists(f)) return;
				String addr = Files.readString(f).trim();
				if (!ServerAddress.isValidAddress(addr)) {
					LOG.warn("eve-body: config/eve-autojoin.txt does not hold a server address; not joining");
					return;
				}
				LOG.info("eve-body: auto-joining {} (config/eve-autojoin.txt, once per start)", addr);
				mc.execute(() -> ConnectScreen.startConnecting(screen, mc, ServerAddress.parseString(addr),
						new ServerData("Eve", addr, ServerData.Type.OTHER), false, null));
			} catch (Exception e) {
				LOG.warn("eve-body: auto-join skipped: {}", e.toString());
			}
		});
		ClientTickEvents.START_CLIENT_TICK.register(mc -> {
			tickStartNs = System.nanoTime();
			clientTick++;
			gameTime = mc.level == null ? -1 : mc.level.getGameTime();
			Arbiter.tick(mc);
		});
		// chat_say's "sent" evidence: the server echoed our line back (signed chat, or a system line on offline servers)
		ClientReceiveMessageEvents.CHAT.register((msg, signed, sender, params, ts) -> Interact.ChatSay.lastHeard = msg.getString());
		ClientReceiveMessageEvents.GAME.register((msg, overlay) -> Interact.ChatSay.lastHeard = msg.getString());
		ClientPlayerBlockBreakEvents.AFTER.register((level, player, pos, state) -> Interact.MineBlock.onClientBreak(pos));
		ClientTickEvents.END_CLIENT_TICK.register(mc -> Telemetry.tick(mc, tickStartNs));
		ClientPlayConnectionEvents.JOIN.register((handler, sender, mc) -> Link.open(mc));
		ClientPlayConnectionEvents.DISCONNECT.register((handler, mc) -> {
			Arbiter.onGameDisconnect(mc);
			Link.close("game_disconnect");
		});
		LOG.info("eve-body {} ready: registry {} hash {}, caps {}, stopped={}", MOD_VERSION, Registry.root.get("registry_version").getAsString(),
				Registry.hash, Registry.IMPLEMENTED, Arbiter.stopped);
	}
}
