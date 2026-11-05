package eve.skills;

import net.minecraft.core.BlockPos;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.*;

/**
 * S93: shear_sheep / milk_cow / harvest_crop (registry 0.16.0 draft) - their pure rules (TendAnimals.why/pick/tool,
 * HarvestReplant.wanted/badArea/replantWhy) driven over a synthetic herd and field. The small drivers stand in for the tick
 * loops only (body's use_item/mine_block, walking, pickups are the live run's job).
 */
class FarmWorldTest {
	static TendAnimals.Animal sheep(double d) { return new TendAnimals.Animal(true, true, false, true, false, true, false, false, false, false, d); }

	static TendAnimals.Animal cow(double d) { return new TendAnimals.Animal(true, false, true, true, false, false, false, false, false, false, d); }

	/** Tends every eligible granted id, nearest first; returns the order; fills refused id -> rule id. */
	static List<Integer> tend(boolean wool, Map<Integer, TendAnimals.Animal> herd, Map<Integer, String> refused) {
		Set<Integer> ids = new LinkedHashSet<>(herd.keySet()), tried = new LinkedHashSet<>();
		List<Integer> order = new ArrayList<>();
		for (int i = 0; i < 64; i++) {
			Integer id = TendAnimals.pick(wool, ids, tried, herd, (r, why) -> { assertNull(refused.put(r, why), "refused once: " + r); tried.add(r); });
			if (id == null) return order;
			tried.add(id);
			order.add(id);
			TendAnimals.Animal a = herd.get(id); // sheared now: wool regrows later, not in this call
			herd.put(id, new TendAnimals.Animal(a.present(), a.sheep(), a.cow(), a.inZone(), a.baby(), false, a.named(), a.tame(), a.leashed(), a.guarded(), a.distSq()));
		}
		return order;
	}

	@Test
	void shearsTheGrantedAdultsNearestFirstAndRefusesTheRestWithSensesRuleIds() {
		Map<Integer, TendAnimals.Animal> herd = new LinkedHashMap<>();
		herd.put(1, sheep(81));
		herd.put(2, sheep(16));
		herd.put(3, new TendAnimals.Animal(true, true, false, true, true, true, false, false, false, false, 4));   // a lamb
		herd.put(4, new TendAnimals.Animal(true, true, false, true, false, true, true, false, false, false, 4));   // named
		herd.put(5, new TendAnimals.Animal(true, true, false, true, false, true, false, false, false, true, 4));   // sense's grant: protected
		herd.put(6, new TendAnimals.Animal(true, true, false, true, false, true, false, false, true, false, 4));   // leashed
		herd.put(7, new TendAnimals.Animal(true, true, false, true, false, false, false, false, false, false, 4)); // sheared already
		herd.put(8, new TendAnimals.Animal(true, true, false, false, false, true, false, false, false, false, 4)); // outside the zone
		herd.put(9, new TendAnimals.Animal(false, false, false, false, false, false, false, false, false, false, 0)); // gone
		herd.put(10, cow(1));                                                                                       // not a sheep
		Map<Integer, String> refused = new HashMap<>();
		assertEquals(List.of(2, 1), tend(true, herd, refused));
		assertEquals(Map.of(3, "animal:baby", 4, "protected:named", 5, "protected:pets", 6, "animal:leashed", 7, "client:sheared",
				8, "client:out_of_zone", 9, "client:gone", 10, "client:wrong_type"), refused);
		assertEquals("minecraft:shears", TendAnimals.tool(true));
	}

	@Test
	void milksCowsOnlyAndATamedOrNamedOneNever() {
		Map<Integer, TendAnimals.Animal> herd = new LinkedHashMap<>();
		herd.put(1, cow(25));
		herd.put(2, new TendAnimals.Animal(true, false, true, true, true, false, false, false, false, false, 1)); // calf
		herd.put(3, new TendAnimals.Animal(true, false, true, true, false, false, true, false, false, false, 1)); // named
		herd.put(4, new TendAnimals.Animal(true, false, true, true, false, false, false, true, false, false, 1)); // tame
		herd.put(5, sheep(1));
		Map<Integer, String> refused = new HashMap<>();
		assertEquals(List.of(1), tend(false, herd, refused), "a cow needs no shearable state");
		assertEquals(Map.of(2, "animal:baby", 3, "protected:named", 4, "protected:pets", 5, "client:wrong_type"), refused);
		assertEquals("minecraft:bucket", TendAnimals.tool(false));
	}

	@Test
	void aUseWithNoEffectIsNotRetried() { // O282: lane B nogrant: 8 useless tries, then not_found
		assertTrue(TendAnimals.noEffect("nothing_to_use"));
		assertFalse(TendAnimals.noEffect("out_of_range"), "she moved or it did: walk again");
		assertFalse(TendAnimals.noEffect("obstructed"));
		assertFalse(TendAnimals.noEffect(null));
		assertEquals("server_denied?", TendAnimals.SERVER_DENIED);
	}

	record Plot(String crop, int age, boolean farmland) {}

	/** harvest_crop's harvest then pooled replant; drops = seeds each harvested plot gives. Returns [harvested, replanted, notReplanted]. */
	static List<List<BlockPos>> harvest(Map<BlockPos, Plot> field, String crop, int limit, boolean replant, Map<BlockPos, Integer> drops) {
		List<BlockPos> harvested = new ArrayList<>(), replanted = new ArrayList<>(), not = new ArrayList<>();
		int seeds = 0;
		for (Map.Entry<BlockPos, Plot> e : field.entrySet()) {
			if (harvested.size() >= limit) break;
			Plot p = e.getValue();
			if (!HarvestReplant.wanted(p.crop(), p.age(), p.farmland(), crop, true)) continue;
			harvested.add(e.getKey());
			seeds += drops.getOrDefault(e.getKey(), 1);
		}
		if (replant) for (BlockPos q : harvested) { // after the whole harvest, from the pooled drops (a9)
			String why = HarvestReplant.replantWhy(field.get(q).crop(), field.get(q).farmland(), seeds);
			if (why == null) { seeds--; replanted.add(q); } else not.add(q);
		}
		return List.of(harvested, replanted, not);
	}

	static Map<BlockPos, Plot> field() {
		Map<BlockPos, Plot> f = new LinkedHashMap<>();
		for (int x = 0; x < 3; x++) f.put(new BlockPos(x, 64, 0), new Plot("minecraft:wheat", 7, true)); // ripe wheat
		f.put(new BlockPos(0, 64, 1), new Plot("minecraft:wheat", 6, true));                              // one stage short
		f.put(new BlockPos(1, 64, 1), new Plot("minecraft:wheat", 7, false));                             // ripe, on dirt
		f.put(new BlockPos(2, 64, 1), new Plot("minecraft:carrots", 7, true));
		f.put(new BlockPos(0, 64, 2), new Plot("minecraft:potatoes", 3, true));
		f.put(new BlockPos(1, 64, 2), new Plot("minecraft:melon_stem", 7, true));                         // not a SEED crop
		return f;
	}

	@Test
	void harvestsOnlyRipeCropsOnFarmlandAndReplantsFromThePooledDrops() {
		var r = harvest(field(), null, 1024, true, Map.of());
		assertEquals(List.of(new BlockPos(0, 64, 0), new BlockPos(1, 64, 0), new BlockPos(2, 64, 0), new BlockPos(2, 64, 1)), r.get(0),
				"age 7 on farmland only: not age 6, not on dirt, not immature potatoes");
		assertEquals(r.get(0), r.get(1), "one seed each: every plot replanted");
		var shortOfSeed = harvest(field(), "minecraft:wheat", 1024, true, Map.of(new BlockPos(1, 64, 0), 0));
		assertEquals(3, shortOfSeed.get(0).size());
		assertEquals(2, shortOfSeed.get(1).size(), "2 seeds pooled for 3 plots");
		assertEquals("no minecraft:wheat_seeds", HarvestReplant.replantWhy("minecraft:wheat", true, 0));
		assertEquals("no farmland (trampled?)", HarvestReplant.replantWhy("minecraft:carrots", false, 5));
		assertEquals("no minecraft:potato", HarvestReplant.replantWhy("minecraft:potatoes", true, 0), "a potato replants a potato");
	}

	@Test
	void aDropOnAPlotIsFetchedFromAStandableCell() { // S96 lane B clean floor: 3 harvested, wheat +1, no seeds
		// the CP-38 layout at y 150: dry stone z -65, water z -64, farmland z -63 and -62, dry pad z -61 and south; she stands at y 151
		java.util.function.Predicate<BlockPos> dry = c -> c.getY() == 151 && (c.getZ() <= -65 || c.getZ() >= -61);
		java.util.function.Predicate<BlockPos> flat = c -> c.getY() == 151 && c.getZ() != -64;
		BlockPos her = new BlockPos(-40, 151, -60);
		int level = 150; // a drop at y 150.94 on farmland: its block level is the farmland itself
		assertNull(HarvestReplant.dropStand(-40.5, -62.5, level, her, dry), "row -63, behind the ditch: no dry cell in pickup range");
		BlockPos s = HarvestReplant.dropStand(-40.5, -62.5, level, her, flat);
		assertEquals(151, s.getY(), "the cell over the farmland, not the farmland block: " + s);
		assertEquals(-62, s.getZ(), "the near farmland row, never the water: " + s);
		assertTrue(Math.abs(s.getX() + 0.5 - -40.5) <= HarvestReplant.PICKUP && Math.abs(s.getZ() + 0.5 - -62.5) <= HarvestReplant.PICKUP);
		assertEquals(-61, HarvestReplant.dropStand(-40.5, -61.5, level, her, dry).getZ(), "row -62: the dry pad reaches it");
		assertNull(HarvestReplant.dropStand(-40.5, -62.5, level, her, c -> false), "nothing standable: it stays");
		assertTrue(HarvestReplant.DROPS.contains("minecraft:wheat") && HarvestReplant.DROPS.contains("minecraft:wheat_seeds"));
		assertFalse(HarvestReplant.DROPS.contains("minecraft:diamond"), "only what crops drop");
	}

	/**
	 * The failing shape from lane B, clean floor (attempt 4): 3 ripe plots, nothing in the bag beforehand and nothing old on the
	 * ground; each plot's 1 wheat and 1 seed land on the plot itself. Every drop must have a stand in pickup range, so the seeds
	 * are in the bag before replanting: harvested == 3, wheat +3, replanted == harvested. (A stand-in for the tick: walking and
	 * the body's pickup are the lane's to prove.)
	 */
	@Test
	void cleanFloorEveryDropIsReachableAndEveryPlotReplanted() {
		java.util.function.Predicate<BlockPos> flat = c -> c.getY() == 151 && c.getZ() != -64;
		BlockPos her = new BlockPos(-40, 151, -60);
		List<BlockPos> plots = List.of(new BlockPos(-42, 151, -63), new BlockPos(-38, 151, -63), new BlockPos(-37, 151, -63));
		int wheat = 0, seeds = 0;
		for (BlockPos q : plots) {
			assertTrue(HarvestReplant.wanted("minecraft:wheat", 7, true, "minecraft:wheat", true));
			assertEquals("no minecraft:wheat_seeds", HarvestReplant.replantWhy("minecraft:wheat", true, 0), "no seeds until the sweep");
			BlockPos s = HarvestReplant.dropStand(q.getX() + 0.5, q.getZ() + 0.5, 150, her, flat);
			assertNotNull(s, "a stand for the drop on " + q.toShortString());
			her = s;
			wheat++;
			seeds++;
		}
		assertEquals(3, wheat);
		int replanted = 0;
		for (BlockPos q : plots) if (HarvestReplant.replantWhy("minecraft:wheat", true, seeds) == null) { seeds--; replanted++; }
		assertEquals(plots.size(), replanted);
	}

	@Test
	void cropKindCountAndBoxLimits() {
		assertEquals(List.of(new BlockPos(2, 64, 1)), harvest(field(), "minecraft:carrots", 1024, false, Map.of()).get(0));
		assertEquals(2, harvest(field(), null, 2, false, Map.of()).get(0).size(), "count 2");
		assertTrue(harvest(field(), null, 1024, false, Map.of()).get(1).isEmpty(), "replant false: nothing planted");
		assertFalse(HarvestReplant.wanted("minecraft:wheat", 7, false, null, true), "harvest_crop (primitive) needs farmland");
		assertTrue(HarvestReplant.wanted("minecraft:wheat", 7, false, null, false), "harvest_replant takes it on any ground");
		assertFalse(HarvestReplant.wanted("minecraft:wheat", 0, true, null, true), "a fresh sprout (age 0)");
		BlockPos o = new BlockPos(0, 64, 0);
		assertFalse(HarvestReplant.badArea(o, o.offset(31, 3, 31)), "32x4x32 is the limit");
		assertTrue(HarvestReplant.badArea(o, o.offset(32, 0, 0)));
		assertTrue(HarvestReplant.badArea(o, o.offset(0, 4, 0)));
	}
}
