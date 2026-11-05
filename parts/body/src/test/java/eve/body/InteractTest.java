package eve.body;

import com.google.gson.JsonObject;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** O101a, offline: where mine_block puts a tool it takes from the main inventory; O98o: when place_block may resume. */
class InteractTest {
	@Test
	void firstEmptyHotbarSlot() {
		assertEquals(3, Interact.MineBlock.hotbarFor(i -> i == 3 || i == 6, 0));
	}

	@Test
	void byHandUsesTheBareHandFirst() {
		assertEquals(4, Interact.MineBlock.handSlot(i -> i == 4, 0), "an empty slot: the bare hand");
		assertEquals(2, Interact.MineBlock.handSlot(i -> false, 2), "a full hotbar: what she holds");
	}

	@Test
	void placeBlockAlwaysResumes() {
		JsonObject pos = new JsonObject();
		pos.addProperty("x", 0);
		pos.addProperty("y", 200);
		pos.addProperty("z", -17);
		JsonObject params = new JsonObject();
		params.add("pos", pos);
		params.addProperty("item", "minecraft:dirt");
		Interact.PlaceBlock a = new Interact.PlaceBlock("o98o", params, 0, 1);
		assertTrue(a.resumable(), "O98o (2): paused before it ever ran (accepted under a shield that yields to it): it simply starts");
		a.ticks = 3; // aiming, no click yet
		assertTrue(a.resumable(), "O102n (ii) CP-10 a25: mid-aim before the click nothing is half-done: it resumes (re-checked)");
		a.waitTicks = 0; // CP-31 a22: the click was sent and the shield came during the server's answer
		assertTrue(a.resumable(), "after the click only the server's answer is left: it resumes and reports what landed");
	}

	@Test
	void fullHotbarKeepsTheHeldItem() {
		assertEquals(8, Interact.MineBlock.hotbarFor(i -> false, 0));
		assertEquals(7, Interact.MineBlock.hotbarFor(i -> false, 8), "slot 8 is selected: its item stays in her hand");
	}

	@Test
	void anItemInTheMainInventoryIsPulledIntoTheHotbar() {
		String table = "minecraft:crafting_table";
		java.util.Map<Integer, String> inv = new java.util.HashMap<>();
		for (int i = 0; i < 9; i++) inv.put(i, "minecraft:dirt");
		inv.put(4, null);
		inv.put(20, table); // O130: crafted into the main inventory
		assertEquals(java.util.List.of(20, 4), toList(Hands.pullPlan(inv::get, 0, table)), "from slot 20 into the empty hotbar slot 4");
		inv.put(4, "minecraft:dirt");
		assertEquals(java.util.List.of(20, 8), toList(Hands.pullPlan(inv::get, 0, table)), "a full hotbar: into slot 8");
		inv.put(2, table);
		assertEquals(java.util.List.of(2, -1), toList(Hands.pullPlan(inv::get, 0, table)), "in the hotbar already: no move");
		assertEquals(null, Hands.pullPlan(inv::get, 0, "minecraft:torch"), "nowhere: no_item");
		inv.put(40, "minecraft:torch");
		assertEquals(null, Hands.pullPlan(inv::get, 0, "minecraft:torch"), "the off-hand (40) is not pulled from");
	}

	@Test
	void dropItemKeepsHerKitAndTossesWholeStacksWhenItCan() { // O124/O134
		java.util.Set<String> worn = java.util.Set.of("minecraft:iron_chestplate");
		for (String k : new String[] {"minecraft:stone_sword", "minecraft:iron_pickaxe", "minecraft:shield", "minecraft:bread", "minecraft:iron_chestplate"})
			assertTrue(Interact.DropItem.protectedKit(k, worn), k);
		for (String k : new String[] {"minecraft:cobblestone", "minecraft:crafting_table", "minecraft:iron_helmet", "minecraft:stone_axe"})
			assertFalse(Interact.DropItem.protectedKit(k, worn), k);
		assertEquals(1, Interact.DropItem.tossButton(64, 64), "the rest wants the whole stack: one toss");
		assertEquals(1, Interact.DropItem.tossButton(40, 16));
		assertEquals(0, Interact.DropItem.tossButton(3, 16), "fewer than the stack: one item at a time");
		assertTrue(63 * Interact.DropItem.TOSS_TICKS < 15 * 20, "63 single tosses fit the 15 s timeout");
	}

	@Test
	void ownerTargetLiftsOnlyTheTargetsTypeAndNeverForAPlayer() { // O136
		assertTrue(Interact.AttackEntity.targetBlocked(true, false, false), "a protected type, no owner_target: refused");
		assertFalse(Interact.AttackEntity.targetBlocked(true, false, true), "the owner named it: swung at");
		assertTrue(Interact.AttackEntity.targetBlocked(true, true, true), "never a player");
		assertFalse(Interact.AttackEntity.targetBlocked(false, false, false));
	}

	@Test
	void buildWalkBudgets() { // O142: {mayBreak, breakBudget, mayPlace, placeBudget}
		assertEquals(java.util.List.of(0, 0, 0, Integer.MAX_VALUE), java.util.Arrays.stream(Skills.walkBudgets(false, false, false, -1, -1, -1)).boxed().toList(), "a plain walk: no break, no place");
		assertEquals(java.util.List.of(1, 2, 0, Integer.MAX_VALUE), java.util.Arrays.stream(Skills.walkBudgets(false, true, false, 2, -1, -1)).boxed().toList(), "CP-33 as before");
		assertEquals(java.util.List.of(0, 0, 0, Integer.MAX_VALUE), java.util.Arrays.stream(Skills.walkBudgets(false, true, false, -1, -1, -1)).boxed().toList(), "allow_break without a budget breaks nothing");
		assertEquals(java.util.List.of(1, 32, 1, 32), java.util.Arrays.stream(Skills.walkBudgets(true, false, false, -1, -1, -1)).boxed().toList(), "build: dig and place, 32 each");
		assertEquals(java.util.List.of(1, 5, 1, 10), java.util.Arrays.stream(Skills.walkBudgets(true, false, false, -1, 5, 10)).boxed().toList());
		assertEquals(java.util.List.of(0, 0, 0, 0), java.util.Arrays.stream(Skills.walkBudgets(true, false, false, -1, 0, 0)).boxed().toList(), "zero budgets: neither");
	}

	@Test
	void idleLevelsHerHeadWithinTenTicks() { // O144: "her neck is still looking straight up"
		for (float start : new float[] {-90f, 90f, 37f, -4f}) {
			float x = start;
			for (int k = 0; k < 10; k++) x = Skills.levelStep(x);
			assertEquals(0f, x, 1e-4, "from " + start);
		}
		assertEquals(-81f, Skills.levelStep(-90f), 1e-4, "at most 9 deg a tick");
	}

	@Test
	void seatsOfWhatSheCanRide() { // O145
		assertEquals(2, Interact.seats(true, false, false, false, false, false), "a boat: her and the owner");
		assertEquals(1, Interact.seats(true, true, false, false, false, false), "a chest boat: one");
		assertEquals(1, Interact.seats(false, false, true, false, false, false), "a minecart");
		assertEquals(1, Interact.seats(false, false, false, true, false, false), "a horse, donkey, llama");
		assertEquals(2, Interact.seats(false, false, false, true, true, false), "a camel");
		assertEquals(1, Interact.seats(false, false, false, false, false, true), "a saddled pig or strider");
		assertEquals(0, Interact.seats(false, false, false, false, false, false), "a cow, an unsaddled pig: not_rideable");
	}

	@Test
	void aFasterToolInTheMainInventoryComesUp() { // O149: a stone shovel in slot 20, dirt, the hand (1.0) in the hotbar
		assertTrue(Interact.MineBlock.pullFaster(0, 1.0f, 20, 4.0f), "the shovel is fetched although the hand breaks dirt");
		assertFalse(Interact.MineBlock.pullFaster(1, 4.0f, 20, 4.0f), "as fast in the hotbar already: no move");
		assertFalse(Interact.MineBlock.pullFaster(1, 4.0f, 1, 4.0f), "the best is a hotbar slot: no move");
		assertTrue(Interact.MineBlock.pullFaster(-1, -1f, 20, 2.0f), "O101a: nothing in the hotbar can take it");
		assertFalse(Interact.MineBlock.pullFaster(-1, -1f, -1, -1f), "nothing anywhere: no_tool");
	}

	static java.util.List<Integer> toList(int[] a) {
		return a == null ? null : java.util.List.of(a[0], a[1]);
	}
}
