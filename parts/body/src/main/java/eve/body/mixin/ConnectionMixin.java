package eve.body.mixin;

import eve.body.EveBody;
import io.netty.channel.ChannelHandlerContext;
import net.minecraft.network.Connection;
import net.minecraft.network.protocol.Packet;
import net.minecraft.network.protocol.PacketFlow;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/** Records when the client last heard from the server, so state.freshness.age_ms means "how old is what I know". */
@Mixin(Connection.class)
abstract class ConnectionMixin {
	@Inject(method = "channelRead0(Lio/netty/channel/ChannelHandlerContext;Lnet/minecraft/network/protocol/Packet;)V", at = @At("HEAD"))
	private void eve$onPacket(ChannelHandlerContext ctx, Packet<?> packet, CallbackInfo ci) {
		if (((Connection) (Object) this).getReceiving() == PacketFlow.CLIENTBOUND) EveBody.lastServerPacketNanos = System.nanoTime();
	}
}
