package eve.sense.mixin;

import net.minecraft.world.inventory.ContainerData;
import net.minecraft.world.level.block.entity.AbstractFurnaceBlockEntity;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Accessor;

/** A furnace's burn and cook counters (DATA_LIT_TIME, DATA_LIT_DURATION, DATA_COOKING_PROGRESS, DATA_COOKING_TOTAL_TIME). */
@Mixin(AbstractFurnaceBlockEntity.class)
public interface FurnaceAccessor {
	@Accessor("dataAccess")
	ContainerData eve$data();
}
