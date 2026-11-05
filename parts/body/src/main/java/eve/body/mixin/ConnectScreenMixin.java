package eve.body.mixin;

import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.ConnectScreen;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.multiplayer.ServerData;
import net.minecraft.client.multiplayer.TransferState;
import net.minecraft.client.multiplayer.resolver.ServerAddress;
import org.slf4j.LoggerFactory;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import java.util.Set;

/**
 * A dev client (any Loom run: body's or sense's fixture) may join loopback servers only. Every join (quick-play, the server
 * list, Direct Connect, auto-join, a server transfer) goes through startConnecting, so a stray click can never reach the 45L
 * from a Shark fixture (sense's CP-11 linkfuzz attempt 2). The production client on the 45L is not a dev environment.
 */
@Mixin(ConnectScreen.class)
abstract class ConnectScreenMixin {
	private static final Set<String> LOOPBACK = Set.of("localhost", "127.0.0.1", "::1", "[::1]");

	@Inject(method = "startConnecting", at = @At("HEAD"), cancellable = true)
	private static void eve$loopbackOnlyInDev(Screen parent, Minecraft mc, ServerAddress addr, ServerData data, boolean quickPlay,
			TransferState transfer, CallbackInfo ci) {
		if (!FabricLoader.getInstance().isDevelopmentEnvironment() || LOOPBACK.contains(addr.getHost().toLowerCase())) return;
		LoggerFactory.getLogger("eve-body").error("eve-body: dev client refused to join {}:{} (dev fixtures join loopback only)",
				addr.getHost(), addr.getPort());
		ci.cancel();
	}
}
