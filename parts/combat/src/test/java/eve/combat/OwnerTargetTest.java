package eve.combat;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

/** O136: owner_target lifts the protected-type rule for the one mob the owner named, and nothing else. */
class OwnerTargetTest {
	@Test
	void theNamedTraderIsEngaged() {
		assertNull(Tactics.ownerTarget(true, 5861, 5861, false, true), "Hunter: 'fight the wandering trader' -> engaged");
	}

	@Test
	void aPlayerIsRefusedEvenWithTheFlag() {
		var r = Tactics.ownerTarget(true, 42, 42, true, true);
		assertNotNull(r);
		assertEquals("protected_entity", r[0]);
	}

	@Test
	void theFlagWithoutEntityIdIsRefused() {
		var r = Tactics.ownerTarget(true, null, 7, false, true);
		assertNotNull(r);
		assertEquals("bad_params", r[0]);
		assertEquals("bad_params", Tactics.ownerTarget(true, null, null, false, false)[0], "refused before any target is chosen");
	}

	@Test
	void withoutTheFlagOrForAnotherMobTheRuleStands() {
		assertEquals("protected_entity", Tactics.ownerTarget(false, 5861, 5861, false, true)[0], "no flag: protected as before");
		assertEquals("protected_entity", Tactics.ownerTarget(true, 5861, 77, false, true)[0], "only the named one, not its neighbour");
		assertNull(Tactics.ownerTarget(false, null, 9, false, false), "an ordinary hostile: unchanged");
	}
}
