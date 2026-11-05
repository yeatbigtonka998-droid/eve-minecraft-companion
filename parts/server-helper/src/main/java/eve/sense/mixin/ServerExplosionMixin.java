package eve.sense.mixin;

import com.llamalad7.mixinextras.injector.wrapoperation.Operation;
import com.llamalad7.mixinextras.injector.wrapoperation.WrapOperation;
import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.level.ServerExplosion;
import net.minecraft.world.phys.Vec3;
import org.spongepowered.asm.mixin.Final;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.Unique;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

import java.util.List;

/** O295 world.explosion. explode() returns every position its rays reached, air included, even for a blast that breaks nothing, so the blocks really gone are counted here. */
@Mixin(ServerExplosion.class)
abstract class ServerExplosionMixin {
	@Shadow @Final private ServerLevel level;
	@Shadow @Final private Vec3 center;
	@Shadow @Final private Entity source;
	@Shadow @Final private float radius;
	@Unique private int eve$broken;

	@WrapOperation(method = "explode", at = @At(value = "INVOKE", target = "Lnet/minecraft/world/level/ServerExplosion;interactWithBlocks(Ljava/util/List;)V"))
	private void eve$countBroken(ServerExplosion self, List<BlockPos> blocks, Operation<Void> original) {
		List<BlockPos> solid = blocks.stream().filter(p -> !level.getBlockState(p).isAir()).toList();
		original.call(self, blocks);
		eve$broken = (int) solid.stream().filter(p -> level.getBlockState(p).isAir()).count();
	}

	@Inject(method = "explode", at = @At("RETURN"))
	private void eve$report(CallbackInfoReturnable<Integer> cir) {
		eve.sense.Explosions.report(level, center, source, radius, eve$broken);
	}
}
