package eve.skills;

import eve.skills.Plans.Grid;
import eve.skills.Plans.Move;
import eve.skills.Plans.Stack;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

import java.nio.file.Path;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.function.Function;

import static org.junit.jupiter.api.Assertions.*;

/**
 * K-style checks: expected values are counted by hand from the vanilla recipes (wiki/game knowledge), not from this code, and
 * run against the pinned 26.3 data extract. Normal cases first, then boundary/unsupported ones.
 */
class PlansTest {
	static Recipes R;
	static Function<String, Set<String>> T;

	@BeforeAll
	static void load() throws Exception {
		Path data = Path.of(System.getProperty("eve.gamedata"));
		R = Recipes.load(data.resolve("recipe"));
		T = Recipes.fileTags(data.resolve("tags/item"));
	}

	static Grid plan(String item, Map<String, Integer> inv, int crafts, int g) {
		return Plans.pick(R.byResult.getOrDefault(item, List.of()), inv, crafts, g, T);
	}

	static final String I = "minecraft:iron_ingot", S = "minecraft:stick", OAK = "minecraft:oak_planks", BIRCH = "minecraft:birch_planks";

	// ---- normal ----
	@Test
	void ironPickaxeOnATable() {
		Grid p = plan("minecraft:iron_pickaxe", Map.of(I, 3, S, 2), 1, 3);
		assertTrue(p.ok());
		assertArrayEquals(new String[] {I, I, I, null, S, null, null, S, null}, p.cells());
		assertEquals(Map.of(I, 3, S, 2), p.uses());
		assertEquals(1, p.produced());
	}

	@Test
	void ironArmourCounts() { // helmet 5, chestplate 8, leggings 7, boots 4 ingots
		Map<String, Integer> inv = Map.of(I, 64);
		assertEquals(5, plan("minecraft:iron_helmet", inv, 1, 3).uses().get(I));
		assertEquals(8, plan("minecraft:iron_chestplate", inv, 1, 3).uses().get(I));
		assertEquals(7, plan("minecraft:iron_leggings", inv, 1, 3).uses().get(I));
		assertEquals(4, plan("minecraft:iron_boots", inv, 1, 3).uses().get(I));
	}

	@Test
	void sticksInTheInventoryGrid() {
		Grid p = plan(S, Map.of(OAK, 2), 1, 2);
		assertArrayEquals(new String[] {OAK, null, OAK, null}, p.cells());
		assertEquals(4, p.produced());
	}

	@Test
	void sticksFromMixedPlanks() { // a tag ingredient matches per cell, so one oak + one birch makes sticks
		Grid p = plan(S, Map.of(OAK, 1, BIRCH, 1), 1, 2);
		assertTrue(p.ok());
		assertEquals(Map.of(OAK, 1, BIRCH, 1), p.uses());
	}

	@Test
	void craftingTableFromFourPlanks() {
		Grid p = plan("minecraft:crafting_table", Map.of(OAK, 4), 1, 2);
		assertArrayEquals(new String[] {OAK, OAK, OAK, OAK}, p.cells());
	}

	@Test
	void planksFromAnyOakLog() { // #oak_logs includes stripped wood; one log -> 4 planks
		Grid p = plan(OAK, Map.of("minecraft:stripped_oak_wood", 3), 3, 2);
		assertTrue(p.ok());
		assertEquals(12, p.produced());
		assertEquals("minecraft:stripped_oak_wood", p.cells()[0]);
	}

	@Test
	void torchTakesCoalOrCharcoal() {
		Grid p = plan("minecraft:torch", Map.of("minecraft:charcoal", 2, S, 2), 2, 2);
		assertTrue(p.ok());
		assertEquals(8, p.produced());
		assertArrayEquals(new String[] {"minecraft:charcoal", null, S, null}, p.cells());
	}

	@Test
	void ingotFromNineNuggetsNeedsTheTable() {
		assertTrue(plan(I, Map.of("minecraft:iron_nugget", 9), 1, 3).ok());
		// 2x2: the nugget recipe does not fit, smelting never fits a grid; the only candidate left is the block recipe, short a block
		assertEquals(Map.of("minecraft:iron_block", 1), plan(I, Map.of("minecraft:iron_nugget", 9), 1, 2).missing());
	}

	@Test
	void ingotFromABlockFitsTheInventoryGrid() {
		Grid p = plan(I, Map.of("minecraft:iron_block", 1), 1, 2);
		assertTrue(p.ok());
		assertEquals(9, p.produced());
	}

	@Test
	void batchOfTenSticksRuns() {
		Grid p = plan(S, Map.of(OAK, 20), 10, 3);
		assertEquals(Map.of(OAK, 20), p.uses());
		assertEquals(40, p.produced());
	}

	@Test
	void smeltingRecipesAreRead() {
		var rs = R.byResult.get(I).stream().filter(r -> r.type().equals("smelting")).toList();
		assertEquals(3, rs.size()); // raw iron, iron ore, deepslate iron ore (blasting is a different station)
		assertTrue(rs.stream().allMatch(r -> r.cookTicks() == 200));
	}

	// ---- boundary / unsupported ----
	@Test
	void helmetShortByTwo() {
		Grid p = plan("minecraft:iron_helmet", Map.of(I, 3), 1, 3);
		assertFalse(p.ok());
		assertEquals(2, p.missing().get(I));
	}

	@Test
	void pickaxeWithoutSticks() {
		Grid p = plan("minecraft:iron_pickaxe", Map.of(I, 3), 1, 3);
		assertEquals(Map.of(S, 2), p.missing());
	}

	@Test
	void noRecipeForBedrock() {
		assertNull(plan("minecraft:bedrock", Map.of(), 1, 3));
	}

	@Test
	void pickaxeDoesNotFitTheInventoryGrid() {
		assertNull(plan("minecraft:iron_pickaxe", Map.of(I, 3, S, 2), 1, 2));
	}

	@Test
	void emptyInventoryReportsEveryCell() {
		Grid p = plan("minecraft:crafting_table", Map.of(), 1, 2);
		assertEquals(Map.of("#minecraft:planks", 4), p.missing());
	}

	@Test
	void specialRecipesAreNotLoaded() { // repair, banner duplicate etc. have no fixed result here (transmute loads as shapeless, O131)
		assertTrue(R.byResult.values().stream().flatMap(List::stream)
				.allMatch(r -> Set.of("crafting_shaped", "crafting_shapeless", "smelting", "blasting", "smoking", "campfire_cooking").contains(r.type())));
	}

	@Test
	void unknownTagIsAnError() {
		assertThrows(IllegalArgumentException.class, () -> T.apply("minecraft:no_such_tag"));
	}

	// ---- container transfer plans ----
	static final String C = "minecraft:cobblestone";

	@Test
	void takeUsesPartialStacksFirst() {
		List<Stack> chest = List.of(new Stack(0, C, 64, 64), new Stack(1, C, 5, 64), new Stack(2, null, 0, 64), new Stack(3, C, 30, 64));
		assertEquals(List.of(new Move(1, 5), new Move(3, 30), new Move(0, 5)), Plans.take(chest, C, 40));
	}

	@Test
	void takeMoreThanThereIsTakesAll() {
		List<Stack> chest = List.of(new Stack(4, C, 10, 64));
		assertEquals(List.of(new Move(4, 10)), Plans.take(chest, C, 64));
		assertEquals(List.of(), Plans.take(chest, "minecraft:dirt", 1));
	}

	@Test
	void roomCountsTopUpsAndEmpties() {
		List<Stack> inv = List.of(new Stack(0, C, 60, 64), new Stack(1, null, 0, 64), new Stack(2, "minecraft:dirt", 1, 64));
		assertEquals(68, Plans.room(inv, C, 64));
		assertEquals(16, Plans.room(List.of(new Stack(0, null, 0, 16)), "minecraft:egg", 16));
		assertEquals(0, Plans.room(List.of(new Stack(0, C, 64, 64)), C, 64)); // full container
	}

	@Test
	void diffKeepsOnlyChanges() {
		assertEquals(Map.of(C, -3, "minecraft:dirt", 2), Plans.diff(Map.of(C, 10, S, 1), Map.of(C, 7, S, 1, "minecraft:dirt", 2)));
		assertTrue(Plans.diff(Map.of(), Map.of()).isEmpty());
	}

	@Test
	void cellsArrayIsGridSized() {
		assertEquals(9, Arrays.asList(plan("minecraft:iron_boots", Map.of(I, 4), 1, 3).cells()).size());
	}
}
