package eve.skills;

import net.minecraft.core.BlockPos;
import org.junit.jupiter.api.Test;

import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.*;

/** H-trees (Hunter): only actual trees count as wood; builds and decoration made of logs never do. Fake worlds, no game. */
class TreeWoodTest {
	final Map<BlockPos, String> w = new HashMap<>();
	final Set<BlockPos> placed = new HashSet<>(); // player-placed (persistent) leaves

	String why(int x, int y, int z) {
		return TreeWood.why(p -> w.getOrDefault(p, "minecraft:air"), placed::contains, new BlockPos(x, y, z));
	}

	void set(String id, int x, int y, int z) { w.put(new BlockPos(x, y, z), "minecraft:" + id); }

	void leavesAround(String kind, int x, int y, int z) {
		for (int dx = -2; dx <= 2; dx++) for (int dz = -2; dz <= 2; dz++) for (int dy = 0; dy <= 1; dy++)
			if (dx != 0 || dz != 0 || dy == 1) set(kind, x + dx, y + dy, z + dz);
	}

	void oak(int x, int z, int h) { // grass, h logs, a leaf canopy at the top
		set("grass_block", x, 63, z);
		for (int y = 64; y < 64 + h; y++) set("oak_log", x, y, z);
		leavesAround("oak_leaves", x, 64 + h - 2, z);
	}

	@Test
	void aNaturalOakIsTreeWood() {
		oak(0, 0, 5);
		assertNull(why(0, 64, 0));
		assertNull(why(0, 66, 0));
		set("grass_block", 5, 63, 5); set("oak_log", 5, 64, 5); set("oak_log", 5, 65, 5); set("oak_leaves", 5, 66, 5);
		assertNull(why(5, 64, 5), "a 2-tall trunk with leaves");
		set("dirt", 9, 63, 9); set("oak_log", 9, 64, 9); set("oak_leaves", 9, 65, 9);
		assertNull(why(9, 64, 9), "1 log with leaves right above");
	}

	@Test
	void buildsAndDecorationAreNot() {
		set("grass_block", 0, 63, 0); set("oak_log", 0, 64, 0); set("oak_log", 0, 65, 0); set("oak_log", 0, 66, 0);
		set("oak_planks", 1, 65, 0);                                         // a cabin wall: a log corner post beside planks
		leavesAround("oak_leaves", 0, 66, 0);
		assertTrue(why(0, 64, 0).contains("oak_planks"), why(0, 64, 0));
		set("grass_block", 10, 63, 0); set("oak_log", 10, 64, 0); set("oak_log", 10, 65, 0); set("oak_fence", 10, 64, 1);
		leavesAround("oak_leaves", 10, 66, 0);
		assertTrue(why(10, 64, 0).contains("fence"), "a log post with a fence on it");
		set("stone", 20, 63, 0); set("oak_log", 20, 64, 0); set("oak_log", 20, 65, 0); leavesAround("oak_leaves", 20, 66, 0);
		assertTrue(why(20, 64, 0).contains("not natural ground"), "a log pillar on stone");
		set("grass_block", 30, 63, 0); set("oak_log", 30, 64, 0); set("oak_log", 30, 65, 0);
		assertTrue(why(30, 64, 0).contains("no natural"), "no leaves: a stump or a post");
		set("grass_block", 40, 63, 0); set("oak_log", 40, 64, 0); set("oak_log", 40, 65, 0); set("oak_leaves", 40, 66, 0);
		placed.add(new BlockPos(40, 66, 0));
		assertTrue(why(40, 64, 0).contains("no natural"), "player-placed leaves on a log stack");
		set("grass_block", 50, 63, 0); set("oak_log", 50, 64, 0); set("oak_log", 50, 65, 0); set("birch_leaves", 50, 66, 0);
		assertTrue(why(50, 64, 0).contains("no natural"), "leaves of another kind");
		set("grass_block", 60, 63, 0); set("stripped_oak_log", 60, 64, 0); set("stripped_oak_log", 60, 65, 0); leavesAround("oak_leaves", 60, 66, 0);
		assertTrue(why(60, 64, 0).startsWith("not a natural log"), "stripped logs are made");
		set("grass_block", 70, 63, 0); set("oak_log", 70, 64, 0); set("torch", 70, 64, 1); set("oak_log", 70, 65, 0); leavesAround("oak_leaves", 70, 66, 0);
		assertTrue(why(70, 64, 0).contains("torch"), "a torch on the trunk");
		set("grass_block", 80, 63, 0); set("oak_log", 80, 64, 0);
		assertTrue(why(80, 64, 0).contains("lone log"), "a single log on the grass");
	}

	@Test
	void bigTreesCountColumnByColumn() {
		for (int dx = 0; dx <= 1; dx++) for (int dz = 0; dz <= 1; dz++) { // a dark oak 2x2 trunk, each column on its own dirt
			set("dirt", dx, 63, dz);
			for (int y = 64; y < 70; y++) set("dark_oak_log", dx, y, dz);
		}
		leavesAround("dark_oak_leaves", 0, 69, 0);
		assertNull(why(0, 64, 0));
		assertNull(why(1, 66, 1));
		assertEquals(java.util.List.of("minecraft:nether_wart_block"), TreeWood.leavesFor("minecraft:crimson_stem"));
		assertNull(TreeWood.leavesFor("minecraft:oak_planks"));
		assertFalse(TreeWood.made("minecraft:moss_carpet"));
		assertTrue(TreeWood.made("minecraft:white_carpet"));
	}

	@Test
	void felledTreeIsEveryConnectedLog() { // O133: trunk + a diagonal branch; a birch beside it and an oak 3 away are other trees
		oak(0, 0, 5);                                                       // trunk y64..67 (oak() leafs the top)
		set("oak_log", 1, 67, 1); set("oak_log", 2, 68, 1);                  // a branch, diagonal steps
		set("grass_block", 1, 63, 0); set("birch_log", 1, 64, 0);           // touching birch
		oak(4, 0, 3);                                                       // gap of 3: its own tree
		Set<BlockPos> t = TreeWood.tree(p -> w.getOrDefault(p, "minecraft:air"), new BlockPos(0, 64, 0), MineResource.TREE_MAX);
		assertEquals(6, t.size(), t.toString());
		assertTrue(t.contains(new BlockPos(2, 68, 1)));
		assertFalse(t.contains(new BlockPos(1, 64, 0)));
		assertFalse(t.contains(new BlockPos(4, 64, 0)));
		assertNotNull(why(2, 68, 1), "the branch fails the trunk rule on its own: only tree() takes it");
		assertEquals(3, TreeWood.tree(p -> w.getOrDefault(p, "minecraft:air"), new BlockPos(0, 64, 0), 3).size(), "capped");
	}
}
