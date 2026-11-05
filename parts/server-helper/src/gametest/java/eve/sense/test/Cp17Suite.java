package eve.sense.test;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import eve.sense.Rails;
import eve.sense.Snapshots;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.gametest.framework.GameTestHelper;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.CropBlock;
import net.minecraft.world.level.block.DoorBlock;
import net.minecraft.world.level.block.LeverBlock;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.AttachFace;
import net.minecraft.world.level.block.state.properties.DoubleBlockHalf;

import java.util.List;
import java.util.TreeSet;
import java.util.function.Supplier;

/**
 * CP-17 exactness suite (K class): zone snapshots and diffs against a hand-built "house and farm" in the home zone
 * (relative x 5..7, all y 0..7, z 0..7; snapshot:true for this suite). Expected answers are written out by hand.
 * Fixture manifest: stone floor y 0; stone-brick wall x 5..7, y 1..3, z 1; oak door (closed) at (5,1..2,3); lever on the
 * floor at (7,1,4); farmland (6,0,6),(7,0,6) with wheat age 2 at (6,1,6) and carrots age 4 at (7,1,6). 24 floor + 9 wall
 * + 2 door + 1 lever + 2 crops = 38 non-air blocks (the farmland replaces two floor blocks).
 * Actors: OwnerTest is the trusted owner, Stranger1 another player, EveTest is Eve (with a task grant).
 */
public class Cp17Suite extends Cp15Suite {
	void cp17Snapshots(GameTestHelper helper) {
		suite(helper, "CP-17", this::run17);
	}

	void run17() {
		apply(o -> o.getAsJsonArray("zones").get(1).getAsJsonObject().addProperty("snapshot", true));
		build();
		variant = "N normal";
		int before = Snapshots.latestVersion("home");
		cmd(owner, "eve save_zone home");
		JsonObject s1 = Snapshots.echo(echoReq("home", OWNER));
		String v1 = s1.get("snapshot_id").getAsString();
		kcase("N01 owner's /eve save_zone writes the next version, counting every block", "home@v" + (before + 1) + " 38",
				v1 + " " + s1.get("block_count").getAsInt());
		kcase("N02 no change: nothing missing, changed or ignored", "", all(diff(v1)));

		stranger.gameMode.destroyBlock(abs(6, 2, 1));
		level.setBlock(abs(7, 1, 1), Blocks.COBBLESTONE.defaultBlockState(), 2);                     // no player: e.g. a blast
		level.setBlock(abs(6, 1, 6), ((CropBlock) Blocks.WHEAT).getStateForAge(7), 2);                // the wheat grows
		level.setBlock(abs(7, 1, 6), Blocks.AIR.defaultBlockState(), 2);                               // the carrots are harvested
		door(true);                                                                                    // the door opens
		level.setBlock(abs(7, 1, 4), lever(true), 2);                                                  // the lever is pulled
		owner.gameMode.destroyBlock(abs(6, 3, 1));                                                     // Hunter renovates the wall
		place(owner, new BlockPos(5, 0, 6), Direction.UP, Items.OAK_PLANKS);                           // ... and adds a block
		long now = level.getServer().getTickCount();
		Rails.grant(grant("cp17", "break", "minecraft:overworld", List.of(new BlockPos(5, 1, 1)), now + 200));
		eve.gameMode.destroyBlock(abs(5, 1, 1));                                                       // Eve breaks under a task grant
		place(stranger, new BlockPos(7, 0, 7), Direction.UP, Items.DIRT);                               // a stranger adds a block
		JsonObject d = diff(v1);
		kcase("N03 stranger's break: missing, with the actor", "5,1,1 minecraft:stone_bricks eve; 6,2,1 minecraft:stone_bricks player:Stranger1",
				list(d, "missing"));
		kcase("N04 a change without a player, and a stranger's new block: changed",
				"7,1,1 minecraft:stone_bricks -> minecraft:cobblestone; 7,1,7 minecraft:air -> minecraft:dirt player:Stranger1", list(d, "changed"));
		kcase("N05 crop growth and harvest: ignored as crop_cycle", "6,1,6 crop_cycle; 7,1,6 crop_cycle", reasons(d, "crop_cycle"));
		kcase("N06 door opened and lever pulled: ignored as operational_state", "5,1,3 operational_state; 5,2,3 operational_state; 7,1,4 operational_state",
				reasons(d, "operational_state"));
		kcase("N07 the owner's break and place: ignored as owner_edit", "5,1,6 owner_edit; 6,3,1 owner_edit", reasons(d, "owner_edit"));
		kcase("N08 Eve's own break is reported (not an owner edit), attributed to eve", "eve", actor(d, "missing", new BlockPos(5, 1, 1)));
		kcase("N09 counts agree with the lists", "{\"changed\":2,\"ignored_crop_cycle\":2,\"ignored_operational_state\":3,\"ignored_owner_edit\":2,\"missing\":2}",
				d.getAsJsonObject("counts").toString());

		cmd(owner, "eve save_zone home");                                                              // Hunter accepts the new state
		String v2 = Snapshots.echo(echoReq("home", OWNER)).get("snapshot_id").getAsString();
		kcase("N10 a new baseline: the diff against it is empty", "", all(diff(v2)));
		kcase("N11 the old baseline still shows the same damage", list(d, "missing"), list(diff(v1), "missing"));
		level.setBlock(abs(6, 3, 1), Blocks.GLASS.defaultBlockState(), 2);                            // later, no player: damage
		kcase("N12 an owner edit before the new baseline does not excuse a later change", "6,3,1 minecraft:air -> minecraft:glass",
				list(diff(v2), "changed"));
		JsonObject pol = s1.getAsJsonObject("policy");
		kcase("N13 the baseline records owner, version, permitted variations and the update procedure", "OwnerTest true true true",
				s1.get("requested_by").getAsString() + " " + pol.has("operational_properties") + " " + pol.has("crop_cycle") + " " + pol.has("update"));

		variant = "R CP-52 repair steps";
		set(6, 1, 7, Blocks.CHEST.defaultBlockState());                                                // a chest appears: never a step
		JsonObject rp = repair(v1, null);
		kcase("R01 exactly the damage to restore, removals first then placements bottom up",
				"7,1,1 remove minecraft:cobblestone; 7,1,7 remove minecraft:dirt; 5,1,1 place minecraft:stone_bricks; 7,1,1 place minecraft:stone_bricks; 6,2,1 place minecraft:stone_bricks",
				steps(rp));
		kcase("R02 a container is never a step: listed for manual (backup) recovery", "6,1,7 minecraft:chest", manual(rp));
		kcase("R03 permitted variation is skipped, listed and counted", "{\"manual_container\":1,\"place\":3,\"remove\":2,"
				+ "\"skipped_crop_cycle\":2,\"skipped_operational_state\":3,\"skipped_owner_edit\":2} 5 false 7",
				rp.getAsJsonObject("counts") + " " + rp.get("total").getAsInt() + " " + rp.get("truncated").getAsBoolean() + " " + rp.getAsJsonArray("skipped").size());
		String crops = rp.getAsJsonArray("skipped").asList().stream().map(JsonElement::getAsJsonObject)
				.filter(k -> k.get("reason").getAsString().equals("crop_cycle")).map(k -> rel(k.getAsJsonObject("pos")) + " " + k.get("block").getAsString())
				.sorted().collect(java.util.stream.Collectors.joining("; "));
		kcase("R03b a skipped crop carries the snapshot's block, so the crop can be replanted", "6,1,6 minecraft:wheat[age=2]; 7,1,6 minecraft:carrots[age=4]", crops);
		JsonObject qb = req(v1);                                                                       // a box: only that part
		JsonObject bx = new JsonObject();
		BlockPos b0 = abs(7, 0, 0), b1 = abs(7, 3, 7);
		bx.add("min", xyzOf(b0));
		bx.add("max", xyzOf(b1));
		qb.add("box", bx);
		JsonObject rb = Snapshots.repair(level.getServer(), qb);
		kcase("R03c a box limits the repair to that part of the zone (x = 7 only)",
				"7,1,1 remove minecraft:cobblestone; 7,1,7 remove minecraft:dirt; 7,1,1 place minecraft:stone_bricks", steps(rb));
		kcase("R03e the reply echoes the box it covers (and a whole-zone reply has none)", "7,0,0 7,3,7 false",
				rel(rb.getAsJsonObject("box").getAsJsonObject("min")) + " " + rel(rb.getAsJsonObject("box").getAsJsonObject("max")) + " " + rp.has("box"));
		JsonObject bad = req(v1);
		JsonObject far = new JsonObject();
		far.add("min", xyzOf(abs(9000, 0, 9000)));
		far.add("max", xyzOf(abs(9001, 1, 9001)));
		bad.add("box", far);
		kcase("R03d a box outside the snapshot is refused", "bad_payload", outcome(() -> Snapshots.repair(level.getServer(), bad)));
		JsonObject r2 = repair(v1, 2);
		kcase("R04 max_steps bounds a reply: the first 2 steps, truncated, total 5", "7,1,1 remove minecraft:cobblestone; 7,1,7 remove minecraft:dirt true 5",
				steps(r2) + " " + r2.get("truncated").getAsBoolean() + " " + r2.get("total").getAsInt());
		kcase("R05 max_steps 0 is refused", "bad_payload", outcome(() -> repair(v1, 0)));
		kcase("R06 max_steps over 1000 is refused", "bad_payload", outcome(() -> repair(v1, 1001)));
		kcase("R07 repair of an unknown snapshot is refused", "bad_payload", outcome(() -> repair("home@v999999", null)));
		for (JsonElement e : rp.getAsJsonArray("steps")) {                                                     // do the repair
			JsonObject o = e.getAsJsonObject(), q = o.getAsJsonObject("pos");
			BlockPos at = new BlockPos(q.get("x").getAsInt(), q.get("y").getAsInt(), q.get("z").getAsInt());
			level.setBlock(at, o.get("op").getAsString().equals("remove") ? Blocks.AIR.defaultBlockState()
					: net.minecraft.world.level.block.Blocks.STONE_BRICKS.defaultBlockState(), 2);
		}
		JsonObject after = repair(v1, null);
		kcase("R08 after the steps are done nothing is left to restore (the chest stays manual)", " 0 6,1,7 minecraft:chest",
				steps(after) + " " + after.get("total").getAsInt() + " " + manual(after));
		set(6, 1, 7, Blocks.AIR.defaultBlockState());

		variant = "B boundary/malformed";
		kcase("B01 the mind's zone.snapshot.save echoes the owner's save", v2, Snapshots.echo(echoReq("home", OWNER)).get("snapshot_id").getAsString());
		kcase("B02 ... but not for a player who did not save", "bad_payload", outcome(() -> Snapshots.echo(echoReq("home", STRANGER))));
		kcase("B03 ... nor for a zone nobody saved", "bad_payload", outcome(() -> Snapshots.echo(echoReq("mine", OWNER))));
		int v = Snapshots.latestVersion("home");
		cmd(stranger, "eve save_zone home");
		kcase("B04 a stranger's /eve save_zone writes nothing", String.valueOf(v), String.valueOf(Snapshots.latestVersion("home")));
		cmd(owner, "eve save_zone mine");
		kcase("B05 the owner cannot snapshot a zone without snapshot:true", "0", String.valueOf(Snapshots.latestVersion("mine")));
		kcase("B06 diff of an unknown snapshot is refused", "bad_payload", outcome(() -> diff("home@v999999")));
		kcase("B07 diff of another zone's snapshot is refused", "bad_payload", outcome(() -> { JsonObject q = req(v1); q.addProperty("zone_id", "mine"); return Snapshots.diff(level.getServer(), q); }));
		kcase("B08 diff without snapshot_id is refused", "bad_payload", outcome(() -> { JsonObject q = req(v1); q.remove("snapshot_id"); return Snapshots.diff(level.getServer(), q); }));
		kcase("B09 malformed snapshot_id is refused", "bad_payload", outcome(() -> diff("home-v1")));
		kcase("B10 a stranger's block is never an owner edit", "player:Stranger1", actor(d, "changed", new BlockPos(7, 1, 7)));
		kcase("B11 an operational change keeps the block id (door halves both reported)", "minecraft:oak_door minecraft:oak_door",
				ids(d, new BlockPos(5, 1, 3)) + " " + ids(d, new BlockPos(5, 2, 3)));
		kcase("B12 unloaded positions are counted, never listed as missing", "0", String.valueOf(d.getAsJsonObject("counts").has("ignored_unloaded") ? 1 : 0));

		variant = "O20 rescue water in a snapshot zone";
		set(5, 1, 7, Blocks.SHORT_GRASS.defaultBlockState());
		cmd(owner, "eve save_zone home");
		String v3 = Snapshots.echo(echoReq("home", OWNER)).get("snapshot_id").getAsString();
		rescueAt(5, 7, true);                                                                          // water replaces the grass, scooped back
		rescueAt(7, 5, false);                                                                         // water left standing
		JsonObject dr = diff(v3);
		kcase("O1 a rescue's water placed and scooped back is ignored (reason rescue), not reported as damage", "5,1,7 rescue",
				reasons(dr, "rescue"));
		kcase("O2 rescue water left standing is still damage (changed)", "7,1,5 minecraft:air -> minecraft:water[level=0]", list(dr, "changed"));
		JsonObject rr = repair(v3, null);
		kcase("O3 repair restores only the standing water, never the rescued grass", "7,1,5 remove minecraft:water[level=0] " + 1,
				steps(rr) + " " + rr.getAsJsonObject("counts").get("skipped_rescue").getAsInt());
		set(7, 1, 5, Blocks.AIR.defaultBlockState());
		set(5, 1, 7, Blocks.AIR.defaultBlockState());
	}

	private void build() {
		for (int x = 5; x <= 7; x++) for (int y = 0; y <= 7; y++) for (int z = 0; z <= 7; z++) set(x, y, z, Blocks.AIR.defaultBlockState());
		for (int x = 5; x <= 7; x++) for (int z = 0; z <= 7; z++) set(x, 0, z, Blocks.STONE.defaultBlockState());
		for (int x = 5; x <= 7; x++) for (int y = 1; y <= 3; y++) set(x, y, 1, Blocks.STONE_BRICKS.defaultBlockState());
		door(false);
		set(7, 1, 4, lever(false));
		set(6, 0, 6, Blocks.FARMLAND.defaultBlockState());
		set(7, 0, 6, Blocks.FARMLAND.defaultBlockState());
		set(6, 1, 6, ((CropBlock) Blocks.WHEAT).getStateForAge(2));
		set(7, 1, 6, ((CropBlock) Blocks.CARROTS).getStateForAge(4));
	}

	private void door(boolean open) {
		BlockState d = Blocks.OAK_DOOR.defaultBlockState().setValue(DoorBlock.FACING, Direction.EAST).setValue(DoorBlock.OPEN, open);
		set(5, 1, 3, d.setValue(DoorBlock.HALF, DoubleBlockHalf.LOWER));
		set(5, 2, 3, d.setValue(DoorBlock.HALF, DoubleBlockHalf.UPPER));
	}

	private static BlockState lever(boolean on) {
		return Blocks.LEVER.defaultBlockState().setValue(LeverBlock.FACE, AttachFace.FLOOR).setValue(LeverBlock.POWERED, on);
	}

	private void set(int x, int y, int z, BlockState s) {
		level.setBlock(abs(x, y, z), s, 2);
	}

	private void place(ServerPlayer who, BlockPos rel, Direction face, net.minecraft.world.item.Item item) {
		ItemStack s = new ItemStack(item, 4);
		who.setItemInHand(InteractionHand.MAIN_HAND, s);
		who.setShiftKeyDown(true);
		who.gameMode.useItemOn(who, level, s, InteractionHand.MAIN_HAND, hit(abs(rel), face));
		who.setShiftKeyDown(false);
		who.setItemInHand(InteractionHand.MAIN_HAND, ItemStack.EMPTY);
	}

	private JsonObject echoReq(String zone, String by) {
		JsonObject p = new JsonObject();
		p.addProperty("zone_id", zone);
		p.addProperty("requested_by", by);
		return p;
	}

	private JsonObject req(String sid) {
		JsonObject p = new JsonObject();
		p.addProperty("zone_id", sid.contains("@") ? sid.substring(0, sid.indexOf('@')) : "home");
		p.addProperty("snapshot_id", sid);
		return p;
	}

	private JsonObject diff(String sid) {
		return Snapshots.diff(level.getServer(), req(sid));
	}

	/** Rule O20: Eve falls over (x, 1, z) and empties a water bucket under her; scoop = she takes it back. */
	private void rescueAt(int x, int z, boolean scoop) {
		BlockPos cell = abs(x, 1, z);
		eve.snapTo(cell.getX() + 0.5, cell.getY() + 1.5, cell.getZ() + 0.5, 0, 90);
		eve.setOnGround(false);
		eve.fallDistance = 8;
		for (net.minecraft.world.item.Item it : scoop ? List.of(Items.WATER_BUCKET, Items.BUCKET) : List.of(Items.WATER_BUCKET)) {
			ItemStack b = new ItemStack(it);
			eve.setItemInHand(InteractionHand.MAIN_HAND, b);
			eve.gameMode.useItem(eve, level, b, InteractionHand.MAIN_HAND);
		}
		eve.setItemInHand(InteractionHand.MAIN_HAND, ItemStack.EMPTY);
		eve.fallDistance = 0;
		eve.setOnGround(true);
	}

	private JsonObject xyzOf(BlockPos b) {
		return xyz(b.getX(), b.getY(), b.getZ());
	}

	private JsonObject repair(String sid, Integer max) {
		JsonObject q = req(sid);
		if (max != null) q.addProperty("max_steps", max);
		return Snapshots.repair(level.getServer(), q);
	}

	/** "x,y,z op block" in reply order. */
	private String steps(JsonObject r) {
		List<String> s = new java.util.ArrayList<>();
		for (JsonElement e : r.getAsJsonArray("steps")) {
			JsonObject o = e.getAsJsonObject();
			s.add(rel(o.getAsJsonObject("pos")) + " " + o.get("op").getAsString() + " " + o.get("block").getAsString());
		}
		return String.join("; ", s);
	}

	private String manual(JsonObject r) {
		TreeSet<String> s = new TreeSet<>();
		for (JsonElement e : r.getAsJsonArray("manual")) {
			JsonObject o = e.getAsJsonObject();
			s.add(rel(o.getAsJsonObject("pos")) + " " + o.get("kind").getAsString());
		}
		return String.join("; ", s);
	}

	private String rel(JsonObject pos) {
		BlockPos o = abs(0, 0, 0);
		return (pos.get("x").getAsInt() - o.getX()) + "," + (pos.get("y").getAsInt() - o.getY()) + "," + (pos.get("z").getAsInt() - o.getZ());
	}

	/** "x,y,z expected[ -> actual][ actor]" in test-relative coordinates, sorted. */
	private String list(JsonObject d, String key) {
		TreeSet<String> s = new TreeSet<>();
		for (JsonElement e : d.getAsJsonArray(key)) {
			JsonObject o = e.getAsJsonObject();
			s.add(rel(o.getAsJsonObject("pos")) + " " + o.get("expected").getAsString() + (o.has("actual") ? " -> " + o.get("actual").getAsString() : "")
					+ (o.has("actor") ? " " + o.get("actor").getAsString() : ""));
		}
		return String.join("; ", s);
	}

	private String reasons(JsonObject d, String reason) {
		TreeSet<String> s = new TreeSet<>();
		for (JsonElement e : d.getAsJsonArray("ignored"))
			if (e.getAsJsonObject().get("reason").getAsString().equals(reason)) s.add(rel(e.getAsJsonObject().getAsJsonObject("pos")) + " " + reason);
		return String.join("; ", s);
	}

	private String all(JsonObject d) {
		return (list(d, "missing") + list(d, "changed") + d.getAsJsonArray("ignored")).replace("[]", "");
	}

	private String actor(JsonObject d, String key, BlockPos relPos) {
		for (JsonElement e : d.getAsJsonArray(key)) {
			JsonObject o = e.getAsJsonObject();
			if (rel(o.getAsJsonObject("pos")).equals(relPos.getX() + "," + relPos.getY() + "," + relPos.getZ())) return o.has("actor") ? o.get("actor").getAsString() : "none";
		}
		return "absent";
	}

	private String ids(JsonObject d, BlockPos relPos) {
		for (JsonElement e : d.getAsJsonArray("ignored")) {
			JsonObject o = e.getAsJsonObject();
			if (rel(o.getAsJsonObject("pos")).equals(relPos.getX() + "," + relPos.getY() + "," + relPos.getZ()))
				return o.get("expected").getAsString().split("\\[")[0];
		}
		return "absent";
	}

	private static String outcome(Supplier<JsonObject> q) {
		try {
			q.get();
			return "ok";
		} catch (IllegalArgumentException | IllegalStateException | NullPointerException | ClassCastException e) {
			return "bad_payload";
		}
	}

	private void kcase(String name, String expected, String observed) {
		attempts.add(new Attempt(attempts.size() + 1, variant, name, null, expected, observed, false, expected.equals(observed), List.of()));
	}
}
