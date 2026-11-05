package eve.builder;

import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.Vec3;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.function.BiFunction;

import static org.junit.jupiter.api.Assertions.*;

/** O89f dev fixture: CP-50 lane C a30/a31's failed click (both body jars), as design measured it (~1.6° to spare). */
class MarginTest {
	/** First outline box a segment enters, and the face it enters through; a miss ends at the segment's end. */
	static BiFunction<Vec3, Vec3, BlockHitResult> world(Map<BlockPos, List<AABB>> blocks) {
		return (a, z) -> {
			BlockHitResult best = BlockHitResult.miss(z, Direction.UP, BlockPos.ZERO);
			double bd = Double.MAX_VALUE;
			for (var e : blocks.entrySet())
				for (AABB box : e.getValue()) {
					Optional<Vec3> h = box.clip(a, z);
					if (h.isEmpty() || h.get().distanceTo(a) >= bd) continue;
					bd = h.get().distanceTo(a);
					best = new BlockHitResult(h.get(), faceOf(box, h.get()), e.getKey(), false);
				}
			return best;
		};
	}

	static Direction faceOf(AABB b, Vec3 h) {
		double e = 1e-7;
		if (Math.abs(h.x - b.minX) < e) return Direction.WEST;
		if (Math.abs(h.x - b.maxX) < e) return Direction.EAST;
		if (Math.abs(h.y - b.minY) < e) return Direction.DOWN;
		if (Math.abs(h.y - b.maxY) < e) return Direction.UP;
		return Math.abs(h.z - b.minZ) < e ? Direction.NORTH : Direction.SOUTH;
	}

	// a30/a31, lane C origin (23,151,-45), hut5_sim as committed (53eb23c) before its step 116: oak_stairs[facing=west,
	// half=bottom] at (4,4,2) placed against the support stair at (4,4,1) = (27,155,-44), its south face, from design's stand
	// (6,1,3) = (29,152,-42); the east wall below (4,3,1..3), the double slab (4,4,0) and the roof planks (3,4,1..2) around
	static final BlockPos SUP = new BlockPos(27, 155, -44);
	static final Map<BlockPos, List<AABB>> HUT = Map.of(
			SUP, List.of(new AABB(27, 155, -44, 28, 155.5, -43), new AABB(27, 155.5, -44, 27.5, 156, -43)), // bottom half + the west (high) step
			new BlockPos(27, 154, -44), List.of(new AABB(27, 154, -44, 28, 155, -43)),
			new BlockPos(27, 154, -43), List.of(new AABB(27, 154, -43, 28, 155, -42)),
			new BlockPos(27, 154, -42), List.of(new AABB(27, 154, -42, 28, 155, -41)),
			new BlockPos(27, 155, -45), List.of(new AABB(27, 155, -45, 28, 156, -44)),
			new BlockPos(26, 155, -44), List.of(new AABB(26, 155, -44, 27, 156, -43)),
			new BlockPos(26, 155, -43), List.of(new AABB(26, 155, -43, 27, 156, -42)));

	static boolean exact(Vec3 eye, Vec3 pt) {
		BlockHitResult h = world(HUT).apply(eye, eye.add(pt.subtract(eye).scale(1 + 1.5 / eye.distanceTo(pt))));
		return h.getBlockPos().equals(SUP) && h.getDirection() == Direction.SOUTH;
	}

	@Test
	void designsStandClickHasNoMargin() {
		Vec3 eye = new Vec3(29.5, 152 + Build.EYE_SNEAK, -41.5), pt = new Vec3(27.9, 155.25, -43.0); // u_x 0.9, u_y 0.25
		assertTrue(exact(eye, pt), "the exact ray clears: the old rule accepted this stand");
		assertTrue(Build.steady(world(HUT), eye, pt, SUP, Plan.Dir.south, "bottom", null), "±1° stays on the face (design's 53eb23c rule kept it)");
		assertFalse(Build.steady(world(HUT), eye, pt, SUP, Plan.Dir.south, "bottom", Direction.EAST), "turned 2° it lands on the support stair's front (east)");
	}

	@Test
	void aStandInFrontKeepsTheMargin() {
		Vec3 eye = new Vec3(27.5, 155 + Build.EYE_SNEAK, -40.5), pt = new Vec3(27.5, 155.25, -43.0); // feet (4,4,4), on the wall
		assertTrue(exact(eye, pt));
		assertTrue(Build.steady(world(HUT), eye, pt, SUP, Plan.Dir.south, "bottom", Direction.EAST));
		// the same stand aiming high into the upper half: its ±1° rays leave the wanted (bottom) half
		Vec3 high = new Vec3(27.25, 155.49, -43.0);
		assertTrue(exact(eye, high));
		assertFalse(Build.steady(world(HUT), eye, high, SUP, Plan.Dir.south, "bottom", Direction.EAST));
	}
}
