package eve.sense.mixin;

import eve.sense.HitLog;
import net.minecraft.network.protocol.game.ServerboundPlayerActionPacket;
import net.minecraft.network.protocol.game.ServerboundUseItemPacket;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.server.network.ServerGamePacketListenerImpl;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/** O174 diagnostic: Eve's use-item packets as the server handled them (RETURN runs on the server thread only:
 * the network-thread pass throws out of ensureRunningOnSameThread). Changes nothing. */
@Mixin(ServerGamePacketListenerImpl.class)
abstract class ServerGamePacketListenerMixin {
	@Shadow
	public ServerPlayer player;

	@Inject(method = "handleUseItem", at = @At("RETURN"))
	private void eve$useItem(ServerboundUseItemPacket p, CallbackInfo ci) {
		HitLog.usePacket(player, p.hand(), p.sequence(), p.yRot(), p.xRot());
	}

	@Inject(method = "handlePlayerAction", at = @At("RETURN"))
	private void eve$action(ServerboundPlayerActionPacket p, CallbackInfo ci) {
		var a = p.getAction();
		if (a == ServerboundPlayerActionPacket.Action.RELEASE_USE_ITEM || a == ServerboundPlayerActionPacket.Action.SWAP_ITEM_WITH_OFFHAND)
			HitLog.use(player, "packet_" + a.name().toLowerCase(java.util.Locale.ROOT), null);
	}
}
