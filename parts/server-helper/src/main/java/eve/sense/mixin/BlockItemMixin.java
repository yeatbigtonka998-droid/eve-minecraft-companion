package eve.sense.mixin;

import com.llamalad7.mixinextras.injector.wrapoperation.Operation;
import com.llamalad7.mixinextras.injector.wrapoperation.WrapOperation;
import eve.sense.Rails;
import eve.sense.Snapshots;
import net.minecraft.world.item.BlockItem;
import net.minecraft.world.item.context.BlockPlaceContext;
import net.minecraft.world.level.block.state.BlockState;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;

/** Wraps the placeBlock call inside BlockItem.place: the context is final there and overrides (doors, beds) have not run yet. */
@Mixin(BlockItem.class)
abstract class BlockItemMixin {
	@WrapOperation(method = "place", at = @At(value = "INVOKE", target = "Lnet/minecraft/world/item/BlockItem;placeBlock(Lnet/minecraft/world/item/context/BlockPlaceContext;Lnet/minecraft/world/level/block/state/BlockState;)Z"))
	private boolean eve$gatePlace(BlockItem self, BlockPlaceContext ctx, BlockState state, Operation<Boolean> original) {
		if (!Rails.allowPlace(ctx, state)) return false; // place() then returns FAIL and nothing changed
		boolean ok = original.call(self, ctx, state);
		if (ok) {
			Rails.placed(ctx);
			Snapshots.edited(ctx.getLevel(), ctx.getClickedPos(), ctx.getPlayer()); // CP-17 owner edits / actors
			eve.sense.PlacedBlocks.placed(ctx.getLevel(), ctx.getClickedPos(), ctx.getPlayer()); // H-trees: a placed log is no tree
			eve.sense.ContainerTags.placed(ctx.getLevel(), ctx.getClickedPos(), ctx.getPlayer()); // O135: who placed a container
			eve.sense.OwnerActivity.placed(ctx.getLevel(), ctx.getClickedPos(), ctx.getPlayer()); // S97: what the owner is doing
			eve.sense.Storage.blockChanged(ctx.getLevel(), ctx.getClickedPos(), null); // link v1.5: workstations in the index
		}
		return ok;
	}
}
