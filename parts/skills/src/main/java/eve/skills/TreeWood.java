package eve.skills;

import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;

import java.util.List;
import java.util.Set;
import java.util.function.Function;
import java.util.function.Predicate;

/**
 * Hunter's rule H-trees: wood comes from actual trees in the area, never from builds or decoration made of logs. A log is tree
 * wood only when ALL hold (checked on what the client sees):
 * (a) it is in a vertical log column (>= 2 logs, or 1 with leaves right above) whose base stands on natural ground
 * (dirt, grass, podzol, mud, moss, rooted dirt, ...);
 * (b) leaves of the matching kind, not player-placed (persistent=false), lie within 2 blocks of some block of the column;
 * (c) no man-made block (planks, stairs, slabs, fences, glass, doors, bricks, torches, wool, ...) touches any log of it, and no
 * log in it is stripped.
 * (d) "not in a zone with a snapshot" is sense's half (server-side): the client does not see zones.
 * Pure over idAt (block id at a pos) and persistent (leaves placed by a player), so it is tested without a world.
 */
final class TreeWood {
	static final Set<String> GROUND = Set.of("minecraft:dirt", "minecraft:grass_block", "minecraft:podzol", "minecraft:mud", "minecraft:moss_block",
			"minecraft:rooted_dirt", "minecraft:coarse_dirt", "minecraft:mycelium", "minecraft:muddy_mangrove_roots", "minecraft:mangrove_roots",
			"minecraft:pale_moss_block", "minecraft:crimson_nylium", "minecraft:warped_nylium", "minecraft:snow_block");
	// ponytail: a token list, not every block in the game; a man-made block it misses lets one build log through - add its token
	static final List<String> MADE = List.of("planks", "stairs", "slab", "fence", "glass", "door", "brick", "torch", "wool", "bed", "sign",
			"banner", "lantern", "chest", "barrel", "ladder", "crafting_table", "furnace", "cobblestone", "smooth_", "concrete", "button",
			"pressure_plate", "rail", "bookshelf", "hay_block", "stripped_", "_wall", "polished_", "chiseled_", "cut_", "candle", "lectern",
			"anvil", "table", "loom", "composter", "campfire", "scaffolding", "chain", "bars", "pane", "carpet");
	static final Set<String> NATURAL_CARPET = Set.of("minecraft:moss_carpet", "minecraft:pale_moss_carpet");

	/** The leaves a log's tree carries; null = not a tree log. */
	static List<String> leavesFor(String log) {
		if (log == null || log.contains("stripped_")) return null;
		String b = log.replace("minecraft:", "");
		if (b.equals("crimson_stem") || b.equals("crimson_hyphae")) return List.of("minecraft:nether_wart_block");
		if (b.equals("warped_stem") || b.equals("warped_hyphae")) return List.of("minecraft:warped_wart_block");
		String kind = b.endsWith("_log") ? b.substring(0, b.length() - 4) : b.endsWith("_wood") ? b.substring(0, b.length() - 5) : null;
		if (kind == null) return null;
		if (kind.equals("oak")) return List.of("minecraft:oak_leaves", "minecraft:azalea_leaves", "minecraft:flowering_azalea_leaves");
		return List.of("minecraft:" + kind + "_leaves");
	}

	static boolean made(String id) {
		if (id == null || NATURAL_CARPET.contains(id)) return false;
		for (String t : MADE) if (id.contains(t)) return true;
		return false;
	}

	/**
	 * O133: the whole tree around a trunk log - every log of the same kind connected to it (26-neighbours: trunk, branches and
	 * crown), at most max logs. Felling takes all of them before the next tree; leaves are left to decay.
	 */
	static java.util.Set<BlockPos> tree(Function<BlockPos, String> idAt, BlockPos start, int max) {
		String log = idAt.apply(start);
		java.util.Set<BlockPos> out = new java.util.LinkedHashSet<>();
		java.util.ArrayDeque<BlockPos> todo = new java.util.ArrayDeque<>(List.of(start.immutable()));
		while (!todo.isEmpty() && out.size() < max) {
			BlockPos c = todo.poll();
			if (!out.add(c)) continue;
			for (BlockPos n : BlockPos.betweenClosed(c.offset(-1, -1, -1), c.offset(1, 1, 1)))
				if (!out.contains(n) && log.equals(idAt.apply(n))) todo.add(n.immutable());
		}
		return out;
	}

	/** Why q is not tree wood (a short reason), or null when it is. */
	static String why(Function<BlockPos, String> idAt, Predicate<BlockPos> persistent, BlockPos q) {
		String log = idAt.apply(q);
		List<String> leaves = leavesFor(log);
		if (leaves == null) return "not a natural log (" + log + ")";
		BlockPos lo = q, hi = q;
		while (log.equals(idAt.apply(lo.below()))) lo = lo.below();
		while (log.equals(idAt.apply(hi.above()))) hi = hi.above();
		int n = hi.getY() - lo.getY() + 1;
		if (n < 2 && !leaves.contains(idAt.apply(hi.above()))) return "a lone log (no column, no leaves on top)";
		if (!GROUND.contains(idAt.apply(lo.below()))) return "the column stands on " + idAt.apply(lo.below()) + ", not natural ground";
		boolean leafy = false;
		for (BlockPos c = lo; c.getY() <= hi.getY() && !leafy; c = c.above())
			for (BlockPos p : BlockPos.betweenClosed(c.offset(-2, -2, -2), c.offset(2, 2, 2)))
				if (leaves.contains(idAt.apply(p)) && !persistent.test(p)) { leafy = true; break; }
		if (!leafy) return "no natural " + leaves.get(0).replace("minecraft:", "") + " within 2 blocks";
		for (BlockPos c = lo; c.getY() <= hi.getY(); c = c.above())
			for (Direction d : Direction.values()) {
				String nb = idAt.apply(c.relative(d));
				if (made(nb)) return "touches " + nb + " at " + c.relative(d).toShortString() + " (a build)";
			}
		return null;
	}
}
