package eve.sense.mixin;

import eve.sense.Storage;
import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.entity.BlockEntity;
import net.minecraft.world.level.block.state.BlockState;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/** The static setChanged is what every container (and furnace ticking) calls when contents change: feeds storage.index partials. */
@Mixin(BlockEntity.class)
abstract class BlockEntityMixin {
	@Inject(method = "setChanged(Lnet/minecraft/world/level/Level;Lnet/minecraft/core/BlockPos;Lnet/minecraft/world/level/block/state/BlockState;)V", at = @At("HEAD"))
	private static void eve$changed(Level level, BlockPos pos, BlockState state, CallbackInfo ci) {
		if (level instanceof ServerLevel sl) Storage.changed(sl, pos);
	}
}
