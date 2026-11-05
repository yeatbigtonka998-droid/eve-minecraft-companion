package eve.combat;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class HarvestRulesTest {
	/** alive, animal, wantedType, baby, named, tamed, leashed, protected, breedingStock, inZone */
	private static HarvestRules.Animal a(boolean... f) {
		return new HarvestRules.Animal(f[0], f[1], f[2], f[3], f[4], f[5], f[6], f[7], f[8], f[9]);
	}

	private static final boolean T = true, F = false;

	@Test
	void onlyAGrantedAdultFreeAnimalOfTheWantedTypeInTheZoneMayBeHit() {
		assertNull(HarvestRules.refusal(a(T, T, T, F, F, F, F, F, F, T)));
		assertEquals("client:gone", HarvestRules.refusal(a(F, T, T, F, F, F, F, F, F, T)));
		assertEquals("client:wrong_type", HarvestRules.refusal(a(T, F, T, F, F, F, F, F, F, T)), "a villager id");
		assertEquals("client:wrong_type", HarvestRules.refusal(a(T, T, F, F, F, F, F, F, F, T)), "a sheep when types = [cow]");
		assertEquals("client:excluded", HarvestRules.refusal(a(T, T, T, F, F, F, F, F, T, T)));
		assertEquals("animal:baby", HarvestRules.refusal(a(T, T, T, T, F, F, F, F, F, T)));
		assertEquals("protected:named", HarvestRules.refusal(a(T, T, T, F, T, F, F, T, F, T)), "named wins over the generic protected");
		assertEquals("protected:pets", HarvestRules.refusal(a(T, T, T, F, F, T, F, T, F, T)));
		assertEquals("animal:leashed", HarvestRules.refusal(a(T, T, T, F, F, F, T, F, F, T)));
		assertEquals("protected:pets", HarvestRules.refusal(a(T, T, T, F, F, F, F, T, F, T)));
		assertEquals("client:out_of_zone", HarvestRules.refusal(a(T, T, T, F, F, F, F, F, F, F)));
		assertEquals("client:excluded", HarvestRules.refusal(a(T, T, T, T, T, T, T, T, T, F)), "the mind's exclusion first of the animal rules");
	}

	@Test
	void keepFloorStopsBeforeTheHerdGoesBelowKeep() {
		assertTrue(HarvestRules.keepAllows(5, 2), "5 adults, keep 2: kill one -> 4 left");
		assertTrue(HarvestRules.keepAllows(3, 2), "3 -> 2 left = keep: allowed");
		assertFalse(HarvestRules.keepAllows(2, 2), "2 -> 1 left < keep 2: refused");
		assertFalse(HarvestRules.keepAllows(1, 1));
		assertTrue(HarvestRules.keepAllows(1, -1), "no floor");
		assertTrue(HarvestRules.keepAllows(1, 0), "keep 0: the last one may go");
	}

	@Test
	void zoneBoxIsInclusiveAndClampKeepsWalksInside() {
		var z = HarvestRules.Zone.of(new int[] {10, 64, -5}, new int[] {0, 60, 5}); // corners in any order
		assertTrue(z.contains(0.0, 60.0, -5.0));
		assertTrue(z.contains(10.9, 64.9, 5.9), "the far blocks are inside");
		assertFalse(z.contains(11.0, 62, 0), "one block past x1");
		assertFalse(z.contains(-0.1, 62, 0), "just past x0");
		double[] c = z.clamp(30, 70, -40); // a fleeing cow far outside: the walk stops at the zone's edge
		assertTrue(z.contains(c[0], c[1], c[2]));
		assertEquals(10.5, c[0]);
		assertEquals(-4.5, c[2]);
		assertArrayEquals(new double[] {4.2, 62, 1.3}, z.clamp(4.2, 62, 1.3), "a point already inside is unchanged");
	}
}
