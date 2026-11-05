package eve.body.api;

import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.world.entity.Entity;

import java.util.Map;

/** API v4: the read-only view {@link EngageHook#pick} decides from. Client thread only. */
public interface ClientView {
	Minecraft minecraft();

	LocalPlayer player();

	/** The body's protection rule (players, villagers, golems, named, tamed, the envelope's extra types). */
	boolean isProtected(Entity e);

	/** Hotbar slot 0-8 holding the item id, or -1. */
	int hotbarSlot(String itemId);

	/** Item id -> count over her whole inventory. */
	Map<String, Integer> inventoryCounts();

	/** Client ticks since start. */
	long ticks();

	/** The entity id that last damaged her, or -1; and the client tick of that hit (-1 when none). */
	int lastAttackerId();

	long lastHitTick();

	/**
	 * sense's mob.targeting frames (sense -> mind -> body) of the last 3 s: entity id -> its latest frame. Data, never a
	 * permission. Empty when the mind does not relay them.
	 */
	Map<Integer, Targeting> targeting();

	/** One mob.targeting frame: {@code target} "owner" | "eve", the target's name, and the client tick it arrived. */
	record Targeting(String target, String targetName, long tick) {
	}
}
