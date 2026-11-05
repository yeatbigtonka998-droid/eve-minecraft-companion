package eve.sense.mixin;

import eve.sense.RconGuard;
import net.minecraft.server.dedicated.DedicatedServer;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/** I28a: runCommand is the RCON entry (RconClient -> ServerInterface.runCommand); the console and players never pass here. */
@Mixin(DedicatedServer.class)
abstract class DedicatedServerMixin {
	@Inject(method = "runCommand", at = @At("HEAD"), cancellable = true)
	private void eve$rconWriteGuard(String command, CallbackInfoReturnable<String> cir) {
		String refusal = RconGuard.refusal(command);
		if (refusal != null) cir.setReturnValue(refusal);
	}
}
