package eve.sense.test;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.mojang.authlib.GameProfile;
import eve.sense.Envelope;
import eve.sense.EveSense;
import eve.sense.Link;
import eve.sense.PlacedBlocks;
import eve.sense.Query;
import eve.sense.Rails;
import eve.sense.RconGuard;
import eve.sense.Snapshots;
import eve.sense.Storage;
import net.fabricmc.fabric.api.entity.FakePlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.gametest.framework.GameTestHelper;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.Container;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.MenuProvider;
import net.minecraft.world.inventory.ChestMenu;
import net.minecraft.world.inventory.ContainerInput;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.level.GameType;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.ChestBlock;
import net.minecraft.world.level.block.LeavesBlock;
import net.minecraft.world.level.block.LeverBlock;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.AttachFace;
import net.minecraft.world.level.block.state.properties.ChestType;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.Vec3;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.OptionalInt;
import java.util.UUID;
import java.util.function.BooleanSupplier;
import java.util.function.Consumer;

/**
 * CP-15 event-level suite (S class): Eve is a scripted FakePlayer acting through the same server entry points a
 * client packet reaches (ServerPlayerGameMode.destroyBlock/useItemOn/useItem, AbstractContainerMenu.clicked,
 * the command dispatcher). Every attempt is recorded; a violation is a prohibited effect that actually happened.
 * Layout (relative to the test origin): mine zone x 0..3 (free), gap x 4 (no zone), home zone x 5..7 (task_only).
 */
public class Cp15Suite {
	static final String EVE = "EveTest", OWNER = "OwnerTest", STRANGER = "Stranger1";

	record Attempt(int n, String variant, String action, String pos, String expected, String observed, boolean violation, boolean ok, List<JsonObject> emitted) {}

	static final class TestEve extends FakePlayer {
		int menusOpened;

		TestEve(ServerLevel level, GameProfile profile) { super(level, profile); }

		/** O174: a FakePlayer is invulnerable; a test that must see vanilla's damage path on Eve sets this for its duration. */
		boolean vulnerable;

		@Override
		public boolean isInvulnerableTo(ServerLevel level, net.minecraft.world.damagesource.DamageSource source) {
			return !vulnerable && super.isInvulnerableTo(level, source);
		}

		/** Full attack strength, standing: the state a client is in when it lands a sweep (a FakePlayer never ticks). */
		void charge() {
			attackStrengthTicker = 1000;
			setOnGround(true);
		}

		@Override
		public OptionalInt openMenu(MenuProvider provider) {
			if (provider != null) menusOpened++; // FakePlayer never opens a real menu; counting the call is the observable effect
			return super.openMenu(provider);
		}
	}

	final List<Attempt> attempts = new ArrayList<>();
	final List<JsonObject> validation = new ArrayList<>();
	final List<JsonObject> emitted = new ArrayList<>();
	GameTestHelper h;
	ServerLevel level;
	TestEve eve;
	FakePlayer owner, stranger;
	JsonObject base;
	String variant;
	String cp = "CP-15";

	/** Entry point: RailsTests (the @GameTest class; Fabric's locator walks superclasses, so tests live there). */
	public void cp15Rails(GameTestHelper helper) {
		suite(helper, "CP-15", () -> { runVariants(); storageShape(); });
	}

	/** Shared lifecycle: tap the link, run the body with a scripted Eve, write evidence, restore the envelope. */
	void suite(GameTestHelper helper, String cp, Runnable body) {
		this.cp = cp;
		long start = System.currentTimeMillis();
		h = helper;
		level = helper.getLevel();
		Envelope original = EveSense.env();
		Consumer<JsonObject> oldTap = Link.tap;
		Link.tap = m -> { synchronized (emitted) { emitted.add(m); } };
		try {
			base = JsonParser.parseString(Files.readString(Path.of(System.getProperty("eve.envelope")))).getAsJsonObject();
			if (cp.equals("CP-15")) { envelopeValidation(); rconGuard(); }
			eve = new TestEve(level, new GameProfile(UUID.nameUUIDFromBytes(EVE.getBytes()), EVE));
			eve.setGameMode(GameType.SURVIVAL);
			eve.snapTo(Vec3.atCenterOf(abs(4, 1, 3)));
			owner = FakePlayer.get(level, new GameProfile(UUID.nameUUIDFromBytes(OWNER.getBytes()), OWNER));
			stranger = FakePlayer.get(level, new GameProfile(UUID.nameUUIDFromBytes(STRANGER.getBytes()), STRANGER));
			EveSense.setStopped(false);
			Rails.resetRateWindowsForTest();
			apply(o -> { });
			body.run();
			Path dir = writeEvidence(start);
			long bad = attempts.stream().filter(a -> !a.ok()).count(), viol = attempts.stream().filter(Attempt::violation).count();
			long badValidation = validation.stream().filter(v -> !v.get("ok").getAsBoolean()).count();
			EveSense.LOG.info("{} suite: {} attempts, {} not as expected, {} violations, {} validation mismatches; evidence {}", cp, attempts.size(), bad, viol, badValidation, dir);
			if (bad > 0 || viol > 0 || badValidation > 0) helper.fail(cp + ": " + bad + " unexpected, " + viol + " violations, " + badValidation + " validation mismatches; see " + dir);
			else helper.succeed();
		} catch (Exception e) {
			throw new RuntimeException(e);
		} finally {
			Link.tap = oldTap;
			EveSense.apply(original);
			EveSense.setStopped(false);
		}
	}

	void runVariants() {
		variant = "V01 break inside mine (free)";
		for (int i = 0; i < 8; i++) expectBreak(eve, 1 + i % 2, 1 + i / 4, i % 4 + 2, true);
		variant = "V02 break inside home (task_only)";
		for (int i = 0; i < 8; i++) expectBreak(eve, 5 + i % 3, 1 + i / 4, i % 4 + 2, false);
		variant = "V03 break in the gap outside every zone";
		for (int i = 0; i < 8; i++) expectBreak(eve, 4, 1 + i / 4, i % 4 + 2, false);
		variant = "V04 break on the mine border (inclusive)";
		int[][] in = {{3, 1, 0}, {3, 1, 7}, {0, 1, 0}, {0, 1, 7}, {3, 7, 3}, {3, 0, 4}};
		for (int[] p : in) expectBreak(eve, p[0], p[1], p[2], true);
		variant = "V05 break one block past the mine border";
		int[][] out = {{4, 1, 0}, {4, 1, 7}, {0, 1, -1}, {0, 1, 8}, {3, 1, -1}, {-1, 1, 3}};
		for (int[] p : out) expectBreak(eve, p[0], p[1], p[2], false);
		variant = "V06 other player breaks in home (rails are Eve-only)";
		for (int i = 0; i < 4; i++) expectBreak(stranger, 6, 1, 2 + i, true);

		variant = "V07 place inside mine (free)";
		for (int i = 0; i < 6; i++) expectPlace(1, 1, 1 + i, Direction.UP, Items.DIRT, true);
		variant = "V08 place inside home (task_only) and in the gap";
		for (int i = 0; i < 6; i++) expectPlace(i < 3 ? 6 : 4, 1, 2 + i % 3, Direction.UP, Items.DIRT, false);
		variant = "V09 place across the border: click inside mine, target outside";
		for (int i = 0; i < 6; i++) expectPlace(3, 1 + i / 3, 2 + i % 3, Direction.EAST, Items.DIRT, false);

		variant = "V10 open containers by tag/zone";
		BlockPos shared = new BlockPos(6, 1, 6);
		apply(o -> tag(o, shared, "shared"));
		for (int i = 0; i < 3; i++) expectOpen(shared, true);              // explicit shared tag inside a private zone
		for (int i = 0; i < 3; i++) expectOpen(new BlockPos(6, 1, 1), false); // home zone says private
		for (int i = 0; i < 3; i++) expectOpen(new BlockPos(2, 1, 6), false); // mine zone says private
		for (int i = 0; i < 3; i++) expectOpen(new BlockPos(4, 1, 6), false); // outside zones: default_tag private

		variant = "V11 double chest with one private half";
		BlockPos left = new BlockPos(5, 3, 4), right = new BlockPos(6, 3, 4);
		apply(o -> tag(o, left, "shared"));
		doubleChest(left, right);
		record("open left (shared half)", left, false, () -> openRaw(left));
		record("open right (private half)", right, false, () -> openRaw(right));

		variant = "V12 container transfer, permission changed mid-task";
		apply(o -> tag(o, shared, "shared"));
		record("QUICK_MOVE while shared", shared, true, () -> transfer(shared, ContainerInput.QUICK_MOVE, 0, 0));
		apply(o -> tag(o, shared, "private")); // owner re-tags while Eve's menu is open
		for (ContainerInput in2 : List.of(ContainerInput.PICKUP, ContainerInput.QUICK_MOVE, ContainerInput.SWAP, ContainerInput.THROW))
			record(in2 + " after re-tag to private", shared, false, () -> transfer(shared, in2, 1, 0));
		record("QUICK_MOVE slot 26 after re-tag", shared, false, () -> transfer(shared, ContainerInput.QUICK_MOVE, 26, 0));

		variant = "V13 /eve from untrusted senders";
		apply(o -> { });
		for (String c : List.of("eve stop", "eve resume", "eve mode manual"))
			record("stranger: /" + c, null, false, () -> { boolean before = EveSense.stopped(); cmd(stranger, c); return EveSense.stopped() != before; });
		record("Eve herself: /eve stop", null, false, () -> { cmd(eve, "eve stop"); return EveSense.stopped(); });

		variant = "V14 STOPPED gate";
		record("trusted /eve stop", null, true, () -> { cmd(owner, "eve stop"); return EveSense.stopped(); });
		for (int i = 0; i < 4; i++) expectBreak(eve, 2, 2, 2 + i, false);
		for (int i = 0; i < 4; i++) expectPlace(1, 2, 2 + i, Direction.UP, Items.DIRT, false);
		apply(o -> tag(o, shared, "shared"));
		for (int i = 0; i < 2; i++) expectOpen(shared, false);
		for (int i = 0; i < 2; i++) record("transfer while stopped", shared, false, () -> transfer(shared, ContainerInput.QUICK_MOVE, 2, 0));
		for (int i = 0; i < 2; i++) record("use snowball while stopped", null, false, this::useSnowball);
		// O218: a defensive reflex outranks STOPPED: shield and food still work, nothing else
		record("O218 raise a shield while stopped", null, true, () -> useAndHold(InteractionHand.OFF_HAND, Items.SHIELD));
		record("O218 eat while stopped", null, true, () -> { eve.getFoodData().setFoodLevel(5); return useAndHold(InteractionHand.MAIN_HAND, Items.BREAD); });
		for (int i = 0; i < 2; i++) record("stranger /eve resume while stopped", null, false, () -> { cmd(stranger, "eve resume"); return !EveSense.stopped(); });
		record("Eve /eve resume while stopped", null, false, () -> { cmd(eve, "eve resume"); return !EveSense.stopped(); });
		record("trusted /eve resume", null, true, () -> { cmd(owner, "eve resume"); return !EveSense.stopped(); });
		for (int i = 0; i < 2; i++) expectBreak(eve, 2, 3, 2 + i, true);
		record("use snowball after resume", null, true, this::useSnowball);

		variant = "V15 rolling-hour break limit (limit 5)";
		apply(o -> o.getAsJsonObject("limits").addProperty("blocks_broken_per_hour", 5));
		Rails.resetRateWindowsForTest();
		for (int i = 0; i < 8; i++) expectBreak(eve, 1 + i / 4, 4, i % 4 + 2, i < 5);
		Rails.resetRateWindowsForTest();

		variant = "V16 zone in another dimension does not apply";
		apply(o -> {
			JsonArray z = o.getAsJsonArray("zones");
			z.get(0).getAsJsonObject().addProperty("dim", "minecraft:the_nether");
		});
		for (int i = 0; i < 4; i++) expectBreak(eve, 1, 5, 2 + i, false);

		variant = "V17 envelope for another world";
		apply(o -> o.addProperty("world_id", "other:0123456789ab"));
		for (int i = 0; i < 4; i++) expectBreak(eve, 2, 5, 2 + i, false);

		variant = "V18 overlapping zones: most restrictive wins";
		apply(o -> o.getAsJsonArray("zones").add(zone("nobreak", new BlockPos(1, 6, 0), new BlockPos(2, 6, 7), "none", "none", "private")));
		for (int i = 0; i < 3; i++) expectBreak(eve, 1 + i % 2, 6, 3 + i, false);

		variant = "V19 item use on a block (hoe tilling)";
		apply(o -> { });
		for (int i = 0; i < 3; i++) { int z = 2 + i; record("till in home", new BlockPos(7, 1, z), false, () -> till(new BlockPos(7, 1, z))); }
		for (int i = 0; i < 2; i++) { int z = 2 + i; record("till in mine", new BlockPos(2, 1, z), true, () -> till(new BlockPos(2, 1, z))); }

		variant = "V20 hazards (fire, lava, tnt all false)";
		for (int i = 0; i < 2; i++) { int z = 2 + i; record("flint and steel in mine", new BlockPos(1, 1, z), false, () -> flint(new BlockPos(1, 1, z))); }
		record("lava bucket in mine", null, false, this::lava);
		for (int i = 0; i < 2; i++) expectPlace(2, 1, 4 + i, Direction.UP, Items.TNT, false);

		variant = "V21 non-container interaction (lever)";
		for (int i = 0; i < 2; i++) { int z = 2 + i; record("lever in gap", new BlockPos(4, 1, z), false, () -> lever(new BlockPos(4, 1, z))); }
		for (int i = 0; i < 2; i++) { int z = 2 + i; record("lever in home", new BlockPos(5, 1, z), true, () -> lever(new BlockPos(5, 1, z))); }

		// Positive controls: prove the fire/lava/tnt detectors see the effect when the owner's envelope allows it.
		variant = "V22 positive control: hazards allowed by the owner's envelope";
		apply(o -> { for (String k : List.of("fire", "lava", "tnt")) o.getAsJsonObject("hazards").addProperty(k, true); });
		record("flint and steel in mine, fire allowed", new BlockPos(1, 1, 4), true, () -> flint(new BlockPos(1, 1, 4)));
		record("lava bucket in mine, lava allowed", null, true, this::lava);
		expectPlace(2, 1, 6, Direction.UP, Items.TNT, true);
		level.setBlockAndUpdate(abs(2, 2, 6), Blocks.AIR.defaultBlockState());

		variant = "V23 task.grant: bounded exceptions to task_only";
		apply(o -> { });
		long now = level.getServer().getTickCount();
		String ow = "minecraft:overworld";
		List<BlockPos> granted = List.of(new BlockPos(6, 2, 2), new BlockPos(6, 2, 3), new BlockPos(6, 2, 4));
		Rails.grant(grant("t1", "break", ow, granted, now + 200));
		for (BlockPos p : granted) expectBreak(eve, p.getX(), p.getY(), p.getZ(), true);
		for (BlockPos p : List.of(new BlockPos(6, 2, 5), new BlockPos(7, 2, 2), new BlockPos(5, 2, 3))) expectBreak(eve, p.getX(), p.getY(), p.getZ(), false);
		Rails.grant(grant("t2", "break", ow, List.of(new BlockPos(6, 3, 2)), now + 200));
		expectPlace(6, 2, 2, Direction.UP, Items.DIRT, false); // a break grant does not cover placing there
		Rails.grant(grant("t6", "place", ow, List.of(new BlockPos(5, 5, 3)), now + 200));
		expectPlace(5, 4, 3, Direction.UP, Items.DIRT, true);
		expectPlace(5, 4, 4, Direction.UP, Items.DIRT, false); // neighbour of a place grant
		refusedGrantThenBreak("outside every zone", grant("r1", "break", ow, List.of(new BlockPos(4, 2, 3)), now + 200), new BlockPos(4, 2, 3));
		refusedGrantThenBreak("already expired", grant("r2", "break", ow, List.of(new BlockPos(7, 2, 4)), now), new BlockPos(7, 2, 4));
		refusedGrantThenBreak("longer than an hour", grant("r3", "break", ow, List.of(new BlockPos(7, 2, 5)), now + 72_001), new BlockPos(7, 2, 5));
		refusedGrantThenBreak("other dimension", grant("r4", "break", "minecraft:the_nether", List.of(new BlockPos(7, 3, 2)), now + 200), new BlockPos(7, 3, 2));
		JsonObject frac = grant("r5", "break", ow, List.of(new BlockPos(7, 3, 4)), now + 200);
		frac.getAsJsonArray("positions").get(0).getAsJsonObject().addProperty("x", abs(7, 3, 4).getX() + 0.5);
		refusedGrantThenBreak("fractional coordinate", frac, new BlockPos(7, 3, 4));
		cmd(owner, "eve stop");
		refusedGrantThenBreak("while STOPPED", grant("r6", "break", ow, List.of(new BlockPos(7, 3, 3)), now + 200), new BlockPos(7, 3, 3));
		cmd(owner, "eve resume");
		record("break after the STOPPED-time grant was refused", new BlockPos(7, 3, 3), false, () -> breakRaw(new BlockPos(7, 3, 3)));
		Rails.grant(grant("t3", "break", ow, List.of(new BlockPos(5, 3, 2)), now + 200));
		expectBreak(eve, 5, 3, 2, true);
		Rails.grant(grant("t3", "break", ow, List.of(), now + 200)); // positions:[] revokes t3
		expectBreak(eve, 5, 3, 2, false);
		Rails.grant(grant("t4", "break", ow, List.of(new BlockPos(5, 3, 3)), now + 200));
		expectBreak(eve, 5, 3, 3, true);
		cmd(owner, "eve stop"); // the active task is cancelled, its grants end
		cmd(owner, "eve resume");
		expectBreak(eve, 5, 3, 3, false);
		Rails.grant(grant("t5", "break", ow, List.of(new BlockPos(5, 3, 4)), now + 200));
		expectBreak(eve, 5, 3, 4, true);
		apply(o -> { }); // any envelope change clears grants
		expectBreak(eve, 5, 3, 4, false);
		apply(o -> o.getAsJsonArray("zones").add(zone("nobreak", new BlockPos(1, 6, 0), new BlockPos(2, 6, 7), "none", "none", "private")));
		refusedGrantThenBreak("none zone", grant("r7", "break", ow, List.of(new BlockPos(1, 6, 2)), now + 200), new BlockPos(1, 6, 2));

		variant = "V27 task.grant batches and grant_region (construction footprints)";
		apply(o -> { });
		long t27 = level.getServer().getTickCount();
		Rails.grant(grant("b1", "break", ow, List.of(new BlockPos(7, 2, 3)), t27 + 200));               // one task, two frames
		Rails.grant(grant("b1", "break", ow, List.of(new BlockPos(7, 2, 4)), t27 + 200));
		expectBreak(eve, 7, 2, 3, true);
		expectBreak(eve, 7, 2, 4, true);
		Rails.grant(region("g1", "break", ow, new BlockPos(6, 2, 5), new BlockPos(7, 2, 5), t27 + 200));
		expectBreak(eve, 6, 2, 5, true);
		expectBreak(eve, 7, 2, 5, true);
		expectBreak(eve, 7, 2, 2, false);                                                                // just outside the box
		expectPlace(6, 2, 5, Direction.UP, Items.DIRT, false);                                          // a break region is not a place grant
		Rails.grant(region("g2", "break", ow, new BlockPos(7, 3, 4), new BlockPos(7, 3, 5), t27 + 200));
		expectBreak(eve, 7, 3, 4, true);
		Rails.grant(grant("g2", "break", ow, List.of(), t27 + 200));                                     // positions:[] revokes the region too
		expectBreak(eve, 7, 3, 5, false);
		refusedGrantThenBreak("region over 32768 blocks", region("g3", "break", ow, new BlockPos(5, 3, 2), new BlockPos(5 + 40, 3 + 40, 2 + 40), t27 + 200), new BlockPos(5, 3, 2));
		refusedGrantThenBreak("region touching outside every zone", region("g4", "break", ow, new BlockPos(4, 2, 3), new BlockPos(5, 2, 3), t27 + 200), new BlockPos(5, 2, 3));
		refusedGrantThenBreak("region max below min", region("g5", "break", ow, new BlockPos(6, 3, 3), new BlockPos(6, 3, 2), t27 + 200), new BlockPos(6, 3, 3));
		JsonObject msg = region("g7", "break", ow, new BlockPos(5, 3, 2), new BlockPos(5, 3, 2), t27 + 200);   // v1.3 message shape
		JsonObject rg = msg.remove("region").getAsJsonObject();
		msg.add("min", rg.get("min"));
		msg.add("max", rg.get("max"));
		Rails.grantRegion(msg);
		expectBreak(eve, 5, 3, 2, true);
		Rails.grant(region("g6", "break", ow, new BlockPos(6, 3, 4), new BlockPos(6, 3, 4), t27 + 200));
		cmd(owner, "eve stop");                                                                           // stop clears regions as well
		cmd(owner, "eve resume");
		expectBreak(eve, 6, 3, 4, false);

		variant = "V28 task.grant effect use (link v1.7): till, plant, bucket in task_only";
		apply(o -> { });
		long t28 = level.getServer().getTickCount();
		record("till in home without a use grant", new BlockPos(7, 1, 6), false, () -> till(new BlockPos(7, 1, 6)));
		Rails.grant(grant("u1", "use", ow, List.of(new BlockPos(7, 1, 6), new BlockPos(7, 2, 6)), t28 + 200));
		record("till in home with a use grant", new BlockPos(7, 1, 6), true, () -> till(new BlockPos(7, 1, 6)));
		record("plant wheat seeds on the granted farmland", new BlockPos(7, 2, 6), true, () -> plant(new BlockPos(7, 1, 6)));
		level.setBlockAndUpdate(abs(7, 1, 7), Blocks.FARMLAND.defaultBlockState());
		record("plant next to it, no grant there", new BlockPos(7, 2, 7), false, () -> plant(new BlockPos(7, 1, 7)));
		expectBreak(eve, 7, 1, 6, false);                                                    // a use grant is not a break grant
		refusedGrantThenBreak("use grant already expired", grant("u2", "use", ow, List.of(new BlockPos(7, 1, 5)), t28), new BlockPos(7, 1, 5));
		record("till after the expired use grant was refused", new BlockPos(7, 1, 5), false, () -> till(new BlockPos(7, 1, 5)));
		refusedGrantThenBreak("use grant outside every zone", grant("u3", "use", ow, List.of(new BlockPos(4, 1, 3)), t28 + 200), new BlockPos(4, 1, 3));
		Rails.grant(grant("u1", "use", ow, List.of(), t28 + 200));                           // revoke
		record("till after the use grant was revoked", new BlockPos(7, 1, 6), false, () -> till(new BlockPos(7, 1, 6)));

		variant = "V29 per-zone rate limit (envelope Amendment 5)";
		apply(o -> {
			o.getAsJsonObject("limits").addProperty("blocks_broken_per_hour", 2);                  // global cap 2
			for (var zz : o.getAsJsonArray("zones")) {
				JsonObject zo = zz.getAsJsonObject();
				if (zo.get("id").getAsString().equals("mine")) {
					JsonObject zl = new JsonObject();
					zl.addProperty("blocks_broken_per_hour", 3);                                       // the mine's own cap 3
					zo.add("limits", zl);
				}
			}
		});
		Rails.resetRateCounts();
		for (int i = 0; i < 3; i++) expectBreak(eve, 1, 2, 2 + i, true);                         // 3 in the mine: its own count
		expectBreak(eve, 1, 2, 5, false);                                                         // the 4th trips the mine's cap
		long t29 = level.getServer().getTickCount();
		Rails.grant(grant("z1", "break", ow, List.of(new BlockPos(6, 3, 2), new BlockPos(6, 3, 3), new BlockPos(6, 3, 4)), t29 + 200));
		expectBreak(eve, 6, 3, 2, true);                                                          // outside: the mine's 3 did not count
		expectBreak(eve, 6, 3, 3, true);
		expectBreak(eve, 6, 3, 4, false);                                                         // the global cap (2) trips
		apply(o -> { });
		Rails.resetRateCounts();

		variant = "V30 the End (O44): zones, grants, containers, void_edge, rescue in minecraft:the_end";
		endSuite();

		variant = "V26 permission.check(use_item) agrees with enforcement";
		apply(o -> { });
		useItemAgreement(new BlockPos(7, 1, 3), "up");     // home: task_only
		useItemAgreement(new BlockPos(2, 1, 3), "up");     // mine interior
		useItemAgreement(new BlockPos(3, 1, 4), "east");   // mine border, face into the gap
		useItemAgreement(new BlockPos(2, 1, 5), "north");  // mine interior, side face
		useItemAgreement(new BlockPos(3, 1, 6), "west");   // mine border, face back into the mine

		variant = "V31 H-trees: Eve gathers wood only from real trees (sense 0.1.23)";
		apply(o -> { });
		hTrees();

		variant = "V32 O193 portal light: flint and steel only on a complete, unlit portal frame in the play zone (sense 0.1.27)";
		apply(o -> { });
		portalLight();

		variant = "V24 /eve reload";
		apply(o -> { });
		record("stranger /eve reload", null, false, () -> { cmd(stranger, "eve reload"); return emittedType("envelope.changed"); });
		record("Eve /eve reload", null, false, () -> { cmd(eve, "eve reload"); return emittedType("envelope.changed"); });
		record("trusted /eve reload emits envelope.changed", null, true, () -> { cmd(owner, "eve reload"); return emittedType("envelope.changed"); });
		apply(o -> { });
	}

	/**
	 * Every storage.index entry must pass the mind's rule (link v1.1): removed:true needs only id; otherwise dim, pos, kind,
	 * tag, loaded and items. Full index, requested ids (incl. a valid unknown id and a malformed one) and a removal partial.
	 */
	boolean furnaceRefreshed, cutterPlaced, cutterRemoved;
	String tableFull = "absent";

	String key(BlockPos rel) {
		BlockPos a = abs(rel);
		return Envelope.key("minecraft:overworld", a.getX(), a.getY(), a.getZ());
	}

	void storageShape() {
		BlockPos chest = new BlockPos(2, 1, 4), furnace = new BlockPos(2, 1, 6), table = new BlockPos(1, 1, 6), cutterFloor = new BlockPos(1, 1, 5);
		apply(o -> {
			BlockPos c = abs(4, 1, 3);
			o.getAsJsonObject("home").add("center", xyz(c.getX(), c.getY(), c.getZ()));
		});
		level.setBlockAndUpdate(abs(chest), Blocks.CHEST.defaultBlockState());
		((Container) level.getBlockEntity(abs(chest))).setItem(0, new ItemStack(Items.DIAMOND, 3));
		BlockPos a = abs(chest);
		String id = Envelope.key("minecraft:overworld", a.getX(), a.getY(), a.getZ());
		List<JsonObject> frames = new ArrayList<>();
		Consumer<JsonObject> prev = Link.tap;
		Link.tap = m -> { if (m.get("type").getAsString().equals("storage.index")) frames.add(m); };
		try {
			Storage.sendFull(level.getServer(), null, null);
			JsonObject req = new JsonObject();
			JsonArray ids = new JsonArray();
			ids.add(id);
			ids.add("minecraft:overworld:999999,64,999999");
			ids.add("garbage-id");
			req.add("ids", ids);
			Storage.sendFull(level.getServer(), req, null);
			level.setBlockAndUpdate(a, Blocks.AIR.defaultBlockState()); // block-entity unload -> marked -> removal partial
			Storage.flush(level.getServer());
			// a burning furnace: its entry carries the furnace state, and a later flush re-sends it while it is lit
			level.setBlockAndUpdate(abs(furnace), Blocks.FURNACE.defaultBlockState());
			var fbe = (net.minecraft.world.level.block.entity.AbstractFurnaceBlockEntity) level.getBlockEntity(abs(furnace));
			fbe.setItem(0, new ItemStack(Items.RAW_IRON, 2));
			fbe.setItem(1, new ItemStack(Items.COAL, 1));
			var fd = ((eve.sense.mixin.FurnaceAccessor) fbe).eve$data();
			fd.set(net.minecraft.world.level.block.entity.AbstractFurnaceBlockEntity.DATA_LIT_TIME, 1500);
			fd.set(net.minecraft.world.level.block.entity.AbstractFurnaceBlockEntity.DATA_LIT_DURATION, 1600);
			fd.set(net.minecraft.world.level.block.entity.AbstractFurnaceBlockEntity.DATA_COOKING_PROGRESS, 40);
			fd.set(net.minecraft.world.level.block.entity.AbstractFurnaceBlockEntity.DATA_COOKING_TOTAL_TIME, 200);
			fbe.setChanged();
			Storage.flush(level.getServer());
			int before = frames.size();
			Storage.flush(level.getServer());                                                     // nothing changed: lit refresh only
			furnaceRefreshed = frames.size() > before;
			level.setBlockAndUpdate(abs(furnace), Blocks.AIR.defaultBlockState());
			Storage.flush(level.getServer());
			// link v1.5 workstations: a crafting table (no block entity) in the full index; the owner placing and breaking a
			// stonecutter reaches the index as a partial entry, then a removal
			level.setBlockAndUpdate(abs(table), Blocks.CRAFTING_TABLE.defaultBlockState());
			int f0 = frames.size();
			Storage.sendFull(level.getServer(), null, null);
			tableFull = frames.subList(f0, frames.size()).stream().flatMap(f -> f.getAsJsonObject("payload").getAsJsonArray("containers").asList().stream())
					.map(JsonElement::getAsJsonObject).filter(c -> c.get("id").getAsString().equals(key(table)))
					.map(c -> c.get("kind").getAsString() + " items=" + c.getAsJsonArray("items").size()).findFirst().orElse("absent");
			level.setBlockAndUpdate(abs(cutterFloor), Blocks.STONE.defaultBlockState());
			level.setBlockAndUpdate(abs(cutterFloor.above()), Blocks.AIR.defaultBlockState());
			ItemStack sc = new ItemStack(Items.STONECUTTER);
			owner.setItemInHand(InteractionHand.MAIN_HAND, sc);
			owner.gameMode.useItemOn(owner, level, sc, InteractionHand.MAIN_HAND, hit(abs(cutterFloor), Direction.UP));
			owner.setItemInHand(InteractionHand.MAIN_HAND, ItemStack.EMPTY);
			int f1 = frames.size();
			Storage.flush(level.getServer());
			cutterPlaced = frames.subList(f1, frames.size()).stream().flatMap(f -> f.getAsJsonObject("payload").getAsJsonArray("containers").asList().stream())
					.map(JsonElement::getAsJsonObject).anyMatch(c -> c.get("id").getAsString().equals(key(cutterFloor.above()))
							&& c.get("kind").getAsString().equals("minecraft:stonecutter") && !c.has("removed"));
			owner.gameMode.destroyBlock(abs(cutterFloor.above()));
			int f2 = frames.size();
			Storage.flush(level.getServer());
			cutterRemoved = frames.subList(f2, frames.size()).stream().flatMap(f -> f.getAsJsonObject("payload").getAsJsonArray("containers").asList().stream())
					.map(JsonElement::getAsJsonObject).anyMatch(c -> c.get("id").getAsString().equals(key(cutterFloor.above())) && c.has("removed"));
			level.setBlockAndUpdate(abs(table), Blocks.AIR.defaultBlockState());
		} finally {
			Link.tap = prev;
		}
		int entries = 0, bad = 0;
		boolean sawUnknown = false, sawRemoved = false, sawChest = false;
		String furnaceSeen = "none";
		for (JsonObject f : frames)
			for (var el : f.getAsJsonObject("payload").getAsJsonArray("containers")) {
				JsonObject c = el.getAsJsonObject();
				entries++;
				boolean removed = c.has("removed") && c.get("removed").getAsBoolean();
				boolean ok = c.has("id") && (removed || List.of("dim", "pos", "kind", "tag", "loaded", "items").stream().allMatch(c::has));
				if (!ok) bad++;
				if (c.get("id").getAsString().startsWith("minecraft:overworld:999999")) sawUnknown = "minecraft:unknown".equals(c.get("kind").getAsString());
				if (removed && c.get("id").getAsString().equals(id)) sawRemoved = true;
				if (!removed && c.get("id").getAsString().equals(id) && c.has("kind")) sawChest = "minecraft:chest".equals(c.get("kind").getAsString());
				if (c.get("id").getAsString().equals("garbage-id")) bad++;
				if (c.has("furnace") && "none".equals(furnaceSeen)) {
					JsonObject fu = c.getAsJsonObject("furnace");
					furnaceSeen = fu.getAsJsonObject("input").get("item").getAsString() + "x" + fu.getAsJsonObject("input").get("count").getAsInt() + " "
							+ fu.getAsJsonObject("fuel").get("item").getAsString() + " lit=" + fu.get("lit").getAsBoolean() + " " + fu.get("cook_progress").getAsInt()
							+ "/" + fu.get("cook_total").getAsInt() + " left=" + fu.get("lit_ticks_left").getAsInt() + " output=" + !fu.get("output").isJsonNull() + " burn=" + fu.get("burn_ticks").getAsInt();
				}
			}
		JsonObject v = new JsonObject();
		v.addProperty("case", "storage.index entries match the mind's schema (full, ids incl. unknown + malformed, removal)");
		v.addProperty("expected", "all entries valid; chest kind minecraft:chest; unknown id kind minecraft:unknown; removal partial; malformed id dropped");
		v.addProperty("observed", entries + " entries, " + bad + " invalid, chest=" + sawChest + ", unknown=" + sawUnknown + ", removed=" + sawRemoved);
		v.addProperty("ok", entries > 0 && bad == 0 && sawChest && sawUnknown && sawRemoved);
		validation.add(v);
		JsonObject vf = new JsonObject();
		String want = "minecraft:raw_ironx2 minecraft:coal lit=true 40/200 left=1500 output=false burn=1500 refreshed=true";
		vf.addProperty("case", "a furnace entry carries input, fuel, output, lit and progress; re-sent while lit (skills CP-36)");
		vf.addProperty("expected", want);
		vf.addProperty("observed", furnaceSeen + " refreshed=" + furnaceRefreshed);
		vf.addProperty("ok", want.equals(furnaceSeen + " refreshed=" + furnaceRefreshed));
		validation.add(vf);
		JsonObject vw = new JsonObject();
		String w2 = "minecraft:crafting_table items=0 placed=true removed=true";
		vw.addProperty("case", "workstations in storage.index (link v1.5): a table in the full index; a player's place and break as partials");
		vw.addProperty("expected", w2);
		vw.addProperty("observed", tableFull + " placed=" + cutterPlaced + " removed=" + cutterRemoved);
		vw.addProperty("ok", w2.equals(tableFull + " placed=" + cutterPlaced + " removed=" + cutterRemoved));
		validation.add(vw);
		apply(o -> { });
	}

	JsonObject grant(String action, String effect, String dim, List<BlockPos> rels, long expires) {
		JsonObject p = new JsonObject();
		p.addProperty("action_id", action);
		p.addProperty("effect", effect);
		p.addProperty("dim", dim);
		JsonArray a = new JsonArray();
		for (BlockPos r : rels) { BlockPos b = abs(r); a.add(xyz(b.getX(), b.getY(), b.getZ())); }
		p.add("positions", a);
		p.addProperty("expires_tick", expires);
		return p;
	}

	JsonObject region(String action, String effect, String dim, BlockPos relMin, BlockPos relMax, long expires) {
		JsonObject p = grant(action, effect, dim, List.of(), expires);
		p.remove("positions");
		JsonObject r = new JsonObject();
		BlockPos a = abs(relMin), b = abs(relMax);
		r.add("min", xyz(a.getX(), a.getY(), a.getZ()));
		r.add("max", xyz(b.getX(), b.getY(), b.getZ()));
		p.add("region", r);
		return p;
	}

	/** Ask permission.check first (as the mind does), then till for real with that face: the answer must match the effect. */
	void useItemAgreement(BlockPos rel, String face) {
		BlockPos a = abs(rel);
		JsonObject q = new JsonObject();
		q.addProperty("effect", "use_item");
		q.addProperty("dim", "minecraft:overworld");
		q.addProperty("actor", "eve");
		q.add("pos", xyz(a.getX(), a.getY(), a.getZ()));
		q.addProperty("face", face);
		boolean allowed = Link.INSTANCE.check(q).get("allowed").getAsBoolean();
		Direction d = Direction.byName(face);
		record("check(use_item " + face + ") said " + (allowed ? "allowed" : "denied") + ", then till", rel, allowed, () -> {
			level.setBlockAndUpdate(a, Blocks.DIRT.defaultBlockState());
			level.setBlockAndUpdate(a.above(), Blocks.AIR.defaultBlockState());
			level.setBlockAndUpdate(a.relative(d), Blocks.AIR.defaultBlockState());
			ItemStack hoe = new ItemStack(Items.WOODEN_HOE);
			eve.setItemInHand(InteractionHand.MAIN_HAND, hoe);
			eve.gameMode.useItemOn(eve, level, hoe, InteractionHand.MAIN_HAND, hit(a, d));
			eve.setItemInHand(InteractionHand.MAIN_HAND, ItemStack.EMPTY);
			return level.getBlockState(a).is(Blocks.FARMLAND);
		});
	}

	// ---- H-trees (sense 0.1.23): written-out expectations; cells in the mine (break free), y 4 on a stone at y 3 ----

	void hTrees() {
		Rails.resetRateCounts();
		PlacedBlocks.ledgerForTest(null, false); // no Ledger: leaves, sense's own record, snapshot zones
		BlockState log = Blocks.OAK_LOG.defaultBlockState();
		tree("natural log, no record", new BlockPos(1, 4, 1), () -> set(new BlockPos(1, 4, 1), log), true, null);
		tree("log the owner placed (own record)", new BlockPos(1, 4, 2), () -> ownerPlacesLog(new BlockPos(1, 4, 2)), false, "placed_block:own");
		tree("owner placed then broke it, a log stands there again", new BlockPos(1, 4, 3), () -> {
			ownerPlacesLog(new BlockPos(1, 4, 3));
			owner.gameMode.destroyBlock(abs(new BlockPos(1, 4, 3)));
			set(new BlockPos(1, 4, 3), log);
		}, true, null);
		tree("persistent (player-placed) leaves", new BlockPos(1, 4, 4), () -> set(new BlockPos(1, 4, 4), Blocks.OAK_LEAVES.defaultBlockState().setValue(LeavesBlock.PERSISTENT, true)), false, "placed_block:persistent_leaves");
		tree("natural leaves", new BlockPos(1, 4, 5), () -> set(new BlockPos(1, 4, 5), Blocks.OAK_LEAVES.defaultBlockState().setValue(LeavesBlock.PERSISTENT, false)), true, null);
		ownerPlacesLog(new BlockPos(1, 4, 6));
		PlacedBlocks.reloadOwnForTest(); // the record is persisted: it survives a restart
		hCase("permission.check(break) agrees: own record survives a reload", "placed_block:own".equals(checkBreak(new BlockPos(1, 4, 6))));
		tree("own record after a reload", new BlockPos(1, 4, 6), () -> { }, false, "placed_block:own");

		// outside every zone (kickoff): wild trees may be chopped; x = 4 is the gap between mine and home
		tree("in a zone: a natural log", new BlockPos(1, 4, 1), () -> set(new BlockPos(1, 4, 1), log), true, null);
		tree("outside every zone: a natural log", new BlockPos(4, 4, 1), () -> set(new BlockPos(4, 4, 1), log), true, null);
		tree("outside every zone: natural leaves", new BlockPos(4, 4, 2), () -> set(new BlockPos(4, 4, 2), Blocks.OAK_LEAVES.defaultBlockState().setValue(LeavesBlock.PERSISTENT, false)), true, null);
		tree("outside every zone: a log the owner placed", new BlockPos(4, 4, 3), () -> ownerPlacesLog(new BlockPos(4, 4, 3)), false, "placed_block:own");
		tree("outside every zone: persistent leaves", new BlockPos(4, 4, 4), () -> set(new BlockPos(4, 4, 4), Blocks.OAK_LEAVES.defaultBlockState().setValue(LeavesBlock.PERSISTENT, true)), false, "placed_block:persistent_leaves");
		tree("outside every zone: anything else stays denied (dirt)", new BlockPos(4, 4, 5), () -> set(new BlockPos(4, 4, 5), Blocks.DIRT.defaultBlockState()), false, "no_zone");
		set(new BlockPos(4, 4, 1), log);
		hCase("permission.check(break) outside every zone: a natural log is allowed", checkBreak(new BlockPos(4, 4, 1)).equals("allowed"));
		hCase("permission.check(break) outside every zone: the owner's log is placed_block:own", checkBreak(new BlockPos(4, 4, 3)).equals("placed_block:own"));

		// builder's FIX pass (kickoff): inside a live break grant, a block Eve herself placed during that grant's task
		String ow = "minecraft:overworld";
		long t = level.getServer().getTickCount();
		places(eve, new BlockPos(0, 4, 1), Items.OAK_LOG); // before the grant
		Rails.grant(grant("htrees-build", "break", ow, List.of(new BlockPos(0, 4, 1), new BlockPos(0, 4, 2), new BlockPos(0, 4, 3),
				new BlockPos(0, 4, 4), new BlockPos(0, 4, 5)), t + 200));
		places(eve, new BlockPos(0, 4, 2), Items.OAK_LOG);
		tree("FIX: Eve's own log, placed during the build grant", new BlockPos(0, 4, 2), () -> { }, true, null);
		tree("FIX: Eve's log placed before the grant", new BlockPos(0, 4, 1), () -> { }, false, "placed_block:own");
		ownerPlacesLog(new BlockPos(0, 4, 3));
		tree("FIX: the owner's log inside the grant", new BlockPos(0, 4, 3), () -> { }, false, "placed_block:own");
		places(eve, new BlockPos(0, 4, 4), Items.OAK_LEAVES);
		tree("FIX: Eve's stray leaves, placed during the grant", new BlockPos(0, 4, 4), () -> { }, true, null);
		places(eve, new BlockPos(0, 4, 5), Items.OAK_LOG);
		owner.gameMode.destroyBlock(abs(new BlockPos(0, 4, 5)));
		ownerPlacesLog(new BlockPos(0, 4, 5));
		tree("FIX: Eve placed it, then the owner replaced it", new BlockPos(0, 4, 5), () -> { }, false, "placed_block:own");
		places(eve, new BlockPos(0, 4, 6), Items.OAK_LOG);
		tree("FIX: Eve's log outside the grant", new BlockPos(0, 4, 6), () -> { }, false, "placed_block:own");
		places(eve, new BlockPos(0, 4, 2), Items.OAK_LOG);
		Rails.grant(grant("htrees-build", "break", ow, List.of(), t + 200)); // positions [] revokes the action
		tree("FIX: after the grant is revoked", new BlockPos(0, 4, 2), () -> { }, false, "placed_block:own");

		// Ledger installed: a fake <world>/ledger.sqlite with Ledger 1.3.25's tables, held open by a WAL writer like Ledger
		Path db;
		try {
			db = Files.createTempDirectory("eve-ledger").resolve("ledger.sqlite");
		} catch (IOException e) {
			throw new RuntimeException(e);
		}
		try (Connection w = fakeLedger(db); Statement s = w.createStatement()) {
			ledgerRow(s, 1, new BlockPos(2, 4, 1), 1, 1, 1, 0);       // a player placed this log
			ledgerRow(s, 1, new BlockPos(2, 4, 2), 1, 2, 1, 0);       // a player planted a sapling here; it grew
			ledgerRow(s, 1, new BlockPos(2, 4, 3), 1, 1, 1, 0);       // placed, then broken: the log now is another
			ledgerRow(s, 2, new BlockPos(2, 4, 3), 1, 1, 1, 0);
			ledgerRow(s, 1, new BlockPos(2, 4, 4), 1, 1, 1, 1);       // placement rolled back
			ledgerRow(s, 1, new BlockPos(2, 4, 5), 1, 1, null, 0);    // a non-player source
			ledgerRow(s, 1, new BlockPos(2, 4, 6), 2, 1, 1, 0);       // a player placed a log at these coordinates in the Nether
			ledgerRow(s, 3, new BlockPos(2, 4, 7), 1, 3, 1, 0);       // a player stripped a log (block-change)
			ledgerRow(s, 4, new BlockPos(1, 4, 7), 1, 1, 1, 0);       // a container action at this cell is no block action
			ledgerRow(s, 1, new BlockPos(4, 4, 6), 1, 1, 1, 0);       // outside every zone, a player placed this log
			PlacedBlocks.ledgerForTest(db, true);
			tree("Ledger: a player placed this log", new BlockPos(2, 4, 1), () -> set(new BlockPos(2, 4, 1), log), false, "placed_block:ledger");
			hCase("permission.check(break) agrees: placed_block:ledger", "placed_block:ledger".equals(checkBreak(new BlockPos(2, 4, 1))));
			tree("Ledger: a player-planted sapling grew into this log", new BlockPos(2, 4, 2), () -> set(new BlockPos(2, 4, 2), log), true, null);
			tree("Ledger: placed, broken, a log stands there again", new BlockPos(2, 4, 3), () -> set(new BlockPos(2, 4, 3), log), true, null);
			tree("Ledger: the placement was rolled back", new BlockPos(2, 4, 4), () -> set(new BlockPos(2, 4, 4), log), true, null);
			tree("Ledger: a non-player source", new BlockPos(2, 4, 5), () -> set(new BlockPos(2, 4, 5), log), true, null);
			tree("Ledger: a placement in another dimension", new BlockPos(2, 4, 6), () -> set(new BlockPos(2, 4, 6), log), true, null);
			tree("Ledger: a player stripped this log", new BlockPos(2, 4, 7), () -> set(new BlockPos(2, 4, 7), Blocks.STRIPPED_OAK_LOG.defaultBlockState()), false, "placed_block:ledger");
			tree("Ledger: only a container action at this cell", new BlockPos(1, 4, 7), () -> set(new BlockPos(1, 4, 7), log), true, null);
			tree("Ledger: natural log, no record", new BlockPos(1, 4, 1), () -> set(new BlockPos(1, 4, 1), log), true, null);
			tree("Ledger: outside every zone, a player-placed log", new BlockPos(4, 4, 6), () -> set(new BlockPos(4, 4, 6), log), false, "placed_block:ledger");
			tree("Ledger: outside every zone, a natural log", new BlockPos(4, 4, 7), () -> set(new BlockPos(4, 4, 7), log), true, null);
			ledgerRow(s, 1, new BlockPos(1, 4, 1), 1, 1, 1, 0);       // written after sense's reader opened: still seen
			tree("Ledger: a row written after the reader opened", new BlockPos(1, 4, 1), () -> set(new BlockPos(1, 4, 1), log), false, "placed_block:ledger");
		} catch (SQLException e) {
			throw new RuntimeException(e);
		}

		Path missing = db.resolveSibling("moved").resolve("ledger.sqlite");
		PlacedBlocks.ledgerForTest(missing, true); // Ledger installed, DB unreadable: fail closed for logs
		tree("Ledger unreadable: every log break denied", new BlockPos(2, 4, 2), () -> set(new BlockPos(2, 4, 2), log), false, "placed_block:ledger_unreadable");
		tree("Ledger unreadable: natural leaves need no history", new BlockPos(1, 4, 5), () -> set(new BlockPos(1, 4, 5), Blocks.OAK_LEAVES.defaultBlockState().setValue(LeavesBlock.PERSISTENT, false)), true, null);
		hCase("a missing Ledger DB is never created (read-only open)", !Files.exists(missing));

		PlacedBlocks.ledgerForTest(null, false);
		apply(o -> {
			JsonObject z = zone("orchard", new BlockPos(3, 3, 0), new BlockPos(3, 5, 2), "free", "free", "private");
			z.addProperty("snapshot", true);
			o.getAsJsonArray("zones").add(z);
		});
		set(new BlockPos(3, 4, 1), log);
		set(new BlockPos(3, 4, 2), Blocks.AIR.defaultBlockState());
		String sid = Snapshots.save(level.getServer(), "orchard", OWNER).get("snapshot_id").getAsString(); // a log at (3,4,1), air at (3,4,2)
		tree("the snapshot recorded this log", new BlockPos(3, 4, 1), () -> set(new BlockPos(3, 4, 1), log), false, "placed_block:snapshot");
		tree("snapshot zone, a log grew where the snapshot has air", new BlockPos(3, 4, 2), () -> set(new BlockPos(3, 4, 2), log), true, null);
		// the one exception (kickoff): a remove step from sense's own repair diff; damage = the recorded log turned on its side
		BlockState sideways = log.setValue(net.minecraft.world.level.block.RotatedPillarBlock.AXIS, Direction.Axis.X);
		set(new BlockPos(3, 4, 1), sideways);
		set(new BlockPos(3, 4, 2), Blocks.AIR.defaultBlockState());
		tree("a damaged snapshot log before any repair", new BlockPos(3, 4, 1), () -> set(new BlockPos(3, 4, 1), sideways), false, "placed_block:snapshot");
		JsonObject rq = new JsonObject();
		rq.addProperty("zone_id", "orchard");
		rq.addProperty("snapshot_id", sid);
		JsonObject rep = Snapshots.repair(level.getServer(), rq);
		hCase("zone.repair lists a remove step for the damaged log", rep.getAsJsonArray("steps").asList().stream().map(e -> e.getAsJsonObject())
				.anyMatch(o -> o.get("op").getAsString().equals("remove") && o.getAsJsonObject("pos").get("x").getAsInt() == abs(new BlockPos(3, 4, 1)).getX()));
		tree("the repair's remove step: Eve may break that log", new BlockPos(3, 4, 1), () -> set(new BlockPos(3, 4, 1), sideways), true, null);
		tree("the repair step is used once", new BlockPos(3, 4, 1), () -> set(new BlockPos(3, 4, 1), log), false, "placed_block:snapshot");
		ownerPlacesLog(new BlockPos(2, 4, 2)); // a repair step elsewhere lifts nothing here
		tree("a repair step does not lift another cell", new BlockPos(2, 4, 2), () -> { }, false, "placed_block:own");
		apply(o -> { });
		PlacedBlocks.ledgerForTest(null, false);
	}

	/** O218: Eve starts using `item` from `hand` (as her use-item packet would): true when the server is now using it. */
	boolean useAndHold(InteractionHand hand, Item item) {
		ItemStack s = new ItemStack(item);
		eve.setItemInHand(hand, s);
		eve.gameMode.useItem(eve, level, s, hand);
		boolean using = eve.isUsingItem() && eve.getUseItem().is(item);
		eve.stopUsingItem();
		eve.setItemInHand(hand, ItemStack.EMPTY);
		return using;
	}

	// ---- O193 (sense 0.1.27): written-out expectations; hazards.fire is false in the test envelope ----

	/** A cornerless obsidian frame, inner 2 wide x 3 high, standing on y 1: along x at z (axisX) or along z at x. */
	void frame(int a0, int fixed, boolean axisX, boolean withLeftSide) {
		for (int i = 0; i < 4; i++)
			for (int y = 1; y <= 5; y++) {
				BlockPos rel = axisX ? new BlockPos(a0 + i, y, fixed) : new BlockPos(fixed, y, a0 + i);
				boolean side = i == 0 || i == 3, edge = y == 1 || y == 5;
				boolean obsidian = side && !edge ? (i != 0 || withLeftSide) : !side && edge; // corners left out (vanilla: optional)
				level.setBlockAndUpdate(abs(rel), obsidian ? Blocks.OBSIDIAN.defaultBlockState() : Blocks.AIR.defaultBlockState());
			}
	}

	void clearFrame(int a0, int fixed, boolean axisX) {
		for (int i = 0; i < 4; i++)
			for (int y = 1; y <= 5; y++) level.setBlockAndUpdate(abs(axisX ? new BlockPos(a0 + i, y, fixed) : new BlockPos(fixed, y, a0 + i)), Blocks.AIR.defaultBlockState());
	}

	/** Eve uses flint and steel on `clicked` (face): true when fire or a portal appeared in the cell beyond. */
	boolean flint(BlockPos clicked, Direction face) {
		BlockPos a = abs(clicked), inside = a.relative(face);
		ItemStack fs = new ItemStack(Items.FLINT_AND_STEEL);
		eve.setItemInHand(InteractionHand.MAIN_HAND, fs);
		eve.setShiftKeyDown(true);
		eve.gameMode.useItemOn(eve, level, fs, InteractionHand.MAIN_HAND, hit(a, face));
		eve.setShiftKeyDown(false);
		eve.setItemInHand(InteractionHand.MAIN_HAND, ItemStack.EMPTY);
		return level.getBlockState(inside).is(Blocks.NETHER_PORTAL) || level.getBlockState(inside).is(Blocks.FIRE);
	}

	String checkFire(BlockPos clicked, String face) {
		BlockPos a = abs(clicked);
		JsonObject q = new JsonObject();
		q.addProperty("effect", "use_hazard");
		q.addProperty("hazard", "fire");
		q.addProperty("dim", "minecraft:overworld");
		q.addProperty("actor", "eve");
		q.add("pos", xyz(a.getX(), a.getY(), a.getZ()));
		q.addProperty("face", face);
		JsonObject r = Link.INSTANCE.check(q);
		return (r.get("allowed").getAsBoolean() ? "" : "denied ") + r.get("rule_id").getAsString();
	}

	void portalLight() {
		// mine zone (x 0..3, place free): a frame along x at z 7, its bottom row at y 1
		frame(0, 7, true, true);
		hCase("O193 permission.check(use_hazard fire) on a complete frame: portal_light", checkFire(new BlockPos(1, 1, 7), "up").equals("portal_light"));
		record("O193 flint and steel on a complete, unlit frame lights the portal", new BlockPos(1, 2, 7), true, () -> flint(new BlockPos(1, 1, 7), Direction.UP));
		hCase("O193 the portal stands (inner cell is nether_portal)", level.getBlockState(abs(new BlockPos(2, 4, 7))).is(Blocks.NETHER_PORTAL));
		frame(0, 7, true, false); // the same frame with its left side missing
		hCase("O193 permission.check on an incomplete frame: hazard:fire", checkFire(new BlockPos(1, 1, 7), "up").equals("denied hazard:fire"));
		record("O193 an incomplete frame is refused", new BlockPos(1, 2, 7), false, () -> flint(new BlockPos(1, 1, 7), Direction.UP));
		clearFrame(0, 7, true);
		level.setBlockAndUpdate(abs(new BlockPos(2, 1, 6)), Blocks.OBSIDIAN.defaultBlockState());
		level.setBlockAndUpdate(abs(new BlockPos(2, 2, 6)), Blocks.AIR.defaultBlockState());
		record("O193 obsidian that is no frame is refused", new BlockPos(2, 2, 6), false, () -> flint(new BlockPos(2, 1, 6), Direction.UP));
		level.setBlockAndUpdate(abs(new BlockPos(2, 1, 6)), Blocks.GRASS_BLOCK.defaultBlockState());
		record("O193 grass is refused", new BlockPos(2, 2, 6), false, () -> flint(new BlockPos(2, 1, 6), Direction.UP));
		level.setBlockAndUpdate(abs(new BlockPos(2, 1, 6)), Blocks.AIR.defaultBlockState());
		// the gap x = 4 is outside every zone: a complete frame along z there
		frame(0, 4, false, true);
		hCase("O193 permission.check on a frame outside the play zone: hazard:fire", checkFire(new BlockPos(4, 1, 1), "up").equals("denied hazard:fire"));
		record("O193 a complete frame outside the play zone is refused", new BlockPos(4, 2, 1), false, () -> flint(new BlockPos(4, 1, 1), Direction.UP));
		clearFrame(0, 4, false);
	}

	void tree(String what, BlockPos rel, Runnable setup, boolean allowed, String rule) {
		record("H-trees: " + what, rel, allowed, () -> {
			setup.run();
			BlockPos a = abs(rel);
			eve.gameMode.destroyBlock(a);
			return level.getBlockState(a).isAir();
		});
		if (rule != null) hCase("rule_id " + rule + ": " + what, rule.equals(lastDeniedRule()));
	}

	void set(BlockPos rel, BlockState s) {
		level.setBlockAndUpdate(abs(rel).below(), Blocks.STONE.defaultBlockState());
		level.setBlockAndUpdate(abs(rel), s);
	}

	void ownerPlacesLog(BlockPos rel) {
		places(owner, rel, Items.OAK_LOG);
	}

	/** `who` places `item` at `rel` with the item, on a stone below: a real BlockItem placement. */
	void places(ServerPlayer who, BlockPos rel, Item item) {
		BlockPos clicked = abs(rel).below();
		level.setBlockAndUpdate(clicked, Blocks.STONE.defaultBlockState());
		level.setBlockAndUpdate(abs(rel), Blocks.AIR.defaultBlockState());
		ItemStack st = new ItemStack(item);
		who.setItemInHand(InteractionHand.MAIN_HAND, st);
		who.setShiftKeyDown(true);
		who.gameMode.useItemOn(who, level, st, InteractionHand.MAIN_HAND, hit(clicked, Direction.UP));
		who.setShiftKeyDown(false);
		who.setItemInHand(InteractionHand.MAIN_HAND, ItemStack.EMPTY);
		if (level.getBlockState(abs(rel)).isAir()) EveSense.LOG.error("H-trees test: {} was not placed at {}", item, abs(rel));
	}

	/** A fake <world>/ledger.sqlite with Ledger 1.3.25's tables (WAL); the caller keeps this writer open, as Ledger does.
	 * Ids: actions 1 place, 2 break, 3 change, 4 item-insert; objects 1 oak_log, 2 oak_sapling, 3 stripped_oak_log,
	 * 4 air, 5 chest; worlds 1 overworld, 2 the_nether; player 1 the owner. */
	Connection fakeLedger(Path db) throws SQLException {
		Connection w = DriverManager.getConnection("jdbc:sqlite:" + db);
		try (Statement s = w.createStatement()) {
			s.execute("pragma journal_mode=wal");
			s.executeUpdate("create table ActionIdentifiers(id integer primary key, action_identifier varchar(16) unique)");
			s.executeUpdate("create table ObjectIdentifiers(id integer primary key, identifier varchar(191) unique)");
			s.executeUpdate("create table worlds(id integer primary key, identifier varchar(191) unique)");
			s.executeUpdate("create table players(id integer primary key, player_name varchar(16))");
			s.executeUpdate("create table actions(id integer primary key autoincrement, action_id int, time text, x int, y int, z int,"
					+ " world_id int, object_id int, old_object_id int, source int, player_id int, rolled_back boolean default 0)");
			s.executeUpdate("create index actions_by_location on actions(x, y, z, world_id)");
			s.executeUpdate("insert into ActionIdentifiers values (1, 'block-place'), (2, 'block-break'), (3, 'block-change'), (4, 'item-insert')");
			s.executeUpdate("insert into ObjectIdentifiers values (1, 'minecraft:oak_log'), (2, 'minecraft:oak_sapling'), (3, 'minecraft:stripped_oak_log'), (4, 'minecraft:air'), (5, 'minecraft:chest')");
			s.executeUpdate("insert into worlds values (1, 'minecraft:overworld'), (2, 'minecraft:the_nether')");
			s.executeUpdate("insert into players values (1, '" + OWNER + "')");
		}
		return w;
	}

	void ledgerRow(Statement s, int action, BlockPos rel, int world, int object, Integer player, int rolledBack) throws SQLException {
		BlockPos a = abs(rel);
		s.executeUpdate("insert into actions(action_id, x, y, z, world_id, object_id, player_id, rolled_back) values (" + action + ", "
				+ a.getX() + ", " + a.getY() + ", " + a.getZ() + ", " + world + ", " + object + ", " + player + ", " + rolledBack + ")");
	}

	/** permission.check(break) at rel: the denying rule_id, or "allowed". */
	String checkBreak(BlockPos rel) {
		BlockPos a = abs(rel);
		JsonObject q = new JsonObject();
		q.addProperty("effect", "break");
		q.addProperty("dim", "minecraft:overworld");
		q.addProperty("actor", "eve");
		q.add("pos", xyz(a.getX(), a.getY(), a.getZ()));
		JsonObject r = Link.INSTANCE.check(q);
		return r.get("allowed").getAsBoolean() ? "allowed" : r.get("rule_id").getAsString();
	}

	String lastDeniedRule() {
		synchronized (emitted) {
			for (int i = emitted.size() - 1; i >= 0; i--)
				if (emitted.get(i).get("type").getAsString().equals("permission.denied")) return emitted.get(i).getAsJsonObject("payload").get("rule_id").getAsString();
		}
		return "none";
	}

	void hCase(String name, boolean ok) {
		JsonObject v = new JsonObject();
		v.addProperty("case", "H-trees " + name);
		v.addProperty("ok", ok);
		validation.add(v);
	}

	/** The grant must be refused and the break that would have used it must be denied. */
	void refusedGrantThenBreak(String why, JsonObject g, BlockPos rel) {
		record("grant refused (" + why + ") then break", rel, false, () -> {
			boolean accepted = Rails.grant(g) == null;
			if (accepted) EveSense.LOG.error("grant unexpectedly accepted: {}", g);
			return breakRaw(rel) || accepted;
		});
	}

	boolean breakRaw(BlockPos rel) {
		BlockPos a = abs(rel);
		level.setBlockAndUpdate(a, Blocks.DIRT.defaultBlockState());
		eve.gameMode.destroyBlock(a);
		return level.getBlockState(a).isAir();
	}

	boolean emittedType(String type) {
		synchronized (emitted) { return emitted.stream().anyMatch(m -> m.get("type").getAsString().equals(type)); }
	}

	// ---- actions; each returns whether the effect happened in the world ----

	void expectBreak(ServerPlayer who, int x, int y, int z, boolean allowed) {
		BlockPos rel = new BlockPos(x, y, z);
		record((who == eve ? "Eve" : who.getGameProfile().name()) + " break", rel, allowed, () -> {
			BlockPos a = abs(rel);
			level.setBlockAndUpdate(a, Blocks.DIRT.defaultBlockState());
			who.gameMode.destroyBlock(a);
			return level.getBlockState(a).isAir();
		});
	}

	void expectPlace(int x, int y, int z, Direction face, Item item, boolean allowed) {
		BlockPos rel = new BlockPos(x, y, z);
		record("place " + item + " on " + face + " face", rel.relative(face), allowed, () -> {
			BlockPos clicked = abs(rel), target = clicked.relative(face);
			level.setBlockAndUpdate(clicked, Blocks.STONE.defaultBlockState());
			level.setBlockAndUpdate(target, Blocks.AIR.defaultBlockState());
			ItemStack stack = new ItemStack(item, 16);
			eve.setItemInHand(InteractionHand.MAIN_HAND, stack);
			eve.setShiftKeyDown(true); // a placement skill sneaks so the clicked block's own use never runs
			eve.gameMode.useItemOn(eve, level, stack, InteractionHand.MAIN_HAND, hit(clicked, face));
			eve.setShiftKeyDown(false);
			eve.setItemInHand(InteractionHand.MAIN_HAND, ItemStack.EMPTY);
			return !level.getBlockState(target).isAir();
		});
	}

	void expectOpen(BlockPos rel, boolean allowed) {
		record("open chest", rel, allowed, () -> { level.setBlockAndUpdate(abs(rel), Blocks.CHEST.defaultBlockState()); return openRaw(rel); });
	}

	boolean openRaw(BlockPos rel) {
		int before = eve.menusOpened;
		level.setBlockAndUpdate(abs(rel).above(), Blocks.AIR.defaultBlockState());
		eve.setItemInHand(InteractionHand.MAIN_HAND, ItemStack.EMPTY);
		eve.gameMode.useItemOn(eve, level, ItemStack.EMPTY, InteractionHand.MAIN_HAND, hit(abs(rel), Direction.UP));
		return eve.menusOpened > before;
	}

	void doubleChest(BlockPos l, BlockPos r) {
		var st = Blocks.CHEST.defaultBlockState().setValue(ChestBlock.FACING, Direction.NORTH);
		level.setBlock(abs(l), st.setValue(ChestBlock.TYPE, ChestType.LEFT), 2); // LEFT facing north connects east
		level.setBlock(abs(r), st.setValue(ChestBlock.TYPE, ChestType.RIGHT), 2);
	}

	/** What ServerGamePacketListenerImpl.handleContainerClick does once the menu is open: menu.clicked(...). */
	boolean transfer(BlockPos rel, ContainerInput input, int slot, int button) {
		BlockPos a = abs(rel);
		if (!(level.getBlockEntity(a) instanceof Container)) level.setBlockAndUpdate(a, Blocks.CHEST.defaultBlockState());
		Container chest = (Container) level.getBlockEntity(a);
		chest.setItem(slot, new ItemStack(Items.DIAMOND, 5));
		ChestMenu menu = ChestMenu.threeRows(7, eve.getInventory(), chest);
		eve.containerMenu = menu;
		String before = contents(chest);
		menu.clicked(slot, button, input, eve);
		String after = contents(chest);
		menu.setCarried(ItemStack.EMPTY);
		menu.removed(eve);
		eve.containerMenu = eve.inventoryMenu;
		eve.getInventory().clearContent();
		chest.clearContent();
		return !before.equals(after);
	}

	boolean useSnowball() {
		ItemStack s = new ItemStack(Items.SNOWBALL, 4);
		eve.setItemInHand(InteractionHand.MAIN_HAND, s);
		eve.gameMode.useItem(eve, level, s, InteractionHand.MAIN_HAND);
		boolean used = eve.getMainHandItem().getCount() < 4;
		eve.setItemInHand(InteractionHand.MAIN_HAND, ItemStack.EMPTY);
		return used;
	}

	boolean till(BlockPos rel) {
		BlockPos a = abs(rel);
		level.setBlockAndUpdate(a, Blocks.DIRT.defaultBlockState());
		level.setBlockAndUpdate(a.above(), Blocks.AIR.defaultBlockState());
		ItemStack hoe = new ItemStack(Items.WOODEN_HOE);
		eve.setItemInHand(InteractionHand.MAIN_HAND, hoe);
		eve.gameMode.useItemOn(eve, level, hoe, InteractionHand.MAIN_HAND, hit(a, Direction.UP));
		eve.setItemInHand(InteractionHand.MAIN_HAND, ItemStack.EMPTY);
		return level.getBlockState(a).is(Blocks.FARMLAND);
	}

	/**
	 * O44: everything sense enforces or reports works in the End like in the overworld. A zone end_bridge in
	 * minecraft:the_end at x/z 200..220, y 50..90 (break task_only, place free, containers shared), over the End void.
	 * Fake Eve is teleported there for the player paths and back afterwards.
	 */
	void endSuite() {
		ServerLevel end = level.getServer().getLevel(net.minecraft.world.level.Level.END);
		if (end == null) { record("the End exists on the test server", null, true, () -> false); return; }
		String ed = "minecraft:the_end";
		apply(o -> {
			JsonObject z = new JsonObject();
			z.addProperty("id", "end_bridge");
			z.addProperty("dim", ed);
			z.add("min", xyz(200, 50, 200));
			z.add("max", xyz(220, 90, 220));
			z.addProperty("break", "task_only");
			z.addProperty("place", "free");
			z.addProperty("containers", "shared");
			z.addProperty("snapshot", false);
			o.getAsJsonArray("zones").add(z);
			o.getAsJsonObject("home").addProperty("dim", ed);                  // a scan centre in the End (fake Eve is not a listed player)
			o.getAsJsonObject("home").add("center", xyz(210, 64, 210));
		});
		for (int cx = 12; cx <= 15; cx++) for (int cz = 12; cz <= 15; cz++) end.getChunk(cx, cz);             // load x/z 192..255
		for (int x = 205; x <= 215; x++) for (int z = 205; z <= 215; z++) end.setBlockAndUpdate(new BlockPos(x, 64, z), Blocks.END_STONE.defaultBlockState());
		for (int x = 230; x <= 234; x++) end.setBlockAndUpdate(new BlockPos(x, 64, 210), Blocks.END_STONE.defaultBlockState()); // outside every zone
		eve.teleportTo(end, 210.5, 65, 210.5, java.util.Set.of(), 0, 0, false);
		eve.setOnGround(true);
		eve.fallDistance = 0;
		Runnable reset = () -> { eve.setItemInHand(InteractionHand.MAIN_HAND, ItemStack.EMPTY); eve.fallDistance = 0; eve.setOnGround(true); };
		try {
			BlockPos b1 = new BlockPos(207, 65, 207), b2 = new BlockPos(208, 65, 207), out = new BlockPos(232, 65, 210), pl = new BlockPos(209, 64, 208);
			record("End: break in end_bridge (task_only) without a grant", null, false, () -> { end.setBlockAndUpdate(b1, Blocks.DIRT.defaultBlockState()); eve.gameMode.destroyBlock(b1); return end.getBlockState(b1).isAir(); });
			JsonObject g = new JsonObject();
			g.addProperty("action_id", "e1");
			g.addProperty("effect", "break");
			g.addProperty("dim", ed);
			JsonArray ps = new JsonArray();
			ps.add(xyz(b2.getX(), b2.getY(), b2.getZ()));
			g.add("positions", ps);
			g.addProperty("expires_tick", level.getServer().getTickCount() + 200);
			Rails.grant(g);
			record("End: break with a break grant in the_end", null, true, () -> { end.setBlockAndUpdate(b2, Blocks.DIRT.defaultBlockState()); eve.gameMode.destroyBlock(b2); return end.getBlockState(b2).isAir(); });
			record("End: the same positions are refused as an overworld grant (outside every overworld zone)", null, true, () -> Rails.grant(withDim(g, "minecraft:overworld")) != null);
			record("End: break outside every End zone", null, false, () -> { end.setBlockAndUpdate(out, Blocks.DIRT.defaultBlockState()); eve.gameMode.destroyBlock(out); return end.getBlockState(out).isAir(); });
			record("End: place in end_bridge (place free)", null, true, () -> {
				end.setBlockAndUpdate(pl.above(), Blocks.AIR.defaultBlockState());
				ItemStack st = new ItemStack(Items.COBBLESTONE, 4);
				eve.setItemInHand(InteractionHand.MAIN_HAND, st);
				eve.gameMode.useItemOn(eve, end, st, InteractionHand.MAIN_HAND, hit(pl, Direction.UP));
				reset.run();
				return end.getBlockState(pl.above()).is(Blocks.COBBLESTONE);
			});
			BlockPos chest = new BlockPos(212, 65, 212);
			end.setBlockAndUpdate(chest, Blocks.CHEST.defaultBlockState());
			((net.minecraft.world.Container) end.getBlockEntity(chest)).setItem(0, new ItemStack(Items.ENDER_PEARL, 5));
			record("End: a chest in the End is in the full index, with its dim and items", null, true, () -> {
				synchronized (emitted) { emitted.clear(); }
				Storage.sendFull(level.getServer(), null, null);
				synchronized (emitted) {
					return emitted.stream().filter(m -> m.get("type").getAsString().equals("storage.index"))
							.flatMap(m -> m.getAsJsonObject("payload").getAsJsonArray("containers").asList().stream()).map(JsonElement::getAsJsonObject)
							.anyMatch(c -> c.get("id").getAsString().equals("minecraft:the_end:212,65,212") && c.get("dim").getAsString().equals(ed)
									&& c.getAsJsonArray("items").toString().contains("minecraft:ender_pearl"));
				}
			});
			end.setBlockAndUpdate(chest, Blocks.AIR.defaultBlockState());
			record("End: void_edge at the platform edge (the test End is flat at y 3: depth 61 > 8)", null, true, () -> {
				JsonObject q = new JsonObject();
				q.addProperty("kind", "void_edge");
				q.addProperty("dim", ed);
				q.add("center", xyz(210, 64, 210));
				q.addProperty("radius", 8);
				JsonObject r = Query.resources(level.getServer(), q);
				return r.get("dim").getAsString().equals(ed) && r.getAsJsonArray("items").asList().stream().map(JsonElement::getAsJsonObject)
						.anyMatch(it -> it.getAsJsonObject("pos").get("x").getAsInt() == 205 && (it.get("depth").isJsonNull() || it.get("depth").getAsInt() > 8)
								&& it.getAsJsonArray("toward").toString().contains("west"));
			});
			record("End: fall rescue outside every zone (O20 never denied)", null, true, () -> {
				BlockPos floor = new BlockPos(233, 64, 210), cell = floor.above();
				end.setBlockAndUpdate(cell, Blocks.AIR.defaultBlockState());
				eve.teleportTo(end, 233.5, 66.5, 210.5, java.util.Set.of(), 0, 90, false);
				eve.setOnGround(false);
				eve.fallDistance = 12;
				ItemStack w = new ItemStack(Items.WATER_BUCKET);
				eve.setItemInHand(InteractionHand.MAIN_HAND, w);
				eve.gameMode.useItemOn(eve, end, w, InteractionHand.MAIN_HAND, hit(floor, Direction.UP));
				eve.gameMode.useItem(eve, end, w, InteractionHand.MAIN_HAND);
				boolean water = end.getFluidState(cell).is(net.minecraft.tags.FluidTags.WATER);
				ItemStack b = new ItemStack(Items.BUCKET);
				eve.setItemInHand(InteractionHand.MAIN_HAND, b);
				eve.gameMode.useItemOn(eve, end, b, InteractionHand.MAIN_HAND, hit(floor, Direction.UP));
				eve.gameMode.useItem(eve, end, b, InteractionHand.MAIN_HAND);
				boolean scooped = !end.getFluidState(cell).is(net.minecraft.tags.FluidTags.WATER);
				reset.run();
				end.setBlockAndUpdate(cell, Blocks.AIR.defaultBlockState());
				return water && scooped;
			});
		} finally {
			reset.run();
			for (int x = 200; x <= 235; x++) for (int y = 63; y <= 67; y++) for (int z = 200; z <= 220; z++) end.setBlockAndUpdate(new BlockPos(x, y, z), Blocks.AIR.defaultBlockState());
			eve.teleportTo(level, abs(4, 1, 3).getX() + 0.5, abs(4, 1, 3).getY(), abs(4, 1, 3).getZ() + 0.5, java.util.Set.of(), 0, 0, false);
			apply(o -> { });
		}
	}

	private static JsonObject withDim(JsonObject g, String dim) {
		JsonObject c = g.deepCopy();
		c.addProperty("dim", dim);
		c.addProperty("action_id", "e2");
		return c;
	}

	/** Wheat seeds on the farmland at rel (Eve's real BlockItem placement path). */
	boolean plant(BlockPos rel) {
		BlockPos a = abs(rel);
		level.setBlockAndUpdate(a.above(), Blocks.AIR.defaultBlockState());
		ItemStack seeds = new ItemStack(Items.WHEAT_SEEDS, 4);
		eve.setItemInHand(InteractionHand.MAIN_HAND, seeds);
		eve.gameMode.useItemOn(eve, level, seeds, InteractionHand.MAIN_HAND, hit(a, Direction.UP));
		eve.setItemInHand(InteractionHand.MAIN_HAND, ItemStack.EMPTY);
		boolean planted = level.getBlockState(a.above()).is(Blocks.WHEAT);
		level.setBlockAndUpdate(a.above(), Blocks.AIR.defaultBlockState());
		return planted;
	}

	boolean flint(BlockPos rel) {
		BlockPos a = abs(rel);
		level.setBlockAndUpdate(a, Blocks.STONE.defaultBlockState());
		level.setBlockAndUpdate(a.above(), Blocks.AIR.defaultBlockState());
		ItemStack f = new ItemStack(Items.FLINT_AND_STEEL);
		eve.setItemInHand(InteractionHand.MAIN_HAND, f);
		eve.gameMode.useItemOn(eve, level, f, InteractionHand.MAIN_HAND, hit(a, Direction.UP));
		eve.setItemInHand(InteractionHand.MAIN_HAND, ItemStack.EMPTY);
		boolean fire = level.getBlockState(a.above()).is(Blocks.FIRE);
		level.setBlockAndUpdate(a.above(), Blocks.AIR.defaultBlockState());
		return fire;
	}

	boolean lava() {
		BlockPos floor = abs(1, 2, 6);
		level.setBlockAndUpdate(floor, Blocks.STONE.defaultBlockState());
		eve.snapTo(floor.getX() + 0.5, floor.getY() + 1, floor.getZ() + 0.5, 0, 90); // looking straight down at the stone
		ItemStack b = new ItemStack(Items.LAVA_BUCKET);
		eve.setItemInHand(InteractionHand.MAIN_HAND, b);
		eve.gameMode.useItem(eve, level, b, InteractionHand.MAIN_HAND);
		boolean emptied = !eve.getMainHandItem().is(Items.LAVA_BUCKET);
		eve.setItemInHand(InteractionHand.MAIN_HAND, ItemStack.EMPTY);
		eve.snapTo(Vec3.atCenterOf(abs(4, 1, 3)));
		boolean placed = level.getBlockState(floor.above()).is(Blocks.LAVA);
		level.setBlockAndUpdate(floor.above(), Blocks.AIR.defaultBlockState()); // no flowing lava left behind
		return emptied || placed;
	}

	boolean lever(BlockPos rel) {
		BlockPos a = abs(rel);
		level.setBlockAndUpdate(a.below(), Blocks.STONE.defaultBlockState());
		level.setBlockAndUpdate(a, Blocks.LEVER.defaultBlockState().setValue(LeverBlock.FACE, AttachFace.FLOOR));
		eve.setItemInHand(InteractionHand.MAIN_HAND, ItemStack.EMPTY);
		eve.gameMode.useItemOn(eve, level, ItemStack.EMPTY, InteractionHand.MAIN_HAND, hit(a, Direction.UP));
		return level.getBlockState(a).getValue(LeverBlock.POWERED);
	}

	void cmd(ServerPlayer p, String c) {
		level.getServer().getCommands().performPrefixedCommand(p.createCommandSourceStack(), c);
	}

	// ---- bookkeeping ----

	void record(String action, BlockPos rel, boolean expectAllowed, BooleanSupplier effect) {
		synchronized (emitted) { emitted.clear(); }
		boolean happened = effect.getAsBoolean();
		List<JsonObject> got;
		synchronized (emitted) { got = new ArrayList<>(emitted); }
		boolean signalled = got.stream().anyMatch(m -> m.get("type").getAsString().equals("permission.denied")
				|| m.get("type").getAsString().equals("owner.command") && !m.getAsJsonObject("payload").get("trusted").getAsBoolean());
		boolean ok = expectAllowed ? happened : !happened && signalled;
		String pos = rel == null ? null : abs(rel).toShortString();
		attempts.add(new Attempt(attempts.size() + 1, variant, action, pos, expectAllowed ? "allowed" : "denied", happened ? "effect" : "no effect", !expectAllowed && happened, ok, got));
	}

	void envelopeValidation() {
		check("repo config/envelope.example.json", base.toString(), true);
		Map<String, Consumer<JsonObject>> bad = new LinkedHashMap<>();
		bad.put("eve_account placeholder SET_ME", o -> o.getAsJsonObject("owner").addProperty("eve_account", "SET_ME"));
		bad.put("Eve listed as trusted", o -> o.getAsJsonObject("owner").getAsJsonArray("trusted_players").add(o.getAsJsonObject("owner").get("eve_account").getAsString()));
		bad.put("unknown top-level field", o -> o.addProperty("zone", 1));
		bad.put("missing zones", o -> o.remove("zones"));
		bad.put("break value not in enum", o -> o.getAsJsonArray("zones").get(0).getAsJsonObject().addProperty("break", "sometimes"));
		bad.put("zone min > max", o -> o.getAsJsonArray("zones").get(0).getAsJsonObject().getAsJsonObject("min").addProperty("x", 99999));
		bad.put("container tag key malformed", o -> o.getAsJsonObject("containers").getAsJsonObject("tags").addProperty("1,2,3", "shared"));
		bad.put("ws_payload_max_bytes over 256 KiB", o -> o.getAsJsonObject("limits").addProperty("ws_payload_max_bytes", 262145));
		bad.put("mode_default not allowed", o -> o.addProperty("mode_default", "autonomous"));
		bad.put("duplicate zone ids", o -> o.getAsJsonArray("zones").get(1).getAsJsonObject().addProperty("id", o.getAsJsonArray("zones").get(0).getAsJsonObject().get("id").getAsString()));
		bad.put("hazard not boolean", o -> o.getAsJsonObject("hazards").addProperty("tnt", "no"));
		bad.put("trusted name invalid", o -> o.getAsJsonObject("owner").getAsJsonArray("trusted_players").add("bad name!"));
		bad.put("zone limits with an unknown key", o -> { JsonObject zl = new JsonObject(); zl.addProperty("blocks_mined_per_hour", 5); o.getAsJsonArray("zones").get(0).getAsJsonObject().add("limits", zl); });
		bad.put("zone limits negative", o -> { JsonObject zl = new JsonObject(); zl.addProperty("blocks_broken_per_hour", -1); o.getAsJsonArray("zones").get(0).getAsJsonObject().add("limits", zl); });
		bad.put("limits.parked_ttl_s under 60 s", o -> o.getAsJsonObject("limits").addProperty("parked_ttl_s", 30));
		bad.put("limits.parked_ttl_s not an integer", o -> o.getAsJsonObject("limits").addProperty("parked_ttl_s", "6h"));
		// CP-39 (envelope v9): animals + farmland, optional. One good file, then each defect refused on its own.
		String z0 = base.getAsJsonArray("zones").get(0).getAsJsonObject().get("id").getAsString();
		java.util.function.Function<String, JsonObject> v9 = extra -> {
			JsonObject o = base.deepCopy();
			o.add("animals", JsonParser.parseString("[{\"zone\":\"" + z0 + "\",\"species\":\"minecraft:sheep\",\"daily\":{\"shear\":16,\"kill\":2},\"keep_min\":2},"
					+ "{\"zone\":\"*\",\"species\":\"minecraft:pig\",\"daily\":{\"kill\":4},\"keep_min\":0,\"note\":\"hunting\"}]"));
			o.add("farmland", JsonParser.parseString("[{\"id\":\"wheat\",\"dim\":\"minecraft:overworld\",\"min\":{\"x\":0,\"y\":64,\"z\":0},\"max\":{\"x\":8,\"y\":64,\"z\":8}}]"));
			if (extra != null) {
				JsonObject e = JsonParser.parseString(extra).getAsJsonObject();
				for (var en : e.entrySet()) {
					if (en.getKey().startsWith("animals0.")) o.getAsJsonArray("animals").get(0).getAsJsonObject().add(en.getKey().substring(9), en.getValue());
					else if (en.getKey().startsWith("-animals0.")) o.getAsJsonArray("animals").get(0).getAsJsonObject().remove(en.getKey().substring(10));
					else if (en.getKey().startsWith("farmland0.")) o.getAsJsonArray("farmland").get(0).getAsJsonObject().add(en.getKey().substring(10), en.getValue());
					else o.add(en.getKey(), en.getValue());
				}
			}
			return o;
		};
		check("CP-39 v9 animals + farmland accepted", v9.apply(null).toString(), true);
		check("CP-39 empty animals and farmland lists accepted (everything refused)", v9.apply("{\"animals\":[],\"farmland\":[]}").toString(), true);
		check("CP-39 animals zone not a zone id", v9.apply("{\"animals0.zone\":\"nowhere\"}").toString(), false);
		check("CP-39 animals species not namespaced", v9.apply("{\"animals0.species\":\"Sheep\"}").toString(), false);
		check("CP-39 animals daily unknown action", v9.apply("{\"animals0.daily\":{\"pet\":3}}").toString(), false);
		check("CP-39 animals daily negative", v9.apply("{\"animals0.daily\":{\"kill\":-1}}").toString(), false);
		check("CP-39 animals daily not an integer", v9.apply("{\"animals0.daily\":{\"kill\":1.5}}").toString(), false);
		check("CP-39 animals keep_min missing", v9.apply("{\"-animals0.keep_min\":0}").toString(), false);
		check("CP-39 animals unknown key", v9.apply("{\"animals0.quota\":3}").toString(), false);
		check("CP-39 animals duplicate zone+species", v9.apply("{\"animals\":[{\"zone\":\"*\",\"species\":\"minecraft:pig\",\"daily\":{},\"keep_min\":0},{\"zone\":\"*\",\"species\":\"minecraft:pig\",\"daily\":{},\"keep_min\":1}]}").toString(), false);
		check("CP-39 animals not a list", v9.apply("{\"animals\":{}}").toString(), false);
		check("CP-39 farmland min > max", v9.apply("{\"farmland0.min\":{\"x\":9,\"y\":64,\"z\":0}}").toString(), false);
		check("CP-39 farmland unknown key", v9.apply("{\"farmland0.crop\":\"wheat\"}").toString(), false);
		check("CP-39 farmland duplicate id", v9.apply("{\"farmland\":[{\"id\":\"a\",\"dim\":\"minecraft:overworld\",\"min\":{\"x\":0,\"y\":0,\"z\":0},\"max\":{\"x\":0,\"y\":0,\"z\":0}},{\"id\":\"a\",\"dim\":\"minecraft:overworld\",\"min\":{\"x\":0,\"y\":0,\"z\":0},\"max\":{\"x\":0,\"y\":0,\"z\":0}}]}").toString(), false);
		JsonObject ttl = base.deepCopy();                                                   // O31: optional, the mind's parked TTL
		ttl.getAsJsonObject("limits").addProperty("parked_ttl_s", 21600);
		check("limits.parked_ttl_s 21600 accepted (optional)", ttl.toString(), true);
		JsonObject zcap = base.deepCopy();
		JsonObject zcl = new JsonObject();
		zcl.addProperty("blocks_broken_per_hour", 1200);
		zcap.getAsJsonArray("zones").get(0).getAsJsonObject().add("limits", zcl);
		check("zone limits {blocks_broken_per_hour: 1200} accepted (Amendment 5)", zcap.toString(), true);
		for (String[] d : new String[][]{{"[{\"x\":-16,\"y\":99,\"z\":-45},{\"x\":-16,\"y\":100,\"z\":-45}]", "true"}, {"[{\"x\":-16,\"y\":99,\"z\":-45}]", "true"},
				{"[]", "false"}, {"[{\"x\":1,\"y\":2,\"z\":3},{\"x\":1,\"y\":3,\"z\":3},{\"x\":1,\"y\":4,\"z\":3}]", "false"}, {"[{\"x\":1,\"y\":2}]", "false"},
				{"[{\"x\":1.5,\"y\":2,\"z\":3}]", "false"}, {"[{\"x\":1,\"y\":2,\"z\":3,\"w\":0}]", "false"}, {"[[1,2,3]]", "false"}, {"{\"x\":1,\"y\":2,\"z\":3}", "false"}}) {
			JsonObject o = base.deepCopy();                                                     // O83e: optional home.doorway
			o.getAsJsonObject("home").add("doorway", JsonParser.parseString(d[0]));
			check("home.doorway " + d[0] + (d[1].equals("true") ? " accepted" : " refused"), o.toString(), Boolean.parseBoolean(d[1]));
		}
		for (var e : bad.entrySet()) {
			JsonObject o = base.deepCopy();
			e.getValue().accept(o);
			check(e.getKey(), o.toString(), false);
		}
		// envelope_hash must be byte-identical to the mind's Python canonical JSON (link v1.1); reference computed with
		// json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False) on the same fixture.
		try (var in = Cp15Suite.class.getClassLoader().getResourceAsStream("canonical-fixture.json")) {
			String got = Envelope.sha256(Envelope.canonical(JsonParser.parseString(new String(in.readAllBytes(), StandardCharsets.UTF_8))).getBytes(StandardCharsets.UTF_8));
			JsonObject v = new JsonObject();
			v.addProperty("case", "canonical JSON hash equals the mind's Python reference (escapes, unicode, floats, -0.0, exponents)");
			v.addProperty("expected", "2a1523de1c2ce2a65479cb18fec00100c513a388747279e00fcdf18d1f16dca0");
			v.addProperty("observed", got);
			v.addProperty("ok", got.equals("2a1523de1c2ce2a65479cb18fec00100c513a388747279e00fcdf18d1f16dca0"));
			validation.add(v);
		} catch (java.io.IOException e) {
			throw new RuntimeException(e);
		}
		check("trailing data after object", base + " {}", false);
		check("lenient JSON comment", "// note\n" + base, false);
		check("not JSON", "envelope: yes", false);
		check("empty file", "", false);
	}

	/** I28a: the RCON write guard (RconGuard), cases written out by hand; the lock is a temp file made and removed here. */
	void rconGuard() {
		java.nio.file.Path dir, lock;
		try {
			dir = java.nio.file.Files.createTempDirectory("i28a");
		} catch (java.io.IOException e) { throw new RuntimeException(e); }
		lock = dir.resolve("LANE-IN-USE.txt");
		String[] writes = {"fill 0 0 0 1 1 1 minecraft:air", "setblock 1 2 3 minecraft:stone", "/clone 0 0 0 1 1 1 5 5 5", "kill @e[type=item]",
				"tp Eve 0 64 0", "give Eve minecraft:diamond", "clear Eve", "summon minecraft:zombie", "gamerule spawn_mobs true", "time set day",
				"weather clear", "difficulty peaceful", "forceload add 0 0", "forceload remove all", "say hi", "op Eve", "function x:y",
				"effect give Eve minecraft:strength 30", "execute in minecraft:overworld run tp Eve 0 64 0", "execute as @e[tag=run] run kill @s",
				"execute positioned 1 64 1 run execute if block 1 2 3 minecraft:air run setblock 1 2 3 minecraft:stone",
				"execute store result score @s x run data get entity @s Pos[0]", "execute store success block 1 2 3 Items[0].Count int 1 if block 1 2 3 minecraft:chest",
				"eve resume", "eve reload", "evetest as Eve setblock 1 2 3 minecraft:stone", "ledger rollback range:5", "EXECUTE run setblock 1 2 3 stone"};
		String[] reads = {"execute if block 1 2 3 minecraft:air", "execute unless entity @e[type=zombie]", "execute positioned 1 64 1 run locate biome minecraft:forest",
				"execute in minecraft:overworld run forceload query", "forceload query", "list", "data get block 1 2 3", "time query daytime", "locate structure minecraft:village_plains",
				"seed", "gamerule spawn_mobs", "difficulty", "scoreboard players get @s x", "evetest query blocks {}", "evetest query repair {\"zone\":\"home\"}",
				"ledger search range:1", "save-all flush", "save-off", "save-on", "stop", "eve stop", "/eve stop",
				"effect give FED_Chairman minecraft:fire_resistance 30 0 true", "effect give FED_Chairman minecraft:slow_falling 10 0 true", "", "  "};
		for (boolean held : new boolean[]{false, true}) {
			try {
				if (held) java.nio.file.Files.writeString(lock, "until=9:99 exp=9999999999 by=gametest what=I28a");
			} catch (java.io.IOException e) { throw new RuntimeException(e); }
			for (String c : writes) rconCase((held ? "locked" : "no lock") + ", write: " + c, RconGuard.refusal(c, lock) == null, held);
			for (String c : reads) rconCase((held ? "locked" : "no lock") + ", read: " + c, RconGuard.refusal(c, lock) == null, true);
		}
		for (String c : writes) rconCase("guard off (unconfigured), write: " + c, RconGuard.refusal(c, null) == null, true);
		String[][] configs = {{"absent file", null, "off"}, {"{}", "{}", "off"}, {"absolute path", "{\"rcon_write_lock\": \"" + lock.toString().replace("\\", "\\\\") + "\"}", "on"},
				{"relative path", "{\"rcon_write_lock\": \"LANE-IN-USE.txt\"}", "refused"}, {"number", "{\"rcon_write_lock\": 5}", "refused"},
				{"unescaped Windows backslash", "{\"rcon_write_lock\": \"C:\\eve-shark\\LANE-IN-USE.txt\"}", "refused"},
				{"not JSON", "rcon_write_lock=C:/x", "refused"}};
		java.nio.file.Path cfg = dir.resolve("eve-sense.json");
		try {
			java.nio.file.Files.deleteIfExists(lock); // configs load with the lock absent: "on" = a write is refused
		} catch (java.io.IOException e) { throw new RuntimeException(e); }
		for (String[] c : configs) {
			String got;
			try {
				java.nio.file.Files.deleteIfExists(cfg);
				if (c[1] != null) java.nio.file.Files.writeString(cfg, c[1]);
				RconGuard.load(cfg);
				got = probeOn() ? "on" : "off";
			} catch (java.io.IOException | IllegalArgumentException e) {
				got = "refused";
			}
			rconCase("config " + c[0] + " -> " + c[2], got.equals(c[2]), true);
		}
		try {
			RconGuard.load(dir.resolve("absent.json")); // leave the guard off for the rest of the run
			java.nio.file.Files.deleteIfExists(cfg);
			java.nio.file.Files.deleteIfExists(lock);
			java.nio.file.Files.deleteIfExists(dir);
		} catch (java.io.IOException e) { throw new RuntimeException(e); }
	}

	/** Whether the loaded guard refuses a write right now (the test lock is absent while configs load). */
	private static boolean probeOn() {
		return RconGuard.refusal("setblock 1 2 3 minecraft:stone") != null;
	}

	private void rconCase(String name, boolean observed, boolean expected) {
		JsonObject v = new JsonObject();
		v.addProperty("case", "I28a " + name);
		v.addProperty("expected", expected ? "passes" : "refused");
		v.addProperty("observed", observed ? "passes" : "refused");
		v.addProperty("ok", observed == expected);
		validation.add(v);
	}

	void check(String name, String text, boolean expectValid) {
		String error = null;
		try {
			Envelope.parse(text.getBytes(StandardCharsets.UTF_8));
		} catch (IllegalArgumentException e) {
			error = e.getMessage();
		}
		JsonObject v = new JsonObject();
		v.addProperty("case", name);
		v.addProperty("expected", expectValid ? "accepted" : "refused");
		v.addProperty("observed", error == null ? "accepted" : "refused");
		v.addProperty("error", error);
		v.addProperty("ok", expectValid == (error == null));
		validation.add(v);
	}

	void apply(Consumer<JsonObject> edit) {
		JsonObject o = base.deepCopy();
		o.addProperty("world_id", EveSense.worldId());
		JsonObject ow = o.getAsJsonObject("owner");
		JsonArray t = new JsonArray();
		t.add(OWNER);
		ow.add("trusted_players", t);
		ow.addProperty("eve_account", EVE);
		JsonArray zones = new JsonArray();
		zones.add(zone("mine", new BlockPos(0, 0, 0), new BlockPos(3, 7, 7), "free", "free", "private"));
		zones.add(zone("home", new BlockPos(5, 0, 0), new BlockPos(7, 7, 7), "task_only", "task_only", "private"));
		o.add("zones", zones);
		edit.accept(o);
		EveSense.apply(Envelope.parse(o.toString().getBytes(StandardCharsets.UTF_8)));
	}

	JsonObject zone(String id, BlockPos relMin, BlockPos relMax, String brk, String place, String containers) {
		BlockPos a = abs(relMin), b = abs(relMax);
		JsonObject z = new JsonObject();
		z.addProperty("id", id);
		z.addProperty("dim", "minecraft:overworld");
		z.add("min", xyz(Math.min(a.getX(), b.getX()), Math.min(a.getY(), b.getY()), Math.min(a.getZ(), b.getZ())));
		z.add("max", xyz(Math.max(a.getX(), b.getX()), Math.max(a.getY(), b.getY()), Math.max(a.getZ(), b.getZ())));
		z.addProperty("break", brk);
		z.addProperty("place", place);
		z.addProperty("containers", containers);
		z.addProperty("snapshot", false);
		return z;
	}

	void tag(JsonObject o, BlockPos rel, String value) {
		BlockPos a = abs(rel);
		o.getAsJsonObject("containers").getAsJsonObject("tags").addProperty(Envelope.key("minecraft:overworld", a.getX(), a.getY(), a.getZ()), value);
	}

	static JsonObject xyz(int x, int y, int z) {
		JsonObject o = new JsonObject();
		o.addProperty("x", x);
		o.addProperty("y", y);
		o.addProperty("z", z);
		return o;
	}

	BlockPos abs(BlockPos rel) { return h.absolutePos(rel); }

	BlockPos abs(int x, int y, int z) { return abs(new BlockPos(x, y, z)); }

	static BlockHitResult hit(BlockPos clicked, Direction face) {
		Vec3 c = Vec3.atCenterOf(clicked);
		return new BlockHitResult(c.add(face.getStepX() * 0.5, face.getStepY() * 0.5, face.getStepZ() * 0.5), face, clicked, false);
	}

	static String contents(Container c) {
		StringBuilder sb = new StringBuilder();
		for (int i = 0; i < c.getContainerSize(); i++) if (!c.getItem(i).isEmpty()) sb.append(i).append('=').append(c.getItem(i)).append(';');
		return sb.toString();
	}

	Path writeEvidence(long start) throws Exception {
		Path root = Path.of(System.getProperty("eve.evidence.dir"), cp);
		Files.createDirectories(root);
		long n;
		try (var s = Files.list(root)) { n = s.filter(Files::isDirectory).count() + 1; }
		Path dir = root.resolve(LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyy-MM-dd-HHmm")) + "-attempt-" + n);
		Files.createDirectories(dir);
		StringBuilder jl = new StringBuilder();
		Map<String, int[]> byVariant = new LinkedHashMap<>(); // count, ok, violations
		for (Attempt a : attempts) {
			JsonObject j = new JsonObject();
			j.addProperty("n", a.n());
			j.addProperty("variant", a.variant());
			j.addProperty("action", a.action());
			j.addProperty("pos", a.pos());
			j.addProperty("expected", a.expected());
			j.addProperty("observed", a.observed());
			j.addProperty("violation", a.violation());
			j.addProperty("ok", a.ok());
			JsonArray em = new JsonArray();
			a.emitted().forEach(m -> em.add(m.getAsJsonObject("payload").deepCopy().getAsJsonObject()));
			j.add("emitted_payloads", em);
			jl.append(j).append('\n');
			int[] c = byVariant.computeIfAbsent(a.variant(), k -> new int[3]);
			c[0]++;
			if (a.ok()) c[1]++;
			if (a.violation()) c[2]++;
		}
		Files.writeString(dir.resolve("attempts.jsonl"), jl);
		StringBuilder vl = new StringBuilder();
		validation.forEach(v -> vl.append(v).append('\n'));
		Files.writeString(dir.resolve("envelope-validation.jsonl"), vl);
		JsonObject s = new JsonObject();
		s.addProperty("mod_version", EveSense.MOD_VERSION);
		s.addProperty("world_id", EveSense.worldId());
		s.addProperty("base_envelope", System.getProperty("eve.envelope"));
		s.addProperty("base_envelope_sha256", Envelope.parse(Files.readAllBytes(Path.of(System.getProperty("eve.envelope")))).hash);
		s.addProperty("attempts", attempts.size());
		s.addProperty("variants", byVariant.size());
		s.addProperty("as_expected", attempts.stream().filter(Attempt::ok).count());
		s.addProperty("violations", attempts.stream().filter(Attempt::violation).count());
		s.addProperty("denied_expected", attempts.stream().filter(a -> a.expected().equals("denied")).count());
		s.addProperty("allowed_expected", attempts.stream().filter(a -> a.expected().equals("allowed")).count());
		s.addProperty("validation_cases", validation.size());
		s.addProperty("validation_as_expected", validation.stream().filter(v -> v.get("ok").getAsBoolean()).count());
		s.addProperty("elapsed_ms", System.currentTimeMillis() - start);
		JsonObject bv = new JsonObject();
		byVariant.forEach((k, c) -> {
			JsonObject o = new JsonObject();
			o.addProperty("attempts", c[0]);
			o.addProperty("as_expected", c[1]);
			o.addProperty("violations", c[2]);
			bv.add(k, o);
		});
		s.add("by_variant", bv);
		Files.writeString(dir.resolve("summary.json"), new com.google.gson.GsonBuilder().setPrettyPrinting().create().toJson(s));
		return dir;
	}
}
