package eve.body;

import io.netty.buffer.ByteBuf;
import net.minecraft.network.codec.ByteBufCodecs;
import net.minecraft.network.codec.StreamCodec;
import net.minecraft.network.protocol.common.custom.CustomPacketPayload;
import net.minecraft.resources.Identifier;

/** eve:control (server helper -> Eve), eve:control_ack and eve:rescue (Eve -> server helper). Body = one UTF-8 JSON string each. */
final class Payloads {
	record Control(String json) implements CustomPacketPayload {
		static final Type<Control> TYPE = new Type<>(Identifier.fromNamespaceAndPath("eve", "control"));
		static final StreamCodec<ByteBuf, Control> CODEC = ByteBufCodecs.STRING_UTF8.map(Control::new, Control::json);

		@Override
		public Type<Control> type() {
			return TYPE;
		}
	}

	/** O20: tags a fall-rescue water place/scoop so sense allows and logs it (rescue_place); sent only by the reflex layer. */
	record Rescue(String json) implements CustomPacketPayload {
		static final Type<Rescue> TYPE = new Type<>(Identifier.fromNamespaceAndPath("eve", "rescue"));
		static final StreamCodec<ByteBuf, Rescue> CODEC = ByteBufCodecs.STRING_UTF8.map(Rescue::new, Rescue::json);

		@Override
		public Type<Rescue> type() {
			return TYPE;
		}
	}

	record Ack(String json) implements CustomPacketPayload {
		static final Type<Ack> TYPE = new Type<>(Identifier.fromNamespaceAndPath("eve", "control_ack"));
		static final StreamCodec<ByteBuf, Ack> CODEC = ByteBufCodecs.STRING_UTF8.map(Ack::new, Ack::json);

		@Override
		public Type<Ack> type() {
			return TYPE;
		}
	}
}
