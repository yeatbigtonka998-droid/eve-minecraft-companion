package eve.body;

import com.google.gson.JsonObject;
import eve.body.api.ClientView;
import eve.body.api.Engage;
import eve.body.api.EngageHook;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.world.entity.Entity;
import org.junit.jupiter.api.Test;

import java.util.HashMap;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** H-autofight (API v4), offline: when the hook is asked, how the reflex ends, the hook adapter and the targeting frames. */
class AutoFightTest {
	/** A fake combat hook's view: only what these tests read. */
	static final class FakeView implements ClientView {
		long ticks, lastHit = 990;
		Map<Integer, Targeting> targeting = new HashMap<>();

		public Minecraft minecraft() {
			return null;
		}

		public LocalPlayer player() {
			return null;
		}

		public boolean isProtected(Entity e) {
			return false;
		}

		public int hotbarSlot(String itemId) {
			return itemId.equals("minecraft:iron_sword") ? 0 : -1;
		}

		public Map<String, Integer> inventoryCounts() {
			return Map.of("minecraft:iron_sword", 1);
		}

		public long ticks() {
			return ticks;
		}

		public int lastAttackerId() {
			return 42;
		}

		public long lastHitTick() {
			return lastHit;
		}

		public Map<Integer, Targeting> targeting() {
			return targeting;
		}
	}

	/** Combat's rule in miniature: engage whoever hit her in the last 3 s, with a sword. */
	static final EngageHook FAKE_COMBAT = v -> v.lastAttackerId() >= 0 && v.ticks() - v.lastHitTick() <= 60 && v.hotbarSlot("minecraft:iron_sword") >= 0
			? new Engage("fight_melee", new JsonObject(), v.lastAttackerId(), "hit") : null;

	@Test
	void theHookIsAskedOnlyWhenSheMayFight() {
		assertTrue(Reflexes.autoFightMayPoll(false, 20f, true, false, true));
		assertFalse(Reflexes.autoFightMayPoll(true, 20f, true, false, true), "never in STOPPED");
		assertFalse(Reflexes.autoFightMayPoll(false, 6f, true, false, true), "at the floor the floor rules own her");
		assertFalse(Reflexes.autoFightMayPoll(false, 20f, false, false, true), "the owner switched auto_fight off");
		assertFalse(Reflexes.autoFightMayPoll(false, 20f, true, true, true), "a fight action already runs");
		assertFalse(Reflexes.autoFightMayPoll(false, 20f, true, false, false), "no provider hook");
	}

	@Test
	void aFightTakesOverAShieldThatOnlyWaits() { // S96 a119: the drawn-bow shield held 120 s and auto_fight never engaged
		assertTrue(Reflexes.engagesOverHeldShield(true, true, false));
		assertFalse(Reflexes.engagesOverHeldShield(true, true, true), "an arrow in flight stays the shield's");
		assertFalse(Reflexes.engagesOverHeldShield(false, true, false), "only a fight the hook asks for");
		assertFalse(Reflexes.engagesOverHeldShield(true, false, false), "another reflex: the priority rule");
	}

	@Test
	void itEndsOnTheFloorTheCapOrSixtyQuietTicks() {
		assertEquals("floor", Reflexes.autoFightEnd(6f, 1, 0));
		assertEquals("timeout", Reflexes.autoFightEnd(20f, Reflexes.AUTO_FIGHT_CAP_NS + 1, 0));
		assertEquals("clear", Reflexes.autoFightEnd(20f, 1, 60), "no eligible hostile for 3 s");
		assertNull(Reflexes.autoFightEnd(20f, 1, 59));
	}

	@Test
	void theFakeCombatHookPicksThroughTheAdapter() {
		FakeView v = new FakeView();
		v.ticks = 1000;
		Engage e = Providers.engage(FAKE_COMBAT, v);
		assertEquals("fight_melee", e.skill());
		assertEquals(42, e.targetId());
		assertEquals("hit", e.reason());
		v.ticks = 1100; // the hit (tick 990) is 110 ticks old
		assertNull(Providers.engage(FAKE_COMBAT, v));
		assertNull(Providers.engage(null, v), "no hook: nothing");
		assertNull(Providers.engage(view -> { throw new IllegalStateException("bug"); }, v), "a hook that throws never kills the tick");
	}

	@Test
	void theStartEventReasonsIncludeTargetingEve() { // kickoff ruling 6a77f4a1
		assertEquals(java.util.Set.of("near", "drawing", "hit", "owner_targeted", "targeting_eve"), Reflexes.ENGAGE_REASONS);
	}

	@Test
	void targetingFramesOlderThanThreeSecondsAreDropped() {
		Map<Integer, ClientView.Targeting> all = new HashMap<>();
		all.put(7, new ClientView.Targeting("owner", "Hunter", 100));
		all.put(8, new ClientView.Targeting("eve", "Eve", 150));
		Map<Integer, ClientView.Targeting> fresh = Providers.freshTargeting(all, 165);
		assertFalse(fresh.containsKey(7), "65 ticks old: stale");
		assertSame(all.get(8), fresh.get(8));
	}

	@Test
	void aTargetThatEndedAFightWithNoKillIsLeftForThirtySeconds() { // O311/O313
		assertTrue(Reflexes.engageCooling(7, "near", 7, 1000, 1599), "just inside 30 s");
		assertFalse(Reflexes.engageCooling(7, "near", 7, 1000, 1600), "30 s on: again");
		assertFalse(Reflexes.engageCooling(8, "near", 7, 1000, 1100), "another target: at once");
		assertFalse(Reflexes.engageCooling(7, "hit", 7, 1000, 1100), "it hit her: at once");
		assertFalse(Reflexes.engageCooling(7, "owner_targeted", 7, 1000, 1100), "it hit the owner: at once");
	}
}
