package eve.sense.mixin;

import eve.sense.HitLog;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.damagesource.DamageSource;
import net.minecraft.world.entity.LivingEntity;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/** O174 diagnostic: observes Eve's damage path and item use (HitLog); changes nothing. */
@Mixin(LivingEntity.class)
abstract class LivingEntityMixin {
	@Inject(method = "hurtServer", at = @At("HEAD"))
	private void eve$hitBegin(ServerLevel level, DamageSource src, float amount, CallbackInfoReturnable<Boolean> cir) {
		HitLog.begin((LivingEntity) (Object) this, src, amount);
	}

	@Inject(method = "hurtServer", at = @At("RETURN"))
	private void eve$hitEnd(ServerLevel level, DamageSource src, float amount, CallbackInfoReturnable<Boolean> cir) {
		HitLog.end((LivingEntity) (Object) this, cir.getReturnValueZ());
	}

	@Inject(method = "applyItemBlocking", at = @At("RETURN"))
	private void eve$blocked(ServerLevel level, DamageSource src, float amount, CallbackInfoReturnable<Float> cir) {
		HitLog.blocked((LivingEntity) (Object) this, cir.getReturnValueF());
	}

	@Inject(method = "getDamageAfterArmorAbsorb", at = @At("RETURN"))
	private void eve$armor(DamageSource src, float amount, CallbackInfoReturnable<Float> cir) {
		HitLog.armor((LivingEntity) (Object) this, amount, cir.getReturnValueF());
	}

	@Inject(method = "startUsingItem", at = @At("TAIL"))
	private void eve$useStart(InteractionHand hand, CallbackInfo ci) {
		HitLog.use((LivingEntity) (Object) this, "server_start_using", hand);
	}

	@Inject(method = "releaseUsingItem", at = @At("HEAD"))
	private void eve$useReleased(CallbackInfo ci) {
		HitLog.released((LivingEntity) (Object) this);
	}

	@Inject(method = "completeUsingItem", at = @At("HEAD"))
	private void eve$useFinished(CallbackInfo ci) {
		HitLog.finished((LivingEntity) (Object) this);
	}

	@Inject(method = "stopUsingItem", at = @At("HEAD"))
	private void eve$useStop(CallbackInfo ci) {
		LivingEntity self = (LivingEntity) (Object) this;
		if (self.isUsingItem()) HitLog.use(self, "server_stop_using", null);
	}
}
