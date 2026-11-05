package eve.skills;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import eve.body.api.BodyServices;
import eve.body.api.Skill;
import eve.body.api.Step;
import eve.body.api.SubAction;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.world.entity.item.ItemEntity;
import net.minecraft.world.inventory.ContainerInput;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.CropBlock;
import net.minecraft.world.level.block.state.BlockState;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;

/**
 * harvest_replant{min, max, replant}: every mature wheat/carrot/potato in the box is broken with the body's mine_block and,
 * if replant, the same plot is replanted with the matching seed by the body's use_item on the farmland. Immature crops are
 * never touched; a plot without a seed is reported in not_replanted (partial). Trampling is measured, not assumed: farmland
 * in the box that turned to dirt is counted.
 */
final class HarvestReplant implements Skill {
	static final Map<String, String> SEED = Map.of("minecraft:wheat", "minecraft:wheat_seeds", "minecraft:carrots", "minecraft:carrot",
			"minecraft:potatoes", "minecraft:potato");
	static final int MAX_BLOCKS = 32 * 4 * 32;

	final BlockPos lo, hi;
	final boolean replant;
	final String crop; // O94 harvest_crop: one crop block id, or null = every kind
	final int limit; // O94 harvest_crop: at most this many crops harvested; MAX_VALUE = all
	final boolean primitive; // O94 harvest_crop: farmland under the crop required, and stop_reason in the evidence
	final Set<BlockPos> tried = new HashSet<>();
	final Deque<BlockPos> toPlant = new ArrayDeque<>();
	final Map<BlockPos, String> cropAt = new java.util.HashMap<>();
	final Map<String, Integer> harvested = new TreeMap<>(), replanted = new TreeMap<>();
	final JsonArray notReplanted = new JsonArray(), walks = new JsonArray();
	Set<BlockPos> farmland0;
	Map<String, Integer> before;
	SubAction child;
	String stage; // walk-harvest, harvest, walk-plant, plant
	BlockPos target;
	int misses, settle, pickups;
	boolean retriedSeeds;
	BlockPos pickupAround;

	HarvestReplant(JsonObject p) {
		this(p, false);
	}

	/**
	 * O94 (CP-39) harvest_crop{min, max, crop?, count?, replant?}: harvest_replant's rules on the granted farm box, limited to
	 * one crop kind and a count, and only crops standing on farmland ("tagged farmland": the mind passes a farm zone's box,
	 * sense enforces break and use grants there). Every end says why in stop_reason.
	 */
	HarvestReplant(JsonObject p, boolean primitive) {
		BlockPos a = Menus.pos(p.getAsJsonObject("min")), c = Menus.pos(p.getAsJsonObject("max"));
		lo = new BlockPos(Math.min(a.getX(), c.getX()), Math.min(a.getY(), c.getY()), Math.min(a.getZ(), c.getZ()));
		hi = new BlockPos(Math.max(a.getX(), c.getX()), Math.max(a.getY(), c.getY()), Math.max(a.getZ(), c.getZ()));
		replant = !p.has("replant") || p.get("replant").getAsBoolean();
		this.primitive = primitive;
		crop = p.has("crop") ? p.get("crop").getAsString() : null;
		limit = p.has("count") ? p.get("count").getAsInt() : Integer.MAX_VALUE;
	}

	int harvestedTotal() {
		return harvested.values().stream().mapToInt(Integer::intValue).sum();
	}

	/** A crop this call may take: the kind asked for, mature, and (harvest_crop) on farmland. */
	boolean wanted(Level lv, BlockPos q, BlockState st) {
		return st.getBlock() instanceof CropBlock && st.hasProperty(CropBlock.AGE)
				&& wanted(id(st), st.getValue(CropBlock.AGE), lv.getBlockState(q.below()).is(Blocks.FARMLAND), crop, primitive);
	}

	static final int MAX_AGE = 7; // wheat, carrots and potatoes: blockstate age 0..7, harvestable at 7

	/** Pure (S93): the crop block id, its blockstate age, farmland under it; crop = the kind asked for (null = any of SEED's). */
	static boolean wanted(String id, int age, boolean farmlandBelow, String crop, boolean primitive) {
		return SEED.containsKey(id) && (crop == null || crop.equals(id)) && age >= MAX_AGE && (!primitive || farmlandBelow);
	}

	static final Set<String> DROPS = Set.of("minecraft:wheat", "minecraft:wheat_seeds", "minecraft:carrot", "minecraft:potato", "minecraft:poisonous_potato");
	static final double PICKUP = 1.2; // the vanilla pickup box reaches 1.3 from her centre on x and z: a margin
	static final int SWEEPS = 8, SWEEP_SETTLE = 10;
	final Set<Integer> swept = new HashSet<>();
	int sweepSettle;

	/**
	 * S96 lane B, clean floor (3 harvested, wheat +1, no seeds): the walk to drops never targets one on a plot, and most land
	 * there. So once the harvest is done, before replanting: for each crop drop left in the box (+1 around it), walk within
	 * pickup range of it, from a dry cell if one reaches, else a flat farmland cell (a drop on a row behind a water ditch has no dry
	 * cell in range; flat walking never tramples, and dryTo keeps the path out of the ditch). Other crop drops lying there are
	 * taken too (kickoff O278), counted apart from what was harvested. Null = nothing left to fetch.
	 */
	Step sweep(BodyServices b, LocalPlayer p) {
		if (sweepSettle > 0) { sweepSettle--; return Step.running(); } // the pickup lands a few ticks after the walk
		Level lv = p.level();
		if (swept.size() >= SWEEPS + 2 * harvestedTotal()) return null; // the cap is on the extras: a big harvest's own drops still fit
		for (ItemEntity e : lv.getEntitiesOfClass(ItemEntity.class, new net.minecraft.world.phys.AABB(lo.getX() - 1, lo.getY(), lo.getZ() - 1, hi.getX() + 2, hi.getY() + 2, hi.getZ() + 2),
				e -> DROPS.contains(Menus.id(e.getItem())) && !e.isInWater() && !swept.contains(e.getId()))) {
			swept.add(e.getId());
			int y = e.blockPosition().getY();
			BlockPos her = p.blockPosition();
			BlockPos s = dropStand(e.getX(), e.getZ(), y, her, c -> walkable(lv, c) && !lv.getBlockState(c.below()).is(Blocks.FARMLAND) && dryTo(p, c));
			if (s == null) s = dropStand(e.getX(), e.getZ(), y, her, c -> walkable(lv, c) && dryTo(p, c));
			if (s == null) continue;
			sweptItems.merge(Menus.id(e.getItem()), e.getItem().getCount(), Integer::sum);
			stage = "pickup";
			sweepSettle = SWEEP_SETTLE;
			child = b.run("walk_to", Gather.at(net.minecraft.world.phys.Vec3.atBottomCenterOf(s), 0.5));
			return Step.running();
		}
		return null;
	}

	final Map<String, Integer> sweptItems = new TreeMap<>();

	/**
	 * Pure: the walkable cell nearest her with the drop (x, z, block level y) inside the pickup box, or null. A drop resting on
	 * farmland (y .94) has the farmland's own block as its level, so the cell to stand in is that level or the one above.
	 */
	static BlockPos dropStand(double x, double z, int y, BlockPos her, java.util.function.Predicate<BlockPos> ok) {
		BlockPos best = null;
		int bc = Integer.MAX_VALUE;
		for (int dy = 0; dy <= 1; dy++)
			for (int cx = (int) Math.floor(x) - 2; cx <= (int) Math.floor(x) + 2; cx++)
				for (int cz = (int) Math.floor(z) - 2; cz <= (int) Math.floor(z) + 2; cz++) {
					if (Math.abs(cx + 0.5 - x) > PICKUP || Math.abs(cz + 0.5 - z) > PICKUP) continue;
					BlockPos c = new BlockPos(cx, y + dy, cz);
					int d = MineResource.cost(her, c);
					if (d < bc && ok.test(c)) { bc = d; best = c; }
				}
		return best;
	}

	/** Pure: harvest_crop's box limit, 32x4x32 at most (inclusive corners). */
	static boolean badArea(BlockPos lo, BlockPos hi) {
		long n = (long) (hi.getX() - lo.getX() + 1) * (hi.getY() - lo.getY() + 1) * (hi.getZ() - lo.getZ() + 1);
		return hi.getX() - lo.getX() >= 32 || hi.getZ() - lo.getZ() >= 32 || hi.getY() - lo.getY() >= 4 || n > MAX_BLOCKS;
	}

	/**
	 * Pure: why a harvested plot cannot be replanted, or null to plant it now. seeds = the matching seed she holds (pooled from
	 * every plot's drops, since plots are replanted after the whole harvest; a loose seed nearby is fetched once before this says no).
	 */
	static String replantWhy(String crop, boolean farmland, int seeds) {
		if (!farmland) return "no farmland (trampled?)";
		if (seeds > 0) return null;
		return "no " + SEED.get(crop);
	}

	@Override
	public Step tick(BodyServices b) {
		LocalPlayer p = b.player();
		Level lv = p.level();
		if (before == null && settle++ == 0) Menus.resync(b); // a fresh inventory view before the baseline (see Menus.resync)
		if (before == null && settle <= Menus.SETTLE_TICKS) return Step.running();
		if (before == null) {
			if (badArea(lo, hi)) return end(b, Step.failed("bad_area", "box is larger than 32x4x32"));
			before = b.inventoryCounts();
			farmland0 = farmland(lv);
		}
		if (child != null) {
			Step s = child.tick();
			if (!s.done()) return s;
			child = null;
			Step r = after(b, p, s);
			if (r != null) return r;
		}
		// replant first (the plot is fresh in reach), then the next mature crop
		if (pickupAround != null) { // the harvest's drops: Baritone walks onto dry cells only (farmland is walkable; no jumping)
			ItemEntity drop = p.level().getEntitiesOfClass(ItemEntity.class, new net.minecraft.world.phys.AABB(pickupAround).inflate(2.5),
					e -> !e.isInWater() && p.level().getBlockState(e.blockPosition()).getFluidState().isEmpty()
							&& !p.level().getBlockState(e.blockPosition().below()).is(Blocks.FARMLAND)
							&& !p.level().getBlockState(e.blockPosition()).is(Blocks.FARMLAND) // never onto farmland (a9: trample)
							&& dryTo(p, e.blockPosition())) // nor across water to reach it (45L a1)
					.stream().min(java.util.Comparator.comparingDouble(e -> e.distanceToSqr(p))).orElse(null);
			if (drop == null || ++pickups > 4) pickupAround = null;
			else {
				stage = "pickup";
				child = b.run("walk_to", Gather.at(drop.position(), 0.5));
				return Step.running();
			}
		}
		// harvest every mature crop first, then replant from the pooled drops (a9: a plot replanted straight after its own
		// harvest found no seed while other plots' seeds were still to come)
		BlockPos next = harvestedTotal() >= limit ? null : nextMature(lv, p); // O94: count reached: replant what was taken, then stop
		if (next == null) { // S96: the drops first, so their seeds are there to replant from
			Step sw = sweep(b, p);
			if (sw != null) return sw;
		}
		if (next == null && replant && !toPlant.isEmpty()) return plant(b, p, toPlant.peek());
		if (next == null && replant && !retriedSeeds) { // seeds picked up after a plot was skipped for want of one: retry once
			retriedSeeds = true;
			for (int i = notReplanted.size() - 1; i >= 0; i--) {
				JsonObject o = notReplanted.get(i).getAsJsonObject();
				String crop = o.get("crop").getAsString();
				if (o.get("reason").getAsString().startsWith("no ") && b.inventoryCounts().getOrDefault(SEED.get(crop), 0) > 0) {
					JsonObject q = o.getAsJsonObject("pos");
					toPlant.add(new BlockPos(q.get("x").getAsInt(), q.get("y").getAsInt(), q.get("z").getAsInt()));
					notReplanted.remove(i);
				}
			}
			if (!toPlant.isEmpty()) return Step.running();
		}
		if (next == null) {
			int left = harvestedTotal() >= limit ? 0 : matureLeft(lv).size();
			if (left > 0) return end(b, Step.failed("partial", left + " mature crop(s) not harvested (unreachable or refused)"));
			if (limit != Integer.MAX_VALUE && harvestedTotal() < limit) // O94: fewer mature crops than asked for
				return end(b, Step.failed(harvestedTotal() > 0 ? "partial" : "not_found", harvestedTotal() + "/" + limit + " " + (crop != null ? crop : "crops")
						+ " harvested; no more mature ones on farmland in the box"));
			return end(b, notReplanted.isEmpty() ? Step.ok() : Step.failed("partial", notReplanted.size() + " plot(s) not replanted"));
		}
		if (p.getInventory().getFreeSlot() < 0) return end(b, Step.failed("inventory_full", "no free slot for the harvest"));
		tried.add(next);
		target = next;
		cropAt.put(next, id(lv.getBlockState(next)));
		return reach(b, p, next, "harvest");
	}

	/** Walks within reach of pos (if needed), then starts the stage's body skill. */
	Step reach(BodyServices b, LocalPlayer p, BlockPos pos, String what) {
		// the body aims at the crop's centre (mine_block) or the farmland's top face (use_item): a crop in between blocks the
		// crosshair (lane B a1: "use_item obstructed" on the far row). So stand where the line of sight is clear, off farmland.
		boolean plant = !what.equals("harvest");
		BlockPos click = plant ? pos.below() : pos;
		Vec3 point = plant ? new Vec3(pos.getX() + 0.5, pos.getY() - 0.1, pos.getZ() + 0.5) : Vec3.atCenterOf(pos);
		if (!(p.getEyePosition().distanceTo(point) <= 4.2 && clear(p, p.getEyePosition(), point, click) && !badSpots.contains(cell(p, pos.getY())))
				&& !walkedFor(pos, what)) {
			BlockPos spot = standSpot(p, pos, point, click);
			lastSpot = spot;
			legs = 0;
			BlockPos goal = spot == null ? pos : java.util.Objects.requireNonNullElse(leg(p, spot), spot); // standSpot only offers dry-reachable spots
			JsonObject a = Gather.at(new Vec3(goal.getX() + 0.5, goal.getY(), goal.getZ() + 0.5), spot != null ? 0.5 : 2.5);
			walked = pos.asLong() * 31 + (plant ? 1 : 0);
			if (walks.size() < 40) {
				JsonObject w = new JsonObject();
				w.addProperty("plot", pos.toShortString());
				w.addProperty("stage", what);
				w.addProperty("spot", spot == null ? "none" : spot.toShortString());
				w.addProperty("from", p.blockPosition().toShortString());
				walks.add(w);
			}
			stage = "walk-" + what;
			child = b.run("walk_to", a);
			return Step.running();
		}
		stage = what;
		if (what.equals("harvest")) {
			JsonObject a = new JsonObject();
			a.add("pos", json(pos));
			a.addProperty("expect_block", cropAt.get(pos));
			// no drop walk: the body's straight walk to drops stepped into the water ditch and jumped out onto farmland
			// (lane B a7: trampled; a6: Baritone found no path out of the ditch). Drops are picked up below, dry cells only.
			a.addProperty("collect", false);
			child = b.run("mine_block", a);
		} else {
			JsonObject a = new JsonObject();
			a.add("target_pos", json(pos.below()));
			a.addProperty("face", "up");
			child = b.run("use_item", a);
		}
		return Step.running();
	}

	/** Handles a finished child; null = carry on with the next decision. */
	Step after(BodyServices b, LocalPlayer p, Step s) {
		boolean ok = "ok".equals(s.status());
		if ("zone_denied".equals(s.code())) return end(b, Step.failed("zone_denied", "the server refused " + stage + " at " + target.toShortString()));
		switch (stage) {
			case "walk-harvest", "walk-plant" -> {
				if (ok && lastSpot != null && !cell(p, lastSpot.getY()).equals(lastSpot) && ++legs < 8) { // next leg of the dry route
					BlockPos l = leg(p, lastSpot);
					if (l != null) {
						child = b.run("walk_to", Gather.at(new Vec3(l.getX() + 0.5, l.getY(), l.getZ() + 0.5), 0.5));
						return Step.running();
					}
				}
				if (!walks.isEmpty()) {
					JsonObject w = walks.get(walks.size() - 1).getAsJsonObject();
					if (!w.has("result")) {
						w.addProperty("result", s.status() + (s.code() != null ? "/" + s.code() : ""));
						w.addProperty("at", p.blockPosition().toShortString());
					}
				}
				if (!ok && lastSpot != null && badSpots.add(lastSpot) && ++spotTries < 3) {
					walked = Long.MIN_VALUE; // Baritone could not reach that spot: try the next best one
					return reach(b, p, target, stage.substring(5));
				}
				spotTries = 0;
				if (!ok && !p.isWithinBlockInteractionRange(target, 0)) {
					if (stage.equals("walk-plant")) skipPlant(target, "unreachable (walk " + s.status() + "/" + s.code() + ": " + s.note() + ")");
					if (++misses >= 4) return end(b, Step.failed("unreachable", "4 plots in a row out of reach"));
				} else {
					return reach(b, p, target, stage.substring(5));
				}
			}
			case "harvest" -> {
				if (primitive && "zone_denied".equals(s.code())) // O94 harvest_crop: the server's first refusal ends it, its rule id in the note
					return end(b, Step.failed("zone_denied", "the server refused breaking " + target.toShortString() + (s.note() != null ? ": " + s.note() : "")));
				if (ok) {
					misses = 0;
					harvested.merge(cropAt.get(target), 1, Integer::sum);
					if (replant) toPlant.add(target);
					pickups = 0;
					pickupAround = target;
				} else if (respot(p, s)) return reach(b, p, target, "harvest");
				else misses++;
			}
			case "pickup" -> {}
			case "plant" -> {
				BlockState st = p.level().getBlockState(target);
				if (ok && st.getBlock() instanceof CropBlock) {
					replanted.merge(cropAt.get(target), 1, Integer::sum);
					toPlant.poll();
				} else if (primitive && "zone_denied".equals(s.code())) { // O94: e.g. crop:untagged_farmland - recorded, then the end
					skipPlant(target, "use_item zone_denied" + (s.note() != null ? ": " + s.note() : ""));
					toPlant.poll();
					return end(b, Step.failed("zone_denied", "the server refused replanting " + target.toShortString() + (s.note() != null ? ": " + s.note() : "")));
				} else if (respot(p, s)) return reach(b, p, target, "plant");
				else skipPlant(target, "use_item " + s.status() + "/" + s.code());
			}
			default -> {}
		}
		return null;
	}

	Step plant(BodyServices b, LocalPlayer p, BlockPos pos) {
		target = pos;
		String seed = SEED.get(cropAt.get(pos));
		if (!p.level().getBlockState(pos.below()).is(Blocks.FARMLAND)) {
			skipPlant(pos, replantWhy(cropAt.get(pos), false, 1));
			return Step.running();
		}
		int hb = b.hotbarSlot(seed);
		if (hb < 0) {
			int inv = -1;
			for (int i = 9; i < 36; i++) if (Menus.id(p.getInventory().getItem(i)) != null && Menus.id(p.getInventory().getItem(i)).equals(seed)) inv = i;
			if (inv < 0) {
				// a dropped seed nearby on dry ground (the harvest drops are not collected by mine_block any more): fetch it once
				ItemEntity loose = seedRetried.contains(pos) ? null : p.level().getEntitiesOfClass(ItemEntity.class,
						new net.minecraft.world.phys.AABB(pos).inflate(5), e -> seed.equals(Menus.id(e.getItem())) && !e.isInWater()
								&& p.level().getBlockState(e.blockPosition()).getFluidState().isEmpty() && dryTo(p, e.blockPosition()))
						.stream().findFirst().orElse(null);
				if (loose != null) {
					seedRetried.add(pos);
					stage = "pickup";
					child = b.run("walk_to", Gather.at(loose.position(), 0.5));
					return Step.running();
				}
				skipPlant(pos, replantWhy(cropAt.get(pos), true, 0));
				return Step.running();
			}
			if (p.containerMenu != p.inventoryMenu) b.closeMenu();
			int to = p.getInventory().getSelectedSlot();
			b.clickSlot(inv, to, ContainerInput.SWAP); // the inventory menu's slot index == inventory index for 9..35
			return Step.running(); // next tick it is in the hotbar
		}
		if (p.getInventory().getSelectedSlot() != hb) {
			b.selectHotbar(hb);
			return Step.running();
		}
		return reach(b, p, pos, "plant");
	}

	final Set<BlockPos> badSpots = new HashSet<>(), seedRetried = new HashSet<>();
	BlockPos lastSpot;
	int spotTries;
	long walked = Long.MIN_VALUE; // the plot+stage we last walked for: one walk per attempt, then act from wherever she is

	boolean walkedFor(BlockPos pos, String what) {
		return walked == pos.asLong() * 31 + (what.equals("harvest") ? 0 : 1);
	}

	/** The ray from eye to point first meets the block we mean to click (or nothing before the point). */
	static boolean clear(LocalPlayer p, Vec3 eye, Vec3 point, BlockPos click) {
		BlockHitResult h = p.level().clip(new ClipContext(eye, point, ClipContext.Block.OUTLINE, ClipContext.Fluid.NONE, p));
		return h.getType() == HitResult.Type.MISS || h.getBlockPos().equals(click);
	}

	/**
	 * Nearest standing block within 3 of the plot: solid floor that is not farmland or water, air at feet and head, the eye
	 * within reach of the point with a clear line. ponytail: a 7x7 scan per plot at plot height only; terraced farms would
	 * need y +-1 too.
	 */
	BlockPos standSpot(LocalPlayer p, BlockPos pos, Vec3 point, BlockPos click) {
		Level lv = p.level();
		Map<BlockPos, BlockPos> prev = flood(p, pos.getY());
		BlockPos best = null;
		double bd = Double.MAX_VALUE;
		for (int dx = -3; dx <= 3; dx++)
			for (int dz = -3; dz <= 3; dz++) {
				BlockPos s = pos.offset(dx, 0, dz);
				BlockState floor = lv.getBlockState(s.below());
				if (floor.is(Blocks.FARMLAND) || floor.getCollisionShape(lv, s.below()).isEmpty() || !floor.getFluidState().isEmpty()) continue;
				if (!lv.getBlockState(s).getCollisionShape(lv, s).isEmpty() || !lv.getBlockState(s.above()).getCollisionShape(lv, s.above()).isEmpty()) continue;
				if (SEED.containsKey(id(lv.getBlockState(s)))) continue; // a crop cell is farmland underneath anyway
				Vec3 eye = new Vec3(s.getX() + 0.5, s.getY() + p.getEyeHeight(), s.getZ() + 0.5);
				if (badSpots.contains(s) || !prev.containsKey(s) || eye.distanceTo(point) > 4.2 || !clear(p, eye, point, click)) continue;
				int d = 0; // route length, not straight distance: the far side of a ditch is a walk around the farm
				for (BlockPos c = s; !prev.get(c).equals(c); c = prev.get(c)) d++;
				if (d < bd) { bd = d; best = s.immutable(); }
			}
		return best;
	}

	/** An obstructed dig or use (the crosshair met another crop) is retried from another spot, 3 tries a plot (45L a1). */
	boolean respot(LocalPlayer p, Step s) {
		if (!"obstructed".equals(s.code()) || obstructed.merge(target, 1, Integer::sum) >= 3) return false;
		badSpots.add(cell(p, target.getY()));
		walked = Long.MIN_VALUE;
		return true;
	}

	final Map<BlockPos, Integer> obstructed = new java.util.HashMap<>();
	int legs;

	static BlockPos cell(LocalPlayer p, int y) {
		return new BlockPos(p.getBlockX(), y, p.getBlockZ());
	}

	/** Solid floor, nothing solid at feet or head, no fluid in any of the three. Farmland counts: flat walking never tramples it. */
	static boolean walkable(Level lv, BlockPos c) {
		BlockPos f = c.below(), h = c.above();
		return !lv.getBlockState(f).getCollisionShape(lv, f).isEmpty() && lv.getBlockState(c).getCollisionShape(lv, c).isEmpty()
				&& lv.getBlockState(h).getCollisionShape(lv, h).isEmpty() && lv.getFluidState(f).isEmpty() && lv.getFluidState(c).isEmpty()
				&& lv.getFluidState(h).isEmpty();
	}

	/**
	 * Every cell of the rectangle between a and b walkable. Any cheapest Baritone path (straight and diagonal steps) stays inside
	 * that rectangle, so it cannot dip into a ditch and jump out onto farmland (45L a1: trampled at (-21,207,15)).
	 */
	static boolean rectWalkable(Level lv, BlockPos a, BlockPos b) {
		for (int x = Math.min(a.getX(), b.getX()); x <= Math.max(a.getX(), b.getX()); x++)
			for (int z = Math.min(a.getZ(), b.getZ()); z <= Math.max(a.getZ(), b.getZ()); z++)
				if (!walkable(lv, new BlockPos(x, b.getY(), z))) return false;
		return true;
	}

	static boolean dryTo(LocalPlayer p, BlockPos c) {
		return rectWalkable(p.level(), cell(p, c.getY()), c);
	}

	/** Breadth-first over walkable cells at level y from her cell: cell -> previous cell (hers -> itself). ponytail: one level, 40 out. */
	static Map<BlockPos, BlockPos> flood(LocalPlayer p, int y) {
		Level lv = p.level();
		BlockPos s = cell(p, y);
		Map<BlockPos, BlockPos> prev = new java.util.HashMap<>();
		prev.put(s, s);
		Deque<BlockPos> q = new ArrayDeque<>(List.of(s));
		while (!q.isEmpty()) {
			BlockPos c = q.poll();
			for (net.minecraft.core.Direction d : net.minecraft.core.Direction.Plane.HORIZONTAL) {
				BlockPos n = c.relative(d);
				if (prev.containsKey(n) || n.distManhattan(s) > 40 || !walkable(lv, n)) continue;
				prev.put(n, c);
				q.add(n);
			}
		}
		return prev;
	}

	/** The next walk_to goal toward spot: the furthest cell of the dry route whose rectangle with her cell is walkable. */
	static BlockPos leg(LocalPlayer p, BlockPos spot) {
		Map<BlockPos, BlockPos> prev = flood(p, spot.getY());
		if (!prev.containsKey(spot)) return null;
		BlockPos s = cell(p, spot.getY()), last = spot;
		for (BlockPos c = spot; !c.equals(s); c = prev.get(c)) {
			if (rectWalkable(p.level(), s, c)) return c;
			last = c;
		}
		return last;
	}

	void skipPlant(BlockPos pos, String why) {
		toPlant.remove(pos);
		JsonObject o = new JsonObject();
		o.add("pos", json(pos));
		o.addProperty("crop", cropAt.get(pos));
		o.addProperty("reason", why);
		notReplanted.add(o);
	}

	BlockPos nextMature(Level lv, LocalPlayer p) {
		BlockPos best = null;
		double bd = Double.MAX_VALUE;
		for (BlockPos q : BlockPos.betweenClosed(lo, hi)) {
			BlockState st = lv.getBlockState(q);
			if (tried.contains(q) || !wanted(lv, q, st)) continue;
			double d = q.distToCenterSqr(p.position());
			if (d < bd) {
				bd = d;
				best = q.immutable();
			}
		}
		return best;
	}

	List<BlockPos> matureLeft(Level lv) {
		List<BlockPos> out = new ArrayList<>();
		for (BlockPos q : BlockPos.betweenClosed(lo, hi)) {
			BlockState st = lv.getBlockState(q);
			if (wanted(lv, q, st)) out.add(q.immutable());
		}
		return out;
	}

	Set<BlockPos> farmland(Level lv) {
		Set<BlockPos> out = new HashSet<>();
		for (BlockPos q : BlockPos.betweenClosed(lo.below(), hi)) if (lv.getBlockState(q).is(Blocks.FARMLAND)) out.add(q.immutable());
		return out;
	}

	Step end(BodyServices b, Step s) {
		JsonObject e = b.evidence();
		LocalPlayer p = b.player();
		e.add("harvested", Menus.json(harvested));
		e.add("replanted", Menus.json(replanted));
		e.add("not_replanted", notReplanted);
		e.add("walks", walks); // diagnostics: plot, stage, chosen spot, start, result, where she ended
		if (p != null && farmland0 != null) {
			Level lv = p.level();
			int immature = 0;
			for (BlockPos q : BlockPos.betweenClosed(lo, hi)) {
				BlockState st = lv.getBlockState(q);
				if (SEED.containsKey(id(st)) && st.getBlock() instanceof CropBlock cb && !cb.isMaxAge(st) && !replantedHere(q)) immature++;
			}
			e.addProperty("immature_left", immature);
			List<String> lost = new ArrayList<>();
			for (BlockPos q : farmland0) if (!lv.getBlockState(q).is(Blocks.FARMLAND)) lost.add(q.toShortString());
			e.addProperty("trampled", lost.size());
			if (!lost.isEmpty()) e.addProperty("trampled_at", String.join(" ", lost));
			JsonArray nh = new JsonArray();
			for (BlockPos q : matureLeft(lv)) nh.add(json(q));
			e.add("not_harvested", nh);
			Map<String, Integer> d = Plans.diff(before, b.inventoryCounts());
			d.values().removeIf(v -> v < 0);
			e.add("drops", Menus.json(d));
		}
		if (primitive) e.addProperty("stop_reason", Gather.stopReason(s, harvestedTotal() + (limit != Integer.MAX_VALUE ? "/" + limit : "") + " harvested, "
				+ replanted.values().stream().mapToInt(Integer::intValue).sum() + " replanted"
				+ (sweptItems.isEmpty() ? "" : ", swept " + sweptItems))); // O278: apart from harvested; its own evidence key needs a registry line
		return s;
	}

	boolean replantedHere(BlockPos q) {
		return cropAt.containsKey(q) && !toPlant.contains(q) && notReplanted.asList().stream().noneMatch(o -> o.getAsJsonObject().get("pos").equals(json(q)));
	}

	@Override
	public void cancel(BodyServices b) {
		end(b, null);
	}

	static String id(BlockState st) {
		return MineResource.id(st);
	}

	static JsonObject json(BlockPos q) {
		JsonObject o = new JsonObject();
		o.addProperty("x", q.getX());
		o.addProperty("y", q.getY());
		o.addProperty("z", q.getZ());
		return o;
	}
}
