package eve.combat;

import com.google.gson.JsonParser;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

/**
 * O98c: on registry 0.15.0 the mind cannot pass doorway, so 5ad81fe3 must fight exactly as the lane-proven d7f5b8ff without it.
 * Every doorway path in fight_melee is behind door != null (the hold, the drift check, doorWalk); without the param door stays
 * null and the only difference is the extra evidence key door_hold_ticks = 0.
 */
class FightMeleeDoorwayParamTest {
	@Test
	void withoutDoorwayThereIsNoDoorAndNoHold() {
		var fm = new FightMelee(JsonParser.parseString("{\"max_targets\":1,\"radius\":16}").getAsJsonObject());
		assertNull(fm.door, "no doorway param: the pre-O98 fight, nothing gated on door runs");
		assertEquals(0, fm.doorHoldTicks);
		var plain = new FightMelee(JsonParser.parseString("{\"entity_id\":7}").getAsJsonObject());
		assertNull(plain.door);
	}

	@Test
	void withDoorwayTheDoorIsTheA16Site() {
		var fm = new FightMelee(JsonParser.parseString(
				"{\"entity_id\":7,\"doorway\":{\"pos\":{\"x\":0,\"y\":200,\"z\":-17},\"out\":\"south\"}}").getAsJsonObject());
		assertEquals(Doorway.of(0, 200, -17, "south"), fm.door);
	}

	@Test
	void aMalformedDoorwayFailsLoudlyNotSilently() {
		assertThrows(IllegalArgumentException.class, () -> new FightMelee(JsonParser.parseString(
				"{\"doorway\":{\"pos\":{\"x\":0,\"y\":200,\"z\":-17},\"out\":\"up\"}}").getAsJsonObject()));
	}
}
