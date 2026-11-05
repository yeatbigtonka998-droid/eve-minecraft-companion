package eve.body.api;

import com.google.gson.JsonObject;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.player.Input;
import net.minecraft.world.phys.Vec3;

import java.util.Map;

/**
 * What the body gives a provider skill. Everything runs on the client thread inside the skill's tick. Read the world through
 * {@link #minecraft()} / {@link #player()} freely; act only through these services and the returned {@link Step}. Never call
 * vanilla's attack/use/destroy methods directly (the body cancels clicks that are not its own) and never set player.input.
 */
public interface BodyServices {
	/** 1 = first release; 2 = useBlock / clickSlot / closeMenu; 3 = guard / aim / holdUse; 4 = registerEngage; 5 = claimCreeper (all additive). */
	int API_VERSION = 5;

	/**
	 * API 5: this fight handles this creeper. Call every tick; the claim lapses 5 ticks after the last call, so no release is needed. One
	 * claim at a time, a new id replaces it. While claimed the creeper_flee reflex leaves that creeper alone until it is primed (within 12).
	 */
	void claimCreeper(int entityId);

	Minecraft minecraft();

	LocalPlayer player();

	/** This action's id, and its params (validated against the registry schema). */
	String actionId();

	JsonObject params();

	/** Ticks this skill has run (0 on the first call). */
	int ticks();

	/** True once the action is being cancelled (stop, link loss, deadline, a newer request); {@link Skill#cancel} follows. */
	boolean cancelled();

	/** The evidence object of this action's result; fill the registry's evidence keys for the skill (sent on every end). */
	JsonObject evidence();

	/** Progress shown in action.progress (0..100, or null). */
	void progress(Integer pct, String note);

	/** event{kind, detail} to the mind (kinds per the link protocol; skill-specific detail is yours). */
	void event(String kind, JsonObject detail);

	/** The body's current state payload (the same object a `state` frame carries). */
	JsonObject state();

	/**
	 * Right-click the block at {@code pos} and leave its menu open (crafting table, furnace, chest, ...): reach, aim, crosshair
	 * and sneak-off checks as open_container. Ends ok once {@code player().containerMenu != player().inventoryMenu}; failed with
	 * out_of_range, obstructed, wrong_block, or zone_denied (no menu came: server refusal, locked, or not a menu block). The menu
	 * stays open until {@link #closeMenu()} or the action ends: the body closes any open menu on every end of a provider action
	 * (ok, failed, stop, reflex pre-emption, deadline, link loss).
	 */
	SubAction useBlock(net.minecraft.core.BlockPos pos);

	/**
	 * One click in {@code player().containerMenu}: vanilla's {@code MultiPlayerGameMode.handleContainerInput(containerId, slot,
	 * button, input, player)}, the same packet a mouse click sends; the server validates it. {@code slot} indexes
	 * containerMenu.slots, as vanilla. With no block menu open this is the player's own inventory menu (containerId 0: the 2x2
	 * crafting grid, armour, hotbar). Call it at most once per tick. False only when there is no player or game mode.
	 */
	boolean clickSlot(int slot, int button, net.minecraft.world.inventory.ContainerInput input);

	/** Close the open menu, if any (vanilla closeContainer: the close packet). */
	void closeMenu();

	/**
	 * Hold the off-hand shield up (true) or down (false) as part of this action, with the body's own use click. Call it every
	 * tick you want to own the shield: while you do (this tick or the last), the shield reflex's MELEE trigger yields to you,
	 * so a fight is not paused just because a hostile is in reach. Projectile shielding, dodge, creeper flee, retreat, fall
	 * rescue and STOPPED are unchanged and still pre-empt you. guard does not turn her: a shield blocks from the front only, so
	 * face the attacker yourself ({@link #aim}). If a reflex pauses your action (e.g. an arrow's shield) your claim is judged as
	 * of the pause, so a shield it raised answers the projectile only and ends on its quiet ticks (O102d). guard(true) does nothing without a usable off-hand shield
	 * (returns false). Released on every end of the action. Do not combine with {@link #holdUse} in the same tick.
	 */
	boolean guard(boolean up);

	/**
	 * O102d (3): true while the body stands back for a fight. She is at the health floor, a retreat ended cornered against a
	 * ranged hostile out of melee reach, and for up to 8 s the reflexes treat any fight_ranged/fight_melee as guarding: only the
	 * projectile shield, fall rescue, hazards and creeper flee still pre-empt it. A fight should not take its own health-floor
	 * disengage while this is true, since it is the one thing that can end the threat. Default false (older bodies).
	 */
	default boolean handedBack() {
		return false;
	}

	/** Turn toward {@code point} by at most {@code maxDegPerTick} on each axis; true when already within 0.5 deg at this tick. */
	boolean aim(net.minecraft.world.phys.Vec3 point, float maxDegPerTick);

	/**
	 * Hold the main-hand use button (true): starts using the main-hand item "in the air" (never clicks the block in view) and
	 * keeps it held, e.g. to draw a bow. holdUse(false) releases it (a drawn bow fires). Ticks held so far:
	 * {@code player().getTicksUsingItem()}. Released on every end of the action.
	 */
	void holdUse(boolean down);

	/** Start one of the body's own skills as a child (see {@link SubAction}); null params = {}. */
	SubAction run(String skill, JsonObject params);

	/** null when {@code skill} with {@code params} would pass the body's own validation, preconditions and pre-checks; else why not. */
	String precheck(String skill, JsonObject params);

	/** One tick of direct steering toward {@code goal} (turn, walk, hop a 1-block step, refuse drops of 2+). Return it in a Step. */
	Input steerTo(Vec3 goal);

	/** Hotbar slot (0-8) holding the item id, or -1; select a slot (sent with this tick); item id -> count over the inventory. */
	int hotbarSlot(String itemId);

	void selectHotbar(int slot);

	Map<String, Integer> inventoryCounts();

	/**
	 * The body's local view of protected entities (players, villagers, golems, pets, named, envelope extra_types). Zones, grants
	 * and containers are enforced by sense on the server and pre-flighted by the mind (permission.check) before dispatch, so a
	 * skill reports the server's refusal (code zone_denied) rather than predicting it.
	 */
	boolean isProtected(Entity e);
}
