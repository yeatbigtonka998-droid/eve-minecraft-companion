package eve.sense.test;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import eve.sense.Link;
import eve.sense.Rails;
import eve.sense.EveSense;
import net.fabricmc.fabric.api.event.player.AttackEntityCallback;
import net.fabricmc.fabric.api.event.player.UseEntityCallback;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.gametest.framework.GameTestHelper;
import net.minecraft.network.chat.Component;
import net.minecraft.tags.FluidTags;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EntitySpawnReason;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.entity.EntityTypes;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.Mob;
import net.minecraft.world.entity.TamableAnimal;
import net.minecraft.world.entity.animal.sheep.Sheep;
import net.minecraft.world.entity.decoration.ItemFrame;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.entity.projectile.Projectile;
import net.minecraft.world.entity.projectile.arrow.Arrow;
import net.minecraft.world.entity.projectile.arrow.ThrownTrident;
import net.minecraft.world.entity.projectile.hurtingprojectile.windcharge.WindCharge;
import net.minecraft.world.entity.projectile.throwableitemprojectile.Snowball;
import net.minecraft.world.entity.projectile.throwableitemprojectile.ThrownEgg;
import net.minecraft.world.entity.projectile.throwableitemprojectile.ThrownLingeringPotion;
import net.minecraft.world.entity.projectile.throwableitemprojectile.ThrownSplashPotion;
import net.minecraft.world.entity.vehicle.VehicleEntity;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.item.alchemy.PotionContents;
import net.minecraft.world.item.alchemy.Potions;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.ButtonBlock;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.AttachFace;
import net.minecraft.world.level.block.state.properties.BlockStateProperties;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.Vec3;

import java.util.ArrayList;
import java.util.List;
import java.util.function.Predicate;
import java.util.function.Supplier;

/**
 * CP-16 event-level suite (S class): protected entities and hazards. Eve is Cp15Suite's scripted FakePlayer. Direct attacks
 * go through AttackEntityCallback and then Player.attack, as the network handler does; sweeps are real Player.attack sweeps
 * (full strength, on the ground, a sword); projectiles are real entities owned by Eve, ticked until they hit; buckets and
 * hazard items go through ServerPlayerGameMode. Layout as Cp15Suite: mine x 0..3 (free), gap x 4, home x 5..7 (task_only).
 */
public class Cp16Suite extends Cp15Suite {
	private final List<Entity> spawned = new ArrayList<>();
	private boolean sweepReached;

	void cp16Hazards(GameTestHelper helper) {
		suite(helper, "CP-16", this::run16);
	}

	void run16() {
		variant = "H01 direct attack on a protected entity";
		for (int i = 0; i < 3; i++) attack("villager", () -> mob(EntityTypes.VILLAGER), false);
		for (int i = 0; i < 2; i++) attack("iron golem", () -> mob(EntityTypes.IRON_GOLEM), false);
		for (int i = 0; i < 2; i++) attack("tamed wolf", () -> tame(mob(EntityTypes.WOLF)), false);
		for (int i = 0; i < 3; i++) attack("named pig", () -> named(mob(EntityTypes.PIG)), false);
		for (int i = 0; i < 2; i++) attack("armor stand", () -> plain(EntityTypes.ARMOR_STAND, 2.5, 3.5), false);
		for (int i = 0; i < 2; i++) attack("item frame with a diamond", this::itemFrame, false);
		for (int i = 0; i < 2; i++) attack("boat", () -> plain(EntityTypes.OAK_BOAT, 2.5, 3.5), false);
		for (int i = 0; i < 2; i++) attack("minecart", () -> plain(EntityTypes.MINECART, 2.5, 3.5), false);
		for (int i = 0; i < 2; i++) attack("another player", () -> stranger, false);
		variant = "C01 control: direct attack on an unprotected pig";
		for (int i = 0; i < 2; i++) attack("unnamed pig", () -> mob(EntityTypes.PIG), true);

		variant = "H02 sweep: protected secondary beside an unprotected primary";
		for (int i = 0; i < 3; i++) sweep("villager", () -> place(mob(EntityTypes.VILLAGER), 2.5, 4.6));
		for (int i = 0; i < 3; i++) sweep("named pig", () -> place(named(mob(EntityTypes.PIG)), 2.5, 4.6));
		for (int i = 0; i < 2; i++) sweep("armor stand", () -> plain(EntityTypes.ARMOR_STAND, 2.5, 4.6));

		variant = "H03 arrow at a protected entity";
		for (int i = 0; i < 3; i++) shoot("arrow at villager", () -> mob(EntityTypes.VILLAGER), this::arrow, false);
		for (int i = 0; i < 3; i++) shoot("arrow at named pig", () -> named(mob(EntityTypes.PIG)), this::arrow, false);
		for (int i = 0; i < 2; i++) shoot("arrow at armor stand", () -> plain(EntityTypes.ARMOR_STAND, 2.5, 3.5), this::arrow, false);
		for (int i = 0; i < 2; i++) shoot("arrow at iron golem", () -> mob(EntityTypes.IRON_GOLEM), this::arrow, false);
		variant = "C03 control: arrow at an unprotected pig";
		for (int i = 0; i < 2; i++) shoot("arrow at unnamed pig", () -> mob(EntityTypes.PIG), this::arrow, true);

		variant = "H04 thrown projectiles at a protected entity";
		for (int i = 0; i < 2; i++) shoot("snowball at villager", () -> mob(EntityTypes.VILLAGER), () -> new Snowball(level, eve, new ItemStack(Items.SNOWBALL)), false);
		for (int i = 0; i < 2; i++) shoot("egg at named pig", () -> named(mob(EntityTypes.PIG)), () -> new ThrownEgg(level, eve, new ItemStack(Items.EGG)), false);
		for (int i = 0; i < 2; i++) shoot("trident at villager", () -> mob(EntityTypes.VILLAGER), () -> new ThrownTrident(level, eve, new ItemStack(Items.TRIDENT)), false);

		variant = "H05 harming potion landing beside a protected entity";
		for (int i = 0; i < 3; i++) potion("splash beside villager", () -> mob(EntityTypes.VILLAGER), false, false);
		for (int i = 0; i < 2; i++) potion("lingering beside villager", () -> mob(EntityTypes.VILLAGER), true, false);
		variant = "C04 control: splash beside an unprotected pig";
		potion("splash beside unnamed pig", () -> mob(EntityTypes.PIG), false, true);

		variant = "H06 projectile block reaction where Eve may not break";
		for (int i = 0; i < 3; i++) arrowAt("arrow into decorated pot in home", 6, 2 + i, Blocks.DECORATED_POT, false, false, st -> !st.is(Blocks.DECORATED_POT));
		for (int i = 0; i < 3; i++) arrowAt("arrow into decorated pot in the gap", 4, 2 + i, Blocks.DECORATED_POT, false, false, st -> !st.is(Blocks.DECORATED_POT));
		for (int i = 0; i < 2; i++) arrowAt("arrow into target block in home", 6, 5 + i, Blocks.TARGET, false, false, Cp16Suite::powered);
		variant = "C05 control: projectile block reaction in the mine";
		for (int i = 0; i < 2; i++) arrowAt("arrow into decorated pot in mine", 1, 2 + i, Blocks.DECORATED_POT, false, true, st -> !st.is(Blocks.DECORATED_POT));
		arrowAt("arrow into target block in mine", 1, 5, Blocks.TARGET, false, true, Cp16Suite::powered);

		variant = "H07 burning arrow at TNT (hazards.fire false)";
		for (int i = 0; i < 4; i++) arrowAt("burning arrow at TNT in mine", 2, 2 + i, Blocks.TNT, true, false, st -> !st.is(Blocks.TNT));
		variant = "C06 control: burning arrow at TNT, fire allowed by the owner's envelope";
		apply(o -> o.getAsJsonObject("hazards").addProperty("fire", true));
		arrowAt("burning arrow at TNT in mine, fire allowed", 2, 6, Blocks.TNT, true, true, st -> !st.is(Blocks.TNT));
		apply(o -> { });

		variant = "H08 fire items";
		for (int i = 0; i < 3; i++) useOn("fire charge on stone in mine", 1, 2 + i, Blocks.STONE, Items.FIRE_CHARGE, false, a -> level.getBlockState(a.above()).is(Blocks.FIRE));
		for (int i = 0; i < 3; i++) useOn("flint and steel on TNT in mine", 2, 2 + i, Blocks.TNT, Items.FLINT_AND_STEEL, false, a -> !level.getBlockState(a).is(Blocks.TNT));

		variant = "H09 explosives";
		for (int i = 0; i < 3; i++) useOn("TNT minecart on a rail in mine", 1, 2 + i, Blocks.RAIL, Items.TNT_MINECART, false, a -> present(EntityTypes.TNT_MINECART, a));
		for (int i = 0; i < 3; i++) useOn("end crystal on obsidian in mine", 2, 2 + i, Blocks.OBSIDIAN, Items.END_CRYSTAL, false, a -> present(EntityTypes.END_CRYSTAL, a));
		for (int i = 0; i < 2; i++) expectPlace(1, 1, 5 + i, Direction.UP, Items.TNT, false);

		variant = "H10 water and fish buckets where Eve may not place";
		for (int i = 0; i < 3; i++) bucket("water bucket in home", 6, 2 + i, Items.WATER_BUCKET, false, false);
		for (int i = 0; i < 3; i++) bucket("water bucket in the gap", 4, 2 + i, Items.WATER_BUCKET, false, false);
		for (int i = 0; i < 2; i++) bucket("cod bucket in the gap", 4, 5 + i, Items.COD_BUCKET, false, false);
		variant = "C07 control: buckets in the mine";
		for (int i = 0; i < 2; i++) bucket("water bucket in mine", 1, 2 + i, Items.WATER_BUCKET, false, true);
		bucket("empty bucket picks up water in mine", 1, 5, Items.BUCKET, true, true);

		variant = "H11 empty bucket picks up water where Eve may not break";
		for (int i = 0; i < 3; i++) bucket("pick up water in home", 6, 2 + i, Items.BUCKET, true, false);
		for (int i = 0; i < 2; i++) bucket("pick up water in the gap", 4, 2 + i, Items.BUCKET, true, false);

		variant = "O20 fall rescue: water under a falling Eve in any zone, scooped back";
		for (int i = 0; i < 2; i++) rescue("home (task_only)", 6, 5 + i);
		for (int i = 0; i < 2; i++) rescue("the gap (no zone)", 4, 5 + i);
		variant = "O20b full-speed 32-block fall: the server's packet state lags behind the use";
		for (int i = 0; i < 2; i++) stale("home: on-ground and fall 0 from a stale packet, body tag for this spot", 7, 2 + i, 0.0, true, 0, true);
		for (int i = 0; i < 2; i++) stale("gap: on-ground and fall 0 from a stale packet, body tag for this spot", 4, 7 + i - 1, 0.0, true, 0, true);
		for (int i = 0; i < 2; i++) stale("home: 32-block fall (30.3) with a stale on-ground flag, no tag", 7, 4 + i, 30.3, true, -1, true);
		variant = "H14 a tag alone is not a rescue anywhere";
		for (int i = 0; i < 2; i++) stale("home: standing, tag for a spot 3 blocks away", 7, 6 + i, 0.0, true, 3, false);
		stale("home: standing, no tag", 7, 7, 0.0, true, -1, false);

		variant = "H13 not a rescue: the ordinary rule decides";
		for (int i = 0; i < 2; i++) falling("fell only 2 blocks, in home", 6, 2 + i, 2.0, false);
		for (int i = 0; i < 2; i++) falling("8-block fall, in home, while STOPPED", 6, 4 + i, 8.0, true);

		variant = "H12 powder snow bucket where Eve may not place";
		for (int i = 0; i < 2; i++) useOn("powder snow in home", 6, 2 + i, Blocks.STONE, Items.POWDER_SNOW_BUCKET, false, a -> level.getBlockState(a.above()).is(Blocks.POWDER_SNOW));
		for (int i = 0; i < 2; i++) useOn("powder snow in the gap", 4, 2 + i, Blocks.STONE, Items.POWDER_SNOW_BUCKET, false, a -> level.getBlockState(a.above()).is(Blocks.POWDER_SNOW));
		variant = "C08 control: powder snow in the mine";
		useOn("powder snow in mine", 1, 2, Blocks.STONE, Items.POWDER_SNOW_BUCKET, true, a -> level.getBlockState(a.above()).is(Blocks.POWDER_SNOW));

		variant = "H13 wind charge bursting in the gap";
		for (int i = 0; i < 2; i++) windCharge("wind charge onto a button in the gap", 4, 2 + i, false);
		variant = "C09 control: wind charge in the mine";
		windCharge("wind charge onto a button in mine", 1, 3, true);

		variant = "H14 STOPPED gate";
		EveSense.setStopped(true);
		for (int i = 0; i < 2; i++) attack("unnamed pig while STOPPED", () -> mob(EntityTypes.PIG), false);
		for (int i = 0; i < 2; i++) shoot("arrow at unnamed pig while STOPPED", () -> mob(EntityTypes.PIG), this::arrow, false);
		for (int i = 0; i < 2; i++) bucket("water bucket in mine while STOPPED", 1, 2 + i, Items.WATER_BUCKET, false, false);
		EveSense.setStopped(false);

		variant = "H15 lava bucket (raycast use)";
		for (int i = 0; i < 2; i++) record("lava bucket in mine", null, false, this::lava);
		clear();

		animals39();
	}

	/**
	 * CP-39 (envelope v9): animals and renewables, every refusal server-side. Rules under test: mine sheep shear 2/day,
	 * kill 1/day, keep_min 1; mine cow milk 1/day (no kill); the wild ("*", x = 4 here) pig kill 5/day; one tagged
	 * farmland block at rel (2,1,6). Expected answers are written out here, not computed by the code under test.
	 */
	void animals39() {
		variant = "A00 control: no animals/farmland keys (v8 envelope) = every animal action allowed, as before";
		apply(o -> { });
		Rails.resetRateCounts();
		record("shear a sheep, no policy", null, true, () -> shear(mob(EntityTypes.SHEEP)));
		record("kill a chicken, no policy", null, true, () -> kill(mob(EntityTypes.CHICKEN)));
		clear();

		apply(o -> {
			JsonArray a = new JsonArray();
			a.add(animalRule("mine", "minecraft:sheep", 1, "shear", 2, "kill", 1));
			a.add(animalRule("mine", "minecraft:cow", 0, "milk", 1));
			a.add(animalRule("*", "minecraft:pig", 0, "kill", 5));
			o.add("animals", a);
			JsonArray f = new JsonArray();
			JsonObject b = new JsonObject();
			BlockPos t = abs(2, 1, 6);
			b.addProperty("id", "plot");
			b.addProperty("dim", "minecraft:overworld");
			b.add("min", xyz(t.getX(), t.getY(), t.getZ()));
			b.add("max", xyz(t.getX(), t.getY(), t.getZ()));
			f.add(b);
			o.add("farmland", f);
		});
		Rails.resetRateCounts();

		variant = "A01 shear: sheep in the mine, daily quota 2";
		for (int i = 0; i < 2; i++) record("shear sheep #" + (i + 1), null, true, () -> shear(mob(EntityTypes.SHEEP)));
		record("shear a 3rd sheep (daily quota used up)", null, false, () -> shear(mob(EntityTypes.SHEEP)));
		checkCase("permission.check shear agrees: quota used up", "shear", mob(EntityTypes.SHEEP), false, "animal:quota:mine:minecraft:sheep:shear");
		clear();

		variant = "A02 milk: cow in the mine, daily quota 1; cows have no kill rule";
		record("milk a cow", null, true, () -> milk(mob(EntityTypes.COW)));
		record("milk a 2nd cow (daily quota used up)", null, false, () -> milk(mob(EntityTypes.COW)));
		record("kill a cow (kill not allowed for cows)", null, false, () -> kill(mob(EntityTypes.COW)));
		clear();

		variant = "A03 breeding stock: sheep keep_min 1, kill quota 1/day";
		record("kill the only sheep in the mine (keep_min 1)", null, false, () -> kill(mob(EntityTypes.SHEEP)));
		clear();
		record("kill one of two sheep in the mine", null, true, () -> { place(mob(EntityTypes.SHEEP), 1.5, 5.5); return kill(mob(EntityTypes.SHEEP)); });
		clear();
		record("kill one of two sheep again (daily kill quota used up)", null, false, () -> { place(mob(EntityTypes.SHEEP), 1.5, 5.5); return kill(mob(EntityTypes.SHEEP)); });
		clear();
		Rails.resetRateCounts(); // fresh quotas: the next cases test baby, leashed and adult-only stock
		record("kill a baby sheep (never)", null, false, () -> { place(mob(EntityTypes.SHEEP), 1.5, 5.5); place(mob(EntityTypes.SHEEP), 1.5, 6.5); return kill(baby(mob(EntityTypes.SHEEP))); });
		clear();
		record("kill a leashed sheep (never)", null, false, () -> { place(mob(EntityTypes.SHEEP), 1.5, 5.5); place(mob(EntityTypes.SHEEP), 1.5, 6.5); return kill(leashed(mob(EntityTypes.SHEEP))); });
		clear();
		record("kill one adult sheep beside a baby (stock = adults: 1 <= keep_min 1)", null, false, () -> { baby(place(mob(EntityTypes.SHEEP), 1.5, 5.5)); return kill(mob(EntityTypes.SHEEP)); });
		clear();

		variant = "A04 no rule: a species or place without an animals rule is refused; the wild rule covers only places in no zone";
		record("kill a chicken in the mine (no rule)", null, false, () -> kill(mob(EntityTypes.CHICKEN)));
		record("kill a pig in the home zone (only a wild rule for pigs)", null, false, () -> kill(place(mob(EntityTypes.PIG), 6.5, 3.5)));
		record("kill a pig in the wild (x 4, no zone)", null, true, () -> kill(place(mob(EntityTypes.PIG), 4.5, 3.5)));
		checkCase("permission.check milk agrees: a cow in home has no rule", "milk", place(mob(EntityTypes.COW), 6.5, 3.5), false, "animal:no_rule");
		checkCase("permission.check attack agrees: a wild pig is allowed", "attack", place(mob(EntityTypes.PIG), 4.5, 3.5), true, "animal:kill");
		clear();

		variant = "A05 crops only on tagged farmland (rel 2,1,6 tagged; 1,1,6 not)";
		level.setBlockAndUpdate(abs(1, 1, 6), Blocks.FARMLAND.defaultBlockState());
		level.setBlockAndUpdate(abs(2, 1, 6), Blocks.FARMLAND.defaultBlockState());
		record("plant wheat on the tagged farmland", new BlockPos(2, 2, 6), true, () -> plant(new BlockPos(2, 1, 6)));
		record("plant wheat on untagged farmland in the mine (place free)", new BlockPos(1, 2, 6), false, () -> plant(new BlockPos(1, 1, 6)));
		level.setBlockAndUpdate(abs(1, 1, 6), Blocks.AIR.defaultBlockState());
		level.setBlockAndUpdate(abs(2, 1, 6), Blocks.AIR.defaultBlockState());

		apply(o -> { });
		Rails.resetRateCounts();
	}

	private static JsonObject animalRule(String zone, String species, int keepMin, Object... daily) {
		JsonObject r = new JsonObject(), d = new JsonObject();
		r.addProperty("zone", zone);
		r.addProperty("species", species);
		for (int i = 0; i < daily.length; i += 2) d.addProperty((String) daily[i], (Integer) daily[i + 1]);
		r.add("daily", d);
		r.addProperty("keep_min", keepMin);
		return r;
	}

	/** What the network handler does for an interact packet: UseEntityCallback, then Player.interactOn if it passed. */
	private void useOn(Entity t, ItemStack item) {
		eve.setItemInHand(InteractionHand.MAIN_HAND, item);
		if (UseEntityCallback.EVENT.invoker().interact(eve, level, InteractionHand.MAIN_HAND, t, null) == InteractionResult.PASS)
			eve.interactOn(t, InteractionHand.MAIN_HAND, Vec3.ZERO);
	}

	private boolean shear(Entity t) {
		useOn(t, new ItemStack(Items.SHEARS));
		eve.setItemInHand(InteractionHand.MAIN_HAND, ItemStack.EMPTY);
		return ((Sheep) t).isSheared();
	}

	private boolean milk(Entity t) {
		useOn(t, new ItemStack(Items.BUCKET));
		boolean milked = eve.getMainHandItem().is(Items.MILK_BUCKET);
		eve.setItemInHand(InteractionHand.MAIN_HAND, ItemStack.EMPTY);
		return milked;
	}

	private static Mob baby(Mob m) {
		((net.minecraft.world.entity.AgeableMob) m).setBaby(true);
		return m;
	}

	private Mob leashed(Mob m) {
		((net.minecraft.world.entity.Leashable) m).setLeashedTo(eve, true);
		return m;
	}

	/** One hit at 1 HP: allowed = the animal dies (the kill counts), denied = it stays alive. */
	private boolean kill(Entity t) {
		((LivingEntity) t).setHealth(1f);
		hit(t, ItemStack.EMPTY);
		return !t.isAlive();
	}

	/** permission.check over the link agrees with enforcement (no permission.denied: a check is read-only). */
	private void checkCase(String name, String effect, Entity t, boolean expectAllowed, String expectRule) {
		JsonObject q = new JsonObject();
		q.addProperty("actor", "eve");
		q.addProperty("effect", effect);
		q.addProperty("dim", "minecraft:overworld");
		q.addProperty("entity_id", t.getId());
		JsonObject r = Link.INSTANCE.check(q);
		JsonObject v = new JsonObject();
		v.addProperty("case", "CP-39 " + name);
		v.addProperty("expected", (expectAllowed ? "allowed " : "denied ") + expectRule);
		v.addProperty("observed", (r.get("allowed").getAsBoolean() ? "allowed " : "denied ") + r.get("rule_id").getAsString());
		v.addProperty("ok", r.get("allowed").getAsBoolean() == expectAllowed && r.get("rule_id").getAsString().equals(expectRule));
		validation.add(v);
	}

	// ---- actions; each returns whether the effect happened in the world ----

	private void attack(String what, Supplier<Entity> make, boolean allowed) {
		record("attack " + what, null, allowed, () -> {
			Entity t = make.get();
			float h0 = health(t);
			for (int i = 0; i < 2; i++) hit(t, ItemStack.EMPTY); // twice: an armor stand only breaks on a second quick hit
			boolean harmed = harmed(t, h0);
			clear();
			return harmed;
		});
	}

	/** One real sweep; the denial attempt is the protected secondary, the control right after is the cow it must reach. */
	private void sweep(String what, Supplier<Entity> secondary) {
		record("sweep: pig primary, " + what + " beside it", null, false, () -> {
			Mob pig = mob(EntityTypes.PIG);
			Mob cow = place(mob(EntityTypes.COW), 2.5, 2.4);
			Entity s = secondary.get();
			float hp = pig.getHealth(), hc = cow.getHealth(), hs = health(s);
			hit(pig, new ItemStack(Items.IRON_SWORD));
			sweepReached = pig.getHealth() < hp && cow.getHealth() < hc;
			boolean harmed = harmed(s, hs);
			clear();
			return harmed;
		});
		String v = variant;
		variant = "C02 control: the same sweep reached the unprotected cow";
		record("sweep reached the cow (" + what + " run)", null, true, () -> sweepReached);
		variant = v;
	}

	private void shoot(String what, Supplier<Entity> make, Supplier<Projectile> projectile, boolean allowed) {
		record(what, null, allowed, () -> {
			Entity t = make.get();
			float h0 = health(t);
			Vec3 c = t.position().add(0, t.getBbHeight() * 0.5, 0);
			launch(projectile.get(), c.add(2, 0, 0), c);
			boolean harmed = harmed(t, h0);
			clear();
			return harmed;
		});
	}

	private void potion(String what, Supplier<Entity> make, boolean lingering, boolean allowed) {
		record(what, null, allowed, () -> {
			Entity t = make.get();
			float h0 = health(t);
			ItemStack s = PotionContents.createItemStack(lingering ? Items.LINGERING_POTION : Items.SPLASH_POTION, Potions.HARMING);
			Projectile pr = lingering ? new ThrownLingeringPotion(level, eve, s) : new ThrownSplashPotion(level, eve, s);
			Vec3 feet = t.position();
			launch(pr, feet.add(1, 2, 0), feet.add(1, -0.5, 0)); // lands one block beside it
			boolean cloud = present(EntityTypes.AREA_EFFECT_CLOUD, BlockPos.containing(feet));
			boolean harmed = harmed(t, h0) || cloud;
			clear();
			return harmed;
		});
	}

	private void arrowAt(String what, int x, int z, Block block, boolean burning, boolean allowed, Predicate<BlockState> reacted) {
		BlockPos rel = new BlockPos(x, 1, z);
		record(what, rel, allowed, () -> {
			BlockPos a = abs(rel);
			level.setBlockAndUpdate(a.below(), Blocks.STONE.defaultBlockState());
			level.setBlockAndUpdate(a, block.defaultBlockState());
			for (int dy = 1; dy <= 3; dy++) level.setBlockAndUpdate(a.above(dy), Blocks.AIR.defaultBlockState());
			Arrow ar = arrow();
			if (burning) ar.igniteForSeconds(100);
			Vec3 c = Vec3.atCenterOf(a);
			launch(ar, c.add(0, 2.5, 0), c);
			boolean r = reacted.test(level.getBlockState(a));
			level.setBlockAndUpdate(a, Blocks.AIR.defaultBlockState());
			clear(); // also any primed TNT, before its fuse runs out
			return r;
		});
	}

	private void useOn(String what, int x, int z, Block on, Item item, boolean allowed, Predicate<BlockPos> effect) {
		BlockPos rel = new BlockPos(x, 1, z);
		record(what, rel, allowed, () -> {
			BlockPos a = abs(rel);
			level.setBlockAndUpdate(a.below(), Blocks.STONE.defaultBlockState());
			level.setBlockAndUpdate(a, on.defaultBlockState());
			level.setBlockAndUpdate(a.above(), Blocks.AIR.defaultBlockState());
			ItemStack s = new ItemStack(item);
			eve.setItemInHand(InteractionHand.MAIN_HAND, s);
			eve.gameMode.useItemOn(eve, level, s, InteractionHand.MAIN_HAND, hit(a, Direction.UP));
			eve.setItemInHand(InteractionHand.MAIN_HAND, ItemStack.EMPTY);
			boolean e = effect.test(a);
			level.setBlockAndUpdate(a.above(), Blocks.AIR.defaultBlockState());
			level.setBlockAndUpdate(a, Blocks.AIR.defaultBlockState());
			clear();
			return e;
		});
	}

	/** Eve stands on the floor looking straight down; the bucket's own raycast picks the block (as BucketItem.use does). */
	private void bucket(String what, int x, int z, Item item, boolean pickup, boolean allowed) {
		BlockPos rel = new BlockPos(x, 1, z);
		record(what, rel.above(), allowed, () -> {
			BlockPos floor = abs(rel), cell = floor.above();
			level.setBlockAndUpdate(floor, Blocks.STONE.defaultBlockState());
			level.setBlockAndUpdate(cell, pickup ? Blocks.WATER.defaultBlockState() : Blocks.AIR.defaultBlockState());
			eve.snapTo(floor.getX() + 0.5, floor.getY() + 1, floor.getZ() + 0.5, 0, 90);
			ItemStack b = new ItemStack(item);
			eve.setItemInHand(InteractionHand.MAIN_HAND, b);
			eve.gameMode.useItem(eve, level, b, InteractionHand.MAIN_HAND);
			boolean water = level.getFluidState(cell).is(FluidTags.WATER);
			eve.setItemInHand(InteractionHand.MAIN_HAND, ItemStack.EMPTY);
			eve.snapTo(Vec3.atCenterOf(abs(4, 1, 3)));
			level.setBlockAndUpdate(cell, Blocks.AIR.defaultBlockState()); // no water left to flow
			clear();
			return pickup != water;
		});
	}

	/** Eve 1.5 blocks above the floor cell, falling `fall` blocks so far, looking straight down. */
	private BlockPos fallOver(int x, int z, double fall) {
		BlockPos floor = abs(new BlockPos(x, 1, z)), cell = floor.above();
		level.setBlockAndUpdate(floor, Blocks.STONE.defaultBlockState());
		level.setBlockAndUpdate(cell, Blocks.AIR.defaultBlockState());
		eve.snapTo(floor.getX() + 0.5, cell.getY() + 1.5, floor.getZ() + 0.5, 0, 90);
		eve.setOnGround(false);
		eve.fallDistance = fall;
		return cell;
	}

	/** As the client does at speed: a use-on-block packet on the floor (or the water) first, then the use-item packet. */
	private boolean use(Item item, BlockPos cell) {
		ItemStack b = new ItemStack(item);
		eve.setItemInHand(InteractionHand.MAIN_HAND, b);
		BlockPos clicked = item == Items.WATER_BUCKET ? cell.below() : cell;
		eve.gameMode.useItemOn(eve, level, b, InteractionHand.MAIN_HAND, hit(clicked, Direction.UP));
		eve.gameMode.useItem(eve, level, b, InteractionHand.MAIN_HAND);
		eve.setItemInHand(InteractionHand.MAIN_HAND, ItemStack.EMPTY);
		return level.getFluidState(cell).is(FluidTags.WATER);
	}

	private void rescue(String where, int x, int z) {
		BlockPos rel = new BlockPos(x, 2, z);
		BlockPos[] cell = {null};
		record("rescue water placed under a falling Eve, " + where, rel, true, () -> use(Items.WATER_BUCKET, cell[0] = fallOver(x, z, 8.0)) && !emittedType("permission.denied")); // no denial on either packet
		record("rescue water scooped back, " + where, rel, true, () -> !use(Items.BUCKET, cell[0]) && !emittedType("permission.denied"));
		record("a second scoop there is an ordinary pickup again, " + where, rel, false, () -> {
			level.setBlockAndUpdate(cell[0], Blocks.WATER.defaultBlockState());
			boolean picked = !use(Items.BUCKET, cell[0]);
			level.setBlockAndUpdate(cell[0], Blocks.AIR.defaultBlockState());
			return picked;
		});
		eve.fallDistance = 0;
		eve.setOnGround(true);
		eve.snapTo(Vec3.atCenterOf(abs(4, 1, 3)));
		clear();
	}

	/** tagOffset: -1 = no body tag; 0 = a tag for the water's own cell; n = a tag n blocks east of it. */
	private void stale(String what, int x, int z, double fall, boolean onGround, int tagOffset, boolean allowed) {
		record(what, new BlockPos(x, 2, z), allowed, () -> {
			BlockPos cell = fallOver(x, z, fall);
			eve.setOnGround(onGround);
			if (tagOffset >= 0) Rails.rescueTag(eve, cell.east(tagOffset));
			boolean water = use(Items.WATER_BUCKET, cell) && !emittedType("permission.denied");
			if (water) use(Items.BUCKET, cell);                                                   // the scoop-back
			level.setBlockAndUpdate(cell, Blocks.AIR.defaultBlockState());
			Rails.rescueTag(eve, cell.above(100));                                                // spend the tag
			eve.fallDistance = 0;
			eve.setOnGround(true);
			eve.snapTo(Vec3.atCenterOf(abs(4, 1, 3)));
			clear();
			return water;
		});
	}

	private void falling(String what, int x, int z, double fall, boolean stopped) {
		record(what, new BlockPos(x, 2, z), false, () -> {
			if (stopped) cmd(owner, "eve stop");
			BlockPos cell = fallOver(x, z, fall);
			boolean water = use(Items.WATER_BUCKET, cell);
			if (stopped) cmd(owner, "eve resume");
			level.setBlockAndUpdate(cell, Blocks.AIR.defaultBlockState());
			eve.fallDistance = 0;
			eve.setOnGround(true);
			eve.snapTo(Vec3.atCenterOf(abs(4, 1, 3)));
			clear();
			return water;
		});
	}

	private void windCharge(String what, int x, int z, boolean allowed) {
		BlockPos rel = new BlockPos(x, 1, z);
		record(what, rel, allowed, () -> {
			BlockPos a = abs(rel);
			level.setBlockAndUpdate(a.below(), Blocks.STONE.defaultBlockState());
			level.setBlockAndUpdate(a, Blocks.STONE_BUTTON.defaultBlockState().setValue(ButtonBlock.FACE, AttachFace.FLOOR));
			for (int dy = 1; dy <= 3; dy++) level.setBlockAndUpdate(a.above(dy), Blocks.AIR.defaultBlockState());
			Vec3 c = Vec3.atBottomCenterOf(a);
			WindCharge w = new WindCharge(eve, level, c.x, c.y + 2.5, c.z);
			launch(w, c.add(0, 2.5, 0), c.add(0, -0.5, 0));
			boolean pressed = level.getBlockState(a).is(Blocks.STONE_BUTTON) && level.getBlockState(a).getValue(ButtonBlock.POWERED);
			level.setBlockAndUpdate(a, Blocks.AIR.defaultBlockState());
			clear();
			return pressed;
		});
	}

	// ---- helpers ----

	/** What ServerGamePacketListenerImpl does for an attack packet: AttackEntityCallback, then Player.attack if it passed. */
	private void hit(Entity t, ItemStack weapon) {
		eve.setItemInHand(InteractionHand.MAIN_HAND, weapon);
		eve.charge();
		if (AttackEntityCallback.EVENT.invoker().interact(eve, level, InteractionHand.MAIN_HAND, t, null) == InteractionResult.PASS) eve.attack(t);
		eve.setItemInHand(InteractionHand.MAIN_HAND, ItemStack.EMPTY);
	}

	private <T extends Projectile> T launch(T pr, Vec3 from, Vec3 to) {
		pr.setPos(from);
		Vec3 d = to.subtract(from);
		pr.shoot(d.x, d.y, d.z, 1.2f, 0f);
		level.addFreshEntity(pr);
		spawned.add(pr);
		for (int i = 0; i < 40 && !pr.isRemoved(); i++) pr.tick();
		return pr;
	}

	private Arrow arrow() {
		return new Arrow(level, eve, new ItemStack(Items.ARROW), null);
	}

	private <T extends Entity> T plain(EntityType<T> type, double x, double z) {
		T e = type.create(level, EntitySpawnReason.COMMAND);
		e.snapTo(h.absoluteVec(new Vec3(x, 1, z)));
		level.addFreshEntity(e);
		spawned.add(e);
		return e;
	}

	private Mob mob(EntityType<? extends Mob> type) {
		Mob m = plain(type, 2.5, 3.5);
		m.setNoAi(true);
		return m;
	}

	private <T extends Entity> T place(T e, double x, double z) {
		e.snapTo(h.absoluteVec(new Vec3(x, 1, z)));
		return e;
	}

	private static Mob named(Mob m) {
		m.setCustomName(Component.literal("Pinky"));
		return m;
	}

	private static Mob tame(Mob m) {
		((TamableAnimal) m).setTame(true, false);
		return m;
	}

	private Entity itemFrame() {
		level.setBlockAndUpdate(abs(1, 1, 3), Blocks.STONE.defaultBlockState()); // the wall it hangs on
		ItemFrame f = new ItemFrame(level, abs(2, 1, 3), Direction.EAST);
		f.setItem(new ItemStack(Items.DIAMOND));
		level.addFreshEntity(f);
		spawned.add(f);
		return f;
	}

	private static float health(Entity e) {
		return e instanceof LivingEntity le ? le.getHealth() : 0;
	}

	private boolean harmed(Entity e, float h0) {
		if (e.isRemoved()) return true;
		if (e instanceof LivingEntity le) return le.getHealth() < h0 || le.getLastHurtByMob() == eve;
		if (e instanceof VehicleEntity v) return v.getDamage() > 0;
		if (e instanceof ItemFrame f) return f.getItem().isEmpty();
		return false;
	}

	private static boolean powered(BlockState st) {
		return st.is(Blocks.TARGET) && st.getValue(BlockStateProperties.POWER) > 0;
	}

	private boolean present(EntityType<?> type, BlockPos at) {
		return !level.getEntities(type, new AABB(at).inflate(5), e -> true).isEmpty();
	}

	/** Every non-player entity in and around the test area (spawned mobs, drops, clouds, primed TNT, minecarts, crystals). */
	private void clear() {
		AABB box = new AABB(Vec3.atLowerCornerOf(abs(-4, -1, -4)), Vec3.atLowerCornerOf(abs(12, 10, 12)));
		for (Entity e : level.getEntities((Entity) null, box, e -> !(e instanceof Player))) e.discard();
		spawned.forEach(Entity::discard);
		spawned.clear();
		level.setBlockAndUpdate(abs(1, 1, 3), Blocks.AIR.defaultBlockState());
	}
}
