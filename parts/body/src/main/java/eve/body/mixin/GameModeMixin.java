package eve.body.mixin;

import eve.body.Hands;
import net.minecraft.client.multiplayer.MultiPlayerGameMode;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.item.BlockItem;
import net.minecraft.world.phys.BlockHitResult;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Unique;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/** Counts the blocks her own client breaks and places, whoever drives it (Baritone's walk_to budget, CP-33). */
@Mixin(MultiPlayerGameMode.class)
public abstract class GameModeMixin {
	@Unique
	private boolean eve$heldBlock;

	@Inject(method = "destroyBlock", at = @At("RETURN"))
	private void eve$broke(BlockPos pos, CallbackInfoReturnable<Boolean> cir) {
		if (cir.getReturnValue()) Hands.blocksBroken++;
	}

	@Inject(method = "useItemOn", at = @At("HEAD"))
	private void eve$beforeUse(LocalPlayer p, InteractionHand hand, BlockHitResult hit, CallbackInfoReturnable<InteractionResult> cir) {
		eve$heldBlock = p.getItemInHand(hand).getItem() instanceof BlockItem; // the last block leaves the hand empty afterwards
	}

	@Inject(method = "useItemOn", at = @At("RETURN"))
	private void eve$placed(LocalPlayer p, InteractionHand hand, BlockHitResult hit, CallbackInfoReturnable<InteractionResult> cir) {
		if (eve$heldBlock && cir.getReturnValue().consumesAction()) Hands.blocksPlaced++;
	}
}
