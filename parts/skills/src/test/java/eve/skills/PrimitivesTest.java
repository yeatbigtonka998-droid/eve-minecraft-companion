package eve.skills;

import com.google.gson.JsonParser;
import eve.body.api.Step;
import net.minecraft.core.BlockPos;
import net.minecraft.world.phys.AABB;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

/** O94 (CP-39) primitives: the rules that need no world - the granted ids, the stop reasons, the grant box. */
class PrimitivesTest {
	static final String ZONE = "\"zone\":{\"min\":{\"x\":9,\"y\":64,\"z\":9},\"max\":{\"x\":0,\"y\":60,\"z\":0}}";

	static TendAnimals tend(String extra) {
		return new TendAnimals(true, JsonParser.parseString("{\"entity_ids\":[11,12,13]," + ZONE + extra + "}").getAsJsonObject());
	}

	@Test
	void eachGrantedAnimalAtMostOnce() {
		assertEquals(3, tend("").want); // no count: every granted id
		assertEquals(2, tend(",\"count\":2").want);
		assertEquals(3, tend(",\"count\":9").want); // never more than were granted
		assertEquals(new AABB(0, 60, 0, 10, 65, 10), tend("").zone); // either corner first, both corner blocks inside
	}

	@Test
	void stopReasonIsNeverNull() {
		assertEquals("count (2/2 sheared)", Gather.stopReason(Step.ok(), "2/2 sheared"));
		assertEquals("no_tool: no empty bucket left (0/1 milked)", Gather.stopReason(Step.failed("no_tool", "no empty bucket left"), "0/1 milked"));
		assertEquals("cancelled (1/3 milked)", Gather.stopReason(null, "1/3 milked"));
	}

	@Test
	void grantBoxIsInclusiveWhicheverCornerComesFirst() {
		AABB a = Gather.box(new BlockPos(9, 64, 9), new BlockPos(0, 60, 0));
		assertTrue(a.contains(9.5, 64.5, 9.5)); // inside the max corner block
		assertTrue(a.contains(0.0, 60.0, 0.0)); // the min corner
		assertFalse(a.contains(10.01, 62, 5)); // just past it
	}

	@Test
	void prospectLeavesAWallOreBesideLava() { // O175: lava on any of the six faces (source or flowing) - never mined
		BlockPos ore = new BlockPos(5, -58, 5);
		for (BlockPos lava : new BlockPos[] {ore.above(), ore.below(), ore.north(), ore.south(), ore.east(), ore.west()})
			assertTrue(Prospect.besideLava(lava::equals, ore), lava.toShortString());
		assertFalse(Prospect.besideLava(new BlockPos(6, -57, 5)::equals, ore), "a diagonal is not a face");
		assertFalse(Prospect.besideLava(q -> false, ore));
	}

	@Test
	void prospectNeverMinesItsOwnFloor() { // CP-86 run 4: stone as the "ore" - the block under her step was mined, she fell in
		BlockPos cursor = new BlockPos(-33, 68, -12), next = new BlockPos(-33, 68, -11), feet = cursor;
		java.util.Set<BlockPos> path = java.util.Set.of(new BlockPos(-33, 69, -13), cursor);
		assertTrue(Prospect.floor(path, feet, next, next.below()), "the next step's floor (the run 4 cell -33, 67, -11)");
		assertTrue(Prospect.floor(path, feet, next, cursor.below()), "her own floor");
		assertTrue(Prospect.floor(path, feet, next, new BlockPos(-33, 68, -13)), "a walked cell's floor");
		assertFalse(Prospect.floor(path, feet, next, next.east()), "a wall beside the step is an ore");
		assertFalse(Prospect.floor(path, feet, next, next.above(2)), "the ceiling is an ore");
	}

	@Test
	void mineResourceStopsWhenOnlyStoneBelowIsLeft() { // CP-86 run 4: 64 walks below drifted her 33 m before prospect
		assertFalse(MineResource.belowOnly(true, false, 0, MineResource.BELOW_TRIES - 1));
		assertTrue(MineResource.belowOnly(true, false, 0, MineResource.BELOW_TRIES));
		assertFalse(MineResource.belowOnly(true, true, 0, 9), "a reachable target is still worth walking to");
		assertFalse(MineResource.belowOnly(true, false, 3, 9), "something mined: keep going (partial, not a dig-down)");
		assertFalse(MineResource.belowOnly(false, false, 0, 9));
	}

	@Test
	void prospectNeverDigsABuild() { // O175 live 15:58: the west tunnel broke the shelter's stone_bricks wall
		BlockPos a = new BlockPos(-1, 76, 0), b = new BlockPos(-1, 77, 0);
		java.util.Map<BlockPos, String> w = java.util.Map.of(a, "minecraft:stone", b, "minecraft:stone_bricks");
		java.util.function.Function<BlockPos, String> at = q -> w.getOrDefault(q, "minecraft:air");
		assertEquals("minecraft:stone_bricks at -1, 77, 0", Prospect.build(at, a, b));
		assertNull(Prospect.build(at, a), "natural stone is dug");
		assertNull(Prospect.build(q -> "minecraft:deepslate_iron_ore", a, b));
	}
}
