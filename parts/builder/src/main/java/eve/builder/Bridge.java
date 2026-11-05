package eve.builder;

import com.google.gson.JsonObject;
import eve.body.api.BodyServices;
import eve.body.api.Skill;
import eve.body.api.Step;
import eve.body.api.SubAction;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.util.Mth;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.monster.Enemy;
import net.minecraft.world.entity.player.Input;
import net.minecraft.world.inventory.ContainerInput;
import net.minecraft.world.item.BlockItem;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.Vec3;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * bridge v1 (CP-53): a straight sneak-bridge along x or z at her floor height, by the body's own place_block. Per cell: lean over
 * the edge with forward + sneak (vanilla's sneak back-off keeps her on the block; the body's edge-sneak reflex holds sneak too),
 * place the next floor cell on the current block's front face, step onto it sneaking. Gear is checked before she steps out;
 * any drop of her feet below the floor ends the skill `fell` (zero falls is the CP-53 threshold, not a guarantee).
 */
final class Bridge implements Skill {
	enum Stage { FACE, LEAN, SWAP, PLACE, MOVE, RETREAT }

	/** Blocks a bridge may be made of when `item` is not given (full cubes she is likely to carry). */
	static final List<String> BRIDGE_ITEMS = List.of("minecraft:cobblestone", "minecraft:dirt", "minecraft:cobbled_deepslate",
			"minecraft:netherrack", "minecraft:end_stone", "minecraft:stone", "minecraft:oak_planks", "minecraft:spruce_planks",
			"minecraft:birch_planks");
	/** Recovery gear before stepping over a gap deeper than a safe fall: an ender pearl or a slow-falling potion anywhere; a water
	 *  bucket only when there is ground under the gap (over open void it saves nothing; aligned with the mind's planner). */
	static final int SAFE_FALL = 3; // the body's maxFallHeightNoWater: a drop that costs no health

	final JsonObject params;
	BlockPos start, cur, to;
	int dx, dz;
	List<BlockPos> cells = new ArrayList<>();
	int i, placed, falls, attempts, lean, hostilesSeen, swapWait;
	Integer depth; // blocks from the first gap cell down to the first solid block (<= 64), null = open void
	double floorTop, minFeetY = Double.MAX_VALUE, lastAlong;
	String item, recovery;
	boolean retreated;
	Stage stage = Stage.FACE;
	SubAction child;

	Bridge(JsonObject params) {
		this.params = params;
	}

	@Override
	public Step tick(BodyServices b) {
		LocalPlayer p = b.player();
		if (b.ticks() == 0) {
			Step s = start(b, p);
			if (s != null) return s;
		}
		minFeetY = Math.min(minFeetY, p.getY());
		if (p.getY() < floorTop - 0.5) { // her feet left the floor: a fall, whatever happens next
			falls++;
			return end(b, Step.failed("fell", "feet at y " + Mth.floor(p.getY() * 100) / 100.0 + " below the floor top " + floorTop));
		}
		if (child != null) {
			Step st = child.tick();
			if (!st.done()) return st;
			JsonObject res = child.result();
			child = null;
			if ("failed".equals(res.get("status").getAsString()) && "zone_denied".equals(str(res, "code")))
				return end(b, Step.failed("zone_denied", "place_block refused by the server at " + cells.get(i).toShortString()));
			ClientLevel lv = b.minecraft().level;
			if (!lv.getBlockState(cells.get(i)).canBeReplaced()) {
				placed++;
				stage = Stage.MOVE;
			} else if (++attempts < 3) {
				stage = Stage.LEAN;
				lean = 0;
			} else return end(b, Step.failed("obstructed", "could not place " + cells.get(i).toShortString() + ": " + str(res, "code") + " " + str(res, "note")));
		}
		if (stage != Stage.RETREAT && hostileNear(b, p, radius() / 2)) { // closing in mid-bridge: back to the start, then stop
			stage = Stage.RETREAT;
			retreated = true;
		}
		return switch (stage) {
			case RETREAT -> retreat(b, p);
			case FACE -> face(b, p);
			case LEAN -> lean(b, p);
			case SWAP -> swap(b, p);
			case PLACE -> place(b, p);
			case MOVE -> move(b, p);
		};
	}

	static Step noItem(String item, Map<String, Integer> inv, int gap) {
		return item == null || inv.getOrDefault(item, 0) < gap
				? Step.failed("no_item", "needs " + gap + " bridge blocks" + (item == null ? "" : ", has " + inv.getOrDefault(item, 0) + " " + item)) : null;
	}

	static String recovery(Map<String, Integer> inv, boolean slowFalling, boolean voidBelow) {
		return inv.getOrDefault("minecraft:ender_pearl", 0) > 0 ? "minecraft:ender_pearl"
				: slowFalling ? "slow_falling" : !voidBelow && inv.getOrDefault("minecraft:water_bucket", 0) > 0 ? "minecraft:water_bucket" : null;
	}

	static Step noRecovery(boolean deep, boolean voidBelow, String recovery) {
		return deep && recovery == null ? Step.failed("no_recovery", "the gap is deeper than " + SAFE_FALL + " blocks" + (voidBelow ? " (open void)" : "")
				+ " and she carries no ender pearl, slow-falling potion" + (voidBelow ? "" : " or water bucket")) : null;
	}

	Step start(BodyServices b, LocalPlayer p) {
		ClientLevel lv = b.minecraft().level;
		if (!p.onGround()) return Step.failed("bad_target", "not standing on a block");
		start = cur = p.blockPosition().below();
		floorTop = start.getY() + 1;
		JsonObject t = params.getAsJsonObject("to");
		to = new BlockPos(t.get("x").getAsInt(), t.get("y").getAsInt(), t.get("z").getAsInt());
		int max = params.has("max_blocks") ? params.get("max_blocks").getAsInt() : 40;
		List<BlockPos> line = line(start, to, max);
		if (line == null) return Step.failed("bad_target", "`to` must be on her floor's height and in a straight x or z line within " + max + " blocks of " + start.toShortString());
		cells = line;
		dx = Integer.signum(to.getX() - start.getX());
		dz = Integer.signum(to.getZ() - start.getZ());
		int gap = 0;
		boolean deep = false, voidBelow = false, first = true;
		for (BlockPos c : cells)
			if (lv.getBlockState(c).canBeReplaced()) {
				gap++;
				Integer dd = dropBelow(lv, c);
				if (first) depth = dd;
				first = false;
				deep |= dd == null || dd > SAFE_FALL;
				voidBelow |= dd == null;
			}
		Map<String, Integer> inv = b.inventoryCounts();
		item = params.has("item") ? params.get("item").getAsString() : BRIDGE_ITEMS.stream().filter(k -> inv.getOrDefault(k, 0) >= 1).findFirst().orElse(null);
		JsonObject ev = b.evidence();
		ev.add("start", Build.pos(start));
		ev.addProperty("depth", depth); // on every end, refusals included (the mind cross-checks it with void_edge)
		Step no = noItem(item, inv, gap);
		if (no != null) return no;
		recovery = recovery(inv, slowFalling(p), voidBelow);
		ev.addProperty("recovery_item", recovery);
		ev.addProperty("depth", depth);
		no = noRecovery(deep, voidBelow, recovery);
		if (no != null) return no;
		if (hostileNear(b, p, radius())) return Step.failed("hostile_near", "hostiles within " + radius() + " blocks: no bridging near a fight");
		i = 0;
		return null;
	}

	double radius() {
		return params.has("hostile_radius") ? params.get("hostile_radius").getAsDouble() : 16;
	}

	/** The cells from the one after `from` up to `to`, or null unless `to` is level with `from` in a straight x/z line within max. */
	static List<BlockPos> line(BlockPos from, BlockPos to, int max) {
		int ddx = to.getX() - from.getX(), ddz = to.getZ() - from.getZ();
		if (to.getY() != from.getY() || (ddx != 0) == (ddz != 0) || Math.abs(ddx + ddz) > max) return null;
		List<BlockPos> out = new ArrayList<>();
		int n = Math.abs(ddx + ddz), sx = Integer.signum(ddx), sz = Integer.signum(ddz);
		for (int k = 1; k <= n; k++) out.add(from.offset(sx * k, 0, sz * k));
		return out;
	}

	/** Blocks from cell c down to the first solid block below it (1 = right under it), or null when none within 64. */
	static Integer dropBelow(ClientLevel lv, BlockPos c) {
		for (int k = 1; k <= 64; k++)
			if (!lv.getBlockState(c.below(k)).getCollisionShape(lv, c.below(k)).isEmpty()) return k;
		return null;
	}

	/** A slow-falling potion (any kind: drink, splash, lingering) in her inventory. */
	static boolean slowFalling(LocalPlayer p) {
		for (int k = 0; k < p.getInventory().getContainerSize(); k++) {
			net.minecraft.world.item.alchemy.PotionContents pc = p.getInventory().getItem(k).get(net.minecraft.core.component.DataComponents.POTION_CONTENTS);
			if (pc != null && (pc.is(net.minecraft.world.item.alchemy.Potions.SLOW_FALLING) || pc.is(net.minecraft.world.item.alchemy.Potions.LONG_SLOW_FALLING)))
				return true;
		}
		return false;
	}

	boolean hostileNear(BodyServices b, LocalPlayer p, double r) {
		for (Entity e : b.minecraft().level.getEntities(p, new AABB(p.blockPosition()).inflate(r)))
			if (e instanceof Enemy && e instanceof LivingEntity le && le.isAlive() && !b.isProtected(e) && e.distanceTo(p) <= r) {
				hostilesSeen++;
				return true;
			}
		return false;
	}

	/** Yaw (degrees) that faces the bridge's direction: +x east -90, -x west 90, +z south 0, -z north 180. */
	float dirYaw() {
		return dx > 0 ? -90f : dx < 0 ? 90f : dz > 0 ? 0f : 180f;
	}

	/** How far along the bridge direction she stands, relative to the far edge of the current floor block (> 0 = over it). */
	double overEdge(LocalPlayer p) {
		return dx != 0 ? (p.getX() - (cur.getX() + 0.5)) * dx - 0.5 : (p.getZ() - (cur.getZ() + 0.5)) * dz - 0.5;
	}

	Step face(BodyServices b, LocalPlayer p) {
		if (i >= cells.size()) return end(b, Step.ok());
		BlockPos c = cells.get(i);
		if (!b.minecraft().level.getBlockState(c).canBeReplaced()) { // already there (the far side, or a cell from an earlier run)
			stage = Stage.MOVE;
			return Step.running(Input.EMPTY, true);
		}
		b.steerTo(Vec3.atCenterOf(cur).add(dx * 8, 0, dz * 8)); // turns her toward the bridge line (its input is not used here)
		if (Math.abs(Mth.wrapDegrees(p.getYRot() - dirYaw())) > 8f) return Step.running(Input.EMPTY, true);
		stage = Stage.LEAN;
		lean = 0;
		lastAlong = overEdge(p);
		return Step.running(Input.EMPTY, true);
	}

	Step lean(BodyServices b, LocalPlayer p) {
		b.steerTo(Vec3.atCenterOf(cur).add(dx * 8, 0, dz * 8)); // keep facing the line
		double along = overEdge(p);
		boolean stalled = ++lean > 6 && along - lastAlong < 0.002; // vanilla's sneak back-off stopped her at the edge
		lastAlong = along;
		if (along >= 0.2 || stalled || lean > 60) {
			stage = b.hotbarSlot(item) >= 0 ? Stage.PLACE : Stage.SWAP;
			swapWait = 0;
			return Step.running(Input.EMPTY, true);
		}
		return Step.running(new Input(true, false, false, false, false, true, false), true);
	}

	Step swap(BodyServices b, LocalPlayer p) {
		if (b.hotbarSlot(item) >= 0) {
			stage = Stage.PLACE;
			return Step.running(Input.EMPTY, true);
		}
		if (swapWait++ == 0) {
			int slot = -1;
			for (int k = 9; k < 36; k++) {
				ItemStack st = p.getInventory().getItem(k);
				if (!st.isEmpty() && st.getItem() instanceof BlockItem && BuiltInRegistries.ITEM.getKey(st.getItem()).toString().equals(item)) {
					slot = k;
					break;
				}
			}
			if (slot < 0) return end(b, Step.failed("no_item", "ran out of " + item + " after " + placed));
			b.clickSlot(slot, 7, ContainerInput.SWAP); // hotbar 8 (slot 7): leaves hotbar 9 for a recovery item
		}
		if (swapWait > 20) return end(b, Step.failed("no_item", "could not move " + item + " to the hotbar"));
		return Step.running(Input.EMPTY, true);
	}

	Step place(BodyServices b, LocalPlayer p) {
		JsonObject pp = new JsonObject();
		pp.add("pos", Build.pos(cells.get(i)));
		pp.addProperty("item", item);
		pp.addProperty("face", dx > 0 ? "east" : dx < 0 ? "west" : dz > 0 ? "south" : "north");
		child = b.run("place_block", pp);
		return Step.running(Input.EMPTY, true);
	}

	Step move(BodyServices b, LocalPlayer p) {
		BlockPos c = cells.get(i);
		Vec3 centre = Vec3.atBottomCenterOf(c.above());
		double h = Math.hypot(p.getX() - centre.x, p.getZ() - centre.z);
		if (h < 0.3) {
			cur = c;
			floorTop = c.getY() + 1;
			i++;
			attempts = 0;
			stage = Stage.FACE;
			b.progress(100 * i / cells.size(), "bridged " + i + "/" + cells.size());
			return Step.running(Input.EMPTY, true);
		}
		Input in = b.steerTo(centre); // stalls at a drop by itself; sneak forced on top
		return Step.running(new Input(in.forward(), false, false, false, false, true, false), true);
	}

	Step retreat(BodyServices b, LocalPlayer p) {
		Vec3 home = Vec3.atBottomCenterOf(start.above());
		if (Math.hypot(p.getX() - home.x, p.getZ() - home.z) < 0.4)
			return end(b, Step.failed("hostile_near", "a hostile came within " + radius() / 2 + " blocks on the bridge; walked back to the start"));
		floorTop = start.getY() + 1; // the whole bridge is at the start's height
		Input in = b.steerTo(home);
		return Step.running(new Input(in.forward(), false, false, false, false, true, false), true);
	}

	Step end(BodyServices b, Step s) {
		LocalPlayer p = b.player();
		JsonObject ev = b.evidence();
		ev.add("end", Build.pos(p.blockPosition().below()));
		ev.addProperty("length", cells.size());
		ev.addProperty("placed", placed);
		ev.addProperty("falls", falls);
		ev.addProperty("min_feet_y", minFeetY == Double.MAX_VALUE ? null : minFeetY);
		ev.addProperty("recovery_item", recovery);
		ev.addProperty("retreated", retreated);
		ev.addProperty("hostiles_seen", hostilesSeen);
		ev.addProperty("depth", depth); // extra key (not in the registry list): the mind cross-checks it with sense's void_edge
		return s;
	}

	static String str(JsonObject o, String k) {
		return o.has(k) && !o.get(k).isJsonNull() ? o.get(k).getAsString() : null;
	}
}
