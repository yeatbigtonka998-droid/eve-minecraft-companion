package eve.skills;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import eve.body.api.BodyServices;
import eve.body.api.Step;
import eve.body.api.SubAction;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.Identifier;
import net.minecraft.tags.TagKey;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.state.BlockState;

import java.util.HashSet;
import java.util.Map;
import java.util.Set;

/**
 * mine_resource{block|tag, count, max_blocks, allow_vein, radius}: mines {@code count} exposed blocks of the resource near Eve
 * with the body's own walk_to (Baritone, no breaking on the way) and mine_block (best hotbar tool, drops collected). Exposed =
 * a face touching air, per the envelope's `ore: exposed_only`. Zones and grants are the server's: the first zone_denied
 * ends the skill. Evidence is what entered the inventory, never the break count alone.
 */
final class MineResource implements eve.body.api.Skill {
	final String block, tag;
	final int want, maxBlocks, radius;
	final boolean vein;
	final Set<BlockPos> tried = new HashSet<>(), notTree = new HashSet<>();
	final java.util.Map<Long, Integer> treeColumns = new java.util.HashMap<>();
	String lastNotTree;
	int placedSkips, digPaths, stillTicks, okSeen, belowSkipped;
	net.minecraft.world.phys.Vec3 lastPos;
	final java.util.List<String> stuck = new java.util.ArrayList<>();

	/** O133: the tree being felled - the rest of it is mined before any other tree. */
	final Set<BlockPos> felling = new java.util.LinkedHashSet<>();
	int treeLogsLeft, treesFelled;

	static final int STALL_TICKS = 100; // 5 s at 20 tps
	static final int TREE_MAX = 256, UNTIL_NONE_BLOCKS = 1024; // ponytail: 1024 breaks bound a no-count call; raise via max_blocks

	/** O129: true after STALL_TICKS with her not moving (> 0.3 block) and no block broken. */
	boolean stalled(LocalPlayer p) {
		boolean moved = lastPos == null || p.position().distanceTo(lastPos) > 0.3;
		if (moved || ok != okSeen) { lastPos = p.position(); okSeen = ok; stillTicks = 0; return false; }
		return ++stillTicks >= STALL_TICKS;
	}
	boolean digPath;
	final java.util.List<String> skipped = new java.util.ArrayList<>();
	final JsonArray mined = new JsonArray();
	Map<String, Integer> before;
	BlockPos target, last;
	SubAction child;
	boolean walking;
	int ok, attempts, misses, settle;

	MineResource(JsonObject p) {
		block = p.has("block") ? p.get("block").getAsString() : null;
		tag = p.has("tag") ? p.get("tag").getAsString() : null;
		want = p.has("count") ? p.get("count").getAsInt() : 0; // O133: 0 = until none is left in radius
		maxBlocks = p.has("max_blocks") ? p.get("max_blocks").getAsInt() : want > 0 ? want * 2 + 4 : UNTIL_NONE_BLOCKS;
		vein = !p.has("allow_vein") || p.get("allow_vein").getAsBoolean();
		radius = p.has("radius") ? p.get("radius").getAsInt() : 16;
	}

	@Override
	public Step tick(BodyServices b) {
		LocalPlayer p = b.player();
		if (before == null) { // resync the inventory view, then let it settle before the baseline (see Menus.resync)
			if (settle++ == 0) Menus.resync(b);
			if (settle <= Menus.SETTLE_TICKS) return Step.running();
			before = b.inventoryCounts();
		}
		if (child != null && stalled(p)) { // O129: 5 s with no move and no break - drop this target, re-pick, quietly
			child.cancel();
			child = null;
			walking = digPath = false;
			stuck.add(target.toShortString());
			if (stuck.size() >= 3) return end(b, Step.failed("unreachable", "stuck on 3 targets in a row: " + String.join("; ", stuck)));
			target = null;
			return Step.running();
		}
		if (child != null) {
			Step s = child.tick();
			if (!s.done()) return s;
			child = null;
			if ("ok".equals(s.status()) && !walking) stuck.clear();
			if (walking) {
				walking = false;
				if (!"ok".equals(s.status()) && !p.isWithinBlockInteractionRange(target, 0)) {
					// O125 (Hunter live: "4 targets in a row out of reach", she gave up): a walk that cannot get there is retried once with
					// a dug path (Baritone's own digging: no lava, water or long drops, at most DIG_PATH blocks, inside what the server
					// allows); a target neither reaches is skipped with why; unreachable only once no candidate is left (ad0bae41)
					String next = failedWalk(p.blockPosition(), s);
					if (next.equals("dig")) return walk(b, target, true);
					if (next.startsWith("unreachable:")) return end(b, Step.failed("unreachable", next.substring(12)));
					return Step.running();
				}
				digPath = false;
			} else {
				attempts++;
				if ("ok".equals(s.status())) {
					ok++;
					misses = 0;
					last = target;
					mined.add(target.toShortString());
				} else if ("zone_denied".equals(s.code()) && logs() && s.note() != null && s.note().contains("placed_block:") && ++placedSkips <= 8) {
					// H-trees, sense 0.1.23: the server says this log is not a real tree (placed_block:<why>). Retrying never changes
					// that: drop the whole column and pick another tree (at most 8 such refusals a call)
					for (BlockPos q = target; id(p.level().getBlockState(q)).equals(id(p.level().getBlockState(target))) || q.equals(target); q = q.above()) {
						notTree.add(q.immutable());
						if (q.getY() > target.getY() + 32) break;
					}
					treeColumns.remove(target.getX() * 31_000_000L + target.getZ());
					felling.clear();
					lastNotTree = "the server: " + s.note().substring(s.note().indexOf("placed_block:")).split("[\\s;,)]")[0];
					target = null;
					return Step.running();
				} else if ("zone_denied".equals(s.code())) {
					return end(b, Step.failed("zone_denied", "the server refused breaking " + target.toShortString()));
				} else if ("no_tool".equals(s.code())) {
					return end(b, Step.failed("no_tool", s.note()));
				} else misses++;
				b.progress(want > 0 ? Math.min(99, 100 * ok / want) : 0, ok + "/" + (want > 0 ? want : "all"));
				target = null;
				return Step.running();
			}
		}
		if (want > 0 && ok >= want) return end(b, Step.ok());
		if (attempts >= maxBlocks && ok == 0 && !skipped.isEmpty()) return end(b, Step.failed("unreachable", (belowSkipped >= skipped.size() ? "below her: dig down (prospect); " : "")
				+ "max_blocks " + maxBlocks + " attempts spent, she at y " + p.blockPosition().getY() + "; tried: " + String.join("; ", skipped)));
		if (attempts >= maxBlocks) return end(b, Step.failed("partial", "max_blocks " + maxBlocks + " reached with " + ok + "/" + (want > 0 ? want : "all")));
		if (target == null) {
			target = pick(p);
			String none = "no more exposed " + what() + " within " + radius
					+ (notTree.isEmpty() ? "" : " that is tree wood (" + notTree.size() + " log(s) skipped as builds or decoration, e.g. " + lastNotTree + ")");
			if (target == null && want <= 0 && ok > 0) { b.evidence().addProperty("note", "until none: " + none); return end(b, Step.ok()); }
			if (target == null && ok == 0 && !skipped.isEmpty()) return end(b, Step.failed("unreachable", (belowSkipped >= skipped.size() ? "below her: dig down (prospect); " : "")
					+ "every candidate within " + radius + " failed: " + skipped.size() + " out of reach on foot or by a dug path, she at y " + p.blockPosition().getY()
					+ ", " + attempts + " attempts; tried: " + String.join("; ", skipped)));
			if (target == null) return end(b, Step.failed(ok > 0 ? "partial" : "not_found", none));
			tried.add(target);
			stillTicks = 0; // a new target starts its own 5 s
			BlockState st = p.level().getBlockState(target);
			if (st.requiresCorrectToolForDrops() && !hasTool(p, st)) return end(b, Step.failed("no_tool", "no tool in the inventory drops " + id(st)));
			if (!room(p, st)) return end(b, Step.failed("inventory_full", "the bag is full after " + ok + " " + what()));
		}
		if (!p.isWithinBlockInteractionRange(target, -0.5)) return walk(b, target, false); // half a block of margin: not at the reach edge
		JsonObject pos = new JsonObject();
		pos.addProperty("x", target.getX());
		pos.addProperty("y", target.getY());
		pos.addProperty("z", target.getZ());
		JsonObject a = new JsonObject();
		a.add("pos", pos);
		a.addProperty("expect_block", id(p.level().getBlockState(target)));
		child = b.run("mine_block", a);
		return Step.running();
	}

	/**
	 * A walk to target failed out of reach, she now at her: "dig" (retry it with a dug path), "next" (target dropped, pick
	 * another) or "unreachable:<note>" (end). No world access, so the tests drive it with a drifting walker (S93).
	 */
	String failedWalk(BlockPos her, Step s) {
		attempts++; // every failed walk still counts against max_blocks
		boolean below = target.getY() < her.getY() - 2;
		if (below) belowSkipped++;
		if (reach(digPath, s, below).equals("dig")) { digPath = true; digPaths++; return "dig"; }
		if (felling.remove(target)) { // a crown log out of reach stays (counted), it never ends the call
			treeLogsLeft++;
			digPath = false;
			target = null;
			return "next";
		}
		skipped.add(target.toShortString() + " " + (digPath ? "dug path" : "walk") + " " + s.status() + "/" + s.code());
		digPath = false;
		target = null;
		// CP-86 run 4 (45L 20:02): with nothing reachable left, 64 walks at stone below her drifted her 33 m west before the
		// mind's prospect fallback started there. Below-only after BELOW_TRIES failed walks: end where she is
		if (belowOnly(below, reachableTarget, ok, belowSkipped))
			return "unreachable:below her: dig down (prospect); nothing reachable within " + radius + ", she at y " + her.getY()
					+ ", " + attempts + " attempts; tried: " + String.join("; ", skipped);
		return "next";
	}

	static final int DIG_PATH = 16, REACH_SKIPS = 8;

	/** O125 (pure): after a failed walk to the target - "dig" (retry with a dug path) or "skip" (another target). */
	String reach(boolean dugAlready, Step walkResult, boolean below) {
		// CP-86 run 2 (45L 18:09): body's dug path mines only feet/head cells on the level line toward the goal (Baritone's own
		// breaking stays off), so it never digs down: a target more than 2 below her feet gets no dug-path retry (8 such, 17
		// attempts, 0 mined). Ending on only those says "below her" so the mind digs down with prospect's staircase instead
		if (!dugAlready && !below && !"zone_denied".equals(walkResult.code()) && digPaths < 2 * REACH_SKIPS) return "dig";
		return "skip"; // ad0bae41: unreachable only once every candidate in radius failed (pick finds none), max_blocks bounds it
	}

	/** Walk near the target; with dig, Baritone may break up to DIG_PATH blocks on the way (body walk_to allow_break). */
	Step walk(BodyServices b, BlockPos t, boolean dig) {
		JsonObject a = new JsonObject(), c = new JsonObject();
		BlockPos goal = t.equals(target) && stand != null ? stand : t; // the stand beside it, when there is one (ad0bae41)
		c.addProperty("x", goal.getX() + 0.5);
		c.addProperty("y", goal.getY());
		c.addProperty("z", goal.getZ() + 0.5);
		a.add("pos", c);
		a.addProperty("tolerance", goal == t ? 3.0 : 1.5);
		if (dig) {
			a.addProperty("allow_break", true);
			a.addProperty("max_blocks_broken", DIG_PATH);
		}
		walking = true;
		child = b.run("walk_to", a);
		return Step.running();
	}

	String what() {
		return block != null ? block : "#" + tag;
	}

	boolean matches(BlockState st) {
		if (block != null) return id(st).equals(block);
		return st.is(TagKey.create(Registries.BLOCK, Identifier.parse(tag)));
	}

	/** Nearest exposed untried match (from the last mined block when following a vein, else from Eve). */
	/** H-trees: any log target takes tree wood only (TreeWood), never a build's or a decoration's logs. Default on, no override. */
	boolean logs() {
		return tag != null ? tag.endsWith("logs") || tag.endsWith("logs_that_burn") || tag.endsWith("stems")
				: block.endsWith("_log") || block.endsWith("_wood") || block.endsWith("_stem") || block.endsWith("_hyphae");
	}

	/** Tree wood, with each passed column remembered (x, z -> base y): once its base log is cut it no longer stands on ground. */
	boolean treeWood(Level lv, BlockPos q) {
		Integer base = treeColumns.get(q.getX() * 31_000_000L + q.getZ());
		if (base != null && q.getY() >= base) return true;
		String why = TreeWood.why(b -> id(lv.getBlockState(b)), b -> {
			BlockState s = lv.getBlockState(b);
			return s.hasProperty(net.minecraft.world.level.block.LeavesBlock.PERSISTENT) && s.getValue(net.minecraft.world.level.block.LeavesBlock.PERSISTENT);
		}, q);
		if (why != null) { notTree.add(q.immutable()); lastNotTree = why; return false; }
		BlockPos lo = q;
		while (id(lv.getBlockState(lo.below())).equals(id(lv.getBlockState(q)))) lo = lo.below();
		treeColumns.put(q.getX() * 31_000_000L + q.getZ(), lo.getY());
		return true;
	}

	BlockPos pick(LocalPlayer p) {
		Level lv = p.level();
		BlockPos c = p.blockPosition();
		boolean trees = logs();
		if (trees) { // O133: finish the tree first - its branches fail the trunk rule but are the same tree
			felling.removeIf(q -> tried.contains(q) || !matches(lv.getBlockState(q)));
			BlockPos q = best(c, felling, x -> true, x -> open(lv, x));
			stand = null;
			if (q != null) return q;
			treeLogsLeft += felling.size(); // what is left has no open face
			felling.clear();
		}
		java.util.function.Predicate<BlockPos> wanted = x -> !tried.contains(x) && matches(lv.getBlockState(x)) && (!trees || !notTree.contains(x) && treeWood(lv, x));
		java.util.function.Predicate<BlockPos> solid = x -> !lv.getBlockState(x).getCollisionShape(lv, x).isEmpty();
		Section sec = (sx, sy, sz) -> mayHold(lv, sx, sy, sz);
		// CP-86 run 2 (kickoff ad0bae41): reachable first - within 3 of her y with a free stand beside it - then anything else
		BlockPos q = choose(c, radius, sec, wanted, x -> exposed(lv, x), solid);
		stand = q == null ? null : standFor(solid, c, q);
		reachableTarget = q != null && reachable(solid, c, q);
		if (q != null && trees) { felling.addAll(TreeWood.tree(x -> id(lv.getBlockState(x)), q, TREE_MAX)); treesFelled++; }
		return q;
	}

	BlockPos stand;
	boolean reachableTarget;
	static final int BELOW_TRIES = 4;

	/** CP-86 run 4: nothing reachable, nothing mined, and BELOW_TRIES walks at stone below her failed: stop where she is. Pure. */
	static boolean belowOnly(boolean below, boolean reachableTarget, int ok, int belowSkipped) {
		return below && !reachableTarget && ok == 0 && belowSkipped >= BELOW_TRIES;
	}

	/** Two tiers: the cheapest reachable target (reachable()), else the cheapest exposed one at all. Pure. */
	static BlockPos choose(BlockPos from, int r, Section sec, java.util.function.Predicate<BlockPos> wanted,
			java.util.function.Predicate<BlockPos> exposed, java.util.function.Predicate<BlockPos> solid) {
		BlockPos q = scan(from, r, sec, wanted.and(x -> reachable(solid, from, x)), exposed);
		return q != null ? q : scan(from, r, sec, wanted, exposed);
	}

	/** Within 3 blocks of her height and with a free cell to stand in beside it. Pure. */
	static boolean reachable(java.util.function.Predicate<BlockPos> solid, BlockPos from, BlockPos q) {
		return Math.abs(q.getY() - from.getY()) <= 3 && standFor(solid, from, q) != null;
	}

	/**
	 * The cheapest cell (from her) to stand in while mining q: beside q at its level, one below or one above, two free cells
	 * on a solid floor. Never on top of q (mining under her own feet). Pure, for the tests.
	 */
	static BlockPos standFor(java.util.function.Predicate<BlockPos> solid, BlockPos from, BlockPos q) {
		BlockPos best = null;
		int bc = Integer.MAX_VALUE;
		for (int dy = -1; dy <= 1; dy++)
			for (Direction d : Direction.Plane.HORIZONTAL) {
				BlockPos s = q.relative(d).above(dy);
				if (solid.test(s) || solid.test(s.above()) || !solid.test(s.below())) continue;
				int c = cost(from, s);
				if (c < bc) { bc = c; best = s.immutable(); }
			}
		return best;
	}

	/** A 16-cube chunk section, by section coordinates. */
	interface Section { boolean mayHold(int sx, int sy, int sz); }

	/**
	 * O163 (Hunter: search out to 100 blocks): the same pick as best() over the whole cube, but section by section, nearest
	 * section first, skipping sections the palette says cannot hold a match, and stopping once no section left can beat the best
	 * found. A full cube at radius 100 is 8.1M blocks a pick; this reads only the sections that may hold the resource. Pure.
	 */
	static BlockPos scan(BlockPos from, int r, Section sec, java.util.function.Predicate<BlockPos> wanted, java.util.function.Predicate<BlockPos> exposed) {
		BlockPos lo = from.offset(-r, -r, -r), hi = from.offset(r, r, r);
		java.util.List<int[]> secs = new java.util.ArrayList<>();
		for (int sx = lo.getX() >> 4; sx <= hi.getX() >> 4; sx++)
			for (int sy = lo.getY() >> 4; sy <= hi.getY() >> 4; sy++)
				for (int sz = lo.getZ() >> 4; sz <= hi.getZ() >> 4; sz++) {
					BlockPos near = new BlockPos(clamp(from.getX(), sx), clamp(from.getY(), sy), clamp(from.getZ(), sz));
					secs.add(new int[] {cost(from, near), sx, sy, sz});
				}
		secs.sort(java.util.Comparator.comparingInt(a -> a[0]));
		BlockPos best = null;
		int bc = Integer.MAX_VALUE;
		for (int[] s : secs) {
			if (s[0] >= bc) break; // every remaining section is at least this far
			if (!sec.mayHold(s[1], s[2], s[3])) continue;
			BlockPos q = best(from, BlockPos.betweenClosed(Math.max(lo.getX(), s[1] << 4), Math.max(lo.getY(), s[2] << 4), Math.max(lo.getZ(), s[3] << 4),
					Math.min(hi.getX(), (s[1] << 4) + 15), Math.min(hi.getY(), (s[2] << 4) + 15), Math.min(hi.getZ(), (s[3] << 4) + 15)), wanted, exposed);
			if (q != null && cost(from, q) < bc) { bc = cost(from, q); best = q; }
		}
		return best;
	}

	static int clamp(int v, int section) {
		return Math.max(section << 4, Math.min((section << 4) + 15, v));
	}

	/** Loaded (the client only has what the server sent: view-distance) and the palette may hold a match. Never loads a chunk. */
	boolean mayHold(Level lv, int sx, int sy, int sz) {
		var ch = lv.getChunkSource().getChunkNow(sx, sz);
		if (ch == null) return false;
		int i = lv.getSectionIndexFromSectionY(sy);
		if (i < 0 || i >= ch.getSections().length) return false;
		var s = ch.getSections()[i];
		return !s.hasOnlyAir() && s.maybeHas(this::matches);
	}

	/** A crown log is reachable through air or leaves (leaves are left to decay, never mined). */
	static boolean open(Level lv, BlockPos q) {
		for (Direction d : Direction.values()) {
			BlockState s = lv.getBlockState(q.relative(d));
			if (s.isAir() || s.is(net.minecraft.tags.BlockTags.LEAVES)) return true;
		}
		return false;
	}

	/** O133: room for the drop - a free slot or a non-full stack of it. */
	static boolean room(LocalPlayer p, BlockState st) {
		if (p.getInventory().getFreeSlot() >= 0) return true;
		var item = st.getBlock().asItem();
		for (int i = 0; i < 36; i++) {
			var s = p.getInventory().getItem(i);
			if (s.is(item) && s.getCount() < s.getMaxStackSize()) return true;
		}
		return false;
	}

	/**
	 * O129 (Hunter live: "mine 8 stone" went for farther blocks): the cheapest exposed match to walk to from where she stands,
	 * re-picked after every block. A buried block is never chosen (it has no face to mine from). Pure, for the fixture tests.
	 */
	static BlockPos best(BlockPos from, Iterable<BlockPos> cands, java.util.function.Predicate<BlockPos> wanted, java.util.function.Predicate<BlockPos> exposed) {
		BlockPos best = null;
		int bc = Integer.MAX_VALUE;
		for (BlockPos q : cands) {
			int c = cost(from, q);
			if (c >= bc || !wanted.test(q) || !exposed.test(q)) continue;
			bc = c;
			best = q.immutable();
		}
		return best;
	}

	/** Walking cost, not straight-line: steps across plus twice the height (climbing or a staircase down costs more).
	 * ponytail: a proxy for Baritone's path cost; ask Baritone for a cost if a wall between her and a near block misleads it. */
	static int cost(BlockPos from, BlockPos q) {
		return Math.abs(q.getX() - from.getX()) + Math.abs(q.getZ() - from.getZ()) + 2 * Math.abs(q.getY() - from.getY());
	}

	static boolean exposed(Level lv, BlockPos q) {
		for (Direction d : Direction.values()) if (lv.getBlockState(q.relative(d)).isAir()) return true;
		return false;
	}

	static boolean hasTool(LocalPlayer p, BlockState st) {
		// the whole inventory: body's mine_block moves a right tool up from the main inventory (O101a), so a pickaxe there is not no_tool
		for (int i = 0; i < 36; i++) if (p.getInventory().getItem(i).isCorrectToolForDrops(st)) return true;
		return false;
	}

	static String id(BlockState st) {
		return BuiltInRegistries.BLOCK.getKey(st.getBlock()).toString();
	}

	Step end(BodyServices b, Step s) {
		JsonObject e = b.evidence();
		e.addProperty("mined", ok);
		e.addProperty("attempts", attempts);
		e.add("blocks", mined);
		if (logs()) { e.addProperty("trees", treesFelled); e.addProperty("tree_logs_left", treeLogsLeft); }
		e.add("drops", Menus.json(gained(b)));
		return s;
	}

	Map<String, Integer> gained(BodyServices b) {
		Map<String, Integer> d = Plans.diff(before == null ? Map.of() : before, b.inventoryCounts());
		d.values().removeIf(v -> v < 0);
		return d;
	}

	@Override
	public void cancel(BodyServices b) {
		end(b, null);
	}
}
