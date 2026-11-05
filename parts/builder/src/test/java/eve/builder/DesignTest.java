package eve.builder;

import org.junit.jupiter.api.Test;

import java.nio.file.Path;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/** Design's own example folders are the inputs; the hash vectors are the contract's (design-project.md Amendments). */
class DesignTest {
	static final Path OUT = Path.of(System.getProperty("eve.designs"));

	@Test
	void hashVectors() throws Exception {
		assertEquals("90a0175d44fb6728663e0781a329782f1d2e15209d24a2b80d83c31b85000edd", Design.hash(Design.read(OUT.resolve("hut5/design.json"))));
		// test3 is design's live fixture (it changes between commits): its stored hash must re-verify; hut5 is the contract vector
		Design t = Design.load(OUT.resolve("test3"));
		assertEquals(t.json.get("hash").getAsString(), t.grid.hash());
	}

	@Test
	void findsExampleFolderByVersion() throws Exception {
		assertEquals(OUT.resolve("test3"), Design.find(List.of(OUT), "test3", 1));
		assertNull(Design.find(List.of(OUT), "test3", 2));
	}

	@Test
	void test3StepsMatchGrid() throws Exception {
		Design t = Design.load(OUT.resolve("test3"));
		List<Design.Step> fin = t.steps.stream().filter(s -> s.layer().equals("final")).toList();
		assertEquals(21, fin.size()); // 27 non-air cells minus air and the door top, which appears by itself
		for (Design.Step s : fin) assertEquals(t.grid.state(s.x(), s.y(), s.z()), s.state(), "step at " + s.x() + "," + s.y() + "," + s.z());
		for (Design.Step s : t.steps) if (s.layer().equals("scaffold")) assertEquals(t.scaffoldAt(s.x(), s.y(), s.z()), s.state());
		assertEquals("minecraft:oak_door[facing=south,half=upper,hinge=left,open=false,powered=false]", t.grid.state(1, 2, 1));
		assertEquals(Map.of("minecraft:oak_slab", 2), fin.stream().filter(s -> s.state().contains("type=double")).findFirst().orElseThrow().items());
	}

	@Test
	void lookFirstMovesDoorAheadOfItsLayerOnly() throws Exception {
		Design t = Design.load(OUT.resolve("test3"));
		List<Design.Step> o = Plan.lookFirst(t.steps);
		assertEquals(t.steps.size(), o.size());
		List<String> y1 = o.stream().filter(s -> s.y() == 1).map(s -> Plan.State.parse(s.state()).id()).toList();
		assertEquals("minecraft:oak_door", y1.get(0));
		assertEquals("minecraft:oak_stairs", o.stream().filter(s -> s.layer().equals("final")).map(s -> Plan.State.parse(s.state()).id()).findFirst().orElseThrow());
		// only reordered inside runs of one y: the y sequence (incl. the hanging lantern after its y=2 support) is unchanged
		assertEquals(t.steps.stream().map(Design.Step::y).toList(), o.stream().map(Design.Step::y).toList());
	}

	@Test
	void hut5CompactOrderWithScaffold() throws Exception {
		Design h = Design.load(OUT.resolve("hut5"));
		// design re-plans the scaffold now and then (2 hand dirt, then 25, ...): check the shape, not a total
		assertEquals(110, h.steps.stream().filter(s -> s.layer().equals("final")).count());
		assertEquals(h.steps.stream().filter(s -> s.layer().equals("scaffold")).count(),
				h.steps.stream().filter(s -> s.layer().equals("scaffold_removal")).count());
		Design.Step first = h.steps.get(0), last = h.steps.get(h.steps.size() - 1);
		assertEquals("scaffold", first.layer());
		assertEquals("minecraft:dirt", first.state());
		assertEquals(Map.of("minecraft:dirt", 1), first.items());
		assertEquals("scaffold_removal", last.layer());
		assertEquals("minecraft:dirt", h.scaffoldAt(last.x(), last.y(), last.z()));
		for (Design.Step s : h.steps)
			if (s.layer().equals("final")) {
				assertEquals(h.grid.state(s.x(), s.y(), s.z()), s.state());
				assertFalse(s.items().isEmpty(), "items for " + s.state());
			}
	}

	@Test
	void bridgeLine() {
		var from = new net.minecraft.core.BlockPos(0, 63, 0);
		var l = Bridge.line(from, new net.minecraft.core.BlockPos(0, 63, -5), 40);
		assertEquals(5, l.size());
		assertEquals(new net.minecraft.core.BlockPos(0, 63, -1), l.get(0));
		assertEquals(new net.minecraft.core.BlockPos(0, 63, -5), l.get(4));
		assertEquals(30, Bridge.line(from, new net.minecraft.core.BlockPos(30, 63, 0), 40).size());
		assertNull(Bridge.line(from, new net.minecraft.core.BlockPos(3, 63, 3), 40)); // diagonal
		assertNull(Bridge.line(from, new net.minecraft.core.BlockPos(5, 64, 0), 40)); // not level
		assertNull(Bridge.line(from, new net.minecraft.core.BlockPos(41, 63, 0), 40)); // beyond max_blocks
		assertNull(Bridge.line(from, from, 40)); // nowhere to go
	}

	@Test
	void repairStepsAsSparseDesign() {
		// shaped like sense's zone.repair.result (CP-52 fixture on lane B): a trampled farmland cell, a crater cell, an untouched gap
		var steps = com.google.gson.JsonParser.parseString("["
				+ "{\"op\":\"remove\",\"pos\":{\"x\":-2,\"y\":160,\"z\":-74},\"block\":\"minecraft:dirt\"},"
				+ "{\"op\":\"place\",\"pos\":{\"x\":-2,\"y\":160,\"z\":-74},\"block\":\"minecraft:farmland[moisture=7]\"},"
				+ "{\"op\":\"place\",\"pos\":{\"x\":-5,\"y\":162,\"z\":-72},\"block\":\"minecraft:cobblestone\"}]").getAsJsonArray();
		Design d = Design.fromSteps(steps);
		Design.Grid g = d.grid;
		assertEquals(-5, g.ox());
		assertEquals(160, g.oy());
		assertEquals(-74, g.oz());
		assertEquals("minecraft:farmland[moisture=7]", g.state(3, 0, 0)); // the later place wins over the removal
		assertEquals("minecraft:cobblestone", g.state(0, 2, 2));
		assertEquals(Design.ANY, g.state(1, 1, 1)); // no step names it: never touched
		assertEquals(List.of("remove", "final", "final"), d.steps.stream().map(Design.Step::layer).toList());
		assertEquals("minecraft:dirt", d.steps.get(0).state()); // what a removal may break
		assertEquals(Map.of("minecraft:dirt", 1), d.steps.get(1).items()); // farmland = dirt, then tilled
		assertEquals(Map.of(), Plan.stillNeeded("minecraft:farmland[moisture=7]", "minecraft:dirt", Map.of("minecraft:dirt", 1)));
		assertEquals("derived_mismatch", Plan.classify("minecraft:farmland[moisture=7]", "minecraft:farmland[moisture=0]"));
		assertEquals(Map.of("minecraft:torch", 1), Plan.repairItems("minecraft:wall_torch[facing=south]"));
		assertEquals(Map.of("minecraft:oak_sign", 1), Plan.repairItems("minecraft:oak_wall_sign[facing=north,waterlogged=false]"));
		assertEquals(Map.of(), Plan.repairItems("minecraft:oak_door[facing=south,half=upper,hinge=left,open=false,powered=false]"));
		// replant (sense 0.1.13 crop_cycle skips carry the snapshot block): planted last, at age 0, with the seed item
		var replant = com.google.gson.JsonParser.parseString("[{\"pos\":{\"x\":-2,\"y\":161,\"z\":-74},\"block\":\"minecraft:wheat[age=7]\"}]").getAsJsonArray();
		Design r = Design.fromSteps(steps, replant);
		Design.Step last = r.steps.get(r.steps.size() - 1);
		assertEquals("minecraft:wheat[age=0]", last.state());
		assertEquals(Map.of("minecraft:wheat_seeds", 1), last.items());
		assertEquals("derived_mismatch", Plan.classify("minecraft:wheat[age=0]", "minecraft:wheat[age=3]")); // it grew: still right
	}

	@Test
	void standColumnBothShapes() {
		assertArrayEquals(new int[] {1, 0, -2}, Design.stand(com.google.gson.JsonParser.parseString("{\"pos\":{\"x\":1,\"y\":0,\"z\":-2},\"yaw\":90}")));
		assertArrayEquals(new int[] {3, 1, 4}, Design.stand(com.google.gson.JsonParser.parseString("[3,1,4]")));
		assertNull(Design.stand(null));
		var bo = com.google.gson.JsonParser.parseString("{\"columns\":[\"layer\",\"x\",\"y\",\"z\",\"after\",\"also_appears\",\"note\",\"stand\"],"
				+ "\"rows\":[[\"final\",0,0,0,null,null,null,{\"pos\":{\"x\":-2,\"y\":0,\"z\":0}}],[\"final\",0,0,0,null,null,null,null]]}").getAsJsonObject();
		var g = Design.grid(com.google.gson.JsonParser.parseString("{\"origin\":{\"x\":0,\"y\":64,\"z\":0},\"size\":{\"x\":1,\"y\":1,\"z\":1},"
				+ "\"palette\":[\"minecraft:air\",\"minecraft:stone\"],\"blocks\":\"AQA=\",\"roles\":\"AQ==\"}").getAsJsonObject());
		List<Design.Step> st = Design.steps(bo, g, null, null);
		assertArrayEquals(new int[] {-2, 0, 0}, st.get(0).stand());
		assertNull(st.get(1).stand());
	}

	@Test
	void compactRowsReadLikeVerboseSteps() {
		var bo = com.google.gson.JsonParser.parseString("{\"columns\":[],\"rows\":[[\"final\",0,0,0,null,null,null],[\"scaffold\",1,0,0,null,null,null],"
				+ "[\"scaffold_removal\",1,0,0,null,null,\"break the minecraft:dirt (scaffold)\"]]}").getAsJsonObject();
		var g = Design.grid(com.google.gson.JsonParser.parseString("{\"origin\":{\"x\":10,\"y\":64,\"z\":0},\"size\":{\"x\":1,\"y\":1,\"z\":1},"
				+ "\"palette\":[\"minecraft:air\",\"minecraft:stone\"],\"blocks\":\"AQA=\",\"roles\":\"AQ==\"}").getAsJsonObject());
		var s = Design.grid(com.google.gson.JsonParser.parseString("{\"origin\":{\"x\":11,\"y\":64,\"z\":0},\"size\":{\"x\":1,\"y\":1,\"z\":1},"
				+ "\"palette\":[\"minecraft:air\",\"minecraft:dirt\"],\"blocks\":\"AQA=\",\"roles\":\"AA==\"}").getAsJsonObject());
		var bom = com.google.gson.JsonParser.parseString("{\"items_per_state\":{\"minecraft:stone\":[{\"item\":\"minecraft:stone\",\"count\":1}],"
				+ "\"minecraft:dirt\":[{\"item\":\"minecraft:dirt\",\"count\":1}]}}").getAsJsonObject();
		List<Design.Step> st = Design.steps(bo, g, s, bom);
		assertEquals("minecraft:stone", st.get(0).state());
		assertEquals("minecraft:dirt", st.get(1).state());
		assertEquals(Map.of("minecraft:dirt", 1), st.get(1).items());
		assertEquals("minecraft:air", st.get(2).state());
		assertTrue(st.get(2).items().isEmpty());
	}

	@Test
	void planOptionsForTest3Families() {
		var stairs = Plan.of("minecraft:oak_stairs[facing=east,half=bottom,shape=straight,waterlogged=false]", Map.of("minecraft:oak_stairs", 1));
		assertEquals(new Plan.Option(Plan.Dir.up, Plan.Dir.east, null), stairs.options().get(0));
		var top = Plan.of("minecraft:oak_stairs[facing=south,half=top,shape=straight,waterlogged=false]", Map.of());
		assertEquals(Plan.Dir.down, top.options().get(0).face());
		assertNotNull(top.options().get(1).manual());
		var logX = Plan.of("minecraft:oak_log[axis=x]", Map.of("minecraft:oak_log", 1));
		assertEquals(Plan.Dir.east, logX.options().get(0).face());
		assertNull(logX.options().get(1).manual());
		assertNotNull(logX.options().get(2).manual());
		var torch = Plan.of("minecraft:wall_torch[facing=south]", Map.of("minecraft:torch", 1));
		assertEquals("minecraft:torch", torch.item());
		assertEquals(List.of(new Plan.Option(Plan.Dir.south, null, null)), torch.options());
		assertEquals(Plan.Dir.down, Plan.of("minecraft:lantern[hanging=true,waterlogged=false]", Map.of()).options().get(0).face());
		var trap = Plan.of("minecraft:oak_trapdoor[facing=north,half=bottom,open=true,powered=false,waterlogged=false]", Map.of());
		assertTrue(trap.useAfter());
		assertEquals(new Plan.Option(Plan.Dir.north, null, null), trap.options().get(0));
		assertEquals(new Plan.Option(Plan.Dir.up, Plan.Dir.south, null), trap.options().get(1));
		assertNotNull(Plan.of("minecraft:oak_slab[type=double,waterlogged=false]", Map.of()).options().get(0).manual());
		assertEquals(new Plan.Option(Plan.Dir.up, Plan.Dir.north, null),
				Plan.of("minecraft:chest[facing=south,type=single,waterlogged=false]", Map.of()).options().get(0));
		assertEquals(new Plan.Option(null, null, null), Plan.of("minecraft:cobblestone", Map.of()).options().get(0));
	}

	@Test
	void stillNeededAfterAPartialBuild() {
		Map<String, Integer> log = Map.of("minecraft:oak_log", 1), slabs = Map.of("minecraft:oak_slab", 2);
		assertEquals(log, Plan.stillNeeded("minecraft:oak_log[axis=z]", "minecraft:air", log));
		assertEquals(Map.of(), Plan.stillNeeded("minecraft:oak_log[axis=z]", "minecraft:oak_log[axis=y]", log)); // reused by the fix round
		assertEquals(Map.of(), Plan.stillNeeded("minecraft:oak_log[axis=z]", "minecraft:oak_log[axis=z]", log));
		assertEquals(Map.of("minecraft:oak_slab", 1),
				Plan.stillNeeded("minecraft:oak_slab[type=double,waterlogged=false]", "minecraft:oak_slab[type=bottom,waterlogged=false]", slabs));
		assertEquals(log, Plan.stillNeeded("minecraft:oak_log[axis=z]", "minecraft:stone", log));
	}

	@Test
	void openOnlyDifferenceIsAUse() {
		String want = "minecraft:oak_trapdoor[facing=north,half=bottom,open=true,powered=false,waterlogged=false]";
		assertTrue(Plan.onlyOpenDiffers(want, want.replace("open=true", "open=false")));
		assertFalse(Plan.onlyOpenDiffers(want, want.replace("open=true", "open=false").replace("facing=north", "facing=south")));
		assertFalse(Plan.onlyOpenDiffers(want, want));
		assertFalse(Plan.onlyOpenDiffers("minecraft:oak_log[axis=z]", "minecraft:oak_log[axis=y]"));
	}

	@Test
	void v2StateAndOptions() {
		assertEquals(Map.of("facing", "south", "half", "top"),
				Plan.v2State("minecraft:oak_stairs[facing=south,half=top,shape=straight,waterlogged=false]"));
		assertEquals(Map.of("type", "bottom"), Plan.v2State("minecraft:oak_slab[type=double,waterlogged=false]")); // first slab
		assertEquals(Map.of("facing", "south", "half", "lower", "hinge", "left"), // half=lower is what vanilla places; only upper is never sent
				Plan.v2State("minecraft:oak_door[facing=south,half=lower,hinge=left,open=false,powered=false]"));
		assertEquals(Map.of("facing", "north", "half", "bottom"),
				Plan.v2State("minecraft:oak_trapdoor[facing=north,half=bottom,open=true,powered=false,waterlogged=false]"));
		assertTrue(Plan.v2State("minecraft:glass_pane[east=true,north=false,south=true,waterlogged=false,west=true]").isEmpty());
		var top = Plan.of("minecraft:oak_stairs[facing=south,half=top,shape=straight,waterlogged=false]", Map.of(), true);
		assertTrue(top.options().stream().allMatch(o -> o.manual() == null));
		assertTrue(top.options().contains(new Plan.Option(Plan.Dir.east, Plan.Dir.south, null))); // a side face's upper half
		var logZ = Plan.of("minecraft:oak_log[axis=z]", Map.of(), true);
		assertEquals(List.of(new Plan.Option(Plan.Dir.south, null, null), new Plan.Option(Plan.Dir.north, null, null)), logZ.options());
		assertTrue(Plan.of("minecraft:oak_slab[type=double,waterlogged=false]", Map.of(), true).options().stream().allMatch(o -> o.manual() == null));
	}

	@Test
	void deficitIsWantInventoryMissing() {
		var d = Build.deficit(new java.util.TreeMap<>(Map.of("minecraft:oak_planks", 29, "minecraft:dirt", 2)),
				Map.of("minecraft:oak_planks", 10, "minecraft:dirt", 5, "minecraft:bread", 16));
		assertEquals(29, d.getAsJsonObject("want").get("minecraft:oak_planks").getAsInt());
		assertEquals(10, d.getAsJsonObject("inventory").get("minecraft:oak_planks").getAsInt());
		assertEquals(2, d.getAsJsonObject("inventory").get("minecraft:dirt").getAsInt()); // capped at want
		assertEquals(19, d.getAsJsonObject("missing").get("minecraft:oak_planks").getAsInt());
		assertFalse(d.getAsJsonObject("missing").has("minecraft:dirt"));
		assertFalse(d.getAsJsonObject("want").has("minecraft:bread"));
	}

	@Test
	void classifyResultClasses() {
		String pane = "minecraft:glass_pane[east=true,north=false,south=false,waterlogged=false,west=true]";
		assertEquals("match", Plan.classify(pane, "minecraft:glass_pane[waterlogged=false,north=false,east=true,south=false,west=true]"));
		assertEquals("derived_mismatch", Plan.classify(pane, "minecraft:glass_pane[east=false,north=false,south=false,waterlogged=false,west=true]"));
		assertEquals("wrong_state", Plan.classify("minecraft:oak_log[axis=x]", "minecraft:oak_log[axis=y]"));
		assertEquals("wrong_block", Plan.classify("minecraft:oak_log[axis=x]", "minecraft:stone"));
		assertEquals("missing", Plan.classify("minecraft:stone", "minecraft:air"));
		assertEquals("extra", Plan.classify("minecraft:air", "minecraft:dirt"));
		assertEquals("match", Plan.classify("minecraft:air", "minecraft:cave_air"));
	}
}
