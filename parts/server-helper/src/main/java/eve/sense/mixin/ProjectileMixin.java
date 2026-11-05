package eve.sense.mixin;

import com.llamalad7.mixinextras.injector.wrapoperation.Operation;
import com.llamalad7.mixinextras.injector.wrapoperation.WrapOperation;
import eve.sense.Rails;
import net.minecraft.world.entity.projectile.Projectile;
import net.minecraft.world.entity.projectile.ProjectileDeflection;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * Eve's projectiles. hitTargetOrDeflectSelf is the one dispatch every projectile type uses (arrows incl. piercing, tridents,
 * thrown items and potions, fireworks, fishing hooks, wind charges and fireballs; checked on the 26.3 bytecode), and
 * onHitBlock is where a block reacts (TNT, bells, targets, pots, dripstone, campfires, candles).
 */
@Mixin(Projectile.class)
abstract class ProjectileMixin {
	@Inject(method = "hitTargetOrDeflectSelf", at = @At("HEAD"), cancellable = true)
	private void eve$gateHit(HitResult hit, CallbackInfoReturnable<ProjectileDeflection> cir) {
		if (!Rails.allowProjectileHit((Projectile) (Object) this, hit)) cir.setReturnValue(ProjectileDeflection.NONE);
	}

	@WrapOperation(method = "onHitBlock", at = @At(value = "INVOKE", target = "Lnet/minecraft/world/level/block/state/BlockState;onProjectileHit(Lnet/minecraft/world/level/Level;Lnet/minecraft/world/level/block/state/BlockState;Lnet/minecraft/world/phys/BlockHitResult;Lnet/minecraft/world/entity/projectile/Projectile;)V"))
	private void eve$gateBlockReaction(BlockState state, Level level, BlockState same, BlockHitResult hit, Projectile self, Operation<Void> original) {
		if (Rails.allowProjectileBlock(self, hit)) original.call(state, level, same, hit, self);
	}
}
