package eve.sense.mixin;

import com.mojang.brigadier.ParseResults;
import eve.sense.Control;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.network.chat.Component;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/** Every command (player, console, RCON, execute ... run) passes performCommand: the Ledger rollback guard (rule O17). */
@Mixin(Commands.class)
abstract class CommandsMixin {
	@Inject(method = "performCommand", at = @At("HEAD"), cancellable = true)
	private void eve$ledgerGuard(ParseResults<CommandSourceStack> parse, String command, CallbackInfo ci) {
		String refusal = Control.ledgerRefusal(command, parse.getContext().getSource().getTextName());
		if (refusal != null) {
			parse.getContext().getSource().sendFailure(Component.literal(refusal));
			ci.cancel();
		}
	}
}
