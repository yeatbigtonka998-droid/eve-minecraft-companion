package eve.skills;

import com.google.gson.JsonObject;
import eve.body.api.BodyServices;
import eve.body.api.Skill;
import eve.body.api.Step;
import eve.body.api.SubAction;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.Identifier;
import net.minecraft.tags.FluidTags;
import net.minecraft.tags.TagKey;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.entity.monster.Enemy;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.Vec3;

import java.util.ArrayDeque;
import java.util.Deque;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * prospect{block|tag, count, max_blocks, torch_every, min_health, minutes}: bounded branch mining for ore that is not
 * exposed. Digs a 1x2 tunnel straight ahead (the cardinal direction she faces) with the body's mine_block, steps in with
 * walk_to, mines every target ore the tunnel exposes within reach, places a torch every torch_every blocks, and walks
 * back to where she started. Stops (then returns) on: count reached, max_blocks broken, inventory full, the timer, or a
 * hazard ahead (lava, water, or no floor under the next step). Never digs down or up. A threat (health below min_health,
 * a hit, a hostile within 6 in sight) ends it at once where she stands: `hurt`, evidence `threat` {kind, health,
 * entity_id, type, distance} and `stop_at`, no walk back.
 */
final class Prospect implements Skill {
	final String block, tag;
	final int want, maxBlocks, torchEvery, minutes;
	final float minHealth;
	final Integer descendTo;
	final Direction forced;
	final BlockPos toward; // dig_up: climb toward this point (home or the mine entry), no walk back; null = prospect
	Integer ascendTo;
	final Deque<BlockPos> ores = new ArrayDeque<>();
	final Set<BlockPos> triedOre = new HashSet<>();
	Map<String, Integer> before;
	BlockPos start, cursor; // cursor = the floor-level cell she stands in at the tunnel face
	Direction dir;
	SubAction child;
	String stage = "init", stop, torchFail;
	boolean noTorches;
	BlockPos digging, steering;
	int steerTicks, pause, waits, idleSteps, realigns, returnWalks, sealsForward;
	static final int SEAL_MAX = 24, SEAL_RESERVE = 6; // O86u
	final java.util.Set<BlockPos> path = new java.util.HashSet<>(); // the cells she has stood in on this tunnel: never sealed
	final java.util.List<BlockPos> walked = new java.util.ArrayList<>(); // the same, in order, with the heading at each (O86v)
	final java.util.List<Direction> walkedDir = new java.util.ArrayList<>();
	int broken, length, torches, settle, oresMined;
	long t0;

	Prospect(JsonObject p) {
		block = p.has("block") ? p.get("block").getAsString() : null;
		tag = p.has("tag") ? p.get("tag").getAsString() : (p.has("block") ? null : "minecraft:iron_ores");
		want = p.get("count").getAsInt();
		maxBlocks = p.has("max_blocks") ? p.get("max_blocks").getAsInt() : 64;
		torchEvery = p.has("torch_every") ? p.get("torch_every").getAsInt() : 8;
		minHealth = p.has("min_health") ? p.get("min_health").getAsFloat() : 12f;
		minutes = p.has("minutes") ? p.get("minutes").getAsInt() : 8;
		descendTo = p.has("descend_to") ? p.get("descend_to").getAsInt() : null;
		forced = p.has("direction") ? Direction.byName(p.get("direction").getAsString()) : null;
		toward = p.has("toward") ? Menus.pos(p.getAsJsonObject("toward")) : null;
		ascendTo = toward != null ? toward.getY() : null;
	}

	/**
	 * dig_up{toward, max_blocks?, min_health?, minutes?, torch_every?} (O86, CP-40 a17: go_home home_blocked in a flooded
	 * tunnel): a 1x2 staircase up, one block a step, heading for `toward` (the larger horizontal axis first), with prospect's
	 * threat, hazard, seal, bridge and zone-edge rules; ores are not its business and it never walks back. Ends ok
	 * ("reached") at toward's level (sky over her, or the level itself), else failed with prospect's codes or not_reached.
	 */
	static Prospect digUp(JsonObject in) {
		JsonObject p = in.deepCopy();
		p.addProperty("tag", "eve:none"); // no ore target: an unknown tag matches nothing
		p.addProperty("count", 1);
		if (!p.has("max_blocks")) p.addProperty("max_blocks", 128);
		return new Prospect(p);
	}

	@Override
	public Step tick(BodyServices b) {
		LocalPlayer p = b.player();
		Level lv = p.level();
		if (before == null) {
			if (settle++ == 0) Menus.resync(b);
			if (settle <= Menus.SETTLE_TICKS) return Step.running();
			before = b.inventoryCounts();
			start = feetCell(p);
			cursor = start;
			path.add(start);
			dir = forced != null ? forced : p.getDirection(); // horizontal facing unless the caller names the heading
			if (toward != null && java.util.stream.IntStream.range(0, 9).noneMatch(i -> {
				String id = Menus.id(p.getInventory().getItem(i));
				return id != null && id.endsWith("_pickaxe");
			})) return end(b, Step.failed("no_tool", "no pickaxe in the hotbar")); // O86a: the mind retools first
			if (toward != null) { // dig_up: the larger horizontal axis toward the target first
				int dx = toward.getX() - start.getX(), dz = toward.getZ() - start.getZ();
				dir = Math.abs(dx) >= Math.abs(dz) ? (dx >= 0 ? Direction.EAST : Direction.WEST) : (dz >= 0 ? Direction.SOUTH : Direction.NORTH);
			}
			t0 = System.nanoTime();
			walked.add(start);
			walkedDir.add(dir);
			stage = "dig";
		}
		// danger every tick, also while a dig, a step or a walk runs. CP-40 a9: a zombie in the tunnel took her from >= 12 to 5
		// inside one child, and the between-steps check never ran (death). Stop where she stands, with no walk back and the mob
		// behind her, so the mind can fight or flee at once.
		JsonObject t = threat(p);
		if (t != null) {
			if (child != null) child.cancel();
			child = null;
			steering = null;
			stop = "hurt";
			b.evidence().add("threat", t); // O58: the mind's runner fights or flees from this and resumes from stop_at
			return end(b, Step.failed("hurt", t.get("kind").getAsString() + (t.has("type") ? " " + t.get("type").getAsString() + " at "
					+ t.get("distance").getAsDouble() : "") + ", health " + t.get("health").getAsFloat() + "; stopped where she stands"));
		}
		if (steering != null) { // one-block step by direct steering (a Baritone path per step cost ~90 ticks a block: CP-40 a5)
			Vec3 c = Vec3.atBottomCenterOf(steering);
			double dh = Math.hypot(p.getX() - c.x, p.getZ() - c.z);
			if (dh < 0.3 && p.onGround() && Math.abs(p.getY() - c.y) < 0.6) {
				steering = null;
				Step r = after(b, p, Step.ok());
				if (r != null) return r;
			} else if (++steerTicks > 40) { // not there in 2 s: let Baritone do this step
				child = b.run("walk_to", Gather.at(c, 0.9));
				steering = null;
				return Step.running();
			} else return Step.running(b.steerTo(c));
		}
		if (child != null) {
			Step s = child.tick();
			if (!s.done()) return s;
			child = null;
			Step r = after(b, p, s);
			if (r != null) return r;
		}
		if (stage.equals("home")) return end(b, stop.equals("count") || stop.equals("reached") ? Step.ok() : outcome());
		if (stage.equals("retreat")) return retreat(b, p, lv);
		if (pause > 0) { pause--; return Step.running(); }
		// plan only from where she stands: the current pushed her back down her tunnel while she dug or sealed (CP-40 a19: she
		// at z -33 with the cursor at -37, its digs and seals out of reach; dig_up a block off its cursor aimed through the block
		// over her own head), or an ore pickup walked her off it. Step back onto the cursor first, 3 tries in a row at most.
		if (stop == null && !feetCell(p).equals(cursor)) {
			if (++realigns > 3) stop = "blocked:walk: pushed off the tunnel at " + feetCell(p).toShortString() + ", cursor " + cursor.toShortString();
			else {
				stage = "realign";
				steering = cursor;
				steerTicks = 0;
				return Step.running(b.steerTo(Vec3.atBottomCenterOf(cursor)));
			}
		} else realigns = 0;
		// dig_up: open floor ahead at her own level (the pocket she is shut in, a cave) is walked flat to its wall first; a
		// climb step there would stand on air (digup a54 u_edge: hazard:no_floor in a 3-wide pocket). Fixed until the step ends.
		BlockPos ahead = cursor.relative(dir);
		flat = ascending() && lv.getBlockState(ahead).getCollisionShape(lv, ahead).isEmpty() && solid(lv, ahead.below());
		if (stop == null) {
			Step f = fill(b, p, lv); // O71: seal visible water touching the next step, bridge a single missing floor cell
			if (f != null) return f;
			stop = stopReason(b, p, lv);
		}
		if (stop != null && toward != null) { stage = "home"; return Step.running(); } // dig_up: she stays where she got to
		if (stop != null && !stage.equals("return")) {
			stage = "return";
			sealsForward = seals;
			if (p.blockPosition().distManhattan(start) <= 1) { stage = "home"; return Step.running(); }
			child = b.run("walk_to", Gather.at(Vec3.atBottomCenterOf(start), 1.0));
			return Step.running();
		}
		// ores the tunnel exposed, within reach: mine them first
		BlockPos ore = nextOre(p, lv);
		if (ore != null) {
			digging = ore;
			stage = "ore";
			child = b.run("mine_block", mine(ore, lv));
			return Step.running();
		}
		// the face ahead: a 1x2 tunnel at her level, or while above descend_to one stair step down (head room for the step:
		// the two blocks at her level ahead and the one below them)
		boolean down = descendTo != null && cursor.getY() > descendTo, up = climbing();
		BlockPos feet = stepCell(), head = feet.above();
		// her own cell first, then the face. Short grass, a flower or a snow layer has no collision but has an outline, and the
		// outline takes the crosshair aimed past it (CP-40 a8: blocked:obstructed on the first dig at a grassy surface entry).
		// Up: the cell over her head first (head room for the hop), then the raised step, its lower cell first: from below,
		// the line to the upper one crosses the lower (digup a54 u_flood: obstructed on 84,31 aiming at 84,32).
		BlockPos[] cells = down ? new BlockPos[] {cursor.above(), cursor, head.above(), head, feet}
				: up ? new BlockPos[] {cursor.above(), cursor, cursor.above(2), feet, head} : new BlockPos[] {cursor.above(), cursor, head, feet};
		for (BlockPos q : cells) {
			BlockState st = lv.getBlockState(q);
			boolean hers = q.equals(cursor) || q.equals(cursor.above()); // never the slab or carpet she stands on: collision-free only
			if (!st.isAir() && !st.getShape(lv, q).isEmpty() && st.getFluidState().isEmpty() && (!hers || st.getCollisionShape(lv, q).isEmpty())) {
				digging = q;
				stage = "dig";
				child = b.run("mine_block", face(q, lv));
				return Step.running();
			}
		}
		// step in (the face is open)
		stage = "step";
		steering = feet;
		steerTicks = 0;
		return Step.running(b.steerTo(Vec3.atBottomCenterOf(feet)));
	}

	Step after(BodyServices b, LocalPlayer p, Step s) {
		boolean ok = "ok".equals(s.status());
		if (stage.equals("rseal")) { // O86u: a reserve seal on the way back (any result, a refusal too): walk again
			if (ok) { seals++; placed.add(filling.immutable()); }
			else unfillable.add(filling);
			returnWalks++;
			stage = "return";
			child = b.run("walk_to", Gather.at(Vec3.atBottomCenterOf(start), 1.0));
			return Step.running();
		}
		if (stage.equals("torch") && !ok) { // a refused or failed torch never stops the tunnel: no more torches (a3: place cap)
			torchFail = s.code() + (s.note() != null ? ": " + s.note() : "");
			noTorches = true;
			return null;
		}
		// body's mine_block reads "not air after the break" as a revert, but water flowing into the opened cell is not one
		// (O71 lane run: zone_denied "server restored minecraft:water"): a broken block, the seal comes next
		// O175: nor is sand or gravel falling back into it from above - the next dig takes the fallen block
		if ("zone_denied".equals(s.code()) && (stage.equals("dig") || stage.equals("ore")) && digging != null
				&& (p.level().getFluidState(digging).is(FluidTags.WATER)
				|| p.level().getBlockState(digging).getBlock() instanceof net.minecraft.world.level.block.FallingBlock
				|| !p.level().getEntitiesOfClass(net.minecraft.world.entity.item.FallingBlockEntity.class, new AABB(digging).inflate(0, 2, 0)).isEmpty())) {
			broken++;
			if (stage.equals("ore")) triedOre.add(digging);
			return null;
		}
		if ("zone_denied".equals(s.code())) { // the rolling break cap is a refusal too: say which (sense rule limit:...)
			boolean cap = s.note() != null && s.note().contains("limit:");
			// O85: the forward face refused at the zone's edge: turn 90 degrees at this depth and go on, left first, right if
			// the first turned dig is refused too, at most 2 turns a call (CP-40 a16 rerun: east ran its 19 blocks of room
			// into the edge while 63 blocks of zone lay to the north at that depth). An ore outside the zone is just skipped.
			if (!cap && stage.equals("ore")) { triedOre.add(digging); return null; }
			if (!cap && stage.equals("filler")) return null; // no filler from there: planks are the last resort
			if (!cap && stage.equals("dig") && pendingTurn == null && turns < 2) {
				turnBase = dir;
				dir = dir.getCounterClockWise();
				pendingTurn = "left";
				turns++;
				return null;
			}
			if (!cap && stage.equals("dig") && "left".equals(pendingTurn)) {
				dir = turnBase.getClockWise();
				pendingTurn = "right";
				return null;
			}
			stop = cap ? "capped" : "zone_denied";
			return null;
		}
		switch (stage) {
			case "dig" -> {
				// a living blocker (an animal wandering past at a grassy entry) is transient: wait a second and dig again, 5 times
				// at most (O41n). Never steps out of her line, so the zone is unchanged.
				if (!ok && "obstructed".equals(s.code()) && livingOnLine(p, digging) && ++waits <= 5) { pause = 20; return null; }
				// the child's note says what was in the way ("crosshair on ..."): CP-40 a10 stopped blocked:obstructed with no clue
				if (!ok) {
					stop = "no_tool".equals(s.code()) ? "no_tool" : "blocked:" + s.code() + (s.note() != null ? ": " + s.note() : "")
							+ " [dig " + digging.toShortString() + ", cursor " + cursor.toShortString() + ", she at " + feetCell(p).toShortString()
							+ ", steps " + trail + "]";
					return null;
				}
				broken++;
				waits = 0;
				pendingTurn = null; // the turned face is inside the zone: committed to it
			}
			case "ore" -> {
				triedOre.add(digging);
				if (ok) { broken++; oresMined++; }
			}
			case "step" -> {
				BlockPos stepTo = stepCell();
				// on open surface terrain (CP-40 a3: the mine entry is on a hill) the exact cell can be unreachable for
				// Baritone while she stands right next to it: within one block counts, and her real cell becomes the cursor
				if (feetCell(p).distManhattan(stepTo) > 1) {
					stop = "blocked:walk:" + s.code() + (s.note() != null ? ":" + s.note() : "") + " " + scene(p, p.level(), stepTo); // O86u
					return null;
				}
				// a step that left her in the same cell is no step: the within-one rule accepted it every time and the tunnel
				// "grew" 29 steps in place (cp40p a30 p_stepdown #2, cancelled). Three in a row stop it as blocked.
				BlockPos here = feetCell(p).distManhattan(stepTo) == 0 ? stepTo : feetCell(p);
				trail.add(stepTo.toShortString() + ">" + here.toShortString() + "@" + Math.round(p.getX() * 10) / 10.0 + "," + Math.round(p.getY() * 10) / 10.0
						+ (s.code() != null ? "/" + s.code() : ""));
				if (trail.size() > 8) trail.remove(0);
				if (here.equals(cursor)) {
					if (++idleSteps >= 3) stop = "blocked:walk: no progress after 3 steps at " + here.toShortString() + " " + scene(p, p.level(), stepTo);
					return null;
				}
				idleSteps = 0;
				cursor = here;
				path.add(here);
				walked.add(here);
				walkedDir.add(dir);
				length++;
				scanOres(p, p.level());
				if (torchEvery > 0 && !noTorches && length % torchEvery == 0) return torch(b, p);
			}
			// a block drop is picked up 10 ticks after it pops at the earliest (and the ceiling's falls first): wait 1.5 s before
			// deciding there is no filler (cp40p a51 p_nofiller: hazard:no_floor 2 s in, the drop still on the ground)
			case "filler" -> { if (ok) { broken++; pause = 30; } }
			case "seal", "bridge" -> {
				if (ok) placed.add(filling.immutable());
				if (ok && stage.equals("seal")) seals++;
				else if (ok) bridged++;
				else {
					unfillable.add(filling); // no support in sight, occupied, ...: stopReason reports the hazard
					fillFail = s.status() + "/" + s.code() + (s.note() != null ? ": " + s.note() : "") + " at " + filling.toShortString();
				}
			}
			case "torch" -> {
				if (ok) torches++;
				else torchFail = s.code() + (s.note() != null ? ": " + s.note() : "");
			}
			case "return" -> { stage = !ok && returnWalks < 3 ? "retreat" : "home"; } // O86u: a failed walk back may seal and retry
			default -> {}
		}
		return null;
	}

	/** A torch on the tunnel floor one step behind her (its floor is the solid block below). */
	Step torch(BodyServices b, LocalPlayer p) {
		if (b.hotbarSlot("minecraft:torch") < 0 && Gather.toHand(b, p, "minecraft:torch") < 0) { torchFail = "no torch"; return null; }
		BlockPos at = cursor.relative(dir.getOpposite());
		if (!p.level().getBlockState(at).isAir()) at = at.above(); // a staircase: the step behind is solid, its head room is not
		if (!p.level().getBlockState(at).isAir()) { torchFail = "cell behind not air"; return null; }
		JsonObject a = new JsonObject();
		a.add("pos", HarvestReplant.json(at));
		a.addProperty("item", "minecraft:torch");
		stage = "torch";
		child = b.run("place_block", a);
		return Step.running();
	}

	/**
	 * {kind: health|hit|hostile, health, entity_id, type, distance} or null = carry on. health = under min_health; hit = damage
	 * just taken; hostile = an Enemy within 6 in her line of sight. The entity is that one, or else the nearest Enemy within 8
	 * (the client is not told who hit her, so the nearest is the likely attacker).
	 */
	JsonObject threat(LocalPlayer p) {
		Entity near = null, seen = null;
		for (Entity e : p.level().getEntities(p, p.getBoundingBox().inflate(8), e -> e instanceof Enemy && e.isAlive())) {
			if (near == null || e.distanceTo(p) < near.distanceTo(p)) near = e;
			if (e.distanceTo(p) <= 6 && (seen == null || e.distanceTo(p) < seen.distanceTo(p)) && p.hasLineOfSight(e)) seen = e;
		}
		// drowning: two seconds of air gone (O86: in a17 body's drowning reflex fired before prospect noticed the water)
		String kind = p.getHealth() < minHealth ? "health" : p.hurtTime > 0 && p.getHealth() < p.getMaxHealth() ? "hit" : seen != null ? "hostile"
				: p.getAirSupply() < p.getMaxAirSupply() - 40 ? "drowning" : null;
		if (kind == null) return null;
		Entity e = seen != null ? seen : near;
		JsonObject t = new JsonObject();
		t.addProperty("kind", kind);
		t.addProperty("health", p.getHealth());
		if (e != null) {
			t.addProperty("entity_id", e.getId());
			t.addProperty("type", e.getType().toShortString());
			t.addProperty("distance", Math.round(e.distanceTo(p) * 10) / 10.0);
		}
		return t;
	}

	/**
	 * The cell she stands in, judged by the block that carries her: at an edge her centre (blockPosition) can be over the
	 * air beside it (CP-40 a13-a17: north planned from (44,71,-18) over a cliff, a 4-block drop, no_floor at once; the
	 * stone she stood on was under (44,71,-17)).
	 */
	static BlockPos feetCell(LocalPlayer p) {
		// from her real position, not getOnPos(): its supporting-block cache ran 2-3 cells ahead of her after a bridge, and the
		// steps it accepted left the tunnel cursor out of her reach (cp40p a47 p_stairdrop 1/6, "cursor 116,34, she at 113,37")
		Level lv = p.level();
		BlockPos c = p.blockPosition();
		if (!p.onGround() || solid(lv, c.below())) return c;
		AABB feet = p.getBoundingBox().move(0, -0.1, 0).setMaxY(p.getY() + 0.1); // the slab under her soles
		for (Direction d : Direction.Plane.HORIZONTAL) {
			BlockPos n = c.relative(d);
			if (solid(lv, n.below()) && feet.intersects(new AABB(n.below()))) return n; // at an edge: the cell whose floor carries her
		}
		return c;
	}

	/** A living entity (not her; a dying one still takes the crosshair for its death animation) whose box the line from her eye to the block's centre passes through. */
	static boolean livingOnLine(LocalPlayer p, BlockPos q) {
		Vec3 eye = p.getEyePosition(), to = Vec3.atCenterOf(q);
		return !p.level().getEntities(p, new AABB(eye, to).inflate(1),
				e -> e instanceof LivingEntity && !e.isRemoved() && e.getBoundingBox().inflate(0.1).clip(eye, to).isPresent()).isEmpty();
	}

	String stopReason(BodyServices b, LocalPlayer p, Level lv) {
		if (toward != null && reached(p, lv)) return "reached";
		if (got(b) >= want && toward == null) return "count";
		if (broken >= maxBlocks) return "max_blocks";
		if (p.getInventory().getFreeSlot() < 0) return "inventory_full";
		if ((System.nanoTime() - t0) / 60_000_000_000L >= minutes) return "timer";
		BlockPos[] t = touching();
		// O175 build guard, lava on any face, O93 water beside the step (inflow floods the tunnel and pushes her off the face;
		// water in the tunnel line keeps O71's seal-and-continue; dig_up's climb keeps sealing), a tall sand/gravel column: S93's
		// pure stepHazard, the same rules the synthetic-world tests drive
		String h = stepHazard(cells(lv), t, climbing(), descendTo != null && cursor.getY() > descendTo, placed, path, toward == null);
		if (h != null) return h;
		// water fill() could not seal (no filler, the seal limit reached, no support). Unseen water behind rock waits for the dig.
		if (!waterCells(p, lv).isEmpty()) return "hazard:water"; // water fill() could not seal (no filler, sealLimit() reached, no support)
		// a missing floor stops unless: the drop is a single block (she steps down), or it is within O74's limits and its floor
		// cell can still be bridged (not yet in sight because the step is rock, or a side wall face in plain view)
		if (!solid(lv, t[5]) && lv.getFluidState(t[5]).isEmpty()) {
			BlockPos land = landing(lv, t[0]);
			boolean depthOk = withinStepDown(lv, t[0], land);
			boolean oneDown = depthOk && land.getY() >= cursor.getY() - 1;
			boolean canBridge = depthOk && bridged < 8 && !unfillable.contains(t[5]) && (fillerAvailable || !fillerDug)
					&& (!stepOpen(lv, t) || bridgeFace(p, lv, t[5]) != null);
			if (!oneDown && !canBridge) return "hazard:no_floor";
		}
		return null;
	}

	/** The cell her feet go to on the next step: one down while above descend_to, one up while below dig_up's level. */
	BlockPos stepCell() {
		return stepCell(cursor, dir, descendTo, climbing());
	}

	boolean ascending() {
		return ascendTo != null && cursor.getY() < ascendTo;
	}

	/** This step goes up: below dig_up's level and no open floor ahead at her own level. */
	boolean climbing() {
		return ascending() && !flat;
	}

	/**
	 * The cells around the next step: its two cells, the face beyond, above, the floor (index 5) and both sides. Climbing,
	 * also the cell over her head that the hop opens and the one above it (lava there stops, water there is sealed).
	 */
	BlockPos[] touching() {
		return touching(cursor, dir, stepCell(), climbing());
	}

	/**
	 * Pure: indices 0..9 as touching() always had them (the step, its head cell, the faces beyond, over the head, the floor and
	 * both sides). S93: a step down also opens the head-room cell (index 4, the level cell ahead's top), so its ceiling, both
	 * sides and the face beyond are touched too (12..15) - lava there opened straight into the tunnel before. Climbing: the hop
	 * cells over her head (10, 11).
	 */
	static BlockPos[] touching(BlockPos cursor, Direction dir, BlockPos next, boolean climbing) {
		Direction l = dir.getCounterClockWise(), r = dir.getClockWise();
		BlockPos h = next.above(2);
		return new BlockPos[] {next, next.above(), next.relative(dir), next.above().relative(dir), h, next.below(),
				next.relative(l), next.relative(r), next.above().relative(l), next.above().relative(r),
				climbing ? cursor.above(2) : h, climbing ? cursor.above(3) : h,
				h.above(), h.relative(l), h.relative(r), h.relative(dir)};
	}

	/** Pure: the cell her feet go to on the next step (stepCell()). */
	static BlockPos stepCell(BlockPos cursor, Direction dir, Integer descendTo, boolean climbing) {
		if (descendTo != null && cursor.getY() > descendTo) return cursor.relative(dir).below();
		if (climbing) return cursor.relative(dir).above();
		return cursor.relative(dir);
	}

	/** What prospect reads of the world for a step decision: block id, lava, water. A Level in play, a map in the tests. */
	interface Cells {
		String id(BlockPos q);
		boolean lava(BlockPos q);
		boolean water(BlockPos q);
	}

	static Cells cells(Level lv) {
		return new Cells() {
			public String id(BlockPos q) { return MineResource.id(lv.getBlockState(q)); }
			public boolean lava(BlockPos q) { return lv.getFluidState(q).is(FluidTags.LAVA); }
			public boolean water(BlockPos q) { return lv.getFluidState(q).is(FluidTags.WATER); }
		};
	}

	static final int FALL_MAX = 3;
	static final Set<String> FALLING = Set.of("minecraft:sand", "minecraft:red_sand", "minecraft:gravel", "minecraft:suspicious_sand",
			"minecraft:suspicious_gravel");

	static boolean falls(String id) {
		return id != null && (FALLING.contains(id) || id.endsWith("_concrete_powder"));
	}

	/**
	 * Pure (S93): why the next step must not be dug, or null. In order: a build in the cells it opens (O175), lava on any face
	 * of any opened cell, source or flowing (never sealed), water beside the step or over its head (O93; water in the tunnel
	 * line itself is sealed by fill(), not a stop), and more than FALL_MAX sand/gravel stacked over the opened head cell (a short
	 * column falls in and is dug again; a tall one refills the tunnel until max_blocks).
	 */
	static String stepHazard(Cells w, BlockPos[] t, boolean climbing, boolean down, Set<BlockPos> placed, Set<BlockPos> path, boolean waterBeside) {
		String build = build(q -> placed.contains(q) ? "minecraft:air" : w.id(q), climbing ? new BlockPos[] {t[0], t[1], t[4], t[10], t[11]} : new BlockPos[] {t[0], t[1], t[4]});
		if (build != null) return "blocked:build: " + build;
		for (BlockPos q : t) if (w.lava(q)) return "hazard:lava at " + q.toShortString();
		if (waterBeside) for (int i : down ? new int[] {4, 6, 7, 8, 9, 12, 13, 14} : new int[] {4, 6, 7, 8, 9})
			if (w.water(t[i]) && !path.contains(t[i])) return "hazard:water: water beside the next step at " + t[i].toShortString();
		int n = 0; // the sand/gravel stacked from the cell over the opened head cell upward
		for (BlockPos q = down ? t[12] : t[4]; falls(w.id(q)) && n <= FALL_MAX; q = q.above()) n++;
		if (n > FALL_MAX) return "hazard:falling: " + n + "+ sand/gravel over the step at " + (down ? t[12] : t[4]).toShortString();
		return null;
	}

	/**
	 * The walls and ceiling of the cursor's cell and of the cell she really stands in: over her head and both sides, at feet
	 * and head height (O86: in a17 water broke in from around her, not around the next step, and nothing looked there until
	 * body's drowning reflex fired). Never a tunnel cell: behind the cursor is her way back, and once the current has pushed
	 * her back it is the tunnel ahead of her (cp40p a51 p_water2: sealed at 112 with her at 111 and the cursor at 113).
	 */
	BlockPos[] around(LocalPlayer p) {
		Direction l = dir.getCounterClockWise(), r = dir.getClockWise();
		java.util.List<BlockPos> out = new java.util.ArrayList<>();
		for (BlockPos c : new BlockPos[] {cursor, feetCell(p)}) {
			BlockPos h = c.above();
			for (BlockPos q : new BlockPos[] {h.above(), c.relative(l), c.relative(r), h.relative(l), h.relative(r)})
				if (!out.contains(q)) out.add(q);
		}
		return out.toArray(new BlockPos[0]);
	}

	/** Her eye sees the cell's centre: nothing solid on the line (water itself has no outline). */
	static boolean visible(LocalPlayer p, BlockPos q) {
		return seen(p, Vec3.atCenterOf(q), q);
	}

	/**
	 * The centre or a point just inside one of its side or top faces is in sight: for a pit's bottom cell, whose centre hides
	 * behind the corner of her own floor (cp40p a31) though place_block can click its side support. Not for water: a seal
	 * then aimed past its own new block (cp40p a33, 12 seals and obstructed).
	 */
	static boolean visibleAny(LocalPlayer p, BlockPos q) {
		Vec3 c = Vec3.atCenterOf(q);
		for (Vec3 pt : new Vec3[] {c, c.add(0.4, 0, 0), c.add(-0.4, 0, 0), c.add(0, 0, 0.4), c.add(0, 0, -0.4), c.add(0, 0.4, 0)})
			if (seen(p, pt, q)) return true;
		return false;
	}

	static boolean seen(LocalPlayer p, Vec3 pt, BlockPos q) {
		BlockHitResult h = p.level().clip(new ClipContext(p.getEyePosition(), pt, ClipContext.Block.OUTLINE, ClipContext.Fluid.NONE, p));
		return h.getType() == HitResult.Type.MISS || h.getBlockPos().equals(q);
	}

	/**
	 * O71/O86: the next placement that makes the step safe, started as body place_block with filler, or null. Water: a
	 * visible water cell touching the next step or around her own cells (never one her body is in), a source before flowing
	 * water, at most sealLimit() seals going forward (O86u). Floor (O86): the next step's missing floor cell itself is bridged, by clicking a solid
	 * side wall whose face is in plain view, once the step is open, within O74's depth limit, at most 8; a pit is never
	 * filled from its bottom; a single-block drop is just stepped down. Lava is never sealed. No filler on her: one side
	 * block of her own cell is dug first, then planks while more than 6 are left.
	 */
	Step fill(BodyServices b, LocalPlayer p, Level lv) {
		String item = filler(b, fillerDug); // planks only once a dig for filler has been tried (O86: planks last)
		fillerAvailable = filler(b, true) != null;
		BlockPos[] t = touching();
		for (BlockPos c : t) if (lv.getFluidState(c).is(FluidTags.LAVA)) return null;
		BlockPos q = null;
		Direction face = null;
		for (BlockPos c : waterCells(p, lv))
			if (q == null || lv.getFluidState(c).isSource() && !lv.getFluidState(q).isSource()) q = c;
		boolean water = q != null;
		if (q == null && !solid(lv, t[5]) && lv.getFluidState(t[5]).isEmpty() && stepOpen(lv, t) && !unfillable.contains(t[5])) {
			BlockPos land = landing(lv, t[0]);
			// bridge the floor cell unless she can simply step one block down onto the landing
			if (withinStepDown(lv, t[0], land) && !(land.getY() >= cursor.getY() - 1 && !bridgePreferred(lv, t[5]))) {
				Direction f = bridgeFace(p, lv, t[5]);
				if (f != null) { q = t[5]; face = f; }
			}
		}
		if (q == null || (water ? seals >= sealLimit() : bridged >= 8)) return null;
		if (item == null && !fillerDug) {
			fillerDug = true; // O86: no filler on her yet (a17 north, first step): dig one block nearby for one
			Direction l = dir.getCounterClockWise(), r = dir.getClockWise();
			// only blocks whose drop lands where she picks it up: beside her at feet or head height, or the ceiling over her head
			// room (its drop falls onto her), never with fluid or sand/gravel above it. A block beside her floor dropped into its
			// own 1-deep hole, out of her reach (cp40p a45 p_nofiller: 1 broken, 0 picked up).
			java.util.List<BlockPos> cand = new java.util.ArrayList<>(List.of(cursor.relative(l), cursor.relative(r), cursor.above().relative(l),
					cursor.above().relative(r)));
			for (BlockPos c : new BlockPos[] {cursor.above(2), cursor.above(3)}) {
				BlockState over = lv.getBlockState(c.above());
				if (over.getFluidState().isEmpty() && !(over.getBlock() instanceof net.minecraft.world.level.block.FallingBlock)) cand.add(c);
				if (solid(lv, c)) break; // the first solid one over her is the ceiling
			}
			for (BlockPos c : cand)
				if (solid(lv, c) && !lv.getBlockState(c).is(net.minecraft.world.level.block.Blocks.BEDROCK) && p.isWithinBlockInteractionRange(c, -0.5)) {
					digging = c;
					stage = "filler";
					child = b.run("mine_block", face(c, lv));
					return Step.running();
				}
			item = filler(b, true); // nothing to dig: planks while more than 6 are left
		}
		if (item == null) return null;
		int h = Gather.toHand(b, p, item);
		if (h < 0) return null;
		if (h == 0) return Step.running();
		filling = q;
		stage = water ? "seal" : "bridge";
		JsonObject a = new JsonObject();
		a.add("pos", HarvestReplant.json(q));
		a.addProperty("item", item);
		if (face != null) a.addProperty("face", face.getName()); // click the side wall she can see, not a support of body's choice
		child = b.run("place_block", a);
		return Step.running();
	}

	/**
	 * A single missing cell in a rock floor (rock below, ahead and on both sides) is bridged rather than stepped into, so the
	 * tunnel keeps its level (O71, p_gap).
	 */
	boolean bridgePreferred(Level lv, BlockPos fl) {
		Direction l = dir.getCounterClockWise(), r = dir.getClockWise();
		return solid(lv, fl.below()) && solid(lv, fl.relative(dir)) && solid(lv, fl.relative(l)) && solid(lv, fl.relative(r));
	}

	/** Visible water touching the next step or around her own cells, not in her body, not given up on. */
	java.util.List<BlockPos> waterCells(LocalPlayer p, Level lv) {
		java.util.List<BlockPos> out = new java.util.ArrayList<>();
		for (BlockPos[] set : new BlockPos[][] {touching(), around(p)})
			for (BlockPos c : set)
				if (lv.getFluidState(c).is(FluidTags.WATER) && !unfillable.contains(c) && !p.getBoundingBox().intersects(new AABB(c))
						&& visible(p, c) && !out.contains(c)) out.add(c);
		return out;
	}

	/** The step's two cells are open (dug or air): only then is its floor cell in view. */
	static boolean stepOpen(Level lv, BlockPos[] t) {
		return lv.getBlockState(t[0]).getCollisionShape(lv, t[0]).isEmpty() && lv.getBlockState(t[1]).getCollisionShape(lv, t[1]).isEmpty();
	}

	/**
	 * The face to click to put a block into fl: a solid horizontal neighbour whose face toward fl her eye hits first (the
	 * ray to a point just inside that face meets the neighbour, on that face). The returned direction is from the neighbour
	 * to fl, as body's place_block `face` wants it. null = no such face in plain view (CP-40 a15/a17: a pit bottom clicked
	 * from above aimed past her own floor).
	 */
	Direction bridgeFace(LocalPlayer p, Level lv, BlockPos fl) {
		for (Direction d : new Direction[] {dir.getCounterClockWise(), dir.getClockWise(), dir, dir.getOpposite()}) {
			BlockPos n = fl.relative(d);
			if (!solid(lv, n)) continue;
			Direction face = d.getOpposite(); // from n toward fl
			Vec3 pt = Vec3.atCenterOf(fl).add(d.getStepX() * 0.51, 0, d.getStepZ() * 0.51);
			BlockHitResult h = lv.clip(new ClipContext(p.getEyePosition(), pt, ClipContext.Block.OUTLINE, ClipContext.Fluid.NONE, p));
			if (h.getType() == HitResult.Type.BLOCK && h.getBlockPos().equals(n) && h.getDirection() == face
					&& p.getEyePosition().distanceTo(h.getLocation()) <= 4.4) return face;
		}
		return null;
	}

	/**
	 * O86u: seals going forward. Prospect keeps SEAL_RESERVE of SEAL_MAX for the way back and never spends them on the face
	 * (CP-40 a20: 12 seals into a zone-edge inflow, then the walk back failed in the water); dig_up's climb is itself the
	 * way out, so it may use them all.
	 */
	int sealLimit() {
		return toward != null ? SEAL_MAX : SEAL_MAX - SEAL_RESERVE;
	}

	/**
	 * O86u: the walk back failed. With seals left in reserve, seal the visible water in the walls and ceiling of her own cell
	 * (never a cell of the tunnel she walked, nor its head), a source first, then walk again; 3 walks at most.
	 */
	Step retreat(BodyServices b, LocalPlayer p, Level lv) {
		BlockPos q = null;
		if (seals < SEAL_MAX && returnWalks < 3) {
			BlockPos c = feetCell(p), h = c.above();
			for (BlockPos w : new BlockPos[] {h.above(), c.north(), c.south(), c.east(), c.west(), h.north(), h.south(), h.east(), h.west()})
				if (lv.getFluidState(w).is(FluidTags.WATER) && !path.contains(w) && !path.contains(w.below()) && !unfillable.contains(w)
						&& !p.getBoundingBox().intersects(new AABB(w)) && visible(p, w)
						&& (q == null || lv.getFluidState(w).isSource() && !lv.getFluidState(q).isSource())) q = w;
		}
		String item = q == null ? null : filler(b, true);
		if (item == null) { stage = "home"; return Step.running(); }
		int hs = Gather.toHand(b, p, item);
		if (hs < 0) { stage = "home"; return Step.running(); }
		if (hs == 0) return Step.running();
		filling = q;
		stage = "rseal";
		JsonObject a = new JsonObject();
		a.add("pos", HarvestReplant.json(q));
		a.addProperty("item", item);
		child = b.run("place_block", a);
		return Step.running();
	}

	/** O86u: where she stands and what is around a failed step: block ids, water or lava (src = source), and her steps. */
	String scene(LocalPlayer p, Level lv, BlockPos step) {
		BlockPos c = feetCell(p);
		return "[" + stand(p) + "; cursor " + cursor.toShortString() + "; step " + cell(lv, step) + ", step head " + cell(lv, step.above())
				+ ", over step head " + cell(lv, step.above(2)) + ", step floor " + cell(lv, step.below()) + ", her cell " + cell(lv, c)
				+ ", her head " + cell(lv, c.above()) + ", over her head " + cell(lv, c.above(2)) + "; steps " + trail + "]";
	}

	/** "x, y, z=id" with "/water src", "/water flow" or "/lava ..." when a fluid is there. */
	static String cell(Level lv, BlockPos q) {
		BlockState st = lv.getBlockState(q);
		net.minecraft.world.level.material.FluidState f = st.getFluidState();
		String id = st.isAir() ? "air" : MineResource.id(st).replace("minecraft:", "");
		return q.toShortString() + "=" + id + (f.isEmpty() ? "" : "/" + (f.is(FluidTags.LAVA) ? "lava" : "water") + (f.isSource() ? " src" : " flow"));
	}

	/** Her exact position, on the ground or not, in water or not. */
	static String stand(LocalPlayer p) {
		return "she stands at " + Math.round(p.getX() * 100) / 100.0 + ", " + Math.round(p.getY() * 100) / 100.0 + ", "
				+ Math.round(p.getZ() * 100) / 100.0 + (p.onGround() ? " on ground" : " not on ground") + (p.isInWater() ? ", in water" : "");
	}

	/** Filler on her: mined stone or dirt; with planks, also planks while more than 6 are left for sticks (O86); null = none. */
	String filler(BodyServices b, boolean planks) {
		Map<String, Integer> inv = b.inventoryCounts();
		for (String i : FILLERS) if (inv.getOrDefault(i, 0) > 0) return i;
		if (planks) for (Map.Entry<String, Integer> e : inv.entrySet()) if (e.getKey().endsWith("_planks") && e.getValue() > 6) return e.getKey();
		return null;
	}

	/** The feet cell she ends in stepping into next: down through air to the first solid floor, at most 3 below next; null if a
	 * fluid is in the way or there is no floor that close. */
	BlockPos landing(Level lv, BlockPos next) {
		BlockPos c = next;
		for (int k = 0; k < 4; k++) {
			if (!lv.getFluidState(c.below()).isEmpty()) return null;
			if (solid(lv, c.below())) return c;
			c = c.below();
		}
		return null;
	}

	/**
	 * O74's limits for a missing floor under the next step: solid, fluid-free ground at most 3 below her feet; under open sky
	 * any width (a hillside), underground only a 1-wide pit (rock beside every hole cell), since a wider cave mouth stops.
	 */
	boolean withinStepDown(Level lv, BlockPos next, BlockPos land) {
		if (land == null || land.equals(next) || cursor.getY() - land.getY() > 3) return false;
		if (!lv.canSeeSky(next)) {
			Direction l = dir.getCounterClockWise(), r = dir.getClockWise();
			for (BlockPos c = next.below(); c.getY() >= land.getY(); c = c.below())
				if (!solid(lv, c.relative(l)) || !solid(lv, c.relative(r)) || !solid(lv, c.relative(dir))) return false;
		}
		return true;
	}

	/** dig_up: at the target's level, under open sky or on the level itself. */
	boolean reached(LocalPlayer p, Level lv) {
		BlockPos c = feetCell(p);
		return c.getY() >= toward.getY() || c.getY() >= toward.getY() - 2 && lv.canSeeSky(c.above());
	}

	static boolean solid(Level lv, BlockPos q) {
		return !lv.getBlockState(q).getCollisionShape(lv, q).isEmpty() && lv.getFluidState(q).isEmpty();
	}

	static final List<String> FILLERS = List.of("minecraft:cobblestone", "minecraft:cobbled_deepslate", "minecraft:dirt", "minecraft:stone",
			"minecraft:deepslate", "minecraft:andesite", "minecraft:diorite", "minecraft:granite", "minecraft:tuff", "minecraft:netherrack");
	final Set<BlockPos> unfillable = new HashSet<>();
	BlockPos filling;
	int seals, bridged, turns;
	boolean hasFiller, fillerAvailable = true, fillerDug, flat;
	Direction turnBase;
	String pendingTurn; // "left" / "right" while the first dig after an edge turn is not yet in
	String fillFail;

	/** Target ores within 4 blocks of her that touch air (the tunnel opened them). */
	void scanOres(LocalPlayer p, Level lv) {
		BlockPos c = p.blockPosition();
		for (BlockPos q : BlockPos.betweenClosed(c.offset(-4, -2, -4), c.offset(4, 3, 4))) {
			if (triedOre.contains(q) || ores.contains(q) || !matches(lv.getBlockState(q)) || !MineResource.exposed(lv, q)) continue;
			if (floor(path, feetCell(p), stepCell(), q)) continue;
			ores.add(q.immutable());
		}
	}

	BlockPos nextOre(LocalPlayer p, Level lv) {
		while (!ores.isEmpty()) {
			BlockPos q = ores.peek();
			if (!matches(lv.getBlockState(q)) || triedOre.contains(q) || floor(path, feetCell(p), stepCell(), q)) { ores.poll(); continue; }
			// O175: never mine a wall ore with lava on any face (source or flowing): it opens the lava into the tunnel. Left, counted
			if (besideLava(x -> lv.getFluidState(x).is(FluidTags.LAVA), q)) { ores.poll(); triedOre.add(q); lavaOres++; continue; }
			if (p.isWithinBlockInteractionRange(q, -0.5)) { ores.poll(); return q; }
			ores.poll(); // out of reach from the tunnel: left for a later branch
			triedOre.add(q);
		}
		return null;
	}

	int lavaOres;

	/**
	 * CP-86 run 4 (45L 20:02, prospect stone): with the ore being stone itself, the wall-ore pass mined the block under her
	 * step; she dropped into it (feet y 67, cursor 68) and the realign failed: blocked:walk after 3 steps. A block that is the
	 * floor of a tunnel cell, of her own cell or of the next step is never an ore to mine. Pure, for the test.
	 */
	static boolean floor(java.util.Set<BlockPos> path, BlockPos feet, BlockPos next, BlockPos q) {
		BlockPos up = q.above();
		return path.contains(up) || up.equals(feet) || up.equals(next);
	}
	final java.util.Set<BlockPos> placed = new java.util.HashSet<>(); // her own seals and bridges: never "a build"

	/** The first man-made block among the cells ("id at x, y, z"), or null. Pure, for the test. */
	static String build(java.util.function.Function<BlockPos, String> idAt, BlockPos... cells) {
		for (BlockPos q : cells) if (TreeWood.made(idAt.apply(q))) return idAt.apply(q) + " at " + q.toShortString();
		return null;
	}

	/** Lava on any of q's six faces. Pure, for the test. */
	static boolean besideLava(java.util.function.Predicate<BlockPos> lava, BlockPos q) {
		for (Direction d : Direction.values()) if (lava.test(q.relative(d))) return true;
		return false;
	}

	boolean matches(BlockState st) {
		if (block != null) return MineResource.id(st).equals(block);
		return st.is(TagKey.create(Registries.BLOCK, Identifier.parse(tag)));
	}

	/**
	 * A face (or filler) dig: no drop walk. Its drop lies on her path (or falls onto her) and is picked up as she steps on;
	 * body's collect walked her to the NEAREST item within 6, which was one trapped under a bridge three steps back, so the
	 * next dig was out of reach (cp40p a47-a49 p_stairdrop). Ore digs keep collecting: ores sit beside the line.
	 */
	static JsonObject face(BlockPos q, Level lv) {
		JsonObject a = mine(q, lv);
		a.addProperty("collect", false);
		return a;
	}

	static JsonObject mine(BlockPos q, Level lv) {
		JsonObject a = new JsonObject();
		a.add("pos", HarvestReplant.json(q));
		a.addProperty("expect_block", MineResource.id(lv.getBlockState(q)));
		return a;
	}

	int got(BodyServices b) {
		return oresMined;
	}

	Step outcome() {
		if (stop.startsWith("hazard")) return Step.failed("hazard", "stopped before " + stop.substring(7) + " after " + length + " blocks");
		if (stop.equals("inventory_full")) return Step.failed("inventory_full", oresMined + " ore(s) mined");
		if (stop.equals("zone_denied")) return Step.failed("zone_denied", "the server refused a break");
		if (stop.equals("capped")) return Step.failed("capped", "the envelope's blocks_broken_per_hour cap is used up");
		if (stop.equals("no_tool")) return Step.failed("no_tool", "no hotbar tool breaks the tunnel face");
		if (stop.startsWith("blocked")) return Step.failed("unreachable", stop);
		if (toward != null) return Step.failed("not_reached", "climbed " + climbed() + " of " + (toward.getY() - start.getY()) + ", stop: " + stop);
		// S93: max_blocks with nothing found says how deep the tunnel got, so the mind can raise max_blocks or branch, not repeat
		String depth = stop.equals("max_blocks") && cursor != null ? " after " + length + " steps at y " + cursor.getY()
				+ (descendTo != null && cursor.getY() > descendTo ? " (descend_to " + descendTo + " not reached)" : "") : "";
		return Step.failed(oresMined > 0 ? "partial" : "not_found", oresMined + "/" + want + " ores, stop: " + stop + depth
				+ (lavaOres > 0 ? "; " + lavaOres + " ore(s) left beside lava" : ""));
	}

	Step end(BodyServices b, Step s) {
		JsonObject e = b.evidence();
		LocalPlayer p = b.player();
		if (before != null) {
			Map<String, Integer> d = Plans.diff(before, b.inventoryCounts());
			d.values().removeIf(v -> v <= 0);
			e.add("mined", Menus.json(d));
		}
		e.addProperty("ores", oresMined);
		e.addProperty("blocks_broken", broken);
		e.addProperty("tunnel_length", length);
		e.addProperty("torches_placed", torches);
		if (torchFail != null) e.addProperty("torch_failure", torchFail);
		// O71: seals and bridges ride in stop_reason until the next registry set declares them as evidence keys
		String extra = String.join("; ", java.util.stream.Stream.of(seals + bridged > 0 ? "seals " + seals + ", bridged " + bridged : null,
				turns > 0 ? "turns " + turns : null, returnWalks > 0 ? "retreat seals " + (seals - sealsForward) + ", walks " + (returnWalks + 1) : null,
				fillFail != null ? "fill failed " + fillFail : null, far(),
				"dir " + heading(p).getName()).filter(java.util.Objects::nonNull).toList()); // O86: dir = the heading she ended on
		// every end carries the extras, a cancel or interrupt too (stop null): the mind resumes with "dir <heading>" (O86)
		e.addProperty("stop_reason", stop == null ? extra : stop + " (" + extra + ")"); // never null (O86a)
		e.addProperty("returned", p != null && start != null && p.blockPosition().distManhattan(start) <= 2);
		if (p != null) e.add("stop_at", HarvestReplant.json(p.blockPosition())); // where she stands at the end (O58: resume point)
		e.addProperty("seals", seals);
		e.addProperty("bridged", bridged);
		if (toward != null) {
			e.addProperty("climbed", climbed());
			e.addProperty("reached", "reached".equals(stop));
		}
		return s;
	}

	/**
	 * O86v: "far x y z heading", the farthest safe cell she stood on and the tunnel's heading there, for the mind's branch
	 * prospect (it walks there and tunnels on a perpendicular heading). After a hazard stop the hazard touches the end cell,
	 * so far is two steps back from it; otherwise far is the end cell. Null before the first tick.
	 */
	String far() {
		if (walked.isEmpty()) return null;
		int i = walked.size() - 1;
		if (stop != null && stop.startsWith("hazard")) i = Math.max(0, i - 2);
		BlockPos f = walked.get(i);
		return "far " + f.getX() + " " + f.getY() + " " + f.getZ() + " " + walkedDir.get(i).getName();
	}

	/** The heading she is on: the tunnel's, else the one asked for, else her facing (an end before the first tick). */
	Direction heading(LocalPlayer p) {
		return dir != null ? dir : forced != null ? forced : p != null ? p.getDirection() : Direction.NORTH;
	}

	final java.util.List<String> trail = new java.util.ArrayList<>(); // last steps (target>accepted@x,y): diagnosis of blocked stops

	int climbed() {
		return start == null || cursor == null ? 0 : cursor.getY() - start.getY();
	}

	@Override
	public void cancel(BodyServices b) {
		end(b, null);
	}
}
