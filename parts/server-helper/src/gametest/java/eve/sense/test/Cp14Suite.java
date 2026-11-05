package eve.sense.test;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import eve.sense.Query;
import net.minecraft.core.BlockPos;
import net.minecraft.gametest.framework.GameTestHelper;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.CropBlock;
import net.minecraft.world.level.block.state.BlockState;

import java.util.List;
import java.util.Set;
import java.util.TreeSet;
import java.util.function.Supplier;

/**
 * CP-14 exactness suite (K class): query.blocks / query.resources against a hand-built fixture whose expected answers are
 * written out here by hand (not computed by the code under test). Fixture manifest (relative to the test origin):
 *   stone cube x 0..4, y 1..5, z 0..4 with: coal_ore (1,5,3) and oak_log (3,5,1) on the top face; iron_ore (0,3,3) on the
 *   west face; a sealed air pocket (1,2,1); iron_ore (2,2,1) beside that pocket; diamond_ore (3,4,3) and oak_log (2,3,3)
 *   fully enclosed. Crops on farmland at y 1/2, z 6: wheat age 3 (1), carrots age 7 (2) in the mine zone, wheat age 5 (4)
 *   in the gap (no zone), potatoes age 2 (6) in the home zone. Eight distinct plain blocks at x 6..7, y 5..6, z 0..1 for the order.
 * Recorded sensor policy under test: loaded chunks only (never loads one); a block is "exposed" when one face touches a
 * non-opaque block, otherwise "hidden"; ore/logs only when exposed; ore honours sensors.ore, crops sensors.crops (zones_only).
 * Note case N06: an ore beside a SEALED air pocket counts as exposed under this face-exposure policy (stated, not hidden).
 */
public class Cp14Suite extends Cp15Suite {
	private static final String STONE = "minecraft:stone", AIR = "minecraft:air";

	void cp14Queries(GameTestHelper helper) {
		suite(helper, "CP-14", this::run14);
	}

	void run14() {
		build();
		variant = "N normal";
		JsonObject top = Query.blocks(level.getServer(), box(new BlockPos(0, 5, 0), new BlockPos(4, 6, 4)));
		StringBuilder bad = new StringBuilder();
		for (int x = 0; x <= 4; x++)
			for (int z = 0; z <= 4; z++) {
				String want = x == 1 && z == 3 ? "minecraft:coal_ore" : x == 3 && z == 1 ? "minecraft:oak_log[axis=y]" : STONE;
				if (!want.equals(at(top, new BlockPos(x, 5, z)))) bad.append(x).append(",5,").append(z).append(' ');
				if (!AIR.equals(at(top, new BlockPos(x, 6, z)))) bad.append(x).append(",6,").append(z).append(' ');
			}
		kcase("N01 top face + air layer: every state exact (50 blocks)", "", bad.toString().trim());
		kcase("N02 enclosed diamond ore is hidden", "hidden", one(new BlockPos(3, 4, 3)));
		kcase("N03 sealed air pocket is hidden", "hidden", one(new BlockPos(1, 2, 1)));
		kcase("N04 enclosed oak log is hidden", "hidden", one(new BlockPos(2, 3, 3)));
		kcase("N05 ore on the west face is visible", "minecraft:iron_ore", one(new BlockPos(0, 3, 3)));
		kcase("N06 ore query: exposed ores only (incl. beside the sealed pocket), not the enclosed diamond",
				set("1,5,3=minecraft:coal_ore", "0,3,3=minecraft:iron_ore", "2,2,1=minecraft:iron_ore"), found(res("ore", new BlockPos(2, 3, 2), 4)));
		kcase("N07 logs query: the exposed log only", set("3,5,1=minecraft:oak_log"), found(res("logs", new BlockPos(2, 3, 2), 4)));
		kcase("N08 crops query: zones only, with ages", set("1,2,6=minecraft:wheat age=3", "2,2,6=minecraft:carrots age=7", "6,2,6=minecraft:potatoes age=2"),
				found(res("crops", new BlockPos(3, 2, 6), 4)));
		JsonObject order = Query.blocks(level.getServer(), box(new BlockPos(6, 5, 0), new BlockPos(7, 6, 1)));
		StringBuilder ob = new StringBuilder();
		for (int i = 0; i < 8; i++) {
			BlockPos p = new BlockPos(6 + (i & 1), 5 + (i >> 2), (i >> 1) & 1);
			String want = net.minecraft.core.registries.BuiltInRegistries.BLOCK.getKey(WOOLS.get(i)).toString();
			if (!want.equals(at(order, p))) ob.append(p.toShortString()).append(' ');
		}
		kcase("N09 data order x,z,y decodes eight distinct blocks", "", ob.toString().trim());
		apply(o -> o.getAsJsonObject("sensors").addProperty("ore", "none"));
		JsonObject none = res("ore", new BlockPos(2, 3, 2), 4);
		kcase("N10 sensors.ore none: nothing reported", "none []", none.get("policy").getAsString() + " " + none.getAsJsonArray("items"));
		apply(o -> o.getAsJsonObject("sensors").addProperty("crops", "none"));
		JsonObject noCrops = res("crops", new BlockPos(3, 2, 6), 4);
		kcase("N11 sensors.crops none: nothing reported", "none []", noCrops.get("policy").getAsString() + " " + noCrops.getAsJsonArray("items"));
		apply(o -> { });
		level.setBlockAndUpdate(abs(1, 5, 3), Blocks.STONE.defaultBlockState());                      // the coal is mined
		level.setBlockAndUpdate(abs(1, 2, 6), ((CropBlock) Blocks.WHEAT).getStateForAge(7));          // the wheat ripens
		kcase("N12 after a fixture change: ore answer refreshed", set("0,3,3=minecraft:iron_ore", "2,2,1=minecraft:iron_ore"),
				found(res("ore", new BlockPos(2, 3, 2), 4)));
		kcase("N13 after a fixture change: crop age refreshed", set("1,2,6=minecraft:wheat age=7", "2,2,6=minecraft:carrots age=7", "6,2,6=minecraft:potatoes age=2"),
				found(res("crops", new BlockPos(3, 2, 6), 4)));

		variant = "B boundary/malformed";
		kcase("B01 volume exactly 32768 is answered", "32768", String.valueOf(Query.blocks(level.getServer(),
				box(new BlockPos(-8, 1, -8), new BlockPos(23, 32, 23))).getAsJsonArray("data").size()));
		kcase("B02 volume 32769 is refused", "bad_payload", outcome(() -> Query.blocks(level.getServer(), box(new BlockPos(0, 1, 0), new BlockPos(330, 9, 10)))));
		kcase("B03 swapped min/max give the same answer", Query.blocks(level.getServer(), box(new BlockPos(0, 5, 0), new BlockPos(4, 6, 4))).toString(),
				Query.blocks(level.getServer(), box(new BlockPos(4, 6, 4), new BlockPos(0, 5, 0))).toString());
		int below = level.getMinY() - 1 - abs(0, 0, 0).getY(), above = level.getMaxY() + 1 - abs(0, 0, 0).getY();
		kcase("B04 y below the world is refused", "bad_payload", outcome(() -> Query.blocks(level.getServer(), box(new BlockPos(0, below, 0), new BlockPos(0, below + 2, 0)))));
		kcase("B05 y above the world is refused", "bad_payload", outcome(() -> Query.blocks(level.getServer(), box(new BlockPos(0, above - 2, 0), new BlockPos(0, above, 0)))));
		kcase("B06 missing dim is refused", "bad_payload", outcome(() -> { JsonObject q = box(BlockPos.ZERO, BlockPos.ZERO); q.remove("dim"); return Query.blocks(level.getServer(), q); }));
		kcase("B07 unknown dim is refused", "bad_payload", outcome(() -> { JsonObject q = box(BlockPos.ZERO, BlockPos.ZERO); q.addProperty("dim", "minecraft:nowhere"); return Query.blocks(level.getServer(), q); }));
		kcase("B08 missing min is refused", "bad_payload", outcome(() -> { JsonObject q = box(BlockPos.ZERO, BlockPos.ZERO); q.remove("min"); return Query.blocks(level.getServer(), q); }));
		kcase("B09 non-numeric coordinate is refused", "bad_payload", outcome(() -> { JsonObject q = box(BlockPos.ZERO, BlockPos.ZERO); q.getAsJsonObject("min").addProperty("x", "a"); return Query.blocks(level.getServer(), q); }));
		kcase("B10 radius 0 is refused", "bad_payload", outcome(() -> res("ore", BlockPos.ZERO, 0)));
		kcase("B11 radius 33 is refused", "bad_payload", outcome(() -> res("ore", BlockPos.ZERO, 33)));
		kcase("B12 radius 32 is answered", "ok", outcome(() -> res("ore", BlockPos.ZERO, 32)));
		kcase("B13 unknown kind is refused", "bad_payload", outcome(() -> res("diamonds", BlockPos.ZERO, 4)));
		// CP-53 void_edge: a sky platform x 10..12 (y 30, z 10) over 20+ blocks of air; a ledge 3 below the south side of
		// x 11; a block on top of x 12 at y 32 (no headroom there, and itself an edge)
		for (int x = 7; x <= 15; x++) for (int y = 18; y <= 35; y++) for (int z = 7; z <= 15; z++) level.setBlockAndUpdate(abs(x, y, z), Blocks.AIR.defaultBlockState());
		for (int x = 10; x <= 12; x++) level.setBlockAndUpdate(abs(x, 30, 10), Blocks.STONE.defaultBlockState());
		level.setBlockAndUpdate(abs(11, 27, 11), Blocks.STONE.defaultBlockState());
		level.setBlockAndUpdate(abs(12, 32, 10), Blocks.STONE.defaultBlockState());
		kcase("V01 void_edge: standable blocks with a walk-off side and nothing solid within 8 below, per direction",
				set("10,30,10=north,south,west", "11,30,10=north", "11,27,11=east,north,south,west", "12,32,10=east,north,south"),
				edges(res("void_edge", new BlockPos(11, 30, 10), 4)));
		kcase("V02 void_edge policy is geometry", "geometry", res("void_edge", new BlockPos(11, 30, 10), 4).get("policy").getAsString());
		for (int x = 7; x <= 15; x++) for (int y = 18; y <= 35; y++) for (int z = 7; z <= 15; z++) level.setBlockAndUpdate(abs(x, y, z), Blocks.AIR.defaultBlockState());
		BlockPos far = new BlockPos(1_000_000, 64, 1_000_000);                                              // absolute, never loaded
		JsonObject q = new JsonObject();
		q.addProperty("dim", "minecraft:overworld");
		q.add("min", xyz(far.getX(), far.getY(), far.getZ()));
		q.add("max", xyz(far.getX() + 3, far.getY() + 1, far.getZ() + 3));
		JsonObject unl = Query.blocks(level.getServer(), q);
		boolean stillUnloaded = level.getChunkSource().getChunkNow(far.getX() >> 4, far.getZ() >> 4) == null;
		kcase("B14 unloaded volume: labelled, and still not loaded afterwards", "[\"unloaded\"] 1 true",
				unl.getAsJsonArray("palette") + " " + unl.getAsJsonArray("unloaded").size() + " " + stillUnloaded);
		JsonObject rq = new JsonObject();
		rq.addProperty("kind", "ore");
		rq.addProperty("dim", "minecraft:overworld");
		rq.add("center", xyz(far.getX(), far.getY(), far.getZ()));
		rq.addProperty("radius", 8);
		JsonObject ur = Query.resources(level.getServer(), rq);
		kcase("B15 resources in an unloaded area: nothing scanned, nothing loaded", "0 true [] true",
				ur.get("scanned_chunks").getAsInt() + " " + (ur.get("unloaded_chunks").getAsInt() > 0) + " " + ur.getAsJsonArray("items") + " "
						+ (level.getChunkSource().getChunkNow(far.getX() >> 4, far.getZ() >> 4) == null));
	}

	private static final List<Block> WOOLS = List.of(Blocks.GRANITE, Blocks.DIORITE, Blocks.ANDESITE, Blocks.DIRT,
			Blocks.COBBLESTONE, Blocks.GLASS, Blocks.OAK_PLANKS, Blocks.BRICKS); // eight distinct plain blocks

	private void build() {
		for (int x = -2; x <= 8; x++) for (int y = 1; y <= 8; y++) for (int z = -2; z <= 9; z++) set(x, y, z, Blocks.AIR.defaultBlockState());
		for (int x = 0; x <= 4; x++) for (int y = 1; y <= 5; y++) for (int z = 0; z <= 4; z++) set(x, y, z, Blocks.STONE.defaultBlockState());
		set(1, 5, 3, Blocks.COAL_ORE.defaultBlockState());
		set(3, 5, 1, Blocks.OAK_LOG.defaultBlockState());
		set(0, 3, 3, Blocks.IRON_ORE.defaultBlockState());
		set(1, 2, 1, Blocks.AIR.defaultBlockState());
		set(2, 2, 1, Blocks.IRON_ORE.defaultBlockState());
		set(3, 4, 3, Blocks.DIAMOND_ORE.defaultBlockState());
		set(2, 3, 3, Blocks.OAK_LOG.defaultBlockState());
		int[][] crops = {{1, 3}, {2, 7}, {4, 5}, {6, 2}};
		Block[] kinds = {Blocks.WHEAT, Blocks.CARROTS, Blocks.WHEAT, Blocks.POTATOES};
		for (int i = 0; i < crops.length; i++) {
			set(crops[i][0], 1, 6, Blocks.FARMLAND.defaultBlockState());
			set(crops[i][0], 2, 6, ((CropBlock) kinds[i]).getStateForAge(crops[i][1]));
		}
		for (int i = 0; i < 8; i++) set(6 + (i & 1), 5 + (i >> 2), (i >> 1) & 1, WOOLS.get(i).defaultBlockState());
	}

	private void set(int x, int y, int z, BlockState s) {
		level.setBlock(abs(x, y, z), s, 2); // flag 2: no neighbour updates, so crops and logs stay exactly as placed
	}

	private JsonObject box(BlockPos relMin, BlockPos relMax) {
		JsonObject p = new JsonObject();
		p.addProperty("dim", "minecraft:overworld");
		BlockPos a = abs(relMin), b = abs(relMax);
		p.add("min", xyz(a.getX(), a.getY(), a.getZ()));
		p.add("max", xyz(b.getX(), b.getY(), b.getZ()));
		return p;
	}

	private String one(BlockPos rel) {
		return at(Query.blocks(level.getServer(), box(rel, rel)), rel);
	}

	/** Decode query.blocks: index = ((y - y0) * dz + (z - z0)) * dx + (x - x0). */
	private String at(JsonObject r, BlockPos rel) {
		BlockPos a = abs(rel);
		JsonObject mn = r.getAsJsonObject("min"), mx = r.getAsJsonObject("max");
		int x0 = mn.get("x").getAsInt(), y0 = mn.get("y").getAsInt(), z0 = mn.get("z").getAsInt();
		int dx = mx.get("x").getAsInt() - x0 + 1, dz = mx.get("z").getAsInt() - z0 + 1;
		int i = ((a.getY() - y0) * dz + (a.getZ() - z0)) * dx + (a.getX() - x0);
		return r.getAsJsonArray("palette").get(r.getAsJsonArray("data").get(i).getAsInt()).getAsString();
	}

	private JsonObject res(String kind, BlockPos relCenter, int radius) {
		JsonObject p = new JsonObject();
		p.addProperty("kind", kind);
		p.addProperty("dim", "minecraft:overworld");
		BlockPos c = abs(relCenter);
		p.add("center", xyz(c.getX(), c.getY(), c.getZ()));
		p.addProperty("radius", radius);
		return Query.resources(level.getServer(), p);
	}

	/** "x,y,z=block[ age=n]" in test-relative coordinates, sorted. */
	private String found(JsonObject r) {
		Set<String> s = new TreeSet<>();
		BlockPos o = abs(0, 0, 0);
		for (JsonElement e : r.getAsJsonArray("items")) {
			JsonObject it = e.getAsJsonObject(), p = it.getAsJsonObject("pos"), st = it.getAsJsonObject("state");
			s.add((p.get("x").getAsInt() - o.getX()) + "," + (p.get("y").getAsInt() - o.getY()) + "," + (p.get("z").getAsInt() - o.getZ())
					+ "=" + it.get("block").getAsString() + (st.has("age") ? " age=" + st.get("age").getAsString() : ""));
		}
		return String.join("; ", s);
	}

	private String edges(JsonObject r) {
		Set<String> s = new TreeSet<>();
		BlockPos o = abs(0, 0, 0);
		for (JsonElement e : r.getAsJsonArray("items")) {
			JsonObject it = e.getAsJsonObject(), p = it.getAsJsonObject("pos");
			List<String> t = new java.util.ArrayList<>();
			for (JsonElement d : it.getAsJsonArray("toward")) t.add(d.getAsString());
			java.util.Collections.sort(t);
			s.add((p.get("x").getAsInt() - o.getX()) + "," + (p.get("y").getAsInt() - o.getY()) + "," + (p.get("z").getAsInt() - o.getZ()) + "=" + String.join(",", t));
		}
		return String.join("; ", s);
	}

	private static String set(String... items) {
		return String.join("; ", new TreeSet<>(List.of(items)));
	}

	/** What Link.answer sends: the result, or error{bad_payload} for a malformed request. */
	private static String outcome(Supplier<JsonObject> q) {
		try {
			q.get();
			return "ok";
		} catch (IllegalArgumentException | IllegalStateException | NullPointerException | ClassCastException | UnsupportedOperationException e) {
			return "bad_payload";
		}
	}

	private void kcase(String name, String expected, String observed) {
		boolean ok = expected.equals(observed);
		attempts.add(new Attempt(attempts.size() + 1, variant, name, null, expected, observed, false, ok, List.of()));
	}
}
