package eve.sense.mixin;

import eve.sense.Rails;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.context.UseOnContext;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/** Item use on a block after the block's own use passed (hoe, axe, shovel, bone meal, spawn egg...). */
@Mixin(ItemStack.class)
abstract class ItemStackMixin {
	@Inject(method = "useOn", at = @At("HEAD"), cancellable = true)
	private void eve$gateUseOn(UseOnContext ctx, CallbackInfoReturnable<InteractionResult> cir) {
		if (!Rails.allowUseOn(ctx)) cir.setReturnValue(InteractionResult.FAIL);
	}
}
