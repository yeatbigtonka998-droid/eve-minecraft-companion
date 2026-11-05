package eve.body.mixin;

import eve.body.Hands;
import net.minecraft.client.Minecraft;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * The arbiter owns attack and use like it owns movement: vanilla's mouse/keyboard-driven clicks are cancelled. Without this,
 * handleKeybinds' continueAttack(false) would stopDestroyBlock() under every mining action, every tick.
 */
@Mixin(Minecraft.class)
abstract class MinecraftMixin {
	@Inject(method = "startAttack", at = @At("HEAD"), cancellable = true)
	private void eve$startAttack(CallbackInfoReturnable<Boolean> cir) {
		if (!Hands.ours && !Hands.baritoneMayBreak) cir.setReturnValue(false);
	}

	@Inject(method = "continueAttack", at = @At("HEAD"), cancellable = true)
	private void eve$continueAttack(boolean leftClick, CallbackInfo ci) {
		if (Hands.ours || Hands.baritoneMayBreak) return; // a walk_to with allow_break and budget left breaks through Baritone's clicks
		// keep vanilla's one side effect of an idle button: it clears the 10000-tick missTime a screen leaves behind
		if (!leftClick) ((Minecraft) (Object) this).missTime = 0;
		ci.cancel();
	}

	@Inject(method = "startUseItem", at = @At("HEAD"), cancellable = true)
	private void eve$startUseItem(CallbackInfo ci) {
		if (!Hands.ours && !Hands.baritoneMayPlace) ci.cancel();
	}
}
