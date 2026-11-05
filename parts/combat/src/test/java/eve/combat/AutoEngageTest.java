package eve.combat;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static eve.combat.AutoEngage.*;
import static org.junit.jupiter.api.Assertions.*;

/** H-autofight: AutoEngage.pick against fake views. */
class AutoEngageTest {
	static final Kit SWORD = new Kit(true, false, 0), SWORD_BOW = new Kit(true, true, 16), NOTHING = new Kit(false, false, 0);
	static final Map<Integer, Targeting> NONE = Map.of();

	static Mob zombie(int id, double d) { return new Mob(id, "minecraft:zombie", d, true, false, false, false, false); }

	static Mob skeleton(int id, double d, boolean drawing) { return new Mob(id, "minecraft:skeleton", d, true, false, false, true, drawing); }

	@Test
	void aZombieWithinEightIsFoughtWithTheSwordInGroupMode() {
		var e = pick(List.of(zombie(1, 5)), SWORD, 100, null, 0, NONE);
		assertEquals("fight_melee", e.skill());
		assertEquals("near", e.reason());
		assertFalse(e.params().has("entity_id"), "group mode, radius 8: it takes the others near her too");
		assertEquals(8, e.params().get("radius").getAsInt());
	}

	@Test
	void aPhantomIsTheBowsOrNobodys() { // live 45L death 17:16:03: 17 auto_fights at phantoms, all fight_melee
		var phantom = new Mob(1, "minecraft:phantom", 3, true, false, false, false, false, true, true);
		var e = pick(List.of(phantom), SWORD_BOW, 100, 1, 95, NONE);
		assertEquals("fight_ranged", e.skill(), "swooping inside 6 m is still a bow fight");
		assertNull(pick(List.of(phantom), SWORD, 100, 1, 95, NONE), "no bow: never melee-chase it");
		assertNull(pick(List.of(phantom), new Kit(true, true, 0), 100, 1, 95, NONE), "S105: her live kit, a bow and 0 arrows: no fight, body's cover");
		assertNull(pick(List.of(phantom), new Kit(true, true, 3), 100, 1, 95, NONE), "under MIN_ARROWS counts as no bow");
		assertNull(pick(List.of(new Mob(1, "minecraft:phantom", 3, true, false, false, false, false, false, true)), SWORD_BOW, 100, 1, 95, NONE), "unseen: no shot");
		assertEquals("fight_melee", pick(List.of(phantom, zombie(2, 4)), SWORD, 100, null, 0, NONE).skill(), "the zombie is still fought");
	}

	@Test
	void nothingNearMeansNoFight() {
		assertNull(pick(List.of(zombie(1, 12)), SWORD, 100, null, 0, NONE));
	}

	@Test
	void anUnseenMobIsNotEngagedUnlessItHitHerOrTheOwnersTarget() {
		var unseen = new Mob(1, "minecraft:skeleton", 7, true, false, false, true, false, false);
		assertNull(pick(List.of(unseen), SWORD_BOW, 100, null, 0, NONE));
		assertNull(pick(List.of(unseen), SWORD_BOW, 100, null, 0, Map.of(1, new Targeting("eve", 100))));
		assertEquals("owner_targeted", pick(List.of(unseen), SWORD_BOW, 100, null, 0, Map.of(1, new Targeting("owner", 100))).reason());
		assertEquals("hit", pick(List.of(unseen), SWORD_BOW, 100, 1, 95, NONE).reason());
	}

	@Test
	void aCreeperWithinTenAndNoBowBreaksTheFightOff() {
		var zombie = new Mob(1, "minecraft:zombie", 2, true, false, false, false, false);
		var creeper = new Mob(2, "minecraft:creeper", 8, true, false, true, false, false);
		assertNull(pick(List.of(zombie, creeper), new Kit(true, false, 0), 100, 1, 95, NONE));
		assertNull(pick(List.of(zombie, creeper), new Kit(true, true, 3), 100, 1, 95, NONE));
		assertNull(pick(List.of(zombie, new Mob(2, "minecraft:creeper", 11, true, false, true, false, false)), new Kit(true, false, 0), 100, null, 0, NONE));
		var far = new Mob(2, "minecraft:creeper", 17, true, false, true, false, false);
		assertEquals("fight_melee", pick(List.of(zombie, far), new Kit(true, false, 0), 100, null, 0, NONE).skill());
		assertTrue(breakOffForCreeper(3, false, 0));
		assertFalse(breakOffForCreeper(3, true, 4));
	}

	@Test
	void aCreeperInBowRangeGoesFirstOnlyWithABow() {
		var zombie = new Mob(1, "minecraft:zombie", 2, true, false, false, false, false);
		var creeper = new Mob(2, "minecraft:creeper", 8, true, false, true, false, false);
		var e = pick(List.of(zombie, creeper), SWORD_BOW, 100, 1, 95, NONE);
		assertEquals("fight_ranged", e.skill());
		assertEquals(2, e.targetId());
		assertEquals("fight_melee", pick(List.of(zombie, new Mob(2, "minecraft:creeper", 11, true, false, true, false, false)), SWORD_BOW, 100, null, 0, NONE).skill());
		assertFalse(creeperFirst(4.9, true, 16));
		assertFalse(creeperFirst(8, true, 3));
		assertTrue(creeperFirst(5, true, 4));
	}

	@Test
	void protectedCalmAndCreepersAreNeverEngaged() {
		var named = new Mob(1, "minecraft:zombie", 3, true, true, false, false, false);
		var calm = new Mob(2, "minecraft:enderman", 3, false, false, false, false, false);
		var creeper = new Mob(3, "minecraft:creeper", 3, true, false, true, false, false);
		assertNull(pick(List.of(named, calm, creeper), SWORD_BOW, 100, null, 0, NONE));
	}

	@Test
	void noWeaponMeansNoFight() {
		assertNull(pick(List.of(zombie(1, 3)), NOTHING, 100, null, 0, NONE), "body's retreat handles it");
		assertNull(pick(List.of(zombie(1, 3)), new Kit(false, true, 2), 100, null, 0, NONE), "a bow with too few arrows is no weapon");
	}

	@Test
	void anArcherDrawingAtHerIsShotWhenSheHasABow() {
		var e = pick(List.of(skeleton(7, 14, true)), SWORD_BOW, 100, null, 0, NONE);
		assertEquals("fight_ranged", e.skill());
		assertEquals("drawing", e.reason());
		assertEquals(7, e.params().get("entity_id").getAsInt());
		assertEquals("fight_melee", pick(List.of(skeleton(7, 14, true)), SWORD, 100, null, 0, NONE).skill(), "no bow: the sword (rushes it)");
	}

	@Test
	void whatHitHerComesFirst() {
		var e = pick(List.of(zombie(1, 2), zombie(9, 7)), SWORD, 100, 9, 80, NONE);
		assertEquals(9, e.targetId());
		assertEquals("hit", e.reason());
		assertEquals("near", pick(List.of(zombie(1, 2), zombie(9, 7)), SWORD, 200, 9, 80, NONE).reason(), "the hit is 6 s old: back to nearest");
	}

	@Test
	void aMobHuntingTheOwnerCountsWithinTwentyFour() {
		var hunter = Map.of(5, new Targeting("owner", 95));
		var e = pick(List.of(zombie(5, 18)), SWORD, 100, null, 0, hunter);
		assertEquals("owner_targeted", e.reason());
		assertEquals(5, e.params().get("entity_id").getAsInt(), "beyond the group radius: named");
		assertNull(pick(List.of(zombie(5, 30)), SWORD, 100, null, 0, hunter), "beyond 24: not hers to chase");
	}

	@Test
	void aStaleTargetingFrameIsIgnored() {
		var old = Map.of(5, new Targeting("owner", 30)); // 70 ticks = 3.5 s old
		assertNull(pick(List.of(zombie(5, 18)), SWORD, 100, null, 0, old));
		assertNotNull(pick(List.of(zombie(5, 18)), SWORD, 100, null, 0, Map.of(5, new Targeting("owner", 40))), "exactly 3 s still counts");
	}
}
