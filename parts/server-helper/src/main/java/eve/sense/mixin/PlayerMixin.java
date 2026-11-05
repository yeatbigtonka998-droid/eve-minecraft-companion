package eve.sense.mixin;

import com.llamalad7.mixinextras.injector.wrapoperation.Operation;
import com.llamalad7.mixinextras.injector.wrapoperation.WrapOperation;
import eve.sense.Rails;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.level.Level;
import net.minecraft.world.phys.AABB;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;

import java.util.List;

/** Sweep-attack secondaries: the candidates are filtered before vanilla hurts, knocks back or enchants any of them. */
@Mixin(Player.class)
abstract class PlayerMixin {
	@SuppressWarnings({"rawtypes", "unchecked"})
	@WrapOperation(method = "doSweepAttack", at = @At(value = "INVOKE", target = "Lnet/minecraft/world/level/Level;getEntitiesOfClass(Ljava/lang/Class;Lnet/minecraft/world/phys/AABB;)Ljava/util/List;"))
	private List eve$filterSweep(Level level, Class cls, AABB box, Operation<List> original) {
		return Rails.sweepTargets((Player) (Object) this, original.call(level, cls, box));
	}
}
