package eve.body;

import eve.body.mixin.MinecraftInvoker;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.EntityHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;

import java.util.HashMap;
import java.util.Map;

/**
 * Clicks go through vanilla's own startAttack / continueAttack / startUseItem (item attack ranges, piercing, swing animations,
 * block prediction), exactly as a mouse click would. Only this class may call them: MinecraftMixin cancels every other call,
 * so the physical mouse cannot attack, mine or use for Eve. Aiming is rotation only; the crosshair (vanilla's pick, which runs
 * after our START-tick rotation) decides what a click hits, so obstruction is real, not computed.
 */
public final class Hands {
	/** Every block her client broke / placed since start (GameModeMixin); walk_to reads its own deltas. */
	public static long blocksBroken, blocksPlaced;
	/** A walk_to with allow_break (budget left) / allow_place is running: its Baritone clicks pass MinecraftMixin. */
	public static boolean baritoneMayBreak, baritoneMayPlace;
	/**
	 * O174 (sense's server log, lane F x_archer: 69 of 186 raises were a release and a re-press in one tick, each restarting the
	 * shield's 5-tick block delay; 53 of 62 arrows landed inside it): while true, a raised shield changes owner (fight guard <->
	 * shield reflex) without being released.
	 */
	static boolean shieldHandover;
	/** O174: a shield reflex left the shield raised for the resuming fight; the fight's guard() takes it, else it is released. */
	static boolean shieldHandedBack;
	static int handedBackTicks;

	/** O174: let go of a shield left raised for a fight that did not take it. */
	static void releaseHandedBack(net.minecraft.client.Minecraft mc, net.minecraft.client.player.LocalPlayer p) {
		shieldHandedBack = false;
		mc.options.keyUse.setDown(false);
		if (p != null && p.isUsingItem()) mc.gameMode.releaseUsingItem(p);
	}

	/** Her off-hand shield is in use right now (raised, or rising). */
	static boolean shieldInUse(net.minecraft.client.player.LocalPlayer p) {
		return p.isUsingItem() && p.getUseItem().get(net.minecraft.core.component.DataComponents.BLOCKS_ATTACKS) != null;
	}

	/** True only while the body itself is inside a vanilla click method. */
	public static boolean ours;

	static boolean attack(Minecraft mc) {
		ours = true;
		try {
			return ((MinecraftInvoker) mc).eve$startAttack();
		} finally {
			ours = false;
		}
	}

	/** One tick of "left button held" (vanilla mining). */
	static void holdAttack(Minecraft mc) {
		ours = true;
		try {
			((MinecraftInvoker) mc).eve$continueAttack(true);
		} finally {
			ours = false;
		}
	}

	static void use(Minecraft mc) {
		ours = true;
		try {
			((MinecraftInvoker) mc).eve$startUseItem();
		} finally {
			ours = false;
		}
	}

	/**
	 * Null when the hands are free. Vanilla blocks attacks while any screen is open; a stray pause menu is closed (multiplayer
	 * never pauses, Eve's client should never sit in it) and the caller waits a tick ("wait"); any other screen is a failure note.
	 */
	static String blockedByScreen(Minecraft mc) {
		if (mc.gui.screen() == null) return null;
		if (mc.gui.screen() instanceof net.minecraft.client.gui.screens.PauseScreen) {
			mc.gui.setScreen(null);
			return "wait";
		}
		return "a " + mc.gui.screen().getClass().getSimpleName() + " is open";
	}

	/** Turns toward {@code point}; true when the rotation was already on it at tick start, so mc.hitResult reflects it. */
	/**
	 * O65: where to aim at a block - the centre of its outline shape, not of its cell. Full cubes: the same point. Thin blocks
	 * (leaf litter 1 px, carpet, snow layers, rails, pressure plates) and offset plants (poppy, short grass: getShape applies the
	 * XZ offset): a near-flat ray to the cell centre passed over the outline and hit the block behind (CP-40 a10 p_litter, a16 p_grass).
	 */
	static Vec3 aimPoint(net.minecraft.world.level.Level l, BlockPos pos) {
		var shape = l.getBlockState(pos).getShape(l, pos);
		return shape.isEmpty() ? Vec3.atCenterOf(pos) : Vec3.atLowerCornerOf(pos).add(shape.bounds().getCenter());
	}

	static boolean aim(LocalPlayer p, Vec3 point) {
		float[] yp = Skills.yawPitch(p.getEyePosition(), point);
		if (Skills.angleError(p, yp[0], yp[1]) <= 0.5f) return true;
		Skills.turn(p, yp[0], yp[1], 30f);
		return false;
	}

	static boolean crosshairOn(Minecraft mc, Entity e) {
		return mc.hitResult instanceof EntityHitResult h && h.getEntity() == e;
	}

	static boolean crosshairOn(Minecraft mc, BlockPos pos) {
		return mc.hitResult instanceof BlockHitResult h && h.getType() == HitResult.Type.BLOCK && h.getBlockPos().equals(pos);
	}

	/** What the crosshair is on, for failure notes. */
	static String crosshair(Minecraft mc) {
		if (mc.hitResult instanceof BlockHitResult h && h.getType() == HitResult.Type.BLOCK)
			return "block " + blockId(mc.level.getBlockState(h.getBlockPos())) + " at " + h.getBlockPos().toShortString();
		if (mc.hitResult instanceof EntityHitResult h) return "entity " + BuiltInRegistries.ENTITY_TYPE.getKey(h.getEntity().getType());
		return "nothing";
	}

	static String blockId(BlockState s) {
		return BuiltInRegistries.BLOCK.getKey(s.getBlock()).toString();
	}

	static String itemId(ItemStack s) {
		return s.isEmpty() ? null : BuiltInRegistries.ITEM.getKey(s.getItem()).toString();
	}

	/** Hotbar slot holding {@code item}, or -1 (reflexes and providers: no inventory moves). */
	static int hotbarSlot(LocalPlayer p, String item) {
		for (int i = 0; i < 9; i++) if (item.equals(itemId(p.getInventory().getItem(i)))) return i;
		return -1;
	}

	/**
	 * O130 (Hunter live: a crafting table crafted into her main inventory was "not a block item in the hotbar"): the hotbar slot
	 * holding {@code item}; else its first main-inventory stack (slots 9-35) is swapped into the hotbar first, as O101a's tool
	 * move: an empty slot, else 8 (7 when 8 is selected), by a number-key swap through menu 0 (the displaced item takes the
	 * stack's old place). -1 when it is nowhere in slots 0-35, or the inventory can't be clicked now (a screen open, an item
	 * carried). {@code moved}, when given, gets item / from / to_hotbar / displaced of a move.
	 */
	static int hotbarSlotPulling(Minecraft mc, LocalPlayer p, String item, com.google.gson.JsonObject moved) {
		var inv = p.getInventory();
		int[] plan = pullPlan(i -> itemId(inv.getItem(i)), inv.getSelectedSlot(), item);
		if (plan == null) return -1;
		if (plan[0] < 9) return plan[0];
		if (mc.gui.screen() != null || p.containerMenu != p.inventoryMenu || !p.containerMenu.getCarried().isEmpty()) return -1;
		if (moved != null) {
			moved.addProperty("item", item);
			moved.addProperty("from", plan[0]);
			moved.addProperty("to_hotbar", plan[1]);
			moved.addProperty("displaced", itemId(inv.getItem(plan[1])));
		}
		mc.gameMode.handleContainerInput(0, plan[0], plan[1], net.minecraft.world.inventory.ContainerInput.SWAP, p);
		return plan[1];
	}

	/** {hotbar slot, -1} when the hotbar holds it; {main slot 9-35, hotbar slot to swap it into}; null when nowhere. */
	static int[] pullPlan(java.util.function.IntFunction<String> idAt, int selected, String item) {
		for (int i = 0; i < 9; i++) if (item.equals(idAt.apply(i))) return new int[] {i, -1};
		for (int i = 9; i < 36; i++)
			if (item.equals(idAt.apply(i))) return new int[] {i, Interact.MineBlock.hotbarFor(h -> idAt.apply(h) == null, selected)};
		return null;
	}

	static Map<String, Integer> counts(LocalPlayer p) {
		Map<String, Integer> m = new HashMap<>();
		var inv = p.getInventory();
		for (int i = 0; i < inv.getContainerSize(); i++) {
			ItemStack s = inv.getItem(i);
			if (!s.isEmpty()) m.merge(itemId(s), s.getCount(), Integer::sum);
		}
		return m;
	}
}
