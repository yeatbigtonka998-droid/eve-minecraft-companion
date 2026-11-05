package eve.sense.mixin;

import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.Item;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.level.Level;
import net.minecraft.world.phys.BlockHitResult;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Invoker;

/** The exact raycast BucketItem.use makes, so the rail checks the block the bucket will really change. */
@Mixin(Item.class)
public interface ItemInvoker {
	@Invoker("getPlayerPOVHitResult")
	static BlockHitResult eve$pov(Level level, Player player, ClipContext.Fluid fluid) {
		throw new AssertionError();
	}
}
