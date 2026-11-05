package eve.sense.mixin;

import eve.sense.Rails;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.inventory.AbstractContainerMenu;
import net.minecraft.world.inventory.ContainerInput;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/** Every slot transfer (clicked covers pickup, shift-move, swap, throw, drag) and menu button is re-checked server-side. */
@Mixin(AbstractContainerMenu.class)
abstract class ContainerMenuMixin {
	@Inject(method = "clicked", at = @At("HEAD"), cancellable = true)
	private void eve$gateClick(int slot, int button, ContainerInput input, Player player, CallbackInfo ci) {
		if (!Rails.allowMenuClick((AbstractContainerMenu) (Object) this, player)) ci.cancel();
	}

	@Inject(method = "clickMenuButton", at = @At("HEAD"), cancellable = true)
	private void eve$gateButton(Player player, int id, CallbackInfoReturnable<Boolean> cir) {
		if (!Rails.allowMenuClick((AbstractContainerMenu) (Object) this, player)) cir.setReturnValue(false);
	}
}
