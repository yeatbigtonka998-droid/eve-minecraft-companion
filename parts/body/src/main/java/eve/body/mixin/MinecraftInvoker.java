package eve.body.mixin;

import net.minecraft.client.Minecraft;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Invoker;

/** Vanilla's private click methods, called only through eve.body.Hands. */
@Mixin(Minecraft.class)
public interface MinecraftInvoker {
	@Invoker("startAttack")
	boolean eve$startAttack();

	@Invoker("continueAttack")
	void eve$continueAttack(boolean leftClick);

	@Invoker("startUseItem")
	void eve$startUseItem();
}
